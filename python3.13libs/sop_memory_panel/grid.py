import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .decode import (
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
    _block_palette,
)
from .block_labels import _base36, _draw_block_labels, _label_inks
from .style import Style, _blend_lut32, _blend_packed32, _rgb_css, _u32, human_bytes


def _attribute_color_map(model):
    """{(owner, scope, name): uint32} bar colour for every attribute in the report."""
    if model is None:
        return {}
    palette = _block_palette(model.num_attribute_positions(), Style.Color.SHARING_ATTRIB_SEED)
    return {attrib.key: int(palette[model.attribute_position(attrib.key)])
            for attrib in model.attributes()}


def _sharing_lut(block_ids):
    """uint32 colour per memory block id (id 0 = muted grey)."""
    size = (int(block_ids.max()) + 1) if len(block_ids) else 1
    lut = np.empty(size, dtype=np.uint32)
    lut[0] = Style.Color.SHARING_NONE32
    if size > 1:
        lut[1:] = _block_palette(size - 1, Style.Color.SHARING_SEED)
    return lut


class MemoryBlockSharingState:
    def __init__(self, attrib):
        self.attrib = attrib
        self.block_ids = np.frombuffer(attrib.memory_block_ids, np.uint32)
        self.mapping_indices = np.frombuffer(attrib.shares_with_mapping_indices, np.uint32)
        self.mapping = attrib.shares_with_mapping
        self.lut = _sharing_lut(self.block_ids)
        self._dimmed_lut_cache = (None, None)
        self._label_inks_cache = (None, None)
        self._block_members_cache = (0, None)

    def dimmed_lut(self, background_rgb):
        """`lut` blended toward the canvas for un-highlighted tiles."""
        cached_rgb, cached = self._dimmed_lut_cache
        if cached is None or cached_rgb != background_rgb:
            cached = _blend_packed32(self.lut, background_rgb + (255,), Style.Color.DIM_BLEND)
            self._dimmed_lut_cache = (background_rgb, cached)
        return cached

    def label_inks(self, background_rgb):
        cached_rgb, cached = self._label_inks_cache
        if cached is None or cached_rgb != background_rgb:
            cached = _label_inks(self.lut, background_rgb)
            self._label_inks_cache = (background_rgb, cached)
        return cached

    def block_at(self, page):
        """Memory block id at `page` of the selected attribute, or 0."""
        if page is None or not (0 <= page < len(self.block_ids)):
            return 0
        return int(self.block_ids[page])

    def block_members(self, block):
        """Pages of the selected attribute on `block`, ascending. Cached."""
        if block == 0:
            return np.zeros(0, np.int64)
        cached_block, cached = self._block_members_cache
        if cached is not None and cached_block == block:
            return cached
        members = np.flatnonzero(self.block_ids == block)
        self._block_members_cache = (block, members)
        return members

    def sharing_bar_indices(self, page):
        entry = int(self.mapping_indices[page])
        return self.mapping[entry] if entry < len(self.mapping) else []


# ---------------------------------------------------------------------------
# Page grid (lower section)
# ---------------------------------------------------------------------------

