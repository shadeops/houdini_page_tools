"""SOP Memory Python Panel.

Upper section: per-attribute memory tree. Lower section: virtualised page grid.
Data source: ``_page_tools.report()`` (schema: ``SOP_Memory_Report.md``).
"""

from PySide6 import QtCore, QtGui, QtWidgets

import hou

import page_tools
from . import diff as pgdiff
from .model import OWNERS, MemoryModel
from .style import Style, _rgb_css, human_bytes
from .decode import DecodedOwner
from .grid import PageGridLegend, PageGridWidget, _attribute_color_map
from .node_tracker import SopNodeTracker
from .report import ATTRIB_ROLE, DiffReport, MemoryReport

# Checkbox label -> scope, in checkbox-row order, which matches the tree's root-branch
# order.
_SCOPE_FOR_LABEL = {
    "Attribute Set": "attribute_set",
    "Public": "public",
    "Private": "private",
    "Groups": "group",
    "Primitive Lists": "primitive_list",
    "Index Maps": "index_maps",
    "Group Tables": "group_tables",
}

# The finer filters inside Attribute Set's box, hidden whenever Attribute Set is off.
_ATTRIBUTE_SET_SUBSCOPES = ("public", "private", "group")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WIDGET_SIZE_MAX = 16777215     # Qt's QWIDGETSIZE_MAX (PySide6 doesn't export it)


# ---------------------------------------------------------------------------
# Collapsible section container
# ---------------------------------------------------------------------------

class CollapsibleSection(QtWidgets.QWidget):
    # Do NOT constrain maximumHeight — it freezes the splitter handle.
    toggled = QtCore.Signal(bool)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._toggle = QtWidgets.QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setObjectName("sectionToggle")
        self._toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(QtCore.Qt.DownArrow)
        self._toggle.toggled.connect(self._on_toggled)

        self._body = QtWidgets.QWidget()
        self._body_layout = QtWidgets.QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._body, 1)

    def body_layout(self):
        return self._body_layout

    def is_open(self):
        return self._toggle.isChecked()

    def header_height(self):
        return self._toggle.sizeHint().height()

    def _on_toggled(self, checked):
        self._toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)
        self._body.setVisible(checked)
        self.toggled.emit(checked)


class ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class PauseButton(QtWidgets.QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pause")
        self.setCheckable(True)
        # Text LEFT of the glyph, as MiniToolButton lays it out (label at x=8, icon in the
        # trailing square). A QPushButton puts its icon first, so mirror it.
        self.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.setToolTip(
            "Freeze the panel: stop following node selection, parameter changes and the "
            "timeline (for comparing panels or avoiding re-cooks of heavy geometry).")
        # Height pinned before the pill sheet, so the sheet's padding cannot feed back into
        # sizeHint; measured with the label, so it holds once the label appears.
        self.setText("Paused")
        self.pill_height = self.sizeHint().height()
        self.setFixedHeight(self.pill_height)
        self.pill_radius = self.pill_height // 2
        self.glyph_px = max(9, round(self.pill_height * Style.GLYPH_RATIO))
        self.setIconSize(QtCore.QSize(self.glyph_px + 4, self.glyph_px + 4))

    def apply_style(self):
        """(Re)build the Pause pill for its current state."""
        checked = self.isChecked()
        fg = Style.role(Style.PAUSE_ON_FG_ROLE if checked else Style.PAUSE_OFF_FG_ROLE,
                        Style.Color.PAUSE_ON_FG if checked else Style.Color.PAUSE_OFF_FG)
        icon = Style.glyph_icon(Style.GLYPH_PAUSE, self.glyph_px, fg)
        self.setIcon(icon or QtGui.QIcon())
        # No label at rest, so the button is a circle round the glyph. Without the font the
        # glyph falls back to text, so the button is never blank.
        label = "Paused" if checked else ""
        self.setText(label if icon else ("❚❚ Paused" if checked else "❚❚"))
        if checked:
            self.setMinimumWidth(0)
            self.setMaximumWidth(_WIDGET_SIZE_MAX)
        else:
            self.setFixedWidth(self.pill_height)    # square -> the radius makes it round
        self.setStyleSheet(Style.pause_qss(checked, self.pill_radius))


# ---------------------------------------------------------------------------
# Root panel
# ---------------------------------------------------------------------------

class SopMemoryPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        # Inert once captured: a report and its path, with no callback and no node, so it
        # survives its node being deleted. Only the live side follows the scene.
        self._pinned = None            # MemoryModel of the pinned node, or None
        self._pinned_path = ""
        self._refresh_queued = False
        self._alive = True             # cleared by teardown(); guards deferred work
        self._output = 0               # selected output index (0-based; never negative)
        self._repolishing = False      # see _repolish: polish() re-enters changeEvent
        self._repolish_queued = False  # see _queue_repolish: the burst is collapsed
        self._theme_token = None       # last application Window colour we polished for

        self._build_ui()
        self._tracker = SopNodeTracker(self)
        self._tracker.nodeChanged.connect(self._populate_outputs)
        self._tracker.pendingNodeChanged.connect(self._refresh_pending_hint)
        self._tracker.refreshNeeded.connect(self._on_refresh_needed)

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        self.setStyleSheet(Style.PANEL_QSS)

        margin = Style.Layout.PANEL_MARGIN
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(margin, margin, margin, margin)

        layout.addLayout(self._build_header_row())

        self._instanced_label = QtWidgets.QLabel("")
        self._instanced_label.setWordWrap(True)
        self._instanced_label.setObjectName("instanced")
        self._instanced_label.setVisible(False)
        layout.addWidget(self._instanced_label)

        self._pending_label = QtWidgets.QLabel("")
        self._pending_label.setObjectName("instanced")   # same amber "needs your attention"
        self._pending_label.setVisible(False)
        layout.addWidget(self._pending_label)

        layout.addWidget(self._build_splitter(), 1)

    def _build_header_row(self):
        # Title bar: SOP path on the left, output selector on the right, as in the geometry
        # spreadsheet.
        header_row = QtWidgets.QHBoxLayout()
        self._header = QtWidgets.QLabel("No SOP node selected")
        self._header.setObjectName("header")
        header_row.addWidget(self._header, 1)
        self._output_combo = QtWidgets.QComboBox()
        self._output_combo.setVisible(False)
        self._output_combo.currentIndexChanged.connect(self._on_output_changed)
        header_row.addSpacing(8)
        header_row.addWidget(self._output_combo)
        self._pin_btn = QtWidgets.QPushButton("Pin")
        self._pin_btn.setObjectName("pin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setLayoutDirection(QtCore.Qt.RightToLeft)
        self._pin_btn.setToolTip(
            "Pin this node's report, then select another node to see what changed.\n"
            "With nothing selected the pinned node's own report is shown.")
        self._pin_btn.setEnabled(False)     # nothing to pin until a report exists
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        header_row.addSpacing(8)
        header_row.addWidget(self._pin_btn)

        self._reload_btn = QtWidgets.QPushButton("Reload")
        self._reload_btn.setObjectName("reload")
        self._reload_btn.setToolTip(
            "Read the selected node once, without unpausing.")
        self._reload_btn.clicked.connect(self._on_reload)
        self._reload_btn.setVisible(False)
        header_row.addSpacing(8)
        header_row.addWidget(self._reload_btn)

        # Pause: freeze the panel so selecting other nodes / changing parms / scrubbing the
        # timeline no longer updates it (compare panels; avoid re-cooking heavy geometry).
        self._pause_btn = PauseButton()
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        glyph_px = self._pause_btn.glyph_px
        self._reload_btn.setIconSize(QtCore.QSize(glyph_px + 4, glyph_px + 4))
        self._reload_btn.setIcon(
            Style.glyph_icon(Style.GLYPH_RELOAD, glyph_px,
                             Style.role(Style.PAUSE_OFF_FG_ROLE, Style.Color.PAUSE_OFF_FG))
            or QtGui.QIcon())
        self._apply_pause_style()
        header_row.addSpacing(8)
        header_row.addWidget(self._pause_btn)
        return header_row

    def _build_memory_section(self):
        # Memory section: the breakdown tree rooted at "Geometry Memory". Scope filters
        # apply to its attribute rows and to the lower section's bars.
        self._mem_section = CollapsibleSection("Memory")
        # Each scope toggle's label takes its scope's row colour.
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(QtWidgets.QLabel("Scopes:"))
        self._scope_cbs = {}           # scope name -> its checkbox (see _enabled_scopes)
        self._scope_labels = {}        # scope name -> its ClickableLabel (for hide/show)

        # Attribute Set's toggle and its three finer filters share a plain native frame,
        # which follows the theme, to show they are one filter group.
        attribute_set_box = QtWidgets.QFrame()
        attribute_set_box.setFrameShape(QtWidgets.QFrame.Box)
        attribute_set_box.setFrameShadow(QtWidgets.QFrame.Plain)
        attribute_set_row = QtWidgets.QHBoxLayout(attribute_set_box)
        attribute_set_row.setContentsMargins(4, 2, 4, 2)
        self._attribute_set_cb = self._scope_toggle(attribute_set_row, "Attribute Set")
        # "Attribs" is dropped from these two labels' text -- the box they sit in
        # already says they belong to Attribute Set.
        self._public_cb = self._scope_toggle(attribute_set_row, "Public")
        self._private_cb = self._scope_toggle(attribute_set_row, "Private")
        self._groups_cb = self._scope_toggle(attribute_set_row, "Groups")
        scope_row.addWidget(attribute_set_box)
        self._attribute_set_cb.stateChanged.connect(self._on_attribute_set_toggled)

        self._primitive_list_cb = self._scope_toggle(scope_row, "Primitive Lists")
        # View-only scopes: each gates a branch's children, never the branch row, and the
        # report knows nothing of them.
        self._index_maps_cb = self._scope_toggle(scope_row, "Index Maps")
        self._group_tables_cb = self._scope_toggle(scope_row, "Group Tables")
        scope_row.addStretch(1)
        self._mem_report = MemoryReport()
        self._mem_report.toggledAttribsChanged.connect(self._update_grid)
        self._mem_report.currentItemChanged.connect(self._on_attribute_selected)
        self._diff_report = DiffReport()
        self._diff_report.setVisible(False)
        self._diff_message = QtWidgets.QLabel("")
        self._diff_message.setObjectName("diffMessage")
        self._diff_message.setAlignment(QtCore.Qt.AlignCenter)
        self._diff_message.setWordWrap(True)
        self._diff_message.setVisible(False)
        self._mem_section.body_layout().addLayout(scope_row)
        self._mem_section.body_layout().addWidget(self._mem_report, 1)
        self._mem_section.body_layout().addWidget(self._diff_report, 1)
        self._mem_section.body_layout().addWidget(self._diff_message, 1)
        return self._mem_section

    def _build_grid_section(self):
        # Lower section: page grid + controls.
        self._grid_section = CollapsibleSection("Index Map Pages")
        controls = QtWidgets.QHBoxLayout()
        self._owner_combo = QtWidgets.QComboBox()
        self._owner_combo.addItems([owner.capitalize() for owner in OWNERS])
        self._owner_combo.setCurrentText("Point")
        self._owner_combo.currentIndexChanged.connect(self._on_owner_changed)
        self._mode_combo = QtWidgets.QComboBox()
        for text, mode in (("Occupancy", "occupancy"), ("Continuous block", "block"),
                           ("Memory block sharing", "sharing")):
            self._mode_combo.addItem(text, mode)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._res_combo = QtWidgets.QComboBox()
        for px in Style.Layout.RES_OPTIONS:
            self._res_combo.addItem(f"{px}px", px)
        self._res_combo.setCurrentText(f"{Style.Layout.DEFAULT_CELL_PX}px")
        self._res_combo.currentIndexChanged.connect(self._on_res_changed)
        self._legend_cb = QtWidgets.QCheckBox("Legend")
        self._legend_cb.toggled.connect(self._on_legend_toggled)
        controls.addWidget(QtWidgets.QLabel("Owner")); controls.addWidget(self._owner_combo)
        controls.addWidget(QtWidgets.QLabel("Mode")); controls.addWidget(self._mode_combo)
        controls.addWidget(QtWidgets.QLabel("Cell")); controls.addWidget(self._res_combo)
        controls.addWidget(self._legend_cb)
        controls.addStretch(1)

        self._legend = PageGridLegend()
        self._legend.setVisible(False)

        self._grid = PageGridWidget()
        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("status")
        self._status.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        self._grid.hoverInfo.connect(self._status.setText)
        self._grid.barHovered.connect(self._on_bar_hovered)
        self._grid_section.body_layout().addLayout(controls)
        self._grid_section.body_layout().addWidget(self._legend)
        self._grid_section.body_layout().addWidget(self._grid, 1)
        self._grid_section.body_layout().addWidget(self._status)
        return self._grid_section

    def _build_splitter(self):
        # A vertical splitter lets the user drag the divider to give the memory
        # list more room when there are many attributes, or grow the page grid.
        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._splitter.addWidget(self._build_memory_section())
        self._splitter.addWidget(self._build_grid_section())
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._open_sizes = list(Style.Layout.SPLITTER_OPEN)  # restored when both open
        self._splitter.setSizes(self._open_sizes)
        self._splitter.splitterMoved.connect(self._remember_open_sizes)
        self._mem_section.toggled.connect(self._on_section_toggled)
        self._grid_section.toggled.connect(self._on_section_toggled)
        return self._splitter

    # -- section collapse / splitter sizing --------------------------------

    def _remember_open_sizes(self, *_args):
        # Remember the user's manual divider position while both are expanded.
        if self._mem_section.is_open() and self._grid_section.is_open():
            self._open_sizes = self._splitter.sizes()

    def _on_section_toggled(self, _checked):
        # Give a collapsed section's room to the other without constraining maximumHeight,
        # which would freeze the divider.
        splitter = self._splitter
        total = sum(splitter.sizes()) or splitter.height()
        mem_open = self._mem_section.is_open()
        grid_open = self._grid_section.is_open()
        mem_h = self._mem_section.header_height()
        grid_h = self._grid_section.header_height()
        if mem_open and grid_open:
            splitter.setSizes(self._open_sizes)
        elif mem_open:
            splitter.setSizes([max(total - grid_h, mem_h), grid_h])
        elif grid_open:
            splitter.setSizes([mem_h, max(total - mem_h, grid_h)])
        else:
            splitter.setSizes([mem_h, grid_h])

    # -- legend -------------------------------------------------------------

    def _scope_toggle(self, layout, label):
        scope = _SCOPE_FOR_LABEL.get(label, label)
        # A checkbox stylesheet `color:` loses to Houdini's app stylesheet, so the label is
        # a separate QLabel coloured with inline HTML.
        checkbox = QtWidgets.QCheckBox()
        checkbox.setChecked(True)          # every scope defaults on
        checkbox.stateChanged.connect(self._on_scope_changed)
        self._scope_cbs[scope] = checkbox
        # A scope with no colour ("Index Maps"/"Group Tables") gets a plain label in
        # the default text colour -- matching its rows, which are also left uncoloured.
        colour = Style.Color.SCOPE_COLORS.get(scope)
        text = label if colour is None \
            else f'<span style="color:{_rgb_css(colour)}">{label}</span>'
        scope_label = ClickableLabel(text)
        scope_label.clicked.connect(checkbox.toggle)
        self._scope_labels[scope] = scope_label
        layout.addWidget(checkbox)
        layout.addWidget(scope_label)
        layout.addSpacing(6)
        return checkbox

    def _on_attribute_set_toggled(self, checked):
        # Public/Private/Groups have nothing left to filter once Attribute Set hides the
        # owner rows.
        checked = bool(checked)
        for scope in _ATTRIBUTE_SET_SUBSCOPES:
            self._scope_cbs[scope].setVisible(checked)
            self._scope_labels[scope].setVisible(checked)

    def _on_legend_toggled(self, checked):
        self._legend.setVisible(checked)
        if checked:
            self._refresh_legend()

    def _refresh_legend(self):
        # Gate on the checkbox, not isVisible() -- the latter is False until the
        # whole panel is shown on screen, which would skip building it headlessly.
        if not self._legend_cb.isChecked():
            return
        self._legend.refresh(self._grid)

    # -- theming ------------------------------------------------------------

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.PaletteChange:
            self._queue_repolish()

    def paintEvent(self, event):
        # changeEvent(PaletteChange) never fires when QStyleSheetStyle pins the palette; one
        # comparison per repaint avoids an application-wide event filter.
        super().paintEvent(event)
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        token = app.palette().color(QtGui.QPalette.Window).rgba()
        if token != self._theme_token:
            self._theme_token = token
            self._queue_repolish()

    def _queue_repolish(self):
        """Defer re-polish to collapse a burst of theme notifications into one."""
        if self._repolish_queued or not self._alive:
            return
        self._repolish_queued = True
        QtCore.QTimer.singleShot(0, self._repolish)

    def _repolish(self):
        """Make the panel pick up a colour scheme changed while it was open."""
        self._repolish_queued = False
        if self._repolishing or not self._alive:
            return
        self._repolishing = True
        try:
            # Record the colour polished for, whichever route got here; otherwise the next
            # repaint handles the same scheme change a second time.
            app = QtWidgets.QApplication.instance()
            if app is not None:
                self._theme_token = app.palette().color(QtGui.QPalette.Window).rgba()
            blank = QtGui.QPalette()
            for widget in (self, self._mem_report.viewport(), self._grid.viewport()):
                widget.setPalette(blank)
            self.setStyleSheet(self.styleSheet())
            # The Pause button carries its own sheet, built from theme roles, so it needs
            # rebuilding rather than re-polishing.
            self._apply_pause_style()
            # Repaint what we draw ourselves: both read the palette at paint time, and
            # nothing else will ask them to.
            self._grid.viewport().update()
            self._mem_report.viewport().update()
        finally:
            self._repolishing = False

    # -- node tracking ------------------------------------------------------

    def set_node(self, node):
        self._tracker.set_node(node)

    def _on_reload(self):
        self._tracker.reload()

    def _apply_pause_style(self):
        """(Re)build the Pause pill for its current state, and show/hide Reload with it."""
        self._pause_btn.apply_style()
        # Reload exists only while paused (NodeToolbar hides it the same way): with updates
        # frozen it is the only way to ask for one.
        self._reload_btn.setVisible(self._pause_btn.isChecked())

    def _on_pause_toggled(self, paused):
        # The label, the glyph colour, the pill and Reload's visibility all follow the
        # state; _apply_pause_style owns the lot.
        self._apply_pause_style()
        self._tracker.set_paused(paused)

    def _populate_outputs(self):
        """Repopulate the output combo and reset to output 0."""
        combo = self._output_combo
        labels = []
        if self._tracker.node is not None:
            try:
                labels = [str(label) for label in self._tracker.node.outputLabels()]
            except hou.Error:
                labels = []
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(labels)
        self._output = 0
        combo.setCurrentIndex(0 if labels else -1)
        # Only when there is a choice: outputLabels() gives an ordinary SOP one label, and a
        # one-entry combo is chrome the user can't act on.
        combo.setVisible(len(labels) > 1)
        combo.blockSignals(False)

    def _on_output_changed(self, idx):
        # User picked a different output -> re-report at that index (never negative).
        if idx < 0 or idx == self._output:
            return
        self._output = idx
        self._queue_refresh()

    def teardown(self):
        # Called from onDestroyInterface. Marked dead first so a deferred _refresh already
        # in flight no-ops instead of touching deleted widgets.
        self._alive = False
        self._tracker.teardown()

    def _on_refresh_needed(self):
        # Looked up on each call rather than connected directly, so the tests' inline
        # replacement for _queue_refresh is the one reached.
        self._queue_refresh()

    def _queue_refresh(self):
        # Coalesce a burst of cook events into one rebuild. Skip once torn down: a stray
        # event can arrive before the callbacks detach.
        if self._refresh_queued or not self._alive:
            return
        self._refresh_queued = True
        # hdefereval is only importable in a graphical Houdini; import lazily so
        # the module still loads headlessly (tests, hython).
        import hdefereval
        hdefereval.executeDeferred(self._refresh)

    # -- data ---------------------------------------------------------------

    def _refresh(self):
        self._refresh_queued = False
        # Runs deferred on the idle loop, and teardown can't cancel it: bail before touching
        # a widget whose C++ side may be gone.
        if not self._alive:
            return
        self._instanced_label.setVisible(False)
        self._refresh_pending_hint()
        if self._tracker.node is None:
            self._model = None
            self._show_without_live_report(self._title())
            return
        try:
            self._model = MemoryModel(page_tools.report(self._tracker.node, self._output))
        except hou.Error as exc:
            self._model = None
            self._show_without_live_report(f"{self._tracker.node.path()}: {exc.instanceMessage()}")
            return
        self._pin_btn.setEnabled(True)
        self._header.setText(self._title())
        instanced_message = self._instanced_message()
        if instanced_message is not None:
            self._instanced_label.setText("⚠ Instanced — %s." % instanced_message)
            self._instanced_label.setVisible(True)
        self._render()
        self._update_grid()

    def _show_without_live_report(self, header_text):
        self._pin_btn.setEnabled(self._pinned is not None)
        self._header.setText(header_text)
        self._render()         # pinned: the pinned node's own report; otherwise empty
        self._update_grid()    # clears the grid AND refreshes the legend (no bars left)

    def _instanced_message(self):
        instanced_paths = [name for name, model in ((self._pinned_path, self._pinned),
                                                    (self._title_live(), self._model))
                           if model is not None and model.is_instanced]
        if not instanced_paths:
            return None
        if self._pinned is None:
            return ("the output is fully shared with another detail; this node owns no "
                    "memory of its own (shared detail total %s)"
                    % human_bytes(self._model.total_memory))
        if len(instanced_paths) > 1:
            return ("%s are both fully shared with other details, so neither owns any "
                    "memory and the Δ New / Δ Unique columns do not apply"
                    % " and ".join(instanced_paths))
        return ("%s is fully shared with another detail, so it owns no memory of "
                "its own and the Δ New / Δ Unique columns do not apply"
                % instanced_paths[0])

    def _on_pin_toggled(self, pinned):
        if pinned:
            if self._model is None:
                self._pin_btn.setChecked(False)
                return
            self._pinned = self._model.without_report_bytes()
            self._pinned_path = self._title_live()
            self._tracker.set_watching_selection(True)
        else:
            self._pinned = None
            self._pinned_path = ""
            self._tracker.set_watching_selection(False)
        self._grid_section.setVisible(not pinned)
        self._apply_pin_style()
        self._header.setText(self._title())
        self._render()
        self._update_grid()

    def _apply_pin_style(self):
        pinned = self._pinned is not None
        self._pin_btn.setText("Pinned" if pinned else "Pin")
        font = self._pin_btn.font()
        font.setBold(pinned)            # Houdini's mark for a control off its default
        self._pin_btn.setFont(font)

    def _render(self):
        """Show the single-node report, the diff, or "No differences." as appropriate."""
        pinned = self._pinned is not None
        is_comparing = pinned and self._model is not None and self._tracker.node is not None
        scopes = self._enabled_scopes()

        if not is_comparing:
            # With a pin held and nothing selected, the pinned node's own report is shown:
            # the diff carries no absolute figures.
            model = self._pinned if pinned else self._model
            self._mem_report.populate(model, scopes)
            self._diff_report.setVisible(False)
            self._diff_message.setVisible(False)
            self._mem_report.setVisible(True)
            return

        root = pgdiff.prune(pgdiff.diff_models(self._pinned, self._model, scopes))
        self._mem_report.setVisible(False)
        if root is None:
            self._diff_report.setVisible(False)
            self._diff_message.setText("No differences.")
            self._diff_message.setVisible(True)
            return
        self._diff_message.setVisible(False)
        self._diff_report.populate(root)
        self._diff_report.setVisible(True)

    def _refresh_pending_hint(self):
        """Show or hide the "selection is waiting" hint."""
        is_hint_shown = self._tracker.has_pending_node()
        if is_hint_shown:
            pending = self._tracker.pending_node
            name = pending.name() if pending is not None else "nothing"
            self._pending_label.setText(
                "%s selected \u2014 Reload or un-pause to read it." % name)
        self._pending_label.setVisible(bool(is_hint_shown))

    def _title_live(self):
        """The live node's path, however far the refresh got."""
        if self._model is not None:
            return self._model.node or (self._tracker.node.path() if self._tracker.node else "")
        return self._tracker.node.path() if self._tracker.node is not None else ""

    def _title(self):
        live_path = self._title_live()
        if self._pinned is None:
            return live_path or "No SOP node selected"
        if live_path and self._tracker.node is not None:
            return "%s  \u2192  %s" % (self._pinned_path, live_path)
        return "%s   (pinned)" % self._pinned_path

    def _current_owner(self):
        return OWNERS[self._owner_combo.currentIndex()]

    def _enabled_scopes(self):
        return {scope for scope, checkbox in self._scope_cbs.items() if checkbox.isChecked()}

    def _bar_attributes_for_owner(self, owner):
        """Toggled attributes for `owner`, in tree display order."""
        return list(self._mem_report.visible_attribute_keys(owner))

    def _update_grid(self):
        # Compare mode is memory-only in every state, including the pinned node's own
        # report: the pinned side is byte-stripped, so there are no pages to draw for it.
        if self._pinned is not None or self._model is None:
            self._grid.set_attribute_colors({})
            self._grid.set_data(None, [])
            self._refresh_legend()
            return
        owner = self._current_owner()
        decoded = DecodedOwner(self._model.owner_map(owner), owner, self._model.page_layout,
                               primitive_list_report=self._model.raw_primitive_list(),
                               attributes_report=self._model.attribute_set_owner(owner))
        # Detail-wide, so it is handed over before the bars: the colours must not depend
        # on which owner is shown or which attribute is selected.
        self._grid.set_attribute_colors(_attribute_color_map(self._model))
        self._grid.set_data(decoded, self._bar_attributes_for_owner(owner))
        self._refresh_legend()      # bar-class entries depend on shown bars
        # set_data cleared any emphasis; re-apply from the current selection so a
        # still-shown attribute stays highlighted (and an invalid one clears).
        self._on_attribute_selected(self._mem_report.currentItem(), None)

    def _on_attribute_selected(self, current, _previous):
        """Emphasize the selected attribute's bar and highlight its sharing peers."""
        key = current.data(0, QtCore.Qt.UserRole) if current is not None else None
        attrib = current.data(0, ATTRIB_ROLE) if current is not None else None
        self._shade_sharing_peers(attrib)

        # Sharing mode draws the selected attribute's blocks and has its own empty-state
        # text, so clear any stale "switch Owner" / "toggle on" hint.
        if self._grid.mode() == "sharing":
            if attrib is not None and attrib.owner != self._current_owner():
                # The grid decodes whichever owner the combo names, and sharing mode
                # draws that decode's pages -- so the combo must follow the selection.
                # This re-enters here (via _on_owner_changed -> _update_grid) once the
                # decode matches, so let that call finish the job.
                self._owner_combo.setCurrentIndex(OWNERS.index(attrib.owner))
                return
            self._grid.set_sharing(attrib)
            self._status.setText("")
        self._emphasize_bar(key)

    def _shade_sharing_peers(self, attrib):
        # Peers on another owner or hidden by the scope filter have no item, so they are
        # filtered out rather than assumed.
        shares_with_attrib_keys = (getattr(attrib, "shares_with_attrib_keys", ())
                                   if attrib is not None else ())
        self._mem_report.set_peer_keys(
            [tuple(p) for p in shares_with_attrib_keys
             if self._mem_report.item_for_key(tuple(p)) is not None])

    def _emphasize_bar(self, key):
        # Bars are keyed on the full (owner, scope, name), so a cross-owner bar in sharing
        # mode emphasizes from its own row without a special case.
        is_checkable_attrib_row = isinstance(key, tuple) and len(key) == 3
        if is_checkable_attrib_row and tuple(key) in self._grid.bar_attributes():
            self._grid.set_emphasis(key)
        else:
            self._grid.set_emphasis(None)
            # Nudge when the row's bar isn't shown, except in sharing mode: there the bars
            # are the selected attribute's peers.
            if is_checkable_attrib_row and self._grid.mode() != "sharing":
                owner, scope, name = key
                if owner != self._current_owner():
                    self._status.setText(
                        f"{name} is a {owner} attribute — switch Owner to "
                        f"{owner.capitalize()} to see its bar")
                else:
                    self._status.setText(f"toggle {name} on to see its bar")

    def _on_bar_hovered(self, owner, scope, name):
        """Outline the hovered bar's report row with a coloured border."""
        if not name:
            self._mem_report.set_hover_key(None)
            return
        key = (owner, scope, name)
        if self._mem_report.item_for_key(key) is None:
            return
        self._mem_report.set_hover_key(key)

    # -- control callbacks --------------------------------------------------

    def _on_scope_changed(self, _state):
        # Scope filters apply to whichever body is up, and to the bars. In compare mode
        # they go to BOTH sides of the join -- one side only would read as rows removed.
        if self._model is None and self._pinned is None:
            return
        self._render()
        self._update_grid()

    def _on_owner_changed(self, _idx):
        self._update_grid()

    def _on_mode_changed(self, idx):
        self._grid.set_mode(self._mode_combo.itemData(idx))
        # Sharing mode draws the SELECTED attribute's peers, so the combo follows the
        # selection (_on_attribute_selected) instead of driving it; disabled so the
        # user can't put it out of sync with the selected attribute's owner.
        self._owner_combo.setEnabled(self._grid.mode() != "sharing")
        if self._grid.mode() == "sharing":
            # Sharing mode draws the selected attribute's peers, so pick up the current
            # selection now and refresh the status line.
            self._on_attribute_selected(self._mem_report.currentItem(), None)
            self._refresh_legend()      # grid entries depend on the mode
        else:
            # Leaving sharing mode — rebuild bars from toggles.
            self._update_grid()

    def _on_res_changed(self, _idx):
        self._grid.set_cell_px(self._res_combo.currentData())
