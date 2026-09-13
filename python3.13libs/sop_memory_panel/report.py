from PySide6 import QtCore, QtGui, QtWidgets

from . import diff as pgdiff
from .model import SCOPES, data_id_text, row_key, total_label
from .style import Style, _rgb_css, human_bytes


# ---------------------------------------------------------------------------
# Memory report (upper section)
# ---------------------------------------------------------------------------

SORT_ROLE = QtCore.Qt.UserRole + 10      # numeric sort key per column
ATTRIB_ROLE = QtCore.Qt.UserRole + 11   # the AttributeStats on every attribute row
ROW_KEY_ROLE = QtCore.Qt.UserRole + 12  # row identity (see model.row_key)


def _is_descendant(item, ancestor):
    """True when `item` sits anywhere under `ancestor` (not counting itself)."""
    parent = item.parent()
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent()
    return False


def _sort_less(a, b):
    try:
        return a < b
    except TypeError:
        return str(a) < str(b)


def _set_font_on_every_column(item, font):
    for column in range(item.columnCount()):
        item.setFont(column, font)


def _set_foreground_on_every_column(item, brush):
    for column in range(item.columnCount()):
        item.setForeground(column, brush)


class _AttributeItem(QtWidgets.QTreeWidgetItem):
    """Sorts by SORT_ROLE; Pages column is not sortable."""

    NO_SORT_COLUMN = 6      # Pages c/s/h

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        if col == _AttributeItem.NO_SORT_COLUMN:
            return False    # stable: leave the current order alone
        a = self.data(col, SORT_ROLE)
        b = other.data(col, SORT_ROLE)
        if a is None:
            a = self.text(col)
        if b is None:
            b = other.text(col)
        return _sort_less(a, b)


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
        self.setObjectName("memReport")
        # Base (input-box colour) is wrong below the last row — Window matches the panel.
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
        self._selected_key = None      # the current row
        self._expansion = {}           # row key -> the user's expanded/collapsed choice
        self._row_items = {}           # row key -> QTreeWidgetItem, rebuilt per populate
        self._applied_expansion = {}   # row key -> what this build applied, rebuilt too
        # Columns are fitted to content once, on the first populate() with data: refitting
        # on every rebuild would discard a width the user dragged.
        self._columns_sized = False
        self.currentItemChanged.connect(self._remember_current)
        self.itemExpanded.connect(self._remember_expanded)
        self.itemCollapsed.connect(self._remember_collapsed)

    def _begin_rebuild(self):
        """Clear for a bulk insert; per-build maps reset, remembered state survives."""
        self.blockSignals(True)
        self.setSortingEnabled(False)          # bulk insert, then re-sort once
        self.clear()
        self._row_items = {}
        self._applied_expansion = {}

    def _end_rebuild(self):
        self.setSortingEnabled(True)
        self.blockSignals(False)

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

    def _apply_expansion(self, item, key):
        """Apply remembered expansion, expanded by default; record the result."""
        is_expanded = self._expansion.get(key, True)
        item.setExpanded(is_expanded)
        self._applied_expansion[key] = is_expanded

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
        were_signals_blocked = self.blockSignals(True)
        try:
            self.setCurrentItem(item)
            # setCurrentItem() expands all ancestors — undo that for any
            # ancestor _build decided should be collapsed.
            ancestor = item.parent()
            while ancestor is not None:
                is_expanded = self._applied_expansion.get(ancestor.data(0, ROW_KEY_ROLE))
                if is_expanded is not None:
                    ancestor.setExpanded(is_expanded)
                ancestor = ancestor.parent()
        finally:
            self.blockSignals(were_signals_blocked)


