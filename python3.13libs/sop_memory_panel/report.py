from PySide6 import QtCore, QtGui, QtWidgets

from . import diff as pgdiff
from .model import SCOPES, row_key
from .style import Style, _rgb_css, human_bytes


# ---------------------------------------------------------------------------
# Memory report (upper section)
# ---------------------------------------------------------------------------

SORT_ROLE = QtCore.Qt.UserRole + 10      # numeric sort key per column
ATTR_ROLE = QtCore.Qt.UserRole + 11     # the AttributeStats on every attribute row
ROW_KEY_ROLE = QtCore.Qt.UserRole + 12  # row identity (see model.row_key)


def _is_descendant(item, ancestor):
    """True when `item` sits anywhere under `ancestor` (not counting itself)."""
    parent = item.parent()
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent()
    return False


class _AttrItem(QtWidgets.QTreeWidgetItem):
    """Sorts by SORT_ROLE; Pages column is not sortable."""

    NO_SORT_COLUMN = 6      # Pages c/s/h

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        if col == _AttrItem.NO_SORT_COLUMN:
            return False    # stable: leave the current order alone
        a = self.data(col, SORT_ROLE)
        b = other.data(col, SORT_ROLE)
        if a is None:
            a = self.text(col)
        if b is None:
            b = other.text(col)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class _OwnerItem(QtWidgets.QTreeWidgetItem):
    """Keeps canonical owner order regardless of sort."""

    def __init__(self, texts, index):
        super().__init__(texts)
        self._index = index

    def __lt__(self, other):
        tree = self.treeWidget()
        ascending = (not tree or
                     tree.header().sortIndicatorOrder() == QtCore.Qt.AscendingOrder)
        other_index = getattr(other, "_index", 0)
        # Invert under descending so Qt's reversal still yields canonical order.
        return self._index < other_index if ascending else self._index > other_index


class PercentBarDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a proportional bar + '%' text for the percentage column."""

    def paint(self, painter, option, index):
        percent = index.data(QtCore.Qt.UserRole)
        if percent is None:
            super().paint(painter, option, index)
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        track = QtGui.QColor(*Style.role(Style.PCT_TRACK_ROLE, Style.Color.PCT_TRACK))
        fill = QtGui.QColor(*Style.role(Style.PCT_FILL_ROLE, Style.Color.PCT_FILL))
        painter.save()
        painter.fillRect(rect, track)
        bar_width = int(rect.width() * min(max(percent, 0.0), 100.0) / 100.0)
        bar = QtCore.QRect(rect.left(), rect.top(), bar_width, rect.height())
        painter.fillRect(bar, fill)

        text = f"{percent:.1f}%"
        for clip, role, fallback in (
                (bar, Style.PCT_FILL_FG_ROLE, Style.Color.PCT_TEXT),
                (QtCore.QRect(bar.right() + 1, rect.top(),
                              rect.right() - bar.right(), rect.height()),
                 Style.PCT_TRACK_FG_ROLE, Style.Color.PCT_TEXT)):
            if clip.width() <= 0:
                continue
            painter.save()
            painter.setClipRect(clip)
            painter.setPen(QtGui.QColor(*Style.role(role, fallback)))
            painter.drawText(rect, QtCore.Qt.AlignCenter, text)
            painter.restore()
        painter.restore()


class RowTree(QtWidgets.QTreeWidget):
    """Tree that remembers selection, expansion, and bar toggles across rebuilds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_key = None      # the current row
        self._expansion = {}           # row key -> the user's expanded/collapsed choice
        self._row_items = {}           # row key -> QTreeWidgetItem, rebuilt per populate
        self._applied_expansion = {}   # row key -> what this build applied, rebuilt too
        # Fit-to-content sizing happens once, on the first populate() that has real
        # data -- never again after that. Without this flag every rebuild (which a
        # scope-filter toggle alone already triggers) re-fits every column, silently
        # discarding a width the user just dragged: "laid out" is a one-time event,
        # not something a later refresh gets to redo.
        self._columns_sized = False
        self.currentItemChanged.connect(self._remember_current)
        self.itemExpanded.connect(self._remember_expanded)
        self.itemCollapsed.connect(self._remember_collapsed)

    def _reset_row_state(self):
        """Clear per-build maps; remembered state survives."""
        self._row_items = {}
        self._applied_expansion = {}

    def _auto_size_columns_once(self):
        """Fit every column to its contents, but only the first time this is called
        with the tree non-empty -- see `_columns_sized`."""
        if self._columns_sized:
            return
        for c in range(self.columnCount()):
            self.resizeColumnToContents(c)
            self.setColumnWidth(c, self.columnWidth(c) + Style.Layout.COL_PAD)
        self._columns_sized = True

    def _record_row(self, item, key):
        item.setData(0, ROW_KEY_ROLE, key)
        self._row_items[key] = item

    def _apply_expansion(self, item, key, default_collapsed):
        """Apply remembered expansion or the row's default; record the result."""
        decided = self._expansion.get(key, not default_collapsed)
        item.setExpanded(decided)
        self._applied_expansion[key] = decided

    # -- Remembered view state ------------------------------------------------

    def _remember_current(self, current, _previous):
        # Ignores None so a clear() / empty model does not lose the remembered row.
        if current is not None:
            self._selected_key = current.data(0, ROW_KEY_ROLE)

    def _remember_expanded(self, item):
        self._set_expansion(item, True)

    def _remember_collapsed(self, item):
        self._set_expansion(item, False)
        # A hidden selection still dims page bars — clear it with the section.
        current = self.currentItem()
        if current is not None and _is_descendant(current, item):
            self.clear_selection()

    def _set_expansion(self, item, expanded):
        key = item.data(0, ROW_KEY_ROLE)
        if key is not None:
            self._expansion[key] = expanded

    def clear_selection(self):
        """Deselect and forget the remembered row."""
        self._selected_key = None
        self.clearSelection()
        self.setCurrentItem(None)

    def mousePressEvent(self, event):
        """Click in empty space clears the selection."""
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if not index.isValid():
            self.clear_selection()          # empty space below the rows
        elif pos.x() < self.visualRect(index).left() - self.indentation():
            self.clear_selection()
            return                          # do NOT let the base class re-select the row
        super().mousePressEvent(event)

    def _restore_selection(self):
        """Restore the remembered selection, expanding ancestors as needed."""
        item = self._row_items.get(self._selected_key)
        if item is None:
            return
        blocked = self.blockSignals(True)
        try:
            self.setCurrentItem(item)
            # setCurrentItem() expands all ancestors — undo that for any
            # ancestor _build decided should be collapsed.
            ancestor = item.parent()
            while ancestor is not None:
                decided = self._applied_expansion.get(ancestor.data(0, ROW_KEY_ROLE))
                if decided is not None:
                    ancestor.setExpanded(decided)
                ancestor = ancestor.parent()
        finally:
            self.blockSignals(blocked)


