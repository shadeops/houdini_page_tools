import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .decode import (
    PAGE_DIM,
    PAGE_SIZE,
    PS_CONSTANT,
    PS_CONSTANT_SHARED,
    PS_HARDENED,
    PS_NONE,
    PS_SHARED,
    PS_UNKNOWN,
    _block_palette,
)
from .style import Style, _u32, human_bytes


def _blend_lut32(lut, bg, t):
    """`lut` (N,4) blended t of the way toward bg (an RGBA tuple), packed to uint32.
    Alpha stays opaque."""
    out = (lut.astype(np.float64) * (1.0 - t)
           + np.array(bg, np.float64) * t).astype(np.uint8)
    out[:, 3] = 255
    return out.view(np.uint32).reshape(-1).copy()


def _blend_packed32(colors, bg, t):
    """Blend packed uint32 colours toward bg; unpacks to (N,4) for _blend_lut32."""
    rgba = np.ascontiguousarray(np.atleast_1d(colors), np.uint32).view(np.uint8).reshape(-1, 4)
    return _blend_lut32(rgba, bg, t)


# sRGB -> Oklab, the direction decode._OKLAB_TO_LMS does not go. Only the L row is kept:
# the label ink is chosen on lightness alone, and a and b would be carried to be thrown
# away. Ottosson's matrix, verified against coloraide in tests/test_page_panel.py.
_LINEAR_RGB_TO_LMS = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005]])
_LMS_TO_OKLAB_L = np.array([0.2104542553, 0.7936177850, -0.0040720468])


def _oklab_lightness(colors):
    """(N,) Oklab L in [0, 1] for packed uint32 RGBA colours."""
    rgba = np.ascontiguousarray(np.atleast_1d(colors), np.uint32).view(np.uint8).reshape(-1, 4)
    srgb = rgba[:, :3] / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    return np.cbrt(linear @ _LINEAR_RGB_TO_LMS.T) @ _LMS_TO_OKLAB_L


def _label_ink(colors):
    """(N, 3) uint8 ink colour (light or dark) for a label drawn ON each of `colors`."""
    light = _oklab_lightness(colors) < Style.Color.LABEL_INK_SWITCH_L
    ink = np.empty((len(light), 3), np.uint8)
    ink[light] = Style.Color.LABEL_INK_LIGHT
    ink[~light] = Style.Color.LABEL_INK_DARK
    return ink


# Houdini's UI font for tile labels. Resolved once (font-db lookup on paint path).
_LABEL_FAMILY = None


def _label_family():
    global _LABEL_FAMILY
    if _LABEL_FAMILY is not None:
        return _LABEL_FAMILY
    families = QtGui.QFontDatabase.families()
    if "Lato" not in families:
        hfs = os.environ.get("HFS")
        if hfs:
            path = os.path.join(hfs, "houdini", "fonts", "Lato-Bold.ttf")
            handle = QtGui.QFontDatabase.addApplicationFont(path)
            names = QtGui.QFontDatabase.applicationFontFamilies(handle)
            # Not an error worth reporting: the labels still draw, in the application
            # font, and a panel that refused to paint because a font was missing would be
            # a worse answer than one that paints in a different face.
            _LABEL_FAMILY = names[0] if names else QtWidgets.QApplication.font().family()
            return _LABEL_FAMILY
        _LABEL_FAMILY = QtWidgets.QApplication.font().family()
        return _LABEL_FAMILY
    _LABEL_FAMILY = "Lato"
    return _LABEL_FAMILY


def _label_font(font_px):
    font = QtGui.QFont(_label_family())
    font.setPixelSize(int(font_px))
    font.setBold(True)                  # small glyphs over a saturated tile
    return font


# Block ids are identifiers, not ordinals — base 36 reads as such (decimal
# invites false inferences about adjacency) and fits more ids per tile.
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value):
    """`value` as a base-36 string, the form the tiles and the status line both use."""
    if value <= 0:
        return _BASE36[0]
    out = ""
    while value:
        value, digit = divmod(value, len(_BASE36))
        out = _BASE36[digit] + out
    return out