class PageGridWidget(QtWidgets.QAbstractScrollArea):
    """Virtualised wrapped grid of page cards for one owner.

    All spacing / sizing is read from Style.Layout (H_GAP, V_GAP, GRID_BAR_GAP,
    BAR_GAP, LEFT_PAD, ...); this class holds no look-and-feel constants of its own.
    """

    hoverInfo = QtCore.Signal(str)
    barHovered = QtCore.Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        # Same reasoning as MemoryReport: the grid is a canvas sitting on the panel, not a
        # text field, so it takes Window rather than the scroll area's default Base.
        self.viewport().setBackgroundRole(QtGui.QPalette.Window)
        self._background_cache_rgb = None       # see _background_packed / _dimmed_storage_lut
        self._background_cache_packed = 0
        self._dimmed_storage_lut_cache = None
        self._dimmed_color_cache = {}      # single packed colours, same blend (see _dimmed_packed)

        self._decoded = None
        self._mode = "occupancy"           # or "block"
        self._cell_px = Style.Layout.DEFAULT_CELL_PX
        self._bar_attribs = []             # ordered [(owner, scope, name)] keys
        self._page_storage_arrays = []            # aligned with _bar_attribs
        self._emphasis = None              # index into _bar_attribs, or None (no highlight)
        self._sharing = None               # MemoryBlockSharingState, or None
        self._selected_attrib = None       # drives the sharing-mode empty-state message
        self._attrib_colors = {}           # (owner, scope, name) -> uint32; see set_attribute_colors
        # The memory block a CLICK pinned (0 = none); ids are detail-wide, so it outlives
        # the selection. Not hover: the highlight repaints the whole grid.
        self._pinned_block = 0
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)

    # -- public API ---------------------------------------------------------

    def set_data(self, decoded, bar_attribs):
        self._decoded = decoded
        self._pinned_block = 0
        if self._mode == "sharing":
            # A re-cook that changed the page count leaves block_ids short of the band: drop
            # it. _update_grid re-applies the selection right after.
            if self._sharing is not None and decoded is not None \
                    and len(self._sharing.block_ids) != decoded.num_pages:
                self._sharing = None
            self._update_scrollbar()
            self.viewport().update()
            return
        self._bar_attribs = list(bar_attribs)
        self._page_storage_arrays = [decoded.attribute_page_storage(s, n)
                                     for (_o, s, n) in self._bar_attribs] if decoded else []
        self._emphasis = None              # bar set changed; panel re-applies after
        self._update_scrollbar()
        self.viewport().update()

    def set_attribute_colors(self, colors):
        """{(owner, scope, name): uint32} bar colour of every attribute, from
        _attribute_color_map, so one attribute has one colour wherever it is drawn."""
        self._attrib_colors = dict(colors)
        self.viewport().update()

    def bar_attributes(self):
        """The (owner, scope, name) key of each bar shown, top to bottom."""
        return list(self._bar_attribs)

    def shows_page_storage_code(self, page_storage_code):
        return any(page_storage_code in codes for codes in self._page_storage_arrays)

    def decoded(self):
        return self._decoded

    def mode(self):
        return self._mode

    def emphasis(self):
        """Index into bar_attributes() of the emphasized bar, or None."""
        return self._emphasis

    def set_emphasis(self, key):
        """Highlight one attribute bar (dim the rest); None clears."""
        idx = None
        if key is not None:
            try:
                idx = self._bar_attribs.index(tuple(key))
            except ValueError:
                idx = None
        if idx != self._emphasis:
            self._emphasis = idx
            self.viewport().update()

    def set_sharing(self, attrib):
        """The selected attribute, for "sharing" mode -- or None when nothing is selected."""
        if self._mode != "sharing":
            return
        # Kept even when there is nothing to draw: which of the three empty states applies
        # depends on the attribute, not on the absence of sharing data.
        self._selected_attrib = attrib
        self._sharing = None
        # has_memory_block_sharing is the whole test: a dict or string attribute shares its
        # value table, not pages; one that only reuses its own blocks still belongs.
        if attrib is not None and attrib.has_memory_block_sharing:
            self._sharing = MemoryBlockSharingState(attrib)
            self._bar_attribs = list(attrib.shares_with_attrib_keys)
            self._page_storage_arrays = []
        else:
            # Clear bar rows too — stale _bar_attribs + None _sharing crashes _hover_at.
            self._bar_attribs = []
            self._page_storage_arrays = []
        self._emphasis = None
        self._update_scrollbar()
        self.viewport().update()

    def sharing(self):
        """The MemoryBlockSharingState for "sharing" mode, or None. Exposed for tests."""
        return self._sharing

    def pinned_block(self):
        """The memory block being highlighted -- the one a click pinned -- or 0."""
        return self._pinned_block

    def _block_at(self, page):
        return self._sharing.block_at(page) if self._sharing is not None else 0

    def set_mode(self, mode):
        self._mode = mode
        # Only "sharing" draws a block highlight, and a pin left behind by a previous
        # visit would come back lit without the click that made it.
        self._pinned_block = 0
        self._update_scrollbar()
        self.viewport().update()

    def set_cell_px(self, px):
        self._cell_px = max(1, int(px))
        self._update_scrollbar()
        self.viewport().update()

    # -- geometry -----------------------------------------------------------

    def _grid_px(self):
        return self._decoded.page_dim * self._cell_px

    def _attribute_bar_height(self):
        return max(4, self._cell_px)

    def _gutter_width(self):
        return Style.Layout.LEFT_PAD

    def _bars_block_h(self):
        num_bars = len(self._bar_attribs)
        if num_bars == 0:
            return 0
        return Style.Layout.GRID_BAR_GAP + num_bars * self._attribute_bar_height() + (num_bars - 1) * Style.Layout.BAR_GAP

    def _card_height(self):
        return self._grid_px() + self._bars_block_h()

    def _card_width(self):
        return self._grid_px()

    def _cards_per_row(self):
        avail = self.viewport().width() - self._gutter_width() - Style.Layout.H_GAP
        step = self._card_width() + Style.Layout.H_GAP
        return max(1, (avail + Style.Layout.H_GAP) // step)

    def _row_stride(self):
        return self._card_height() + Style.Layout.V_GAP

    def _total_page_count(self):
        return self._decoded.num_pages if self._decoded else 0

    def _update_scrollbar(self):
        n = self._total_page_count()
        if n == 0:
            self.verticalScrollBar().setRange(0, 0)
            return
        cols = self._cards_per_row()
        rows = (n + cols - 1) // cols
        total_h = rows * self._row_stride()
        page_step = self.viewport().height()
        self.verticalScrollBar().setPageStep(page_step)
        self.verticalScrollBar().setSingleStep(self._row_stride())
        self.verticalScrollBar().setRange(0, max(0, total_h - page_step))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scrollbar()

    # -- painting -----------------------------------------------------------

    def background_rgb(self):
        """The grid canvas colour from the current colour scheme."""
        pal = self.viewport().palette()
        return pal.color(self.viewport().backgroundRole()).getRgb()[:3]

    def _background_packed(self):
        """`background_rgb()` packed for the uint32 band, cached per colour."""
        rgb = self.background_rgb()
        if rgb != self._background_cache_rgb:
            self._background_cache_rgb = rgb
            self._background_cache_packed = _u32(rgb + (255,))
            # Everything dimmed blends toward the background, so both caches go.
            self._dimmed_storage_lut_cache = None
            self._dimmed_color_cache = {}
        return self._background_cache_packed

    def _dimmed_storage_lut(self):
        """Page-storage colours blended toward the canvas, for un-emphasised bars."""
        self._background_packed()                      # refreshes the cache / invalidates this one
        if self._dimmed_storage_lut_cache is None:
            self._dimmed_storage_lut_cache = _blend_lut32(
                Style.Color.PAGE_STORAGE_LUT, self._background_cache_rgb + (255,),
                Style.Color.DIM_BLEND)
        return self._dimmed_storage_lut_cache

    def _dimmed_packed(self, color):
        """One packed colour blended toward the canvas, for per-attribute bar colours."""
        self._background_packed()                      # refreshes the cache / invalidates this one
        dimmed = self._dimmed_color_cache.get(color)
        if dimmed is None:
            dimmed = int(_blend_packed32(np.uint32(color), self._background_cache_rgb + (255,),
                                         Style.Color.DIM_BLEND)[0])
            self._dimmed_color_cache[color] = dimmed
        return dimmed

    def paintEvent(self, event):
        painter = QtGui.QPainter(self.viewport())
        # Whatever Houdini's current colour scheme puts behind a view. The canvas is
        # chrome, not data -- only the cards and bars drawn on it carry meaning.
        painter.fillRect(self.viewport().rect(), QtGui.QColor(*self.background_rgb()))
        n = self._total_page_count()
        if n == 0:
            painter.setPen(QtGui.QColor(*Style.Color.NO_PAGES_FG))
            painter.drawText(self.viewport().rect(), QtCore.Qt.AlignCenter,
                             "No pages (empty geometry or no node selected)")
            return

        # Sharing mode draws ONE attribute's peers, so it needs a selection. Blank with a
        # message beats a grid that silently shows nothing.
        if self._mode == "sharing" and self._sharing is None:
            painter.setPen(QtGui.QColor(*Style.Color.NO_PAGES_FG))
            painter.drawText(self.viewport().rect(), QtCore.Qt.AlignCenter,
                             self._sharing_empty_text())
            return

        cols = self._cards_per_row()
        stride = self._row_stride()
        scroll_y = self.verticalScrollBar().value()
        vp_h = self.viewport().height()
        first_row = scroll_y // stride
        last_row = (scroll_y + vp_h - 1) // stride
        max_row = (n - 1) // cols
        last_row = min(last_row, max_row)
        if first_row > last_row:
            return

        gutter_w = self._gutter_width()
        cards_w = self.viewport().width() - gutter_w
        band_top = first_row * stride
        band_h = (last_row - first_row + 1) * stride

        # One QImage per frame borrowing the band buffer, no copy: drawImage reads it
        # synchronously. The band is viewport-bounded, so this stays O(viewport).
        band = np.full((band_h, cards_w), self._background_packed(), dtype=np.uint32)
        self._render_band(band, first_row, last_row, cols)
        qimg = QtGui.QImage(band.data, cards_w, band_h, 4 * cards_w,
                            QtGui.QImage.Format_RGBA8888)
        painter.drawImage(gutter_w, band_top - scroll_y, qimg)

    def _draw_pinned_outline(self, grids, block_ids, pinned):
        """Ring the pinned block's tiles in the theme's accent, in place."""
        if not pinned:
            return
        pages = np.flatnonzero(block_ids == pinned)
        if not len(pages):
            return
        grid_px = grids.shape[1]
        width = max(1, min(Style.Layout.PIN_OUTLINE_MAX_PX,
                           grid_px // Style.Layout.PIN_OUTLINE_PER_PX))
        ring = _u32(Style.role(Style.PIN_OUTLINE_ROLE, Style.Color.PEER_BG[:3]) + (255,))
        # Fancy indexing hands back a copy, so the ring is drawn on that and put back.
        tiles = grids[pages]
        tiles[:, :width, :] = ring
        tiles[:, -width:, :] = ring
        tiles[:, :, :width] = ring
        tiles[:, :, -width:] = ring
        grids[pages] = tiles

    # Pages of these types hold handles into a value table. AttribCopy gives the copy fresh
    # handles and shares the table, so the shared memory has no page geometry to draw.
    _VALUE_TABLE_TYPES = ("string", "dict")

    def _sharing_empty_text(self):
        """Diagnostic string for the empty sharing view."""
        attrib = self._selected_attrib
        if attrib is None:
            return "Select an attribute"
        if not attrib.has_page_details:
            return f"Page info is unavailable for {attrib.name} ({attrib.type_name})"
        if not attrib.shares_with_attrib_keys:
            # Only reached when the attribute does not reuse its own blocks either:
            # set_sharing() enters the mode on has_memory_block_sharing alone.
            return f"{attrib.name} shares no memory with another attribute"
        if attrib.type_name in self._VALUE_TABLE_TYPES:
            return (f"{attrib.name} shares "
                    f"{human_bytes(attrib.intra_detail_memory_sharing)} in its "
                    f"value table, not its pages")
        return (f"{attrib.name} shares "
                f"{human_bytes(attrib.intra_detail_memory_sharing)}, but which "
                f"pages could not be determined")

    def _render_band(self, band, first_row, last_row, cols):
        """Composite the visible cards into the uint32 band image (numpy only)."""
        n = self._total_page_count()
        grid_px = self._grid_px()
        stride = self._row_stride()
        band_top = first_row * stride

        p0 = first_row * cols
        p1 = min((last_row + 1) * cols, n)
        grids = self._card_top_images(p0, p1)

        for page in range(p0, p1):
            row = page // cols
            col = page % cols
            x = col * (grid_px + Style.Layout.H_GAP)
            y = row * stride - band_top
            band[y:y + grid_px, x:x + grid_px] = grids[page - p0]
            # Attribute bars beneath the card, in the order the toggled rows appear in the
            # report.
            bar_y = y + grid_px + Style.Layout.GRID_BAR_GAP
            if self._mode == "sharing":
                self._fill_sharing_bars(band, page, x, bar_y)
            else:
                self._fill_page_storage_bars(band, page, x, bar_y)

    def _card_top_images(self, p0, p1):
        cell_px = self._cell_px
        page_dim = self._decoded.page_dim
        grid_px = self._grid_px()
        # Card tops: page_dim x page_dim uint32 cells upscaled by cell px (only the visible
        # pages, so the work is bounded by the viewport, not the total map).
        if self._mode == "sharing":
            block_ids = self._sharing.block_ids[p0:p1].astype(np.int64)
            lut = self._sharing.lut
            ids = np.clip(block_ids, 0, len(lut) - 1)
            pinned_block = self._pinned_block
            if pinned_block:
                flat = np.where(block_ids == pinned_block, lut[ids],
                                self._sharing.dimmed_lut(self._background_cache_rgb)[ids])
            else:
                flat = lut[ids]
            grids = np.repeat(flat, page_dim * page_dim).reshape(-1, page_dim, page_dim)
            label_ids = block_ids
        elif self._mode == "block":
            grids = self._decoded.block_colors_u32(p0, p1).reshape(-1, page_dim, page_dim)
            label_ids = None
        else:
            states = self._decoded.occupancy_states(p0, p1)
            grids = Style.Color.OCC_LUT32[states.reshape(-1, page_dim, page_dim)]
            label_ids = None
        if cell_px > 1:
            grids = np.repeat(np.repeat(grids, cell_px, axis=1), cell_px, axis=2)

        # Cell separators at higher zoom. Not in sharing mode — the whole page is one
        # colour, so the cell lattice would divide identical slots.
        if label_ids is None and cell_px >= Style.Layout.CELL_GRIDLINE_MIN:
            edge_px = 2 if cell_px >= Style.Layout.CELL_GRIDLINE_THICK else 1
            edge = (np.arange(grid_px) % cell_px) < edge_px
            grids[:, edge, :] = Style.Color.GRIDLINE32
            grids[:, :, edge] = Style.Color.GRIDLINE32

        if label_ids is not None:
            self._draw_pinned_outline(grids, label_ids, self._pinned_block)
            lit_ink, dim_ink = self._sharing.label_inks(self._background_cache_rgb)
            _draw_block_labels(grids, label_ids, self._pinned_block, self._sharing.lut,
                               lit_ink, dim_ink)
        return grids

    def _fill_sharing_bars(self, band, page, x, bar_y):
        grid_px = self._grid_px()
        bar_h = self._attribute_bar_height()
        pinned_block = self._pinned_block
        # One bar row per peer, filled where that peer is on this page's block, in the
        # peer's own colour.
        sharing_bar_indices = self._sharing.sharing_bar_indices(page)
        # A page off the pinned block is dimmed whole, bars included. `_emphasis` plays no
        # part: the bars are the selected attribute's peers, never the selected row.
        is_off_pinned_block = (bool(pinned_block)
                               and self._sharing.block_at(page) != pinned_block)
        for bar_idx, key in enumerate(self._bar_attribs):
            if bar_idx not in sharing_bar_indices:
                band[bar_y:bar_y + bar_h, x:x + grid_px] = self._background_packed()
                bar_y += bar_h + Style.Layout.BAR_GAP
                continue
            color = self._attrib_colors.get(key, Style.Color.SHARING_NONE32)
            if is_off_pinned_block:
                color = self._dimmed_packed(color)
            band[bar_y:bar_y + bar_h, x:x + grid_px] = color
            bar_y += bar_h + Style.Layout.BAR_GAP

    def _fill_page_storage_bars(self, band, page, x, bar_y):
        grid_px = self._grid_px()
        bar_h = self._attribute_bar_height()
        emphasized_bar = self._emphasis
        for bar_idx, page_storage_codes in enumerate(self._page_storage_arrays):
            page_storage_code = page_storage_codes[page]
            lut = (Style.Color.PAGE_STORAGE_LUT32
                   if (emphasized_bar is None or bar_idx == emphasized_bar)
                   else self._dimmed_storage_lut())
            band[bar_y:bar_y + bar_h, x:x + grid_px] = (
                lut[page_storage_code] if page_storage_code != PS_NONE
                else self._background_packed())
            bar_y += bar_h + Style.Layout.BAR_GAP

    # -- interaction --------------------------------------------------------

    _PAGE_STORAGE_LABEL = {PS_CONSTANT: "constant", PS_SHARED: "shared",
                           PS_HARDENED: "hardened", PS_UNKNOWN: "unknown (no hardness API)",
                           PS_CONSTANT_SHARED: "constant, shared"}

    def _page_offsets(self, page):
        """``[first - last]`` element offset range for `page`."""
        page_size = self._decoded.page_size
        start = page * page_size
        end = min(start + page_size, self._decoded.offset_size) - 1
        return f"[{start} - {max(start, end)}]"

    # How many block-mates the hover names before counting them: a 256-way self-merge puts
    # every page on one block, and the status line is one line.
    _MAX_NAMED_BLOCK_MATES = 6

    def _block_mates_text(self, page):
        """Sharing info for the hovered page, or ""."""
        if self._sharing is None:
            return ""
        block = self._block_at(page)
        if block == 0:
            return "on a block no other page uses"
        block_label = _base36(block)
        other_pages = [int(p) for p in self._sharing.block_members(block) if int(p) != page]
        if not other_pages:
            return f"block {block_label}"
        named_pages = ", ".join(str(p) for p in other_pages[:self._MAX_NAMED_BLOCK_MATES])
        if len(other_pages) > self._MAX_NAMED_BLOCK_MATES:
            named_pages += f", +{len(other_pages) - self._MAX_NAMED_BLOCK_MATES} more"
        return f"block {block_label}, also on pages {named_pages}"

    def _hover_at(self, pos):
        """``(info_str, bar_idx, page)`` for the cursor position."""
        decoded = self._decoded
        if decoded is None or decoded.num_pages == 0:
            return "", None, None
        # Mirrors paintEvent: sharing mode with nothing to share draws the empty-state text,
        # so there is no card under the cursor.
        if self._mode == "sharing" and self._sharing is None:
            return "", None, None
        grid_px = self._grid_px()
        stride = self._row_stride()
        cols = self._cards_per_row()
        x = pos.x() - self._gutter_width()
        if x < 0:
            return "", None, None
        col = x // (grid_px + Style.Layout.H_GAP)
        if col >= cols or (x % (grid_px + Style.Layout.H_GAP)) >= grid_px:
            return "", None, None
        y = pos.y() + self.verticalScrollBar().value()
        page = (y // stride) * cols + col
        if not (0 <= page < decoded.num_pages):
            return "", None, None
        local_y = y % stride
        if local_y < grid_px:
            return self._tile_hover_text(page), None, page
        # Below the grid: which attribute bar (if any) is under the cursor.
        bar_offset = local_y - grid_px - Style.Layout.GRID_BAR_GAP
        step = self._attribute_bar_height() + Style.Layout.BAR_GAP
        bar_idx = bar_offset // step
        if (bar_offset < 0 or bar_idx >= len(self._bar_attribs)
                or (bar_offset % step) >= self._attribute_bar_height()):
            return "", None, None
        # Guard on the DATA, not just the mode: _bar_attribs and _sharing are set together,
        # but a future path that clears one without the other must not crash the hover.
        if self._mode == "sharing" and self._sharing is None:
            return "", None, None
        return self._bar_hover_text(page, int(bar_idx)), int(bar_idx), page

    def _tile_hover_text(self, page):
        decoded = self._decoded
        info = f"{decoded.owner} page {page} {self._page_offsets(page)}"
        if self._mode == "sharing":
            # No occupancy counts here: the card answers which block, and slot counts would
            # crowd that out.
            mates = self._block_mates_text(page)
            if mates:
                info += f"   {mates}"
        else:
            info += (f"   active {int(decoded.num_active[page])}   "
                     f"temporary {int(decoded.num_temp[page])}   "
                     f"vacant {int(decoded.num_vacant[page])}")
        return info

    def _bar_hover_text(self, page, bar_idx):
        owner, scope, name = self._bar_attribs[bar_idx]
        offsets = self._page_offsets(page)
        if self._mode == "sharing":
            attrib = self._sharing.attrib
            block_state_text = ("shares this page's block"
                                if bar_idx in self._sharing.sharing_bar_indices(page)
                                else "is not on this page's block")
            # The peer's OWNER is named: it need not be the grid's, and "id / class" with
            # no owner would read as two point attributes.
            return (f"{attrib.name} / {name} [{owner} {scope}]   page {page} {offsets}   "
                    f"{block_state_text}")
        page_storage_code = int(self._page_storage_arrays[bar_idx][page])
        label = self._PAGE_STORAGE_LABEL.get(page_storage_code, "n/a")
        return f"{name} [{scope}]   page {page} {offsets}   {label}"

    def mouseMoveEvent(self, event):
        # Reads the grid, never repaints it. The block highlight is pinned by a CLICK, so
        # the whole grid does not re-render under a moving cursor.
        info, idx, _page = self._hover_at(event.position().toPoint())
        self.hoverInfo.emit(info)
        if idx is not None:
            self.barHovered.emit(*self._bar_attribs[idx])
        else:
            self.barHovered.emit("", "", "")

    def mousePressEvent(self, event):
        """Click a page to pin its memory block highlight."""
        super().mousePressEvent(event)
        if self._mode != "sharing":
            return
        _info, _idx, page = self._hover_at(event.position().toPoint())
        block = self._block_at(page)
        self._pinned_block = 0 if block in (0, self._pinned_block) else block
        self.viewport().update()

    def leaveEvent(self, event):
        self.hoverInfo.emit("")
        self.barHovered.emit("", "", "")


class PageGridLegend(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(*Style.Layout.LEGEND_MARGINS)

    @staticmethod
    def _swatch(color):
        # Styled inline: each swatch has its own background, which one static PANEL_QSS rule
        # cannot express. Both colours still come from Style.
        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(Style.Layout.SWATCH, Style.Layout.SWATCH)
        swatch.setStyleSheet(
            f"background: {_rgb_css(color)}; "
            f"border: 1px solid {_rgb_css(Style.Color.HAIRLINE)};")
        return swatch

    def refresh(self, grid):
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # setParent(None) removes immediately; deleteLater() alone leaves
                # old labels painted until the next event-loop pass.
                w.setParent(None)
                w.deleteLater()

        def add_legend_entry(color, text):
            if color is not None:
                layout.addWidget(self._swatch(color))
            legend_label = QtWidgets.QLabel(text)
            legend_label.setObjectName("legendLabel")   # coloured by Style.PANEL_QSS
            layout.addWidget(legend_label)
            layout.addSpacing(8)

        occupancy_lut = Style.Color.OCC_LUT
        page_storage_lut = Style.Color.PAGE_STORAGE_LUT
        mode = grid.mode()
        if mode == "sharing":
            add_legend_entry(None, "unique colour per memory block / attribute"
                if grid.bar_attributes() else "unique colour per memory block")
            add_legend_entry(Style.Color.SHARING_NONE, "block no other page uses")
        elif mode == "block":
            add_legend_entry(None, "contiguous block: unique colour per run")
            add_legend_entry(Style.Color.BLOCK_BG, "vacant / out-of-range")
        else:
            add_legend_entry(occupancy_lut[ST_ACTIVE], "active")
            add_legend_entry(occupancy_lut[ST_VACANT], "vacant")
            add_legend_entry(occupancy_lut[ST_TEMP], "temporary")
            add_legend_entry(occupancy_lut[ST_OOR], "out-of-range")
        if grid.bar_attributes():
            layout.addWidget(self._vsep())
            layout.addSpacing(8)
            if mode == "sharing":
                # Swatch must be the live canvas colour, not a literal.
                add_legend_entry(grid.background_rgb() + (255,), "bar not on this block")
            else:
                add_legend_entry(page_storage_lut[PS_CONSTANT], "constant")
                add_legend_entry(page_storage_lut[PS_SHARED], "shared")
                add_legend_entry(page_storage_lut[PS_HARDENED], "hardened")
                if grid.shows_page_storage_code(PS_CONSTANT_SHARED):
                    add_legend_entry(page_storage_lut[PS_CONSTANT_SHARED], "constant, shared")
                if grid.shows_page_storage_code(PS_UNKNOWN):
                    add_legend_entry(page_storage_lut[PS_UNKNOWN], "unknown")
        layout.addStretch(1)

    @staticmethod
    def _vsep():
        line = QtWidgets.QFrame()
        line.setObjectName("vsep")
        line.setFrameShape(QtWidgets.QFrame.VLine)
        return line