class MemoryReport(RowTree):
    """Renders a MemoryModel's breakdown tree, with a per-attribute bar toggle."""

    # Emitted with the set of (owner, scope, name) whose bar is toggled on.
    toggledAttrsChanged = QtCore.Signal()

    COLS = ("Component", "%", "Total Memory", "New Memory", "Unique Memory",
            "Data ID", "Pages c/s/h", "Type")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("memReport")
        # Base (input-box colour) is wrong below the last row — Window matches the panel.
        self.viewport().setBackgroundRole(QtGui.QPalette.Window)
        self.viewport().setAutoFillBackground(True)
        self.setColumnCount(len(self.COLS))
        self.setHeaderLabels(self.COLS)
        self.setItemDelegateForColumn(1, PercentBarDelegate(self))
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        header = self.header()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.setSortingEnabled(True)
        self.sortByColumn(2, QtCore.Qt.DescendingOrder)
        # Blank icon the width of a checkbox, so rows with no checkbox of their own
        # (uncheckable attribute leaves, and structural leaf rows like "Name Map"/"Data
        # Structure Overhead") still line up with rows in the same section that do.
        # self.style() can return an already-deleted QProxyStyle under Houdini's
        # PySide — fall back to app style, then a default.
        try:
            iw = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth)
        except RuntimeError:
            app = QtWidgets.QApplication.instance()
            try:
                iw = app.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth)
            except (RuntimeError, AttributeError):
                iw = 16
        blank = QtGui.QPixmap(iw, iw)
        blank.fill(QtCore.Qt.transparent)
        self._blank_icon = QtGui.QIcon(blank)
        self._toggled = set()          # {(owner, scope, name)}
        self._attr_items = {}          # (owner, scope, name) -> QTreeWidgetItem
        self._hover_key = None
        self._peer_keys = frozenset()
        self.itemChanged.connect(self._on_item_changed)

    def set_hover_key(self, key):
        """Outline the row for `key` with a coloured border, or clear with None."""
        if key != self._hover_key:
            self._hover_key = key
            self.viewport().update()

    def set_peer_keys(self, keys):
        """Shade the rows sharing storage with the selected attribute."""
        keys = frozenset(keys or ())
        if keys != self._peer_keys:
            self._peer_keys = keys
            self.viewport().update()

    def drawRow(self, painter, option, index):
        # Peers get a wash OVER the finished row (under would be erased by
        # opaque style fills — H22's UI does this); hover gets a border after.
        # Keyed off ATTR_ROLE, not UserRole — UserRole is absent on non-bar rows.
        super().drawRow(painter, option, index)
        attr = index.data(ATTR_ROLE)
        key = attr.key if attr is not None else None
        row_rect = self.visualRect(index)
        if key is not None and key in self._peer_keys:
            painter.fillRect(
                QtCore.QRect(row_rect.left(), row_rect.top(),
                             self.viewport().width() - row_rect.left(), row_rect.height()),
                QtGui.QColor(*Style.peer_wash()))
        if key is None or key != self._hover_key:
            return
        rect = QtCore.QRect(1, row_rect.top(), self.viewport().width() - 3, row_rect.height() - 1)
        painter.save()
        pen = QtGui.QPen(QtGui.QColor(*Style.Color.HOVER_BORDER))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(rect)
        painter.restore()

    def toggled_attrs(self):
        return set(self._toggled)

    def item_for_key(self, key):
        """The attribute-row item for (owner, scope, name), or None."""
        return self._attr_items.get(key)

    def populate(self, model, scopes=None):
        """Render `model`'s breakdown tree, filtering attribute leaves by `scopes`."""
        if scopes is None:
            scopes = set(SCOPES)
        root_row = model.breakdown(scopes) if model else None
        self.blockSignals(True)
        self.setSortingEnabled(False)          # bulk insert, then re-sort once
        self.clear()
        self._attr_items = {}                  # rebuilt as attr rows are added
        self._reset_row_state()
        try:
            if root_row is not None:
                self._build(None, root_row, model.total_memory or 1)
        finally:
            # Always restore, even on the empty-model early return -- otherwise the
            # tree would be left unsortable with its signals still blocked.
            self.setSortingEnabled(True)
            self.blockSignals(False)
        if root_row is None:
            return
        self._restore_selection()
        # Fit each column to its contents with a little breathing room -- but only
        # once (see _auto_size_columns_once): every scope-filter toggle alone already
        # calls populate() again, and re-fitting then would silently undo a width the
        # user had dragged since the first layout.
        was_sized = self._columns_sized
        self._auto_size_columns_once()
        if not was_sized:
            self.setColumnWidth(1, max(self.columnWidth(1),
                                       Style.Layout.PCT_COL_MIN))   # % bar column

    def visible_attr_keys(self, owner):
        """Toggled attribute keys for `owner`, in the tree's CURRENT VISUAL ORDER."""
        keys = []

        def walk(item):
            key = item.data(0, QtCore.Qt.UserRole)   # set only on toggleable attr rows
            if isinstance(key, tuple) and key[0] == owner and key in self._toggled:
                keys.append(key)
            for i in range(item.childCount()):       # child order tracks the sort
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        return keys

    # -- Row -> item ---------------------------------------------------------

    def _build(self, parent, row, report_total, parent_path=(), pad_if_uncheckable=False):
        """Recursively turn a Row (and its children) into tree items."""
        item = (self._attr_item(row.attr, report_total, bool(row.children), pad_if_uncheckable,
                                label=row.label,
                                data_id_label="" if row.data_id_hidden else None,
                                type_label="" if row.type_hidden else None)
                if row.attr is not None else self._section_item(row, report_total, pad_if_uncheckable))
        # The label path names structural rows; attribute leaves are keyed by their
        # attribute instead (see ROW_KEY_ROLE). Both descend by label, so a row under an
        # attribute row -- the Primitive List per-type breakdown -- still gets a path.
        path = parent_path + (row.label,)
        key = row_key(row, parent_path)
        self._record_row(item, key)
        if parent is None:
            self.addTopLevelItem(item)
            font = item.font(0)                # the geometry total reads as the total
            font.setBold(True)
            for col in range(len(self.COLS)):
                item.setFont(col, font)
        else:
            parent.addChild(item)
        # User's remembered choice outranks the row's default collapsed state.
        self._apply_expansion(item, key, row.collapsed)
        # Icon padding is a per-SIBLING-GROUP decision, not per row: a group where no
        # child is ever checkable (e.g. Index Maps' owner rows, Group Tables > Edge's
        # edge-group rows + Name Map, since EdgeGroupStats.has_page_details is always
        # False) needs no padding on any of them, so they can sit flush with each other.
        any_child_checkable = any(c.attr is not None and c.attr.has_page_details
                                   for c in row.children)
        for child in row.children:
            self._build(item, child, report_total, path,
                        pad_if_uncheckable=any_child_checkable)
        return item

    def _section_item(self, row, report_total, pad_if_uncheckable=False):
        """A structural breakdown row. Uses _OwnerItem to keep canonical owner order."""
        pct = 100.0 * row.total_memory / report_total
        # `note` is display-only (e.g. Unaccounted's tail-initializer count). The model owns
        # the text; we only place it. row.label stays the row's identity.
        label = "%s  (%s)" % (row.label, row.note) if row.note else row.label
        # A structural row rarely names one underlying object with its own data id --
        # but the Primitive List branch does, in full representation (no child there
        # names the list AS A WHOLE the way "Paged Vertex List" does in the compact
        # case) -- so row.data_id is not always blank the way it is for most section
        # rows (Index Maps, Group Tables, etc., where it stays None).
        data_id_label = "" if row.data_id is None else (
            str(row.data_id) if row.data_id >= 0 else "-")
        item = _OwnerItem(
            [label, "", human_bytes(row.total_memory),
             human_bytes(row.new_memory) if row.new_memory is not None else "",
             human_bytes(row.unique_memory) if row.unique_memory is not None else "",
             data_id_label, "", row.type_str],
            row.order)
        item.setData(1, QtCore.Qt.UserRole, pct)               # % bar
        item.setData(1, SORT_ROLE, pct)
        item.setData(2, SORT_ROLE, row.total_memory)
        item.setData(3, SORT_ROLE, row.new_memory if row.new_memory is not None else -1)
        item.setData(4, SORT_ROLE, row.unique_memory if row.unique_memory is not None else -1)
        item.setData(5, SORT_ROLE, row.data_id if row.data_id is not None else -1)
        colour = Style.Color.SCOPE_COLORS.get(row.scope)
        if colour is not None:
            brush = QtGui.QBrush(QtGui.QColor(*colour))
            for c in range(len(self.COLS)):
                item.setForeground(c, brush)

        # Bold marks user-created data that allocated: colour and bold share one
        # gate (Style.Color.SCOPE_COLORS) -- a container or bookkeeping row (no
        # colour) is never bold, no matter what its New column says (root is bolded
        # separately, unconditionally, in _build).
        if colour is not None and row.new_memory:
            font = item.font(0)
            font.setBold(True)
            for c in range(len(self.COLS)):
                item.setFont(c, font)
        if row.data_id is not None and row.data_id >= 0 and row.data_id_inherited:
            # Italic = inherited from an input, same convention as an attribute leaf's
            # Data ID column (see _attr_item) -- applied on a font copy (which may
            # already be bold, above) so it is preserved alongside the italic.
            id_font = QtGui.QFont(item.font(5))
            id_font.setItalic(True)
            item.setFont(5, id_font)
        if pad_if_uncheckable:
            # Same alignment pad _attr_item gives an uncheckable leaf: a structural leaf
            # (e.g. "Name Map", "Data Structure Overhead") can sit beside a checkable
            # attribute leaf (Attribute Set's owner rows mix the two), and needs the
            # same pad to keep its name flush with theirs. Only applied when a SIBLING
            # is actually checkable -- see _build's any_child_checkable.
            item.setIcon(0, self._blank_icon)
        return item

    def _attr_item(self, attr, report_total, has_own_expander=False, pad_if_uncheckable=False,
                   label=None, data_id_label=None, type_label=None):
        """One attribute leaf. Scope is conveyed by row colour, not a name suffix.

        `label` overrides the displayed/sorted name (defaults to attr.name): the
        Primitive List's compact-representation child is labelled "Paged Vertex List"
        in the tree even though attr.name (PrimitiveList.NAME) stays "Primitive List"
        everywhere else the same attr object is used (the grid, the legend, hover text).

        `data_id_label`/`type_label` override the Data ID and Type columns (default to
        attr.data_id_label/attr.type_label): the same compact-representation child
        shares its .attr with the Primitive List branch root, which is where that data
        id and representation label are shown instead, so this row blanks its own
        copies."""
        label = attr.name if label is None else label
        data_id_text = attr.data_id_label if data_id_label is None else data_id_label
        type_text = attr.type_label if type_label is None else type_label
        pct = 100.0 * attr.total_memory / report_total
        item = _AttrItem(
            [label, "", attr.total_label(human_bytes),
             human_bytes(attr.new_memory), human_bytes(attr.unique_memory),
             data_id_text, attr.pages_label, type_text])
        item.setData(1, QtCore.Qt.UserRole, pct)
        if attr.is_tail_initialized:
            item.setToolTip(7, "Registered with the detail's tail-initialize table, so "
                               "appended elements get the default re-asserted.\nThis table "
                               "is what the Unaccounted row measures.")
        # Per-column magnitude keys so header-click sorting is numeric, not by the
        # formatted "1.2 KB" strings.
        item.setData(0, SORT_ROLE, label)
        item.setData(1, SORT_ROLE, pct)
        item.setData(2, SORT_ROLE, attr.total_memory)
        item.setData(3, SORT_ROLE, attr.new_memory)
        item.setData(4, SORT_ROLE, attr.unique_memory)
        item.setData(5, SORT_ROLE, attr.data_id if data_id_label is None else -1)
        item.setData(7, SORT_ROLE, attr.type_name)
        brush = QtGui.QBrush(QtGui.QColor(*Style.Color.SCOPE_COLORS.get(
            attr.scope, Style.Color.ATTR_DEFAULT_FG)))
        font = item.font(0)
        # Every attribute row is colour-bearing user-created data by definition (see
        # _section_item's bold rule), so bold tracks New alone -- no colour-bearing
        # row in this tree has children, so there is nothing left to exclude.
        font.setBold(attr.new_memory != 0)
        for c in range(len(self.COLS)):
            item.setForeground(c, brush)
            item.setFont(c, font)
        # Italic data-id = inherited from an input (applied on a font copy
        # so the row's bold state is preserved). Not applicable when this row's own
        # Data ID column is blanked (data_id_label="") -- nothing there to mark.
        if attr.is_data_id_found_in_inputs and data_id_label is None:
            id_font = QtGui.QFont(font)
            id_font.setItalic(True)
            item.setFont(5, id_font)
            item.setToolTip(5, "This data id was found on an input, so this node did not "
                               "change the VALUES.\nSays nothing about storage -- such an "
                               "attribute can still have New > 0.")
        # Every attribute row, bar or not: peer highlighting has to be able to find and
        # outline attributes that have no page bar (array types, and any row the scope
        # filter left visible).
        item.setData(0, ATTR_ROLE, attr)
        self._attr_items[attr.key] = item

        # Only page-detail attributes can produce a bar; others get no checkbox.
        if attr.has_page_details:
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if attr.key in self._toggled
                               else QtCore.Qt.Unchecked)
            # UserRole only on checkable rows — _on_item_changed uses it to
            # drive the bar toggle; non-checkable rows must not carry it.
            item.setData(0, QtCore.Qt.UserRole, attr.key)
        elif not has_own_expander and pad_if_uncheckable:
            # Pad with blank icon so uncheckable rows align with checkable ones.
            # Rows with an expander arrow already occupy that space. Only applied
            # when a SIBLING is actually checkable -- see _build's any_child_checkable.
            item.setIcon(0, self._blank_icon)
        return item

    def _on_item_changed(self, item, column):
        key = item.data(0, QtCore.Qt.UserRole)
        if column != 0 or key is None:
            return
        if item.checkState(0) == QtCore.Qt.Checked:
            self._toggled.add(key)
        else:
            self._toggled.discard(key)
        self.toggledAttrsChanged.emit()