class MemoryReport(RowTree):
    """Renders a MemoryModel's breakdown tree, with a per-attribute bar toggle."""

    # Emitted with the set of (owner, scope, name) whose bar is toggled on.
    toggledAttribsChanged = QtCore.Signal()

    COLS = ("Component", "%", "Total Memory", "New Memory", "Unique Memory",
            "Data ID", "Pages c/s/h", "Type")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegateForColumn(1, PercentBarDelegate(self))
        self.sortByColumn(2, QtCore.Qt.DescendingOrder)
        # Blank checkbox-wide icon so rows without a checkbox line up. self.style() can be
        # an already-deleted QProxyStyle under Houdini's PySide, hence the fallbacks.
        try:
            indicator_width = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth)
        except RuntimeError:
            app = QtWidgets.QApplication.instance()
            try:
                indicator_width = app.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth)
            except (RuntimeError, AttributeError):
                indicator_width = 16
        blank_pixmap = QtGui.QPixmap(indicator_width, indicator_width)
        blank_pixmap.fill(QtCore.Qt.transparent)
        self._blank_icon = QtGui.QIcon(blank_pixmap)
        self._toggled = set()          # {(owner, scope, name)}
        self._attrib_items = {}        # (owner, scope, name) -> QTreeWidgetItem
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
        # Wash OVER the finished row, since opaque style fills erase anything under it, then
        # the hover border. Keyed off ATTRIB_ROLE: UserRole is absent on non-bar rows.
        super().drawRow(painter, option, index)
        attrib = index.data(ATTRIB_ROLE)
        key = attrib.key if attrib is not None else None
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

    def item_for_key(self, key):
        """The attribute-row item for (owner, scope, name), or None."""
        return self._attrib_items.get(key)

    def populate(self, model, scopes=None):
        """Render `model`'s breakdown tree, filtering attribute leaves by `scopes`."""
        if scopes is None:
            scopes = set(SCOPES)
        root_row = model.breakdown(scopes) if model else None
        self._begin_rebuild()
        self._attrib_items = {}                # rebuilt as attrib rows are added
        try:
            if root_row is not None:
                self._build(None, root_row, model.total_memory or 1)
        finally:
            # Always restore, even on the empty-model early return -- otherwise the
            # tree would be left unsortable with its signals still blocked.
            self._end_rebuild()
        if root_row is None:
            return
        self._restore_selection()
        # Fitted once (see _columns_sized); the % bar column is widened on that first fit.
        was_sized = self._columns_sized
        self._auto_size_columns_once()
        if not was_sized:
            self.setColumnWidth(1, max(self.columnWidth(1),
                                       Style.Layout.PCT_COL_MIN))   # % bar column

    def visible_attribute_keys(self, owner):
        """Toggled attribute keys for `owner`, in the tree's CURRENT VISUAL ORDER."""
        keys = []

        def walk(item):
            key = item.data(0, QtCore.Qt.UserRole)   # set only on toggleable attrib rows
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
        item = (self._attribute_item(row, report_total, bool(row.children), pad_if_uncheckable)
                if row.attrib is not None
                else self._section_item(row, report_total, pad_if_uncheckable))
        # Structural rows are keyed by label path, attribute leaves by attribute; both
        # descend by label, so a row under an attribute row still gets a path.
        path = parent_path + (row.label,)
        key = row_key(row, parent_path)
        self._record_row(item, key)
        if parent is None:
            self.addTopLevelItem(item)
            font = item.font(0)                # the geometry total reads as the total
            font.setBold(True)
            _set_font_on_every_column(item, font)
        else:
            parent.addChild(item)
        self._apply_expansion(item, key)
        # Padding is decided per sibling group: a group with no checkable child needs none,
        # so its rows sit flush with each other.
        any_child_checkable = any(c.attrib is not None and c.attrib.has_page_details
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
        # Most section rows have no data id (None). The Primitive List branch has one, which
        # no child shows in the full representation.
        data_id_label = "" if row.data_id is None else data_id_text(row.data_id)
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
            _set_foreground_on_every_column(item, QtGui.QBrush(QtGui.QColor(*colour)))

        # Bold marks user-created data that allocated. Colour and bold share the
        # SCOPE_COLORS gate, so a container is never bold (the root is bolded in _build).
        if colour is not None and row.new_memory:
            font = item.font(0)
            font.setBold(True)
            _set_font_on_every_column(item, font)
        if row.data_id is not None and row.data_id >= 0 and row.data_id_inherited:
            # Italic = inherited from an input, as on attribute leaves, applied to a copy so
            # a bold font stays bold.
            id_font = QtGui.QFont(item.font(5))
            id_font.setItalic(True)
            item.setFont(5, id_font)
        if pad_if_uncheckable:
            # The pad _attribute_item gives an uncheckable leaf, so a structural leaf lines
            # up beside a checkable one. Only when a sibling is checkable (see _build).
            item.setIcon(0, self._blank_icon)
        return item

    def _attribute_item(self, row, report_total, has_own_expander=False,
                        pad_if_uncheckable=False):
        """One attribute leaf. Scope is conveyed by row colour, not a name suffix."""
        attrib = row.attrib
        data_id_label = "" if row.data_id is None else data_id_text(row.data_id)
        pct = 100.0 * attrib.total_memory / report_total
        item = _AttributeItem(
            [row.label, "", total_label(attrib, human_bytes),
             human_bytes(attrib.new_memory), human_bytes(attrib.unique_memory),
             data_id_label, attrib.pages_label, row.type_str])
        item.setData(1, QtCore.Qt.UserRole, pct)
        if attrib.is_tail_initialized:
            item.setToolTip(7, "Registered with the detail's tail-initialize table, so "
                               "appended elements get the default re-asserted.\nThis table "
                               "is what the Unaccounted row measures.")
        # Per-column magnitude keys so header-click sorting is numeric, not by the
        # formatted "1.2 KB" strings.
        item.setData(0, SORT_ROLE, row.label)
        item.setData(1, SORT_ROLE, pct)
        item.setData(2, SORT_ROLE, attrib.total_memory)
        item.setData(3, SORT_ROLE, attrib.new_memory)
        item.setData(4, SORT_ROLE, attrib.unique_memory)
        item.setData(5, SORT_ROLE, row.data_id if row.data_id is not None else -1)
        item.setData(7, SORT_ROLE, attrib.type_name)
        brush = QtGui.QBrush(QtGui.QColor(*Style.Color.SCOPE_COLORS.get(
            attrib.scope, Style.Color.ATTRIB_DEFAULT_FG)))
        font = item.font(0)
        # Attribute rows are colour-bearing by definition and have no children, so bold
        # tracks New alone.
        font.setBold(attrib.new_memory != 0)
        _set_foreground_on_every_column(item, brush)
        _set_font_on_every_column(item, font)
        # Italic = inherited from an input, applied to a copy so bold survives. Nothing to
        # mark when this row's Data ID is blank.
        if row.data_id is not None and row.data_id_inherited:
            id_font = QtGui.QFont(font)
            id_font.setItalic(True)
            item.setFont(5, id_font)
            item.setToolTip(5, "This data id was found on an input, so this node did not "
                               "change the VALUES.\nSays nothing about storage -- such an "
                               "attribute can still have New > 0.")
        # On every attribute row, bar or not, so peer highlighting can find rows that have
        # no page bar.
        item.setData(0, ATTRIB_ROLE, attrib)
        self._attrib_items[attrib.key] = item

        # Only page-detail attributes can produce a bar; others get no checkbox.
        if attrib.has_page_details:
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if attrib.key in self._toggled
                               else QtCore.Qt.Unchecked)
            # UserRole only on checkable rows — _on_item_changed uses it to
            # drive the bar toggle; non-checkable rows must not carry it.
            item.setData(0, QtCore.Qt.UserRole, attrib.key)
        elif not has_own_expander and pad_if_uncheckable:
            # Blank-icon pad so uncheckable rows align with checkable ones; an expander
            # arrow already fills that space. Only when a sibling is checkable (see _build).
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
        self.toggledAttribsChanged.emit()