def _label_font_px(grid_px, num_chars):
    """Largest font pixel size fitting ``num_chars`` in a ``grid_px`` tile, or 0."""
    budget = grid_px - 2 * Style.Layout.LABEL_PAD
    largest = int(grid_px * Style.Layout.LABEL_MAX_FRACTION)
    for font_px in range(largest, Style.Layout.LABEL_MIN_PX - 1, -1):
        metrics = QtGui.QFontMetrics(_label_font(font_px))
        advance = max(metrics.horizontalAdvance(c) for c in _BASE36)
        height = metrics.tightBoundingRect(_BASE36).height()
        if num_chars * advance <= budget and height <= budget:
            return font_px
    return 0


_GLYPH_ATLAS_CACHE = {}


def _glyph_atlas(font_px):
    """(atlas, width, height, middle, advances) — coverage masks for base-36 glyphs."""
    key = int(font_px)
    cached = _GLYPH_ATLAS_CACHE.get(key)
    if cached is not None:
        return cached
    font = _label_font(key)
    metrics = QtGui.QFontMetrics(font)
    width = max(metrics.horizontalAdvance(c) for c in _BASE36)
    box = metrics.tightBoundingRect(_BASE36)
    height = box.height()
    baseline = -box.top()
    middle = baseline - metrics.capHeight() / 2.0
    atlas = np.zeros((len(_BASE36), height, width), np.uint8)
    image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
    for index, glyph in enumerate(_BASE36):
        image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(image)
        painter.setFont(font)
        painter.setPen(QtCore.Qt.white)         # only the alpha channel is read back
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.drawText(QtCore.QPointF((width - metrics.horizontalAdvance(glyph)) / 2.0,
                                        baseline), glyph)
        painter.end()
        # Format_ARGB32 is BGRA in memory on a little-endian host, so alpha is byte 3.
        # bytesPerLine, not width * 4: Qt pads scanlines to a 4-byte boundary and a
        # reshape on width alone would shear the glyph at odd widths.
        buffer = np.frombuffer(image.constBits(), np.uint8)
        atlas[index] = buffer.reshape(height, image.bytesPerLine() // 4, 4)[:, :width, 3]
    advances = np.array([metrics.horizontalAdvance(c) for c in _BASE36], np.int64)
    _GLYPH_ATLAS_CACHE[key] = (atlas, width, height, middle, advances)
    return _GLYPH_ATLAS_CACHE[key]


def _attr_color_map(model):
    """{(owner, scope, name): uint32} bar colour for every attribute in the report."""
    if model is None:
        return {}
    palette = _block_palette(model.num_attr_positions(), Style.Color.SHARING_ATTR_SEED)
    return {attr.key: int(palette[model.attr_position(attr.key)])
            for attr in model.attrs()}


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
        self._bar_attrs = []               # ordered [(owner, scope, name)] keys
        self._page_storage_arrays = []            # aligned with _bar_attrs
        self._emphasis = None              # index into _bar_attrs, or None (no highlight)
        self._sharing = None               # (attr, block_ids, map_indices, mapping) or None
        self._selected_attr = None         # drives the sharing-mode empty-state message
        self._sharing_lut_cache = None
        self._sharing_dimmed_storage_lut_cache = None
        self._sharing_ink_cache = None
        self._attr_colors = {}             # (owner, scope, name) -> uint32; see set_attr_colors
        # The memory block a CLICK pinned (0 = none). Block ids are detail-wide, so a pin
        # outlives the selection that made it. Deliberately not driven by hover: the
        # highlight repaints the whole grid, and doing that under the moving cursor is
        # noise rather than information.
        self._pinned_block = 0
        self._block_members_cache = (0, None)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)

    # -- public API ---------------------------------------------------------

    def set_data(self, decoded, bar_attrs):
        self._decoded = decoded
        self._pinned_block = 0
        self._block_members_cache = (0, None)
        if self._mode == "sharing":
            # Sharing bars come from selection, not toggles — keep them, UNLESS a live
            # re-cook changed the page count out from under the cached block-id array:
            # its length no longer matches decoded.num_pages, and _render_band's
            # `self._sharing[1][p0:p1]` would silently return fewer rows than the band
            # it is asked to fill, so the fill loop walks past the end of `grids`
            # (IndexError). Drop the now-mismatched drawable cache instead (leaving
            # _selected_attr alone -- the user's selection has not changed, only its
            # cached geometry has) -- panel.py's _update_grid() calls _on_attr_selected
            # right after this, which re-applies set_sharing() from the CURRENT
            # selection's fresh attr object if one is still selected, so this is not
            # visible in the normal refresh flow.
            if self._sharing is not None and decoded is not None \
                    and len(self._sharing[1]) != decoded.num_pages:
                self._sharing = None
                self._sharing_lut_cache = None
                self._sharing_dimmed_storage_lut_cache = None
                self._sharing_ink_cache = None
            self._update_scrollbar()
            self.viewport().update()
            return
        self._bar_attrs = list(bar_attrs)
        self._page_storage_arrays = [decoded.attr_page_storage(s, n)
                                     for (_o, s, n) in self._bar_attrs] if decoded else []
        self._emphasis = None              # bar set changed; panel re-applies after
        self._update_scrollbar()
        self.viewport().update()

    def set_attr_colors(self, colors):
        """{(owner, scope, name): uint32} -- the bar colour of every attribute in the
        report, from _attr_color_map. Held rather than derived here so one attribute has
        one colour wherever it is drawn."""
        self._attr_colors = dict(colors)
        self.viewport().update()

    def bar_attrs(self):
        """The (owner, scope, name) key of each bar shown, top to bottom."""
        return list(self._bar_attrs)

    def decoded(self):
        return self._decoded

    def mode(self):
        return self._mode

    def emphasis(self):
        """Index into bar_attrs() of the emphasized bar, or None."""
        return self._emphasis

    def set_emphasis(self, key):
        """Highlight one attribute bar (dim the rest); None clears."""
        idx = None
        if key is not None:
            try:
                idx = self._bar_attrs.index(tuple(key))
            except ValueError:
                idx = None
        if idx != self._emphasis:
            self._emphasis = idx
            self.viewport().update()

    def set_sharing(self, attr):
        """The selected attribute, for "sharing" mode -- or None when nothing is selected."""
        if self._mode != "sharing":
            return
        # Kept even when there is nothing to draw: which of the three empty states applies
        # depends on the attribute, not on the absence of sharing data.
        self._selected_attr = attr
        self._sharing = None
        self._sharing_lut_cache = None
        self._sharing_dimmed_storage_lut_cache = None
        self._sharing_ink_cache = None
        self._block_members_cache = (0, None)
        # has_memory_block_sharing is the whole test. A dict or string attribute shares
        # its VALUE TABLE while every page holds its own handles, and it is what separates
        # that from real page sharing; an attribute that only reuses its own blocks has no
        # peer entry at all and still belongs in the mode.
        if attr is not None and attr.has_memory_block_sharing:
            self._sharing = (attr,
                             np.frombuffer(attr.memory_block_ids, np.uint32),
                             np.frombuffer(attr.shares_with_mapping_indices, np.uint32),
                             attr.shares_with_mapping)
            self._bar_attrs = list(attr.shares_with_attrib_keys)
            self._page_storage_arrays = []
        else:
            # Clear bar rows too — stale _bar_attrs + None _sharing crashes _hover_at.
            self._bar_attrs = []
            self._page_storage_arrays = []
        self._emphasis = None
        self._update_scrollbar()
        self.viewport().update()

    def sharing(self):
        """(attr, block_ids, mapping_indices, mapping) for "sharing" mode, or None.
        Exposed for tests."""
        return self._sharing

    def pinned_block(self):
        """The memory block being highlighted -- the one a click pinned -- or 0."""
        return self._pinned_block

    def _block_at(self, page):
        """Memory block id at `page` of the selected attribute, or 0."""
        if self._sharing is None or page is None:
            return 0
        block_ids = self._sharing[1]
        if not (0 <= page < len(block_ids)):
            return 0
        return int(block_ids[page])

    def _block_members(self, block):
        """Pages of the selected attribute on `block`, ascending. Cached."""
        if block == 0 or self._sharing is None:
            return np.zeros(0, np.int64)
        cached_block, cached = self._block_members_cache
        if cached is not None and cached_block == block:
            return cached
        members = np.flatnonzero(self._sharing[1] == block)
        self._block_members_cache = (block, members)
        return members

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
        return PAGE_DIM * self._cell_px

    def _attr_bar_height(self):
        return max(4, self._cell_px)

    def _gutter_width(self):
        return Style.Layout.LEFT_PAD

    def _bars_block_h(self):
        num_bars = len(self._bar_attrs)
        if num_bars == 0:
            return 0
        return Style.Layout.GRID_BAR_GAP + num_bars * self._attr_bar_height() + (num_bars - 1) * Style.Layout.BAR_GAP

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

    def _background_rgb(self):
        """The grid canvas colour from the current colour scheme."""
        pal = self.viewport().palette()
        return pal.color(self.viewport().backgroundRole()).getRgb()[:3]

    def _background_packed(self):
        """`_background_rgb()` packed for the uint32 band, cached per colour."""
        rgb = self._background_rgb()
        if rgb != self._background_cache_rgb:
            self._background_cache_rgb = rgb
            self._background_cache_packed = _u32(rgb + (255,))
            # Everything dimmed blends toward the background, so all four caches go.
            self._dimmed_storage_lut_cache = None
            self._sharing_dimmed_storage_lut_cache = None
            self._sharing_ink_cache = None
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
        painter.fillRect(self.viewport().rect(), QtGui.QColor(*self._background_rgb()))
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

        # One QImage per frame, borrowing the band buffer (NO copy) -- band stays
        # alive across drawImage, which reads it synchronously into the paint
        # device. This is the interactive hot path (up to a few thousand visible
        # tiles); do not build a copying QImage wrapper here. The band is
        # viewport-bounded, so this stays O(viewport), not O(total pages).
        band = np.full((band_h, cards_w), self._background_packed(), dtype=np.uint32)
        self._render_band(band, first_row, last_row, cols)
        qimg = QtGui.QImage(band.data, cards_w, band_h, 4 * cards_w,
                            QtGui.QImage.Format_RGBA8888)
        painter.drawImage(gutter_w, band_top - scroll_y, qimg)

    def _sharing_lut(self):
        """uint32 colour per memory block id (id 0 = muted grey)."""
        block_ids = self._sharing[1] if self._sharing else None
        size = (int(block_ids.max()) + 1) if (block_ids is not None and len(block_ids)) else 1
        if self._sharing_lut_cache is not None and len(self._sharing_lut_cache) == size:
            return self._sharing_lut_cache
        lut = np.empty(size, dtype=np.uint32)
        lut[0] = Style.Color.SHARING_NONE32
        if size > 1:
            lut[1:] = _block_palette(size - 1, Style.Color.SHARING_SEED)
        self._sharing_lut_cache = lut
        self._sharing_dimmed_storage_lut_cache = None
        self._sharing_ink_cache = None
        return lut

    def _sharing_dimmed_storage_lut(self):
        """`_sharing_lut` blended toward the canvas for un-highlighted tiles."""
        lut = self._sharing_lut()
        self._background_packed()                      # refreshes the cache / invalidates this one
        if self._sharing_dimmed_storage_lut_cache is None:
            self._sharing_dimmed_storage_lut_cache = _blend_packed32(
                lut, self._background_cache_rgb + (255,), Style.Color.DIM_BLEND)
        return self._sharing_dimmed_storage_lut_cache

    def _sharing_ink(self):
        """``(lit, dim)`` label ink per block id, aligned with `_sharing_lut`."""
        lut = self._sharing_lut()
        if self._sharing_ink_cache is None:
            lit = _label_ink(lut)
            dim = _blend_lut32(
                np.concatenate([lit, np.full((len(lit), 1), 255, np.uint8)], axis=1),
                self._background_cache_rgb + (255,), Style.Color.DIM_BLEND)
            dim = dim.view(np.uint8).reshape(-1, 4)[:, :3]
            self._sharing_ink_cache = (lit, dim)
        return self._sharing_ink_cache

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

    def _draw_block_labels(self, grids, block_ids, pinned):
        """Stamp each tile with its block id in base 36, in place.

        Vectorised: glyphs are laid out one PLACE at a time across all pages, so the
        Python cost is the label length, not the page count."""
        grid_px = grids.shape[1]
        base = len(_BASE36)
        lut = self._sharing_lut()
        # The widest id decides the size for ALL of them: a label that shrank as the id
        # grew would read as a difference between blocks rather than a difference in
        # characters.
        chars = len(_base36(max(1, len(lut) - 1)))
        font_px = _label_font_px(grid_px, chars)
        if font_px == 0:                    # tile too small for a readable label
            return
        atlas, cell_w, glyph_h, middle, advances = _glyph_atlas(font_px)
        lit_ink, dim_ink = self._sharing_ink()

        # Block 0 is "no other page reaches this block" -- the grey tile. It is not a
        # block anyone can look up, and labelling it would invite the reader to.
        ids = np.clip(block_ids, 0, len(lit_ink) - 1)
        off_block = (block_ids != pinned) if pinned else np.zeros(len(ids), bool)
        ink = np.where(off_block[:, None], dim_ink[ids], lit_ink[ids]).astype(np.int16)

        view = grids.view(np.uint8).reshape(len(grids), grid_px, grid_px, 4)
        # `middle` is where the cap band sits inside the box, so this puts the cap band --
        # not the box -- on the tile's centre line.
        top = max(0, min(grid_px - glyph_h, int(round(grid_px / 2.0 - middle))))
        lengths = np.zeros(len(ids), np.int64)
        labelled = block_ids > 0
        lengths[labelled] = (
            np.floor(np.log(block_ids[labelled]) / np.log(base)).astype(np.int64) + 1)
        for length in np.unique(lengths[labelled]):
            pages = np.flatnonzero(lengths == length)
            symbols = np.stack([(block_ids[pages] // (base ** (length - 1 - place))) % base
                                for place in range(length)], axis=1)
            # Where each glyph starts, from its own advance, with the whole run centred in
            # a strip wide enough for the worst case. Two cumulative sums, not a loop over
            # pages: the label is laid out for every tile in the band at once.
            widths = advances[symbols]
            starts = np.cumsum(widths, axis=1) - widths
            strip_w = length * cell_w
            starts = starts + ((strip_w - widths.sum(axis=1)) // 2)[:, None]

            # One gather builds the whole strip: for every output column, WHICH symbol
            # covers it and which column of that symbol's cell to read. The loop is over
            # glyph PLACES (at most three), never over pages.
            column = np.arange(strip_w)[None, :]
            symbol_at = np.zeros((len(pages), strip_w), np.int64)
            cell_at = np.zeros((len(pages), strip_w), np.int64)
            covered = np.zeros((len(pages), strip_w), bool)
            for place in range(length):
                start = starts[:, place:place + 1]
                width = widths[:, place:place + 1]
                inside = (column >= start) & (column < start + width)
                symbol_at = np.where(inside, symbols[:, place:place + 1], symbol_at)
                cell_at = np.where(inside, column - start + (cell_w - width) // 2, cell_at)
                covered |= inside
            coverage = np.where(covered[:, None, :],
                                atlas[symbol_at, :, cell_at].transpose(0, 2, 1), 0)

            left = (grid_px - strip_w) // 2
            under = view[pages, top:top + glyph_h, left:left + strip_w, :3].astype(np.int16)
            view[pages, top:top + glyph_h, left:left + strip_w, :3] = (
                under + ((ink[pages][:, None, None, :] - under)
                         * coverage[..., None].astype(np.int16)) // 255
            ).astype(np.uint8)

    # Attribute types whose pages hold HANDLES into a value table rather than the values
    # themselves. AttribCopy allocates a fresh handle array while sharing the table, so
    # these share real memory with no page geometry to draw for it.
    _VALUE_TABLE_TYPES = ("string", "dict")

    def _sharing_empty_text(self):
        """Diagnostic string for the empty sharing view."""
        attr = self._selected_attr
        if attr is None:
            return "Select an attribute"
        if not attr.has_page_details:
            return f"Page info is unavailable for {attr.name} ({attr.type_name})"
        if not attr.shares_with_attrib_keys:
            # Only reachable when the attribute does not reuse its own blocks either:
            # set_sharing() enters the mode on has_memory_block_sharing alone, so an
            # attribute whose two pages sit on one allocation is drawn rather than
            # told it shares nothing.
            return f"{attr.name} shares no memory with another attribute"
        if attr.type_name in self._VALUE_TABLE_TYPES:
            return (f"{attr.name} shares "
                    f"{human_bytes(attr.intra_detail_memory_sharing)} in its "
                    f"value table, not its pages")
        return (f"{attr.name} shares "
                f"{human_bytes(attr.intra_detail_memory_sharing)}, but which "
                f"pages could not be determined")

    def _render_band(self, band, first_row, last_row, cols):
        """Composite the visible cards into the uint32 band image (numpy only)."""
        n = self._total_page_count()
        cell = self._cell_px
        grid_px = self._grid_px()
        bar_h = self._attr_bar_height()
        stride = self._row_stride()
        band_top = first_row * stride

        p0 = first_row * cols
        p1 = min((last_row + 1) * cols, n)

        # Card tops: 32x32 uint32 cells upscaled by cell px (only the visible
        # pages, so the work is bounded by the viewport, not the total map).
        if self._mode == "sharing":
            block_ids = self._sharing[1][p0:p1].astype(np.int64)
            lut = self._sharing_lut()
            ids = np.clip(block_ids, 0, len(lut) - 1)
            active = self._pinned_block
            if active:
                flat = np.where(block_ids == active, lut[ids], self._sharing_dimmed_storage_lut()[ids])
            else:
                flat = lut[ids]
            grids = np.repeat(flat, PAGE_DIM * PAGE_DIM).reshape(-1, PAGE_DIM, PAGE_DIM)
            label_ids = block_ids
        elif self._mode == "block":
            grids = self._decoded.block_colors_u32(p0, p1).reshape(-1, PAGE_DIM, PAGE_DIM)
            label_ids = None
        else:
            states = self._decoded.occupancy_states(p0, p1)
            grids = Style.Color.OCC_LUT32[states.reshape(-1, PAGE_DIM, PAGE_DIM)]
            label_ids = None
        if cell > 1:
            grids = np.repeat(np.repeat(grids, cell, axis=1), cell, axis=2)

        # Cell separators at higher zoom. Not in sharing mode — the whole page
        # is one colour, so the 32x32 lattice would divide identical slots.
        if label_ids is None and cell >= Style.Layout.CELL_GRIDLINE_MIN:
            edge_px = 2 if cell >= Style.Layout.CELL_GRIDLINE_THICK else 1
            edge = (np.arange(grid_px) % cell) < edge_px
            grids[:, edge, :] = Style.Color.GRIDLINE32
            grids[:, :, edge] = Style.Color.GRIDLINE32

        if label_ids is not None:
            self._draw_pinned_outline(grids, label_ids, self._pinned_block)
            self._draw_block_labels(grids, label_ids, self._pinned_block)

        for page in range(p0, p1):
            row = page // cols
            col = page % cols
            x = col * (grid_px + Style.Layout.H_GAP)
            y = row * stride - band_top
            band[y:y + grid_px, x:x + grid_px] = grids[page - p0]
            # Attribute class bars beneath the grid (solid uint32 fills), in the
            # same order as the toggled attributes appear in the memory report. When
            # a bar is emphasized (linked highlight), the others use the dim LUT.
            bar_y = y + grid_px + Style.Layout.GRID_BAR_GAP
            emphasized = self._emphasis
            if self._mode == "sharing":
                # One bar row per PEER, filled only where that peer is on this page's
                # block, in that attribute's own colour. A page two attributes hold shows
                # two bars in two colours, which is the whole point of the mode.
                _attr, block_ids, mapping_indices, mapping = self._sharing
                entry = int(mapping_indices[page])
                members = mapping[entry] if entry < len(mapping) else []
                # A page not on the highlighted block is dimmed whole -- bars included, or
                # a dimmed tile would sit under bars as loud as the lit ones. `_emphasis`
                # plays no part here: the bars are the SELECTED attribute's peers, so the
                off_block = bool(active) and int(block_ids[page]) != active
                for bar_idx, key in enumerate(self._bar_attrs):
                    if bar_idx not in members:
                        band[bar_y:bar_y + bar_h, x:x + grid_px] = self._background_packed()
                        bar_y += bar_h + Style.Layout.BAR_GAP
                        continue
                    color = self._attr_colors.get(key, Style.Color.SHARING_NONE32)
                    if off_block:
                        color = self._dimmed_packed(color)
                    band[bar_y:bar_y + bar_h, x:x + grid_px] = color
                    bar_y += bar_h + Style.Layout.BAR_GAP
            else:
                for bar_idx, codes in enumerate(self._page_storage_arrays):
                    code = codes[page]
                    lut = (Style.Color.PAGE_STORAGE_LUT32
                           if (emphasized is None or bar_idx == emphasized)
                           else self._dimmed_storage_lut())
                    band[bar_y:bar_y + bar_h, x:x + grid_px] = \
                        lut[code] if code != PS_NONE else self._background_packed()
                    bar_y += bar_h + Style.Layout.BAR_GAP

    # -- interaction --------------------------------------------------------

    _PAGE_STORAGE_LABEL = {PS_CONSTANT: "constant", PS_SHARED: "shared",
                           PS_HARDENED: "hardened", PS_UNKNOWN: "unknown (no hardness API)",
                           PS_CONSTANT_SHARED: "constant, shared"}

    def _page_offsets(self, page):
        """``[first - last]`` element offset range for `page`."""
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, self._decoded.offset_size) - 1
        return f"[{start} - {max(start, end)}]"

    # How many block-mates the hover names before it gives up and counts them: a 256-way
    # self-merge puts every page of the source on one block, and the status line is one
    # line.
    _MAX_NAMED_BLOCK_MATES = 6

    def _block_mates_text(self, page):
        """Sharing info for the hovered page, or ""."""
        if self._sharing is None:
            return ""
        block = self._block_at(page)
        if block == 0:
            return "on a block no other page uses"
        label = _base36(block)
        others = [int(p) for p in self._block_members(block) if int(p) != page]
        if not others:
            return f"block {label}"
        named = ", ".join(str(p) for p in others[:self._MAX_NAMED_BLOCK_MATES])
        if len(others) > self._MAX_NAMED_BLOCK_MATES:
            named += f", +{len(others) - self._MAX_NAMED_BLOCK_MATES} more"
        return f"block {label}, also on pages {named}"

    def _hover_at(self, pos):
        """``(info_str, bar_idx, page)`` for the cursor position."""
        decoded = self._decoded
        if decoded is None or decoded.num_pages == 0:
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
            info = f"{decoded.owner} page {page} {self._page_offsets(page)}"
            if self._mode == "sharing":
                # The occupancy counts belong to the mode that draws them. Here the card
                # answers WHICH BLOCK, and three slot counts alongside it would crowd out
                # the answer the user switched modes to get.
                mates = self._block_mates_text(page)
                if mates:
                    info += f"   {mates}"
            else:
                info += (f"   active {int(decoded.num_active[page])}   "
                         f"temporary {int(decoded.num_temp[page])}   "
                         f"vacant {int(decoded.num_vacant[page])}")
            return info, None, page
        # Below the grid: which attribute bar (if any) is under the cursor.
        bar_offset = local_y - grid_px - Style.Layout.GRID_BAR_GAP
        step = self._attr_bar_height() + Style.Layout.BAR_GAP
        bar_idx = bar_offset // step
        if bar_offset < 0 or bar_idx >= len(self._bar_attrs) or (bar_offset % step) >= self._attr_bar_height():
            return "", None, None
        owner, scope, name = self._bar_attrs[bar_idx]
        offsets = self._page_offsets(page)
        # Guard on the DATA, not just the mode: _bar_attrs and _sharing are set together,
        # but a future path that clears one without the other must not crash the hover.
        if self._mode == "sharing":
            if self._sharing is None:
                return "", None, None
            attr, _block_ids, mapping_indices, mapping = self._sharing
            entry = int(mapping_indices[page])
            members = mapping[entry] if entry < len(mapping) else []
            state = ("shares this page's block" if bar_idx in members
                     else "is not on this page's block")
            # The peer's OWNER is named: it need not be the grid's, and "id / class" with
            # no owner would read as two point attributes.
            return (f"{attr.name} / {name} [{owner} {scope}]   page {page} {offsets}   "
                    f"{state}", int(bar_idx), page)
        code = int(self._page_storage_arrays[bar_idx][page])
        label = self._PAGE_STORAGE_LABEL.get(code, "n/a")
        return f"{name} [{scope}]   page {page} {offsets}   {label}", int(bar_idx), page

    def mouseMoveEvent(self, event):
        # Reads the grid, never repaints it. The block highlight is pinned by a CLICK, so
        # the whole grid does not re-render under a moving cursor.
        info, idx, _page = self._hover_at(event.position().toPoint())
        self.hoverInfo.emit(info)
        if idx is not None:
            self.barHovered.emit(*self._bar_attrs[idx])
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

