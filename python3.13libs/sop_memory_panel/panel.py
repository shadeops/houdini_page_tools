"""SOP Memory Python Panel.

Upper section: per-attribute memory tree. Lower section: virtualised page grid.
Data source: ``_page_tools.report()`` (schema: ``SOP_Memory_Report.md``).
"""

from PySide6 import QtCore, QtGui, QtWidgets

import hou

import page_tools
from . import diff as pgdiff
from .model import GROUP_TABLES, OWNERS, SCOPES, MemoryModel, row_key  # noqa: F401 re-export
from .style import Style, _rgb_css, _u32, human_bytes  # noqa: F401 re-export
from .decode import (  # noqa: F401 re-export
    PAGE_DIM,
    PAGE_SIZE,
    PS_CONSTANT,
    PS_CONSTANT_SHARED,
    PS_HARDENED,
    PS_NONE,
    PS_SHARED,
    PS_UNKNOWN,
    ST_ACTIVE,
    ST_OOR,
    ST_TEMP,
    ST_VACANT,
    DecodedOwner,
    _block_palette,
    _radical_inverse_base2,
)
from .grid import (  # noqa: F401 re-export
    PageGridWidget,
    _attr_color_map,
    _base36,
    _label_font_px,
    _label_ink,
    _oklab_lightness,
)
from .report import (  # noqa: F401 re-export
    ATTR_ROLE,
    ROW_KEY_ROLE,
    SORT_ROLE,
    DiffReport,
    MemoryReport,
)

# Checkbox display label -> scope, in checkbox-row order (matches the tree's own
# root-branch order: Attribute Set's own toggle plus its three finer filters, then
# one per remaining branch).
_SCOPE_FOR_LABEL = {
    "Attribute Set": "attribute_set",
    "Public": "public",
    "Private": "private",
    "Groups": "group",
    "Primitive Lists": "primitive_list",
    "Index Maps": "index_maps",
    "Group Tables": "group_tables",
}

# The three fine-grained filters that only matter once "Attribute Set" itself has let
# the owner rows through -- boxed together with it in the toggle row, and hidden
# (not just left checked-but-inert) whenever it is off.
_ATTRIBUTE_SET_SUBSCOPES = ("public", "private", "group")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SELECTION_EVENTS = (hou.nodeEventType.ChildSelectionChanged,)

COOK_EVENTS = (
    hou.nodeEventType.ParmTupleChanged,
    hou.nodeEventType.InputDataChanged,
    hou.nodeEventType.InputRewired,
    hou.nodeEventType.BeingDeleted,
)

_UNSET = object()               # "no node change was deferred while paused" sentinel
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


# ---------------------------------------------------------------------------
# Root panel
# ---------------------------------------------------------------------------

class SopMemoryPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._node = None
        self._model = None
        # Compare mode. The pinned side is INERT once captured -- a report and the path it
        # came from, nothing more. It holds no event callback, does not track an output
        # index, cannot go stale, and survives its node being deleted, because there is no
        # node in it. Only the live side follows the scene.
        self._pinned = None            # MemoryModel of the pinned node, or None
        self._pinned_path = ""
        self._pinned_output = 0
        self._refresh_queued = False
        self._watched_network = None   # network whose selection we follow while pinned
        self._alive = True             # cleared by teardown(); guards deferred work
        self._playbar_cb_added = False
        self._output = 0               # selected output index (0-based; never negative)
        self._paused = False           # frozen: ignore selection/cook/frame events
        self._pending_node = _UNSET    # node selected while paused, applied on resume
        self._repolishing = False      # see _repolish: polish() re-enters changeEvent
        self._repolish_queued = False  # see _queue_repolish: the burst is collapsed
        self._theme_token = None       # last application Window colour we polished for

        self._build_ui()
        self._add_playbar_callback()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        self.setStyleSheet(Style.PANEL_QSS)

        margin = Style.Layout.PANEL_MARGIN
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(margin, margin, margin, margin)

        # Title bar: SOP path on the left, output selector on the right (like the
        # geometry spreadsheet). The combo shows the node's output label names and
        # resets to output 0 when the node changes (see _populate_outputs).
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
        self._pause_btn = QtWidgets.QPushButton()
        self._pause_btn.setObjectName("pause")
        self._pause_btn.setCheckable(True)
        # Text LEFT of the glyph, as MiniToolButton lays it out (label at x=8, icon in the
        # trailing square). A QPushButton puts its icon first, so mirror it.
        self._pause_btn.setLayoutDirection(QtCore.Qt.RightToLeft)
        self._pause_btn.setToolTip(
            "Freeze the panel: stop following node selection, parameter changes and the "
            "timeline (for comparing panels or avoiding re-cooks of heavy geometry).")
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        # Pin the height BEFORE the pill sheet is applied, so the radius that makes it a
        # pill is half of a height that can no longer drift (the sheet's own padding would
        # otherwise feed back into sizeHint). Measured with a label present, so the pill
        # keeps that height once the label appears.
        self._pause_btn.setText("Paused")
        self._pause_h = self._pause_btn.sizeHint().height()
        self._pause_btn.setFixedHeight(self._pause_h)
        self._pause_radius = self._pause_h // 2
        glyph_px = max(9, round(self._pause_h * Style.GLYPH_RATIO))
        self._pause_btn.setIconSize(QtCore.QSize(glyph_px + 4, glyph_px + 4))
        self._reload_btn.setIconSize(QtCore.QSize(glyph_px + 4, glyph_px + 4))
        self._reload_btn.setIcon(
            Style.glyph_icon(Style.GLYPH_RELOAD, glyph_px,
                             Style.role(Style.PAUSE_OFF_FG_ROLE, Style.Color.PAUSE_OFF_FG))
            or QtGui.QIcon())
        self._apply_pause_style()
        header_row.addSpacing(8)
        header_row.addWidget(self._pause_btn)
        layout.addLayout(header_row)

        self._instanced_label = QtWidgets.QLabel("")
        self._instanced_label.setWordWrap(True)
        self._instanced_label.setObjectName("instanced")
        self._instanced_label.setVisible(False)
        layout.addWidget(self._instanced_label)

        self._pending_label = QtWidgets.QLabel("")
        self._pending_label.setObjectName("instanced")   # same amber "needs your attention"
        self._pending_label.setVisible(False)
        layout.addWidget(self._pending_label)

        # Memory section: a single breakdown tree rooted at "Geometry Memory", with
        # six direct branches mirroring GA_Detail::countMemory() (Attribute Set,
        # Primitive List, Index Maps, Group Tables, Detail Object, Unaccounted).
        # Scope filters apply to the attribute rows and to the lower section's bars.
        self._mem_section = CollapsibleSection("Memory")
        # A coloured swatch next to each scope toggle matches the scope's row
        # colour in the table (reliable regardless of Houdini's stylesheet).
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(QtWidgets.QLabel("Scopes:"))
        self._scope_cbs = {}           # scope name -> its checkbox (see _enabled_scopes)
        self._scope_labels = {}        # scope name -> its ClickableLabel (for hide/show)

        # Attribute Set's own coarse toggle plus its three finer filters are boxed
        # together (a plain native frame, not a pinned colour, so it follows the
        # theme like every other chrome element -- see [[houdini-ui-theming]]) to
        # show they are one filter group: turning Attribute Set off hides the owner
        # rows / Data Structure Overhead below AND these three checkboxes, since they
        # would have nothing left to filter.
        attr_set_box = QtWidgets.QFrame()
        attr_set_box.setFrameShape(QtWidgets.QFrame.Box)
        attr_set_box.setFrameShadow(QtWidgets.QFrame.Plain)
        attr_set_row = QtWidgets.QHBoxLayout(attr_set_box)
        attr_set_row.setContentsMargins(4, 2, 4, 2)
        self._attribute_set_cb = self._scope_toggle(attr_set_row, "Attribute Set")
        # "Attribs" is dropped from these two labels' text -- the box they sit in
        # already says they belong to Attribute Set.
        self._public_cb = self._scope_toggle(attr_set_row, "Public")
        self._private_cb = self._scope_toggle(attr_set_row, "Private")
        self._groups_cb = self._scope_toggle(attr_set_row, "Groups")
        scope_row.addWidget(attr_set_box)
        self._attribute_set_cb.stateChanged.connect(self._on_attribute_set_toggled)

        self._primitive_list_cb = self._scope_toggle(scope_row, "Primitive Lists")
        # The next two gate a branch's CHILDREN only -- the branch row itself is
        # always shown. VIEW-ONLY scopes: no attribute has either, and the report
        # knows nothing about them.
        self._index_maps_cb = self._scope_toggle(scope_row, "Index Maps")
        self._group_tables_cb = self._scope_toggle(scope_row, "Group Tables")
        scope_row.addStretch(1)
        self._mem_report = MemoryReport()
        self._mem_report.toggledAttrsChanged.connect(self._update_grid)
        self._mem_report.currentItemChanged.connect(self._on_attr_selected)
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

        # Lower section: page grid + controls.
        self._grid_section = CollapsibleSection("Index Map Pages")
        controls = QtWidgets.QHBoxLayout()
        self._owner_combo = QtWidgets.QComboBox()
        self._owner_combo.addItems([owner.capitalize() for owner in OWNERS])
        self._owner_combo.setCurrentText("Point")
        self._owner_combo.currentIndexChanged.connect(self._on_owner_changed)
        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItems(["Occupancy", "Continuous block", "Memory block sharing"])
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

        self._legend = QtWidgets.QWidget()
        self._legend_layout = QtWidgets.QHBoxLayout(self._legend)
        self._legend_layout.setContentsMargins(*Style.Layout.LEGEND_MARGINS)
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

        # A vertical splitter lets the user drag the divider to give the memory
        # list more room when there are many attributes, or grow the page grid.
        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._splitter.addWidget(self._mem_section)
        self._splitter.addWidget(self._grid_section)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._open_sizes = list(Style.Layout.SPLITTER_OPEN)  # restored when both open
        self._splitter.setSizes(self._open_sizes)
        self._splitter.splitterMoved.connect(self._remember_open_sizes)
        self._mem_section.toggled.connect(self._on_section_toggled)
        self._grid_section.toggled.connect(self._on_section_toggled)
        layout.addWidget(self._splitter, 1)

    # -- section collapse / splitter sizing --------------------------------

    def _remember_open_sizes(self, *_args):
        # Remember the user's manual divider position while both are expanded.
        if self._mem_section.is_open() and self._grid_section.is_open():
            self._open_sizes = self._splitter.sizes()

    def _on_section_toggled(self, _checked):
        # Reallocate splitter space so a collapsed section frees room for the
        # other, without constraining maximumHeight (which would freeze the
        # divider). The divider stays draggable whenever both are expanded.
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

    @staticmethod
    def _swatch(color):
        # The background is a per-instance argument (every swatch a different colour),
        # which a single static PANEL_QSS rule cannot express -- so this one widget is
        # styled inline. Both colours still come from Style.
        sw = QtWidgets.QLabel()
        sw.setFixedSize(Style.Layout.SWATCH, Style.Layout.SWATCH)
        sw.setStyleSheet(
            f"background: {_rgb_css(color)}; "
            f"border: 1px solid {_rgb_css(Style.Color.HAIRLINE)};")
        return sw

    def _scope_toggle(self, layout, label):
        scope = _SCOPE_FOR_LABEL.get(label, label)
        # A checkbox stylesheet `color:` loses to Houdini's app stylesheet no
        # matter the selector, so the label lives in a separate QLabel whose
        # colour is set with inline HTML (which the stylesheet cannot override).
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
        # Public/Private/Groups have nothing left to filter once Attribute Set itself
        # hides the owner rows -- hidden, not just left checked-but-inert, so the
        # boxed group visibly shrinks to just its own toggle.
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
        layout = self._legend_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # setParent(None) removes immediately; deleteLater() alone leaves
                # old labels painted until the next event-loop pass.
                w.setParent(None)
                w.deleteLater()

        def add(color, text):
            if color is not None:
                layout.addWidget(self._swatch(color))
            legend_label = QtWidgets.QLabel(text)
            legend_label.setObjectName("legendLabel")   # coloured by Style.PANEL_QSS
            layout.addWidget(legend_label)
            layout.addSpacing(8)

        occ = Style.Color.OCC_LUT
        ps = Style.Color.PAGE_STORAGE_LUT
        mode = self._grid.mode()
        if mode == "sharing":
            add(None, "unique colour per memory block / attribute"
                if self._grid.bar_attrs() else "unique colour per memory block")
            add(Style.Color.SHARING_NONE, "block no other page uses")
        elif mode == "block":
            add(None, "contiguous block: unique colour per run")
            add(Style.Color.BLOCK_BG, "vacant / out-of-range")
        else:
            add(occ[ST_ACTIVE], "active")
            add(occ[ST_VACANT], "vacant")
            add(occ[ST_TEMP], "temporary")
            add(occ[ST_OOR], "out-of-range")
        if self._grid.bar_attrs():
            layout.addWidget(self._vsep())
            layout.addSpacing(8)
            if mode == "sharing":
                # Swatch must be the live canvas colour, not a literal.
                add(self._grid._background_rgb() + (255,), "bar not on this block")
            else:
                add(ps[PS_CONSTANT], "constant")
                add(ps[PS_SHARED], "shared")
                add(ps[PS_HARDENED], "hardened")
                on_screen = self._grid._page_storage_arrays or []
                if any(PS_CONSTANT_SHARED in codes for codes in on_screen):
                    add(ps[PS_CONSTANT_SHARED], "constant, shared")
                if any(PS_UNKNOWN in codes for codes in on_screen):
                    add(ps[PS_UNKNOWN], "unknown")
        layout.addStretch(1)

    @staticmethod
    def _vsep():
        line = QtWidgets.QFrame()
        line.setObjectName("vsep")
        line.setFrameShape(QtWidgets.QFrame.VLine)
        return line

    # -- theming ------------------------------------------------------------

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.PaletteChange:
            self._queue_repolish()

    def paintEvent(self, event):
        # changeEvent(PaletteChange) never fires when QStyleSheetStyle pins the
        # palette. Detecting the scheme change here (one comparison per repaint)
        # avoids an application-wide event filter.
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
            # Record what is being polished for, whichever route got us here. Without this
            # the event route leaves the token stale, the next repaint sees a mismatch that
            # has already been handled, and one scheme change costs two re-polishes.
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

    def _adopt_node(self, node):
        """Switch the tracked node and move the cook callback. Returns True if changed.

        Does NOT check the pause gate — that belongs to the caller."""
        if node is self._node:
            return False
        if self._node is not None:
            try:
                self._node.removeEventCallback(COOK_EVENTS, self._on_node_event)
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
        self._node = node if isinstance(node, hou.SopNode) else None
        self._populate_outputs()       # new node -> its output labels, reset to output 0
        if self._node is not None:
            self._node.addEventCallback(COOK_EVENTS, self._on_node_event)
        # Deselection is a property of the NETWORK, not the node, so the watch follows the
        # live node from one network to the next.
        self._watch_network(self._node)
        return True

    def set_node(self, node):
        # The scene talking: gated by Pause. Frozen, remember the latest selection but
        # don't switch or refresh -- Reload or resuming applies it.
        if self._paused:
            self._pending_node = node
            self._refresh_pending_hint()
            return
        if self._adopt_node(node):
            self._queue_refresh()

    def _on_reload(self):
        """Reload: adopts any pending selection, then refreshes. Not pause-gated."""
        pending, self._pending_node = self._pending_node, _UNSET
        if pending is not _UNSET:
            self._adopt_node(pending)
        self._refresh_pending_hint()
        self._queue_refresh()

    def _apply_pause_style(self):
        """(Re)build the Pause pill for its current state, and show/hide Reload with it."""
        btn = self._pause_btn
        checked = btn.isChecked()
        fg = Style.role(Style.PAUSE_ON_FG_ROLE if checked else Style.PAUSE_OFF_FG_ROLE,
                        Style.Color.PAUSE_ON_FG if checked else Style.Color.PAUSE_OFF_FG)
        glyph_px = max(9, round(self._pause_h * Style.GLYPH_RATIO))
        icon = Style.glyph_icon(Style.GLYPH_PAUSE, glyph_px, fg)
        btn.setIcon(icon or QtGui.QIcon())
        # No label at rest -> a circle around the glyph; the label (and a wider pill) only
        # while paused. Without the font, the glyph falls back to text so the button is
        # never blank.
        label = "Paused" if checked else ""
        btn.setText(label if icon else ("❚❚ Paused" if checked else "❚❚"))
        if checked:
            btn.setMinimumWidth(0)
            btn.setMaximumWidth(_WIDGET_SIZE_MAX)
        else:
            btn.setFixedWidth(self._pause_h)        # square -> the radius makes it round
        btn.setStyleSheet(Style.pause_qss(checked, self._pause_radius))
        # Reload exists only while paused (NodeToolbar hides it the same way): with updates
        # frozen it is the only way to ask for one.
        self._reload_btn.setVisible(checked)

    def _on_pause_toggled(self, paused):
        # Pause gates the three event sources (selection, cook, frame). Resuming applies
        # whatever node was selected while paused, then refreshes to catch up.
        self._paused = bool(paused)
        # The label, the glyph colour, the pill and Reload's visibility all follow the
        # state; _apply_pause_style owns the lot.
        self._apply_pause_style()
        if not self._paused:
            # Adopt whatever was selected while paused, then catch up on cooks missed.
            # _adopt_node rather than set_node: the pause flag is already cleared, but
            # going through the gate again to reach the same place is one indirection
            # that only reads as correct by accident.
            pending, self._pending_node = self._pending_node, _UNSET
            if pending is not _UNSET:
                self._adopt_node(pending)
            self._queue_refresh()

    def _populate_outputs(self):
        """Repopulate the output combo and reset to output 0."""
        combo = self._output_combo
        labels = []
        if self._node is not None:
            try:
                labels = [str(label) for label in self._node.outputLabels()]
            except hou.Error:
                labels = []
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(labels)
        self._output = 0
        combo.setCurrentIndex(0 if labels else -1)
        # Only when there is a CHOICE to make. outputLabels() returns one label per
        # output, so an ordinary SOP reports ('Output 1',) -- a combo with a single
        # entry is chrome the user can't act on.
        combo.setVisible(len(labels) > 1)
        combo.blockSignals(False)

    def _on_output_changed(self, idx):
        # User picked a different output -> re-report at that index (never negative).
        if idx < 0 or idx == self._output:
            return
        self._output = idx
        self._queue_refresh()

    def teardown(self):
        # Called from the panel's onDestroyInterface. Mark the panel dead first so
        # any executeDeferred(_refresh) already in flight (which teardown cannot
        # cancel) no-ops instead of touching now-deleted C++ widgets.
        self._alive = False
        self._remove_playbar_callback()
        self._unwatch_network()
        if self._node is not None:
            try:
                self._node.removeEventCallback(COOK_EVENTS, self._on_node_event)
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
        self._node = None

    def _on_node_event(self, **kwargs):
        if kwargs.get("event_type") == hou.nodeEventType.BeingDeleted:
            self._node = None       # always drop a deleted node's ref, even while paused
        if self._paused:
            return                  # frozen: ignore cooks (parm/input changes)
        self._queue_refresh()

    # -- frame / time-dependency --------------------------------------------

    def _add_playbar_callback(self):
        # hou.playbar exists only in graphical Houdini.
        try:
            hou.playbar.addEventCallback(self._on_frame_change)
            self._playbar_cb_added = True
        except (AttributeError, hou.Error):
            self._playbar_cb_added = False

    def _remove_playbar_callback(self):
        if not self._playbar_cb_added:
            return
        try:
            hou.playbar.removeEventCallback(self._on_frame_change)
        except (AttributeError, hou.Error):
            pass
        self._playbar_cb_added = False

    def _on_frame_change(self, event_type, frame):
        # for_last_cook=True reads dependency without forcing a cook.
        if event_type != hou.playbarEvent.FrameChanged:
            return
        if not self._alive or self._node is None or self._paused:
            return
        try:
            if self._node.isTimeDependent(for_last_cook=True):
                self._queue_refresh()
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass

    def _queue_refresh(self):
        # Coalesce a burst of cook events into a single rebuild. Skip once torn
        # down (a stray node event could still arrive before the callback is
        # fully detached).
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
        # This runs from executeDeferred on the idle loop; the panel may have been
        # torn down since it was queued (teardown can't cancel a pending deferred).
        # Bail before touching any widget -- their C++ side would be gone.
        if not self._alive:
            return
        self._instanced_label.setVisible(False)
        self._refresh_pending_hint()
        if self._node is None:
            self._model = None
            self._pin_btn.setEnabled(self._pinned is not None)
            self._header.setText(self._title())
            self._render()         # pinned: the pinned node's own report; otherwise empty
            self._update_grid()    # clears the grid AND refreshes the legend (no bars left)
            return
        try:
            self._model = MemoryModel(page_tools.report(self._node, self._output))
        except hou.Error as exc:
            self._model = None
            self._pin_btn.setEnabled(self._pinned is not None)
            self._header.setText(f"{self._node.path()}: {exc.instanceMessage()}")
            self._render()
            self._update_grid()    # clears the grid AND refreshes the legend (no bars left)
            return
        self._pin_btn.setEnabled(True)
        self._header.setText(self._title())
        instanced = [name for name, model in ((self._pinned_path, self._pinned),
                                              (self._title_live(), self._model))
                     if model is not None and model.instanced]
        if instanced:
            if self._pinned is None:
                who = ("the output is fully shared with another detail; this node owns no "
                       "memory of its own (shared detail total %s)"
                       % human_bytes(self._model.total_memory))
            elif len(instanced) > 1:
                who = ("%s are both fully shared with other details, so neither owns any "
                       "memory and the Δ New / Δ Unique columns do not apply"
                       % " and ".join(instanced))
            else:
                who = ("%s is fully shared with another detail, so it owns no memory of "
                       "its own and the Δ New / Δ Unique columns do not apply"
                       % instanced[0])
            self._instanced_label.setText("⚠ Instanced — %s." % who)
            self._instanced_label.setVisible(True)
        self._render()
        self._update_grid()

    # -- compare mode --------------------------------------------------------

    # Sub-reports whose remaining fields are meaningless without byte arrays —
    # set to None, not just stripped. page_details is NOT here: its c/s/h
    # counts stay valid without the masks.
    _STRIP_TO_NONE = ("memory_block_sharing",)

    def _strip_report(self, report):
        """Strip all `bytes` values from the report for pinning."""
        if isinstance(report, dict):
            out = {}
            for key, value in report.items():
                if isinstance(value, bytes):
                    continue
                out[key] = None if key in self._STRIP_TO_NONE else self._strip_report(value)
            return out
        if isinstance(report, list):
            return [self._strip_report(v) for v in report]
        return report

    # -- deselection ---------------------------------------------------------
    # onNodePathChanged does not fire on deselect. ChildSelectionChanged on the
    # enclosing network fills that gap. Watched only while pinned.
    # hou.ui.addSelectionCallback is the obvious alternative but requires a
    # graphical session, making it untestable here.

    def _watch_network(self, node):
        """Follow selection changes in `node`'s network. None keeps the current watch."""
        if node is None:
            return
        network = node.parent()
        # `==`, not `is` — hou.Node creates a new wrapper on every call.
        if network == self._watched_network:
            return
        self._unwatch_network()
        if self._pinned is None:
            return
        try:
            network.addEventCallback(SELECTION_EVENTS, self._on_network_selection)
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            return
        self._watched_network = network

    def _unwatch_network(self):
        if self._watched_network is None:
            return
        try:
            self._watched_network.removeEventCallback(SELECTION_EVENTS,
                                                      self._on_network_selection)
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass
        self._watched_network = None

    def _on_network_selection(self, **kwargs):
        """Handle deselect and re-select (onNodePathChanged fires for neither)."""
        if not self._alive or self._pinned is None:
            return
        selected = hou.selectedNodes()
        if not selected:
            self.set_node(None)
            return
        for node in reversed(selected):
            if isinstance(node, hou.SopNode):
                self.set_node(node)
                return

    def _on_pin_toggled(self, pinned):
        if pinned:
            if self._model is None:
                self._pin_btn.setChecked(False)
                return
            self._pinned = MemoryModel(self._strip_report(self._model._report))
            self._pinned_path = self._model.node or (self._node.path() if self._node else "")
            self._pinned_output = self._output
        else:
            self._pinned = None
            self._pinned_path = ""
        if pinned:
            self._watch_network(self._node)
        else:
            self._unwatch_network()
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
        comparing = pinned and self._model is not None and self._node is not None
        scopes = self._enabled_scopes()

        if not comparing:
            # With a pin held but nothing selected, the report shown is the PINNED node's,
            # not the live one's -- that is the point of the state, and it is where the
            # absolute figures live now that the diff carries none.
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
        pending = self._pending_node
        show = (self._paused and pending is not _UNSET and pending is not self._node)
        if show:
            name = pending.name() if pending is not None else "nothing"
            self._pending_label.setText(
                "%s selected \u2014 Reload or un-pause to read it." % name)
        self._pending_label.setVisible(bool(show))

    def _title_live(self):
        """The live node's path, however far the refresh got."""
        if self._model is not None:
            return self._model.node or (self._node.path() if self._node else "")
        return self._node.path() if self._node is not None else ""

    def _title(self):
        live = ""
        if self._model is not None:
            live = self._model.node or (self._node.path() if self._node else "")
        elif self._node is not None:
            live = self._node.path()
        if self._pinned is None:
            return live or "No SOP node selected"
        if live and self._node is not None:
            return "%s  \u2192  %s" % (self._pinned_path, live)
        return "%s   (pinned)" % self._pinned_path

    def _current_owner(self):
        return OWNERS[self._owner_combo.currentIndex()]

    def _enabled_scopes(self):
        return {scope for scope, checkbox in self._scope_cbs.items() if checkbox.isChecked()}

    def _bar_attrs_for_owner(self, owner):
        """Toggled attributes for `owner`, in tree display order."""
        return list(self._mem_report.visible_attr_keys(owner))

    def _update_grid(self):
        # Compare mode is memory-only in every state, including the pinned node's own
        # report: the pinned side is byte-stripped, so there are no pages to draw for it.
        if self._pinned is not None:
            self._grid.set_attr_colors({})
            self._grid.set_data(None, [])
            self._refresh_legend()
            return
        if self._model is None:
            self._grid.set_attr_colors({})
            self._grid.set_data(None, [])
            self._refresh_legend()
            return
        owner = self._current_owner()
        decoded = DecodedOwner(self._model.owner_map(owner), owner,
                               primitive_list_report=self._model.raw_primitive_list(),
                               attributes_report=self._model.attribute_set_owner(owner))
        # Detail-wide, so it is handed over before the bars: the colours must not depend
        # on which owner is shown or which attribute is selected.
        self._grid.set_attr_colors(_attr_color_map(self._model))
        self._grid.set_data(decoded, self._bar_attrs_for_owner(owner))
        self._refresh_legend()      # bar-class entries depend on shown bars
        # set_data cleared any emphasis; re-apply from the current selection so a
        # still-shown attribute stays highlighted (and an invalid one clears).
        self._on_attr_selected(self._mem_report.currentItem(), None)

    def _on_attr_selected(self, current, _previous):
        """Emphasize the selected attribute's bar and highlight its sharing peers."""
        key = current.data(0, QtCore.Qt.UserRole) if current is not None else None

        # Shade the attributes this one shares memory with. Peers can be on another
        # owner or hidden by the scope filter -- item_for_key returns None for those and
        # they are simply not drawn, which is why this filters rather than assuming.
        attr = current.data(0, ATTR_ROLE) if current is not None else None
        peers = getattr(attr, "shares_with_attrib_keys", ()) if attr is not None else ()
        self._mem_report.set_peer_keys(
            [tuple(p) for p in peers if self._mem_report.item_for_key(tuple(p)) is not None])

        # "Memory block sharing" mode draws THIS attribute's blocks, so it follows the
        # selection rather than the toggles.
        if self._grid.mode() == "sharing":
            self._grid.set_sharing(attr)

        # Bars are keyed on the full (owner, scope, name), so a cross-owner bar in sharing
        # mode emphasizes from its own row without a special case.
        if isinstance(key, tuple) and len(key) == 3 and tuple(key) in self._grid.bar_attrs():
            self._grid.set_emphasis(key)
        else:
            self._grid.set_emphasis(None)
            # Helpful nudge when the attribute exists but isn't currently shown. Not in
            # sharing mode: there the bars are the SELECTED attribute's peers, so neither
            # the owner combo nor a toggle would put this row's bar on screen.
            if isinstance(key, tuple) and len(key) == 3 and self._grid.mode() != "sharing":
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

    _MODES = {0: "occupancy", 1: "block", 2: "sharing"}

    def _on_mode_changed(self, idx):
        self._grid.set_mode(self._MODES.get(idx, "occupancy"))
        if self._grid.mode() == "sharing":
            # Sharing mode draws the SELECTED attribute's peers, so entering it has to pick
            # up whatever is already selected rather than waiting for the next click.
            self._sync_sharing_selection()
            self._refresh_legend()      # grid entries depend on the mode
        else:
            # Leaving sharing mode — rebuild bars from toggles.
            self._update_grid()

    def _sync_sharing_selection(self):
        """Pass the selected attribute to the grid for sharing mode."""
        current = self._mem_report.currentItem()
        attr = current.data(0, ATTR_ROLE) if current is not None else None
        self._grid.set_sharing(attr)

    def _on_res_changed(self, _idx):
        self._grid.set_cell_px(self._res_combo.currentData())