class _DiffItem(QtWidgets.QTreeWidgetItem):
    """Diff attribute row: sorts on SORT_ROLE, unknowns sort below real numbers."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        if col in DiffReport.NO_SORT_COLUMNS:
            return False        # stable: leave the current order alone
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if a is None or b is None:
            # Sink the unknown. A descending sort puts it on top, where it reads as "could
            # not be ranked" rather than mixed in with the zeros.
            return b is not None
        return _sort_less(a, b)


class DiffReport(RowTree):
    """Renders a diff.DiffRow tree: delta columns only, no absolutes or percentages."""

    COLS = ("Component", "Scope", "Δ Total", "Δ New", "Δ Unique",
            "Δ Pages", "Δ c/s/h", "Data ID", "Type")
    COL_DELTA_TOTAL = 2
    # c/s/h is three numbers in one cell and "19 -> 35" is two values, so neither can rank
    # rows; clicking them does nothing, as with the report's Pages column.
    NO_SORT_COLUMNS = (6, 7)

    def __init__(self, parent=None):
        super().__init__(parent)
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
        _set_font_on_every_column(item, font)

    def _item(self, row):
        # Structural rows keep canonical order under every sort.
        texts = [
            self._name_cell(row),
            row.scope or "",
            self._bytes(row.d_total), self._bytes(row.d_new), self._bytes(row.d_unique),
            self._count(row.d_pages), self._csh(row.d_csh),
            row.data_id, row.type_str]
        item = _DiffItem(texts) if row.is_attrib else _OwnerItem(texts, row.order)

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
        # A paired value is the widest in its column and Type is last, so the panel edge
        # cuts off the half that says what changed; the tooltip carries it whole.
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
        self._begin_rebuild()
        try:
            if root is not None:
                self._build(None, root, ())
        finally:
            self._end_rebuild()
        if root is None:
            return
        self._restore_selection()
        # Fitted once (see _columns_sized): a new pin or a live re-cook must not undo a
        # width the user set.
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
            _set_font_on_every_column(item, font)
        else:
            parent.addChild(item)
        # A pruned tree is all signal, so every row opens. The user's own choice still
        # outranks that, exactly as it does in the report.
        self._apply_expansion(item, row.key)
        for child in row.children:
            self._build(item, child, path)
        return item