class _DiffItem(QtWidgets.QTreeWidgetItem):
    """Diff attribute row: sorts on SORT_ROLE, unknowns sort below real numbers."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        if col in DiffReport.NO_SORT_COLUMNS:
            return False        # stable: leave the current order alone
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if a is None or b is None:
            # Sink the unknown. Under a descending sort Qt reverses the result, which puts
            # it back on top -- acceptable, because there it reads as "these could not be
            # ranked" at the head of the list rather than mixed in among the zeros.
            return b is not None
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class DiffReport(RowTree):
    """Renders a diff.DiffRow tree: delta columns only, no absolutes or percentages."""

    COLS = ("Component", "Scope", "Δ Total", "Δ New", "Δ Unique",
            "Δ Pages", "Δ c/s/h", "Data ID", "Type")
    COL_DELTA_TOTAL = 2
    # Neither of these can rank rows meaningfully: c/s/h is three numbers in one cell, and
    # a paired "19 -> 35" is two values. Clicking them does nothing, as the Pages column
    # already does nothing in the report.
    NO_SORT_COLUMNS = (6, 7)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("memReport")     # shares the report's stylesheet rule
        self.viewport().setBackgroundRole(QtGui.QPalette.Window)
        self.viewport().setAutoFillBackground(True)
        self.setColumnCount(len(self.COLS))
        self.setHeaderLabels(self.COLS)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        header = self.header()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.setSortingEnabled(True)
        # Largest growth first: the question this mode is opened to answer.
        self.sortByColumn(self.COL_DELTA_TOTAL, QtCore.Qt.DescendingOrder)

    # -- cells ---------------------------------------------------------------

    @staticmethod
    def _bytes(value):
        """A signed byte delta. A dash where the provider did not measure it, and a plain
        "0" where it measured no change -- a row kept for one column can be genuinely
        unchanged in another, and the two must not look alike."""
        if value is None:
            return "-"
        if value == 0:
            return "0"
        return ("+" if value > 0 else "−") + human_bytes(abs(value))

    @staticmethod
    def _count(value):
        if value is None:
            return "-"
        if value == 0:
            return "0"
        return ("+%d" if value > 0 else "−%d") % abs(value)

    @classmethod
    def _csh(cls, triple):
        """Three slots always, so the numbers stay under their header letters. A dash per
        slot the provider could not measure -- never a zero, which would claim a
        measurement was taken."""
        if all(v is None for v in triple):
            return "-"
        return "/".join(cls._count(v) if v is not None else "-" for v in triple)

    def _colour_for(self, value):
        if value is None:
            return Style.Color.DELTA_UNKNOWN
        if value == 0:
            return Style.Color.DELTA_ZERO
        return Style.Color.DELTA_UP if value > 0 else Style.Color.DELTA_DOWN

    # Font cue per status — colour alone is insufficient (red/green/violet tints).
    STATUS_FONT = {"added": "bold", "removed": "strike", "replaced": "italic"}

    @classmethod
    def _name_cell(cls, row):
        # Display-only annotation from the model (Unaccounted's tail-initializer count).
        return "%s  (%s)" % (row.label, row.note) if row.note else row.label

    @classmethod
    def _apply_status_font(cls, item, status):
        """Across EVERY column, not just the name. The state is a fact about the whole row
        -- a removed attribute's figures describe something that is gone, and striking only
        its name leaves them reading as current."""
        style = cls.STATUS_FONT.get(status)
        if style is None:
            return
        font = item.font(0)
        if style == "bold":
            font.setBold(True)
        elif style == "italic":
            font.setItalic(True)
        else:
            font.setStrikeOut(True)
        for col in range(len(cls.COLS)):
            item.setFont(col, font)

    def _item(self, row):
        # Structural rows keep canonical order under every sort.
        texts = [
            self._name_cell(row),
            row.scope or "",
            self._bytes(row.d_total), self._bytes(row.d_new), self._bytes(row.d_unique),
            self._count(row.d_pages), self._csh(row.d_csh),
            row.data_id, row.type_str]
        item = _DiffItem(texts) if row.is_attr else _OwnerItem(texts, row.order)

        # Sort on the magnitudes, not the rendered "+4.1 KB" strings. None sorts below any
        # real number rather than as zero: unknown is not a small change.
        item.setData(0, SORT_ROLE, row.label)
        item.setData(1, SORT_ROLE, row.scope or "")
        for col, value in ((2, row.d_total), (3, row.d_new), (4, row.d_unique),
                           (5, row.d_pages)):
            item.setData(col, SORT_ROLE, value)
        item.setData(8, SORT_ROLE, row.type_str)

        if row.a_total is not None and row.b_total is not None:
            item.setToolTip(self.COL_DELTA_TOTAL, "%s → %s"
                            % (human_bytes(row.a_total), human_bytes(row.b_total)))

        for col, value in ((2, row.d_total), (3, row.d_new), (4, row.d_unique),
                           (5, row.d_pages)):
            item.setForeground(col, QtGui.QBrush(QtGui.QColor(*self._colour_for(value))))

        # The name and scope columns keep the report's scope colouring, so a group still
        # reads as a group here.
        colour = Style.Color.SCOPE_COLORS.get(row.scope)
        if colour is not None:
            brush = QtGui.QBrush(QtGui.QColor(*colour))
            item.setForeground(0, brush)
            item.setForeground(1, brush)
        # A paired value is the widest thing in either column and the Type column is last,
        # so "paged primitive list → full representation" loses its right half to the panel
        # edge -- and the half it loses is the half that says what changed.
        for col, text in ((7, row.data_id), (8, row.type_str)):
            if pgdiff.ARROW in text:
                item.setToolTip(col, text)
        self._apply_status_font(item, row.status)
        if row.status == "replaced":
            item.setToolTip(8, "Same owner, scope and name, but a different attribute "
                               "type.\nThe memory delta is a replacement rather than "
                               "growth, and\nthe page split compares different kinds of "
                               "storage.")
        return item

    # -- build ---------------------------------------------------------------

    def populate(self, root):
        """Render an already-pruned DiffRow tree. `root` is None when nothing differs;
        the panel draws its own message for that rather than an empty tree."""
        self.blockSignals(True)
        self.setSortingEnabled(False)          # bulk insert, then re-sort once
        self.clear()
        self._reset_row_state()
        try:
            if root is not None:
                self._build(None, root, ())
        finally:
            self.setSortingEnabled(True)
            self.blockSignals(False)
        if root is None:
            return
        self._restore_selection()
        # Only once -- see MemoryReport.populate()'s identical comment. A diff refresh
        # (a new pin, a live re-cook) must not undo a width the user already set.
        self._auto_size_columns_once()

    def _build(self, parent, row, parent_path):
        item = self._item(row)
        path = parent_path + (row.label,)
        # Uses DiffRow.key (the join key), so a selection survives switching
        # between MemoryReport and DiffReport.
        self._record_row(item, row.key)
        if parent is None:
            self.addTopLevelItem(item)
            font = item.font(0)
            font.setBold(True)
            for col in range(len(self.COLS)):
                item.setFont(col, font)
        else:
            parent.addChild(item)
        # A pruned tree is all signal, so every row opens. The user's own choice still
        # outranks that, exactly as it does in the report.
        self._apply_expansion(item, row.key, False)
        for child in row.children:
            self._build(item, child, path)
        return item
