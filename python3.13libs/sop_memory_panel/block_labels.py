import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .style import Style, _blend_lut32, _unpack_rgba32


# sRGB -> Oklab L only: the label ink is chosen on lightness alone. Ottosson's matrix,
# verified against coloraide in tests/test_page_panel.py.
_LINEAR_RGB_TO_LMS = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005]])
_LMS_TO_OKLAB_L = np.array([0.2104542553, 0.7936177850, -0.0040720468])


def _oklab_lightness(colors):
    """(N,) Oklab L in [0, 1] for packed uint32 RGBA colours."""
    srgb = _unpack_rgba32(colors)[:, :3] / 255.0
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
            # Not an error worth reporting: the labels still draw, in the application font.
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
        # Format_ARGB32 is BGRA on a little-endian host, so alpha is byte 3. Reshape on
        # bytesPerLine, not width * 4: Qt pads scanlines to a 4-byte boundary.
        buffer = np.frombuffer(image.constBits(), np.uint8)
        atlas[index] = buffer.reshape(height, image.bytesPerLine() // 4, 4)[:, :width, 3]
    advances = np.array([metrics.horizontalAdvance(c) for c in _BASE36], np.int64)
    _GLYPH_ATLAS_CACHE[key] = (atlas, width, height, middle, advances)
    return _GLYPH_ATLAS_CACHE[key]


def _label_inks(lut, background_rgb):
    """``(lit, dim)`` label ink per block id, aligned with `lut`."""
    lit = _label_ink(lut)
    dim = _blend_lut32(
        np.concatenate([lit, np.full((len(lit), 1), 255, np.uint8)], axis=1),
        background_rgb + (255,), Style.Color.DIM_BLEND)
    dim = dim.view(np.uint8).reshape(-1, 4)[:, :3]
    return lit, dim


def _draw_block_labels(grids, block_ids, pinned, lut, lit_ink, dim_ink):
    """Stamp each tile with its block id in base 36, in place.

    Vectorised: glyphs are laid out one PLACE at a time across all pages, so the
    Python cost is the label length, not the page count."""
    grid_px = grids.shape[1]
    base = len(_BASE36)
    # The widest id sets one size for every label.
    num_label_chars = len(_base36(max(1, len(lut) - 1)))
    font_px = _label_font_px(grid_px, num_label_chars)
    if font_px == 0:                    # tile too small for a readable label
        return
    atlas, cell_w, glyph_h, middle, advances = _glyph_atlas(font_px)

    # Block 0 is "no other page reaches this block" -- the grey tile. It is not a
    # block anyone can look up, and labelling it would invite the reader to.
    ids = np.clip(block_ids, 0, len(lit_ink) - 1)
    is_off_pinned_block = (block_ids != pinned) if pinned else np.zeros(len(ids), bool)
    ink = np.where(is_off_pinned_block[:, None], dim_ink[ids], lit_ink[ids]).astype(np.int16)

    view = grids.view(np.uint8).reshape(len(grids), grid_px, grid_px, 4)
    # `middle` is where the cap band sits inside the box, so this puts the cap band --
    # not the box -- on the tile's centre line.
    top = max(0, min(grid_px - glyph_h, int(round(grid_px / 2.0 - middle))))
    label_lengths = np.zeros(len(ids), np.int64)
    is_labelled = block_ids > 0
    label_lengths[is_labelled] = (
        np.floor(np.log(block_ids[is_labelled]) / np.log(base)).astype(np.int64) + 1)
    for length in np.unique(label_lengths[is_labelled]):
        pages = np.flatnonzero(label_lengths == length)
        symbols = np.stack([(block_ids[pages] // (base ** (length - 1 - place))) % base
                            for place in range(length)], axis=1)
        # Each glyph starts at its own advance, the run centred in a strip wide enough for
        # the worst case. Cumulative sums lay out every tile in the band at once.
        widths = advances[symbols]
        starts = np.cumsum(widths, axis=1) - widths
        strip_w = length * cell_w
        starts = starts + ((strip_w - widths.sum(axis=1)) // 2)[:, None]

        # One gather builds the strip: which symbol covers each output column, and which of
        # its cell's columns to read. The loop is over glyph places (at most three).
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
