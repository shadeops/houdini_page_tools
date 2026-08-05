"""SOP Memory Python Panel.

Visualises the per-attribute memory and per-page IndexMap occupancy reported by
the ``_page_tools`` HDK extension (``src/_page_tools.C``; the report dict schema is
documented in ``SOP_Memory_Report.md``) for the currently selected SOP node.

Two collapsible sections:

* Upper -- per-attribute memory grouped by owner, each attribute's contribution
  to total geometry memory as a percentage, with a per-attribute toggle that
  drives whether its page-storage bar is drawn in the lower section.
* Lower -- a wrapped grid of "page cards" for one owner. Each card is a 32x32
  occupancy grid (Occupancy mode) or contiguous-block colouring (Continuous
  block mode), with a stack of per-attribute constant/shared/hardened bars
  below it (one per toggled attribute).

Rendering is virtualised: only the pages visible in the scroll viewport are ever
rasterised, so the panel stays responsive from 1 to >1,000,000 pages. The decode
and pixel-building helpers at module scope are pure numpy/QImage and are unit
tested headlessly (see tests/test_page_panel.py).
"""

import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import hou

import page_tools
from . import diff as pgdiff
from .model import GROUP_TABLES, OWNERS, SCOPES, MemoryModel, row_key  # noqa: F401 re-export

# Checkbox label -> the scope it filters, for the labels that differ. "primlist" is
# shortened to keep the Scopes row narrow; "groups" reads better in the plural.
_SCOPE_FOR_LABEL = {"groups": "group", "primlist": "primitive_list"}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 1024
PAGE_DIM = 32                       # 32 x 32 == PAGE_SIZE

# SOP recook drivers -- there is no dedicated "cooked" event (mirrors the viewer
# state in viewer_states/sop_page_viewer.py).
# Selection inside a network, including to empty -- what tells the panel a node was
# deselected. See the deselection block in SopMemoryPanel.
SELECTION_EVENTS = (hou.nodeEventType.ChildSelectionChanged,)

COOK_EVENTS = (
    hou.nodeEventType.ParmTupleChanged,
    hou.nodeEventType.InputDataChanged,
    hou.nodeEventType.InputRewired,
    hou.nodeEventType.BeingDeleted,
)

_UNSET = object()               # "no node change was deferred while paused" sentinel

# Qt's QWIDGETSIZE_MAX -- the "no maximum" width. PySide6 does not re-export the constant,
# so it is spelled out here; it is what releases a setFixedWidth().
_WIDGET_SIZE_MAX = 16777215

# Occupancy state codes (index into Style.Color.OCC_LUT). Semantic, not styling:
# which element SLOTS are used.
ST_VACANT, ST_ACTIVE, ST_TEMP, ST_OOR = 0, 1, 2, 3

# Attribute PAGE STORAGE codes (index into Style.Color.PAGE_STORAGE_LUT). Distinct
# from index-map OCCUPANCY (ST_* above): page storage is how the attribute's page
# DATA is held.
# PS_UNKNOWN: the attribute has pages but no hardness API (element groups, the
# paged <primitive list>), so this page is non-constant and nothing more is known.
# PS_CONSTANT_SHARED: a constant page whose single stored value is refcounted, so
# another page array holds the same block. It is its own class because it is neither
# what "constant" means here (a page nobody else holds) nor what "shared" means (a
# full page of data held in common).
PS_CONSTANT, PS_SHARED, PS_HARDENED, PS_UNKNOWN, PS_CONSTANT_SHARED, PS_NONE = \
    0, 1, 2, 3, 4, 255



def _u32(color):
    """Pack an (R, G, B, A) tuple as a single RGBA8888 uint32 (little-endian)."""
    return int(np.array(color, np.uint8).view(np.uint32)[0])


def _rgb_css(color):
    return f"rgb({color[0]},{color[1]},{color[2]})"


def _blend_lut32(lut, bg, t):
    """`lut` (N,4) blended t of the way toward bg (an RGBA tuple), packed to uint32.
    Alpha stays opaque."""
    out = (lut.astype(np.float64) * (1.0 - t)
           + np.array(bg, np.float64) * t).astype(np.uint8)
    out[:, 3] = 255
    return out.view(np.uint32).reshape(-1).copy()


def _blend_packed32(colors, bg, t):
    """The same blend for colours that are already packed uint32.

    _blend_lut32's input is an (N, 4) uint8 table, which is how the hand-written LUTs are
    written; the generated palettes (`_block_palette`) come out packed, so they unpack
    here rather than each caller doing it differently."""
    rgba = np.ascontiguousarray(np.atleast_1d(colors), np.uint32).view(np.uint8).reshape(-1, 4)
    return _blend_lut32(rgba, bg, t)


# ---------------------------------------------------------------------------
# Style -- every "look and feel" value in one namespace
# ---------------------------------------------------------------------------

class Style:
    """Central namespace for the panel's look and feel."""

    class Color:
        # Every colour is an int tuple (never a hex string): 4-tuple RGBA for values
        # packed into the uint32 paint band or a numpy LUT (the alpha byte matters
        # there), 3-tuple RGB for text / chrome colours. Stylesheets take a CSS string,
        # so those are wrapped with _rgb_css() at the point of use.

        # Index-map occupancy (index == ST_* code). Muted / low-saturation so the
        # grid reads without harsh contrast.
        OCC_LUT = np.array(
            [
                [200, 200, 200, 255],   # vacant       -> light gray
                [110, 175, 110, 255],   # active       -> muted green
                [110, 140, 205, 255],   # temporary    -> muted blue
                [ 28,  28,  28, 255],   # out-of-range -> near-black
            ],
            dtype=np.uint8,
        )
        # Attribute page storage (index == PS_* code). Also muted.
        PAGE_STORAGE_LUT = np.array(
            [
                [200, 195, 120, 255],   # constant -> muted yellow
                [200, 130, 185, 255],   # shared   -> muted magenta
                [120, 195, 195, 255],   # hardened -> muted cyan
                [130, 130, 130, 255],   # unknown  -> neutral gray (deliberately not a
                                        #   hue: it is an absence of data, not a state)
                [220, 150,  80, 255],   # constant AND shared -> muted orange, between the
                                        #   constant yellow and the shared magenta
            ],
            dtype=np.uint8,
        )

        # The grid canvas -- behind the cards, and under a card where an attribute has no
        # bar -- is NOT here: PageGridWidget takes it from the palette so it follows the
        # colour scheme (see _bg_rgb). These two are marks drawn ON that canvas.
        BLOCK_BG = (78, 78, 78, 255)    # non-block cells in Continuous block mode
        GRIDLINE = (45, 45, 45, 255)    # cell separators, drawn only at high zoom

        # uint32 (RGBA8888) variants of the lookups. Compositing the band as a single
        # uint32 image (rather than an (h, w, 4) uint8 array) makes the colour gather
        # and tile placement move a quarter of the bytes.
        OCC_LUT32 = OCC_LUT.view(np.uint32).reshape(-1).copy()
        PAGE_STORAGE_LUT32 = PAGE_STORAGE_LUT.view(np.uint32).reshape(-1).copy()
        BLOCK_BG32 = _u32(BLOCK_BG)
        # "Memory block sharing" mode. Block id 0 is "no other page in the detail
        # reaches this page's block". It reuses the muted grey of the unknown page class,
        # which no longer carries a second meaning here: constant pages are measured now,
        # so a grey card in this mode says nobody else holds the page, not that the answer
        # could not be had.
        SHARING_NONE = (86, 86, 86, 255)
        SHARING_SEED = 0x7C41
        # The bars in that mode are one colour per ATTRIBUTE, indexed by the model's
        # detail-wide attribute order, so a bar keeps its colour across selections and
        # owners. A separate seed from SHARING_SEED: the two palettes sit on the same card
        # and must not hand out the same colour to a block and to an attribute.
        SHARING_ATTR_SEED = 0x1E33

        GRIDLINE32 = _u32(GRIDLINE)
        SHARING_NONE32 = _u32(SHARING_NONE)

        # Blend for the "other" bars when one is emphasized (linked highlight): each class
        # colour goes most of the way toward the canvas so the selected bar stands out.
        # The LUT itself is built per canvas colour in PageGridWidget._dim_lut, since the
        # canvas moves with the theme. Empty (PS_NONE) cells are already canvas.
        DIM_BLEND = 0.62

        # The generated palettes (continuous-block runs, memory blocks, per-attribute bar
        # colours) all sit on one ring in Oklch: constant lightness, constant chroma, hue
        # varying. See _block_palette.
        #
        # L is where the ring can be widest -- sRGB admits more chroma at 0.75 than either
        # side of it -- and C is 94% of the largest chroma that is in gamut at EVERY hue
        # there (0.1275), the margin left so rounding cannot push a hue out. Raising either
        # clips the blues first, which would flatten distinct hues onto one colour.
        BLOCK_OKLCH_L = 0.75
        BLOCK_OKLCH_C = 0.1199
        BLOCK_SEED = 0x50A6

        # Ink for the block-id label drawn on a sharing-mode tile. Two neutrals rather
        # than one: the ring is light today, but the moment a palette varies lightness a
        # single ink goes unreadable on one end of it. SWITCH_L is where the two are
        # equally far from the tile in Oklab L -- |L - 0.209| == |0.961 - L| -- so each
        # tile takes whichever ink its own lightness separates further from.
        LABEL_INK_DARK = (24, 24, 24)           # Oklab L 0.209
        LABEL_INK_LIGHT = (242, 242, 242)       # Oklab L 0.961
        LABEL_INK_SWITCH_L = 0.585

        # Attribute scope -> row text colour (low saturation / high value pastels, so
        # the scope reads clearly without harsh contrast). The scope filter checkboxes
        # are tinted to match. Scope is conveyed by colour instead of a "[scope]" suffix.
        SCOPE_COLORS = {
            "public":  (170, 200, 230),     # soft blue
            "private": (228, 205, 165),     # soft amber
            "group":   (205, 178, 222),     # soft violet
            "primitive_list": (150, 205, 190),  # soft teal -- the <primitive list> row
        }

        # Compare mode: a delta that GREW against one that SHRANK. A third meaning on the
        # scope hues or the page-storage hues would break both of those legends, so this is
        # its own pair -- and it is deliberately a hue pair rather than a lightness ramp,
        # since the two directions are opposite rather than ordered.
        #
        # Colour is never the only channel. Every delta is drawn with an explicit + or -,
        # so the sign survives a colour-blind reader, a greyscale print and a theme that
        # washes the hues out.
        DELTA_UP = (216, 122, 96)       # grew   -- warm
        DELTA_DOWN = (122, 190, 140)    # shrank -- cool
        DELTA_ZERO = (140, 140, 140)    # measured zero, dimmed so it recedes
        # Unknown, NOT a fourth direction: the same neutral grey the page legend uses for
        # an absence of data, for the same reason. It must not read as a small change.
        DELTA_UNKNOWN = (130, 130, 130)
        # There is deliberately NO row colour for added / removed / replaced. A pinned row
        # BACKGROUND is the thing this panel does not do (see the theming note in the spec),
        # the three states already read from the name's font, and a second row-background
        # meaning alongside the peer wash muddies both. The delta colours below are on TEXT
        # and carry their own sign, which is a different proposition.

        # Percentage-bar delegate (QPainter fills -- not stylesheet-able).
        PCT_TRACK = (55, 55, 55)
        PCT_FILL = (70, 130, 200)
        PCT_TEXT = (230, 230, 230)

        # Attribute row text when the scope carries no colour.
        ATTR_DEFAULT_FG = (220, 220, 220)
        # "No pages" placeholder text in the grid.
        NO_PAGES_FG = (180, 180, 180)

        # The few colours that are still ours. Backgrounds and body text are NOT here any
        # more: the tree, the status line, the legend labels and the separators take
        # whatever the current Houdini colour scheme paints them.
        INSTANCED_FG = (224, 192, 96)   # amber note -- a warning, not chrome
        HAIRLINE = (85, 85, 85)         # border around a legend swatch we paint ourselves
        # Pause pill, used only when a build publishes no pluto roles to read instead.
        PAUSE_ON = (200, 129, 60)       # fill while frozen  (theme: checkedSurface)
        PAUSE_ON_FG = (32, 24, 12)      # label + border     (theme: checkedSurfaceFg)
        PAUSE_OFF = (86, 86, 86)        # fill at rest       (theme: button)
        PAUSE_OFF_FG = (220, 220, 220)  # label at rest      (theme: buttonFg)
        HOVER_BORDER = (100, 170, 240)  # border on the report row a hovered page bar maps to
        # Wash over rows sharing storage with the SELECTED row. A soft lift rather than a
        # border: an outline on every peer is loud, and the relationship is "these rows go
        # with the selected one", which a background says more quietly than a frame.
        #
        # The colour is the THEME'S ACCENT, not ours -- see Style.peer_wash. This literal is
        # only the fallback for a UI that publishes no theme (H21, H22's old UI), and it is
        # roughly what that accent looks like on the default scheme.
        #
        # Translucent because it is painted OVER the finished row, not under it -- a style
        # is free to fill each item's panel opaquely (PE_PanelItemViewItem), and H22's new
        # UI does, which swallowed an under-painted fill without a trace. Alpha is the
        # trade: the row's text takes a slight tint, which is the price of a highlight that
        # cannot be painted over.
        PEER_BG = (110, 150, 220, 72)
        PEER_ALPHA = 72

    class Font:
        TITLE_PT = 17                   # panel header
        SECTION_PT = 13                 # collapsible section toggle

    class Layout:
        # PageGridWidget spacing (px).
        H_GAP = 10                      # between cards horizontally
        V_GAP = 14                      # between card rows
        GRID_BAR_GAP = 4                # grid bottom -> first attribute bar
        BAR_GAP = 6                     # between attribute bars
        LEFT_PAD = 2                    # small left margin

        DEFAULT_CELL_PX = 3
        CELL_GRIDLINE_MIN = 4           # draw cell separators at/above this zoom
        CELL_GRIDLINE_THICK = 8         # 2px separators at/above this zoom (else 1px)
        RES_OPTIONS = (1, 2, 3, 4, 6, 8, 10)

        # Block-id labels on the sharing-mode tiles (see _glyph_atlas). PAD is the
        # clearance kept inside the tile on every side, so the widest id never touches
        # the edge. MIN_PX is the smallest Lato pixel size the labels are drawn at --
        # below it the glyphs stop being readable and the tile is better left plain than
        # speckled. MAX_FRACTION caps the size against the tile so a 320px tile gets a
        # legible number rather than a poster.
        LABEL_PAD = 2
        LABEL_MIN_PX = 8
        LABEL_MAX_FRACTION = 0.42

        # Ring width on a pinned tile: thin, and thin at every zoom. It marks a tile, it
        # does not decorate one, and a ring that grew with the tile would start eating the
        # colour it is pointing at.
        PIN_OUTLINE_MAX_PX = 3
        PIN_OUTLINE_PER_PX = 48         # one ring pixel per this much tile

        SWATCH = 14                     # legend swatch square (px)
        COL_PAD = 20                    # extra width added to each fitted column
        PCT_COL_MIN = 72                # minimum width of the % bar column

        SPLITTER_OPEN = (260, 520)      # restored sizes when both sections are open
        PANEL_MARGIN = 4                # root layout contents margin
        LEGEND_MARGINS = (8, 2, 2, 2)   # legend row contents margins

    # -- Widget chrome ----------------------------------------------------------
    #
    # Colours the theme owns are NOT set here. Houdini's UI ships colour schemes -- two in
    # H21, 52 in H22's new UI -- and a panel that pins a background or a text colour opts
    # out of all of them. Its own panels do not pin: across the 32 non-deprecated shipped
    # Python Panels there are 8 colour-bearing setStyleSheet calls in total, and most
    # setStyleSheet calls simply re-apply hou.qt.styleSheet() to a popup. The Viewer Handle
    # Browser sets no colour at all beyond its message brushes.
    #
    # So this sheet carries typography and one semantic colour, and nothing else. What we
    # paint ourselves -- the page grid, the bars, the percent column, the peer wash -- keeps
    # fixed values below, the same way that panel keeps fixed error/warning brushes: those
    # pixels encode meaning and are keyed to the legend.
    # `transparent` is not a colour of ours -- it is what makes the tree sit ON the panel
    # instead of on a slab. Houdini's own application stylesheet carries
    # `QAbstractItemView {{ background: rgb(@ListEntry2@) }}` (H22 base.qss line 41), a
    # LEGACY .hcs colour that does not move with the new UI theme, and it fills everything
    # below the last row with it. Measured: a viewport backgroundRole of Window is not
    # enough, the app rule wins (67,67,67); with this rule the panel shows through (45,45,45).
    # The peer wash is the one colour that has to be asked for rather than inherited: it is
    # painted by hand, so no style will supply it, and it must not clash with whatever the
    # scheme uses for selection. H22's new UI publishes its palette as QApplication
    # properties (`pluto_<role>`; the roles are hutil.qt.pluto.colors.Role). `primary` is
    # the theme's ACCENT -- it is one of the three HSV values each of the 52 schemes is
    # generated from, so it moves with the scheme -- while `highlight` is what selection
    # uses, which is exactly what this must not look like.
    PEER_ROLE = "pluto_primary"

    # The ring around the pinned block's tiles. The SAME accent as the peer wash, on
    # purpose: the panel marks "this is the thing you asked about" in one colour, and a
    # second accent would read as a second kind of answer. Never `highlight` here either --
    # a pin is not a selection, and a page grid that borrowed the selection colour would
    # claim to be one.
    PIN_OUTLINE_ROLE = PEER_ROLE

    # The percentage column is a meter, and Houdini draws its sliders from exactly two
    # roles -- `field` for the groove, `primary` for the filled part
    # (hutil/qt/pluto/parts.py, drawSliderGroove). Borrowing the pair puts the column in
    # the same visual language as every slider in the UI, for free, on all 52 schemes. The
    # matching foregrounds keep the text legible over each half.
    PCT_TRACK_ROLE = "pluto_field"
    PCT_FILL_ROLE = "pluto_primary"
    PCT_TRACK_FG_ROLE = "pluto_fieldFg"
    PCT_FILL_FG_ROLE = "pluto_primaryFg"

    @staticmethod
    def role(name, fallback):
        """A colour from Houdini's live UI theme as (r, g, b), or `fallback`.

        The new UI publishes its palette as QApplication properties (`pluto_<role>`; the
        full list is hutil.qt.pluto.colors.Role). Older UIs publish nothing, so every
        lookup falls back to the literal that was in use before them."""
        app = QtWidgets.QApplication.instance()
        colour = app.property(name) if app is not None else None
        return fallback if colour is None else colour.getRgb()[:3]

    @classmethod
    def peer_wash(cls):
        """RGBA for the sharing-peer wash: the theme's accent, or the fallback literal."""
        return cls.role(cls.PEER_ROLE, cls.Color.PEER_BG[:3]) + (cls.Color.PEER_ALPHA,)

    PANEL_QSS = """
        QLabel#header {{ font-size: {title}px; font-weight: bold; }}
        QLabel#instanced {{ color: {instanced}; }}
        QToolButton#sectionToggle {{
            border: none; font-weight: bold; font-size: {section}px;
            color: palette(window-text);
        }}
        QTreeWidget#memReport {{ background: transparent; }}
    """.format(
        title=Font.TITLE_PT,
        section=Font.SECTION_PT,
        instanced=_rgb_css(Color.INSTANCED_FG),
    )

    # Pause copies Houdini's OWN pause toggle -- the one on the Node Info window's node
    # toolbar. That is `MiniToolButton` (`$HFS/qt/qml/houdini/ui/MiniToolButton.qml`), used
    # by `houdini/info/NodeToolbar.qml`: a fully rounded pill in BOTH states, filled
    # `button`/`buttonFg` at rest and `checkedSurface`/`checkedSurfaceFg` when ticked, with
    # a 2px border that is transparent at rest and `checkedSurfaceFg` when ticked. The
    # border is the thing that makes "on" unmistakable.
    PAUSE_OFF_BG_ROLE = "pluto_button"
    PAUSE_OFF_FG_ROLE = "pluto_buttonFg"
    PAUSE_ON_BG_ROLE = "pluto_checkedSurface"
    PAUSE_ON_FG_ROLE = "pluto_checkedSurfaceFg"
    PAUSE_BORDER_PX = 2

    # Houdini's own icon font, the one PhosphorIcon draws from -- `variant: "fill"` is
    # Phosphor-Fill. Using it means the pause/reload symbols here are the SAME glyphs the
    # Node Info toolbar shows, and an icon-font glyph is centred on the em box rather than
    # sitting on the text baseline (a text character like "❚❚" rides low in a button).
    # Both H21 and H22 ship the font and agree on these codepoints (verified by render).
    PHOSPHOR_FILL = "$HFS/houdini/fonts/Phosphor-Fill.ttf"
    GLYPH_PAUSE = ""          # PhosphorGlyphs.pause
    GLYPH_RELOAD = ""         # PhosphorGlyphs.arrows_clockwise
    GLYPH_RATIO = 11.0 / 24.0       # MiniToolButton: an 11px glyph in a 24px control
    _phosphor_family = _UNSET       # resolved once, then cached (None if unavailable)

    @classmethod
    def phosphor_family(cls):
        """The Phosphor-Fill family name, or None on a build without the font."""
        if cls._phosphor_family is _UNSET:
            path = os.path.expandvars(cls.PHOSPHOR_FILL)
            font_id = QtGui.QFontDatabase.addApplicationFont(path)
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id) \
                if font_id != -1 else []
            cls._phosphor_family = families[0] if families else None
        return cls._phosphor_family

    @classmethod
    def glyph_icon(cls, glyph, px, rgb):
        """One Phosphor glyph as a QIcon in `rgb`, or None when the font is unavailable."""
        family = cls.phosphor_family()
        if not family:
            return None
        font = QtGui.QFont(family)
        font.setPixelSize(px)
        side = px + 4                       # padding so no variant clips its own ink
        pixmap = QtGui.QPixmap(side, side)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(*rgb))
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, glyph)
        painter.end()
        return QtGui.QIcon(pixmap)

    @classmethod
    def pause_qss(cls, checked, radius):
        """The Pause button's sheet for one toggle state, mimicking MiniToolButton."""
        if checked:
            bg = cls.role(cls.PAUSE_ON_BG_ROLE, cls.Color.PAUSE_ON)
            fg = cls.role(cls.PAUSE_ON_FG_ROLE, cls.Color.PAUSE_ON_FG)
            edge = _rgb_css(fg)
            # Room for the label; at rest the button is a bare circle around the glyph.
            padding = "0px 10px"
        else:
            bg = cls.role(cls.PAUSE_OFF_BG_ROLE, cls.Color.PAUSE_OFF)
            fg = cls.role(cls.PAUSE_OFF_FG_ROLE, cls.Color.PAUSE_OFF_FG)
            edge = "transparent"
            padding = "0px"
        return ("QPushButton#pause {{ background: {bg}; color: {fg}; "
                "border: {px}px solid {edge}; border-radius: {radius}px; "
                "padding: {padding}; }}"
                .format(bg=_rgb_css(bg), fg=_rgb_css(fg), edge=edge,
                        px=cls.PAUSE_BORDER_PX, radius=radius, padding=padding))


# ---------------------------------------------------------------------------
# Decode (pure numpy -- no Qt, no hou)
# ---------------------------------------------------------------------------

def _unpack_page_bits(raw, num_pages):
    """uint32[32]-per-page bitstream -> (num_pages, 1024) uint8 (0/1), bit b at slot b."""
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return bits.reshape(num_pages, PAGE_SIZE)


def _unpack_flags(raw, num_pages):
    """Packed bit-per-page mask -> (num_pages,) bool."""
    if not raw:
        return np.zeros(num_pages, dtype=bool)
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return bits[:num_pages].astype(bool)


# Oklab -> LMS' and linear-sRGB <- LMS, from Bjorn Ottosson's Oklab definition. Written
# out rather than pulled from coloraide (which Houdini does ship): the palette is built on
# the paint path for every visible band, and this is three matrix multiplies on an (n, 3)
# array where the library is per-colour Python. tests/test_page_panel.py checks the output
# against coloraide, so the shortcut is verified rather than assumed.
_OKLAB_TO_LMS = np.array([
    [1.0,  0.3963377774,  0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480]])
_LMS_TO_LINEAR_RGB = np.array([
    [ 4.0767416621, -3.3077115913,  0.2309699292],
    [-1.2684380046,  2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147,  1.7076147010]])

def _radical_inverse_base2(count):
    """(count,) floats in [0, 1): the van der Corput sequence, term i being i's bits
    reflected about the binary point (0, 1/2, 1/4, 3/4, 1/8, ...)."""
    index = np.arange(count, dtype=np.uint64)
    out = np.zeros(count, dtype=np.float64)
    place = 0.5
    while index.any():
        out += (index & np.uint64(1)).astype(np.float64) * place
        index >>= np.uint64(1)
        place *= 0.5
    return out


def _block_palette(num_blocks, seed=Style.Color.BLOCK_SEED):
    """(num_blocks,) uint32 RGBA of distinct soft colours, stable per index."""
    if num_blocks <= 0:
        return np.zeros(0, dtype=np.uint32)
    start = np.random.default_rng(seed).random()
    hue = np.mod(start + _radical_inverse_base2(num_blocks), 1.0)

    lightness = Style.Color.BLOCK_OKLCH_L
    chroma = Style.Color.BLOCK_OKLCH_C
    angle = hue * (2.0 * np.pi)
    lab = np.stack([np.full(num_blocks, lightness),
                    chroma * np.cos(angle),
                    chroma * np.sin(angle)], axis=-1)
    linear = ((lab @ _OKLAB_TO_LMS.T) ** 3) @ _LMS_TO_LINEAR_RGB.T
    # The ring is in gamut, so this clips nothing; it is here so a future edit to L or C
    # cannot silently produce a wrapped uint8 instead of an obviously flattened colour.
    linear = np.clip(linear, 0.0, 1.0)
    srgb = np.where(linear <= 0.0031308, linear * 12.92,
                    1.055 * linear ** (1.0 / 2.4) - 0.055)

    out = np.empty((num_blocks, 4), dtype=np.uint8)
    out[:, :3] = np.rint(srgb * 255.0).astype(np.uint8)
    out[:, 3] = 255
    return out.view(np.uint32).reshape(-1)


# sRGB -> Oklab, the direction _OKLAB_TO_LMS above does not go. Only the L row is kept:
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
    """(N, 3) uint8 ink for a label drawn ON each of `colors`.

    Per colour, not per palette: the ring is one lightness today, but a palette that
    varies L would leave a single ink unreadable at one end of it, and the failure would
    be invisible until someone changed the ring."""
    light = _oklab_lightness(colors) < Style.Color.LABEL_INK_SWITCH_L
    ink = np.empty((len(light), 3), np.uint8)
    ink[light] = Style.Color.LABEL_INK_LIGHT
    ink[~light] = Style.Color.LABEL_INK_DARK
    return ink


# Houdini's own UI font, so a label on a tile reads as part of the panel rather than as
# something pasted onto it. A Houdini session has it registered already; hython and a
# bare Qt app do not, hence the addApplicationFont. Resolved once -- the lookup walks the
# font database, and this is called from the paint path.
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


# Base 36, lowercase -- the conventional alphabet, and the narrow one. A block id is a
# LINK between tiles and nothing else: the ids are handed out in the order the provider
# walks the pages, so "block 41" next to "block 42" says nothing about the two blocks, and
# a decimal label invites a reader to think it does. Letters read as identifiers.
#
# It also costs less than decimal, both ways that matter. 196 blocks need two base-36
# characters where they need three decimal ones, so a label is one glyph narrower AND the
# paint pass runs one place fewer; the widest lowercase glyph is 11px against a digit's 8
# at 13px Lato, so two of them (22px) still undercut three digits (24px). Uppercase would
# undo all of it -- W and M take 14px, and a uniform cell has to be as wide as the widest
# glyph it will ever hold.
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
    """The largest Lato size at which `num_chars` base-36 characters fit a `grid_px` tile,
    or 0 when even the smallest readable size does not fit.

    Both the width and the height are checked against the same budget: the tile is square,
    and a label that fits across it can still be taller than it."""
    budget = grid_px - 2 * Style.Layout.LABEL_PAD
    largest = int(grid_px * Style.Layout.LABEL_MAX_FRACTION)
    for font_px in range(largest, Style.Layout.LABEL_MIN_PX - 1, -1):
        metrics = QtGui.QFontMetrics(_label_font(font_px))
        advance = max(metrics.horizontalAdvance(c) for c in _BASE36)
        height = metrics.tightBoundingRect(_BASE36).height()
        if num_chars * advance <= budget and height <= budget:
            return font_px
    return 0


# Keyed by pixel size, which RES_OPTIONS and the id width between them bound to a handful
# of entries. Never cleared: a rasterised glyph does not go stale, and the whole table at
# every size a tile can ask for is a few tens of kilobytes -- 5 KB at the 13px a 32px tile
# takes, 45 KB at the 40px a 96px tile takes.
_GLYPH_ATLAS_CACHE = {}


def _glyph_atlas(font_px):
    """``(atlas, width, height, middle, advances)`` -- `atlas` is (36, height, width) uint8
    coverage, one entry per base-36 symbol, each centred in a cell of the widest advance.
    `middle` is the row of the box to sit on the tile's centre line, and `advances` is
    each symbol's real advance -- the cell is as wide as the widest glyph, but laying the
    label out on that width would strand the narrow ones: `i`, `j` and `l` advance 3px
    where `m` advances 11, and "4l" set on an 11px step reads as two tokens.

    Coverage rather than colour: the ink is per block (see _label_ink), so a rasterised
    glyph has to stay a mask that any ink can be pushed through.

    The box is sized to the whole alphabet so nothing clips, but it is CENTRED on the
    cap band. Sizing and centring on the same box would push every label up by half a
    descender, since most read out with no descender in them at all."""
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


class DecodedOwner:
    """Decoded, virtualisation-friendly view of one owner's index-map report."""

    def __init__(self, owner_report, owner):
        self.owner = owner
        self.num_pages = int(owner_report["num_pages"])
        self.offset_size = int(owner_report["offset_size"])
        self.index_size = int(owner_report["index_size"])
        self.monotonic = bool(owner_report["is_monotonic"])
        self.trivial = bool(owner_report["is_trivial"])

        n = self.num_pages
        # Raw bit buffers as byte views, reshaped per page (128 bytes/page).
        self._active_raw = owner_report["active_page_bits"]
        self._temp_raw = owner_report["temporary_page_bits"]

        # Per-page occupancy counts (small: 8 bytes/page).
        self.num_active = np.frombuffer(owner_report["num_active_per_page"], np.int64)
        self.num_temp = np.frombuffer(owner_report["num_temporary_per_page"], np.int64)
        self.num_vacant = np.frombuffer(owner_report["num_vacant_per_page"], np.int64)

        # Contiguous full blocks: half-open [start, end) offset pairs.
        raw_blocks = owner_report.get("full_block_ranges", b"")
        blocks = np.frombuffer(raw_blocks, np.int64).reshape(-1, 2) if raw_blocks \
            else np.zeros((0, 2), np.int64)
        self.block_starts = np.ascontiguousarray(blocks[:, 0])
        self.block_ends = np.ascontiguousarray(blocks[:, 1])
        self._block_palette = _block_palette(len(self.block_starts))

        # Attribute page-storage arrays are decoded lazily + cached (see attr_page_storage).
        self._attribs = owner_report.get("attributes", {})
        self._page_storage_cache = {}

    # -- per visible-range producers ----------------------------------------

    def occupancy_states(self, p0, p1):
        """(n, 1024) uint8 occupancy codes for pages [p0, p1)."""
        n = p1 - p0
        active = _unpack_page_bits(self._active_raw[p0 * 128:p1 * 128], n)  # 0/1
        temp = _unpack_page_bits(self._temp_raw[p0 * 128:p1 * 128], n)      # 0/1
        state = active + (temp << 1)               # 0 vacant, 1 active, 2 temporary
        # Out-of-range slots (flat offset >= offset_size) are contiguous at the
        # very end of the map, so they only ever affect the tail of the last
        # page. Skip the whole per-slot scan unless this band reaches them.
        if p1 * PAGE_SIZE > self.offset_size:
            first_oor = max(0, self.offset_size - p0 * PAGE_SIZE)
            state.reshape(-1)[first_oor:] = ST_OOR
        return state

    def block_colors_u32(self, p0, p1):
        """(n, 1024) uint32 RGBA for pages [p0, p1) in Continuous block mode."""
        n = p1 - p0
        elem_start = p0 * PAGE_SIZE
        elem_stop = p1 * PAGE_SIZE
        out = np.full(n * PAGE_SIZE, Style.Color.BLOCK_BG32, dtype=np.uint32)
        if len(self.block_starts):
            first_block = int(np.searchsorted(self.block_ends, elem_start, side="right"))
            last_block = int(np.searchsorted(self.block_starts, elem_stop, side="left"))
            palette = self._block_palette
            for block in range(first_block, last_block):
                run_start = max(int(self.block_starts[block]), elem_start) - elem_start
                run_stop = min(int(self.block_ends[block]), elem_stop) - elem_start
                if run_stop > run_start:
                    out[run_start:run_stop] = palette[block]
        return out.reshape(n, PAGE_SIZE)

    def attr_page_storage(self, scope, name):
        """(num_pages,) uint8 page-storage code array for one page-detail attribute."""
        key = (scope, name)
        cached = self._page_storage_cache.get(key)
        if cached is not None:
            return cached
        attr = self._attribs.get(scope, {}).get(name)
        n = self.num_pages
        page_details = attr.get("page_details") if attr else None
        if page_details is None:
            codes = np.full(n, PS_NONE, dtype=np.uint8)
        else:
            constant_mask = _unpack_flags(page_details["constant_page_bits"], n)
            hardened_mask = _unpack_flags(page_details["hardened_page_bits"], n)
            shared_mask = _unpack_flags(page_details["shared_page_bits"], n)
            # Default depends on whether the split was measurable at all. Without a
            # hardness API "neither constant nor hardened" means UNKNOWN, not shared --
            # filling PS_SHARED there claimed something the provider never reported.
            default = (PS_SHARED if page_details["has_hardened_details"]
                       else PS_UNKNOWN)
            codes = np.full(n, default, dtype=np.uint8)
            codes[hardened_mask] = PS_HARDENED
            # Constant last, so it beats the PS_SHARED default -- then the constant pages
            # that ARE shared are separated back out. Order matters: every
            # constant-and-shared page is in constant_mask too.
            codes[constant_mask] = PS_CONSTANT
            codes[constant_mask & shared_mask] = PS_CONSTANT_SHARED
        self._page_storage_cache[key] = codes
        return codes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def human_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# ---------------------------------------------------------------------------
# Collapsible section container
# ---------------------------------------------------------------------------

class CollapsibleSection(QtWidgets.QWidget):
    """A titled section whose body can be collapsed. Self-contained (no reliance
    on the internal hutil Expander)."""

    # Emitted when the section is expanded (True) / collapsed (False). The owner
    # manages the surrounding splitter sizes -- we deliberately do NOT constrain
    # maximumHeight here, as that leaves the splitter handle unable to resize.
    toggled = QtCore.Signal(bool)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._toggle = QtWidgets.QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        # Styled by Style.PANEL_QSS (QToolButton#sectionToggle) on the parent panel.
        # Both section instances share this objectName -- fine for stylesheet matching.
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
        layout.addWidget(self._body, 1)     # body absorbs the section's height

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


# ---------------------------------------------------------------------------
# Memory report (upper section)
# ---------------------------------------------------------------------------

class ClickableLabel(QtWidgets.QLabel):
    """A QLabel that emits `clicked` (used for colour-in-HTML scope labels that a
    stylesheet can't override, paired with a checkbox)."""

    clicked = QtCore.Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# Role holding a per-column value to sort by (numeric where the display text is a
# formatted string like "1.2 KB"), so header-click sorting is by magnitude.
SORT_ROLE = QtCore.Qt.UserRole + 10
# The AttributeStats behind a row, on EVERY attribute row. Distinct from UserRole, which
# carries the key only for checkable (page-detail) rows and drives the bar toggle -- peer
# highlighting has to reach attributes that have no bar, such as array types.
ATTR_ROLE = QtCore.Qt.UserRole + 11
# The row's IDENTITY, on every row -- what the view remembers a selection and a
# collapsed section by, so both survive being pointed at another SOP. Two forms:
#
#   ("attr", owner, scope, name)   attribute leaves, keyed like the bar toggles
#   ("path", label, label, ...)    structural rows, by their position in the tree
#
# Structural rows have no key of their own, but Row.label IS their identity (the model
# says so, and `note` is display-only), and labels are unique among siblings -- so the
# label path down from the root names the row. Attribute leaves take their attribute key
# instead: it is stable wherever the row ends up, and two attributes in one owner group
# can share a name across scopes, which a label path could not tell apart.
ROW_KEY_ROLE = QtCore.Qt.UserRole + 12


def _is_descendant(item, ancestor):
    """True when `item` sits anywhere under `ancestor` (not counting itself)."""
    parent = item.parent()
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent()
    return False


class _AttrItem(QtWidgets.QTreeWidgetItem):
    """Attribute row: sorts by the SORT_ROLE value of the active sort column.

    The Pages column is deliberately NOT sortable. Its three numbers are per-attribute but
    the page TOTAL behind them belongs to the owner's index map, so any single-number key
    ranks most rows equal -- a sort that visibly does nothing is worse than no sort."""

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
    """Owner group header: always keeps canonical owner order (vertex, point,
    primitive, detail) regardless of the sort column or direction, so attributes
    stay grouped the same way however they are sorted."""

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
        pct = index.data(QtCore.Qt.UserRole)
        if pct is None:
            super().paint(painter, option, index)
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        track = QtGui.QColor(*Style.role(Style.PCT_TRACK_ROLE, Style.Color.PCT_TRACK))
        fill = QtGui.QColor(*Style.role(Style.PCT_FILL_ROLE, Style.Color.PCT_FILL))
        painter.save()
        painter.fillRect(rect, track)
        w = int(rect.width() * min(max(pct, 0.0), 100.0) / 100.0)
        bar = QtCore.QRect(rect.left(), rect.top(), w, rect.height())
        painter.fillRect(bar, fill)

        text = f"{pct:.1f}%"
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
    """A breakdown tree that remembers where the user was looking.

    Shared by the single-node report and the A/B diff, which draw different columns from
    different data but keep the same three pieces of view state across a rebuild -- the
    selected row, which sections are open, and (for the report) the bar toggles. All of it
    is restored by ROW_KEY_ROLE, so a key that does not come back simply never matches.

    Only the state lives here. Everything about WHAT a row shows -- columns, sorting,
    colours, delegates -- belongs to the subclass, because that is the part where the two
    trees genuinely differ.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_key = None      # the current row
        self._expansion = {}           # row key -> the user's expanded/collapsed choice
        self._row_items = {}           # row key -> QTreeWidgetItem, rebuilt per populate
        self._applied_expansion = {}   # row key -> what this build applied, rebuilt too
        self.currentItemChanged.connect(self._remember_current)
        self.itemExpanded.connect(self._remember_expanded)
        self.itemCollapsed.connect(self._remember_collapsed)

    def _reset_row_state(self):
        """Clear the per-build maps. Called by populate() before it rebuilds; the
        REMEMBERED state (`_selected_key`, `_expansion`) deliberately survives."""
        self._row_items = {}
        self._applied_expansion = {}

    def _record_row(self, item, key):
        item.setData(0, ROW_KEY_ROLE, key)
        self._row_items[key] = item

    def _apply_expansion(self, item, key, default_collapsed):
        """Open or close a row: the user's remembered choice if they made one, otherwise
        the row's own default. Recorded so _restore_selection can put ancestors back."""
        decided = self._expansion.get(key, not default_collapsed)
        item.setExpanded(decided)
        self._applied_expansion[key] = decided

    # -- Remembered view state ------------------------------------------------
    #
    # These three slots only ever RECORD. Nothing here reads the tree back to decide
    # what to show -- populate() is the single place that applies remembered state, so a
    # rebuild cannot be confused by the signals it causes. They are also connected before
    # the panel's own currentItemChanged handler (this __init__ runs first), so the
    # remembered key is already up to date by the time the panel reacts to a selection.

    def _remember_current(self, current, _previous):
        # Deliberately ignores None: clear() and an empty model both deselect, and losing
        # the row because the panel was momentarily pointed at nothing would defeat the
        # point. The same reasoning keeps _toggled through a clear().
        if current is not None:
            self._selected_key = current.data(0, ROW_KEY_ROLE)

    def _remember_expanded(self, item):
        self._set_expansion(item, True)

    def _remember_collapsed(self, item):
        self._set_expansion(item, False)
        # Closing a section over the selection hides it, but a hidden selection is still
        # FELT: it dims every page bar but its own. Close it out with the section rather
        # than leave the user an emphasis they cannot see the source of.
        current = self.currentItem()
        if current is not None and _is_descendant(current, item):
            self.clear_selection()

    def _set_expansion(self, item, expanded):
        key = item.data(0, ROW_KEY_ROLE)
        if key is not None:
            self._expansion[key] = expanded

    def clear_selection(self):
        """Deselect, and FORGET the row -- otherwise the next rebuild would restore it.

        `_remember_current` deliberately ignores a None current (a clear() deselects
        momentarily and that must not lose the row), so the key has to be dropped here.
        """
        self._selected_key = None
        self.clearSelection()
        self.setCurrentItem(None)

    def mousePressEvent(self, event):
        """A click in the empty margin LEFT of a row's toggle/label clears the selection,
        as does a click below the last row. There is no other way to deselect: a tree keeps
        its current row once it has one."""
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if not index.isValid():
            self.clear_selection()          # empty space below the rows
        elif pos.x() < self.visualRect(index).left() - self.indentation():
            self.clear_selection()
            return                          # do NOT let the base class re-select the row
        super().mousePressEvent(event)

    def _restore_selection(self):
        """Re-select the remembered row, if this tree has it."""
        item = self._row_items.get(self._selected_key)
        if item is None:
            return
        blocked = self.blockSignals(True)
        try:
            self.setCurrentItem(item)
            # setCurrentItem() EXPANDS every ancestor so the current row is visible, which
            # silently reopens a section the user had closed over their own selection --
            # the remembered collapse was applied moments ago in _build and is undone here.
            # Put the ancestors back the way _build decided. (Signals are blocked, so this
            # cannot be mistaken for the user reopening them.)
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

    # "Total Memory" / "New Memory" / "Unique Memory" -- Houdini's own vocabulary for
    # these numbers ("Geometry Memory" / "Unique Memory" in its SOP info panel), not
    # the invented "Full".
    COLS = ("Component", "%", "Total Memory", "New Memory", "Unique Memory",
            "Data ID", "Pages c/s/h", "Type")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("memReport")
        # Sit the rows on the panel rather than on a text field. An item view fills its
        # viewport from QPalette.Base -- the colour of an input box -- so everything below
        # the last row reads as a large pale slab that has nothing to do with the panel
        # around it. Window is the surface the panel itself is on, which is what that empty
        # area should be. Still no colour of ours: it names a palette role and the current
        # colour scheme supplies the value.
        self.viewport().setBackgroundRole(QtGui.QPalette.Window)
        self.viewport().setAutoFillBackground(True)
        self.setColumnCount(len(self.COLS))
        self.setHeaderLabels(self.COLS)
        self.setItemDelegateForColumn(1, PercentBarDelegate(self))
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        # All columns are user-resizable; populate() gives them padded default
        # widths, and the last column takes any slack so the table fills the
        # window without leaving a gap.
        header = self.header()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(True)
        # Click a header to sort attributes within their owner groups. Default:
        # largest Full first (matches the previous fixed order).
        self.setSortingEnabled(True)
        self.sortByColumn(2, QtCore.Qt.DescendingOrder)
        # Transparent icon the width of the check indicator, used to indent the
        # names of attributes that have no toggle so they line up with those that
        # do (otherwise their text sits where the checkbox would be).
        # self.style() can hand back an already-deleted QProxyStyle under Houdini's
        # PySide (seen when the widget has no own stylesheet and isn't parented yet);
        # fall back to the application style, then a sane default, rather than crash.
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
        self._hover_key = None         # (owner, scope, name) to outline (bar-hover border)
        self._peer_keys = frozenset()  # rows sharing storage with the SELECTED row
        # View state the user set, remembered across populate() exactly as _toggled is,
        # so switching SOPs (or unpausing) does not throw away where they were looking.
        # Keyed by ROW_KEY_ROLE; a key that does not come back simply never matches.
        # The selection, the open sections and the per-build maps live on RowTree; only
        # the bar toggles are this class's, since only this tree drives the page grid.
        self.itemChanged.connect(self._on_item_changed)

    def set_hover_key(self, key):
        """Outline the row for `key` with a coloured border (a hovered page bar's linked row),
        or clear it with None. Distinct from selection, which keeps its background highlight."""
        if key != self._hover_key:
            self._hover_key = key
            self.viewport().update()

    def set_peer_keys(self, keys):
        """Shade the rows sharing storage with the selected attribute.

        A third cue alongside selection (its own background highlight) and bar-hover
        (border): these are the attributes the selected one shares memory with, which is
        what explains why the attribute rows can exceed their owner's row."""
        keys = frozenset(keys or ())
        if keys != self._peer_keys:
            self._peer_keys = keys
            self.viewport().update()

    def drawRow(self, painter, option, index):
        # Two cues on different channels, so a row can carry both: peers get a translucent
        # wash, the bar-hover row gets a border.
        #
        # The wash goes OVER the finished row. Painting it underneath is the obvious
        # reading -- text on top, selection wins -- but the app style paints the item panel
        # in between, and a style that fills that panel opaquely erases an under-painted
        # wash silently. H22's new UI does exactly that. Over the top is the only placement
        # nothing downstream can undo; the hover border is drawn after so it stays crisp.
        #
        # Both are keyed off ATTR_ROLE, not UserRole: UserRole is absent on rows with no
        # bar, and those can still be peers.
        super().drawRow(painter, option, index)
        attr = index.data(ATTR_ROLE)
        key = attr.key if attr is not None else None
        row_rect = self.visualRect(index)
        if key is not None and key in self._peer_keys:
            # Start where the row's own content starts, not at x=0. `index` is column 0, so
            # visualRect already excludes the tree indentation -- which is exactly where
            # the selection fill begins, so the two cues line up instead of the quieter one
            # reaching further left than the louder one.
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
        """The attribute-row item for (owner, scope, name), or None. Drives the
        bar-hover -> row highlight link."""
        return self._attr_items.get(key)

    def populate(self, model, scopes=None):
        """Render `model`'s breakdown tree. `scopes` hides attribute leaf rows to cut
        clutter -- it never changes a number (the model guarantees that)."""
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
        # Fit each column to its contents with a little breathing room. (Re-enabling
        # sorting above re-applies the header's sort; resize-by-contents is
        # order-independent, so widths are unaffected.)
        for c in range(len(self.COLS)):
            self.resizeColumnToContents(c)
            self.setColumnWidth(c, self.columnWidth(c) + Style.Layout.COL_PAD)
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

    def _build(self, parent, row, report_total, parent_path=()):
        """Recursively turn a Row (and its children) into tree items."""
        item = (self._attr_item(row.attr, report_total, bool(row.children))
                if row.attr is not None else self._section_item(row, report_total))
        # The label path names structural rows; attribute leaves are keyed by their
        # attribute instead (see ROW_KEY_ROLE). Both descend by label, so a row under an
        # attribute row -- the <primitive list> per-type breakdown -- still gets a path.
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
        # Rows that ask to start closed (the <primitive list> per-type breakdown) do, and
        # everything else opens -- but only until the user says otherwise. Their choice
        # is remembered per row and outranks the default in both directions, so a closed
        # section stays closed and an opened <primitive list> stays open.
        # _apply_expansion also records what this build DECIDED, default or remembered
        # choice alike -- _restore_selection needs it to undo the expansion
        # setCurrentItem() forces on ancestors.
        self._apply_expansion(item, key, row.collapsed)
        for child in row.children:
            self._build(item, child, report_total, path)
        return item

    def _section_item(self, row, report_total):
        """A structural breakdown row (Geometry Memory / a category / an owner group / an
        index map / a group table). Uses _OwnerItem so siblings keep their canonical
        order regardless of the active sort column; attribute leaves still sort."""
        pct = 100.0 * row.total_memory / report_total
        # `note` is display-only (e.g. Unaccounted's tail-initializer count). The model owns
        # the text; we only place it. row.label stays the row's identity.
        label = "%s  (%s)" % (row.label, row.note) if row.note else row.label
        item = _OwnerItem(
            [label, "", human_bytes(row.total_memory),
             human_bytes(row.new_memory) if row.new_memory is not None else "",
             human_bytes(row.unique_memory) if row.unique_memory is not None else "",
             "", "", row.type_str],
            row.order)
        item.setData(1, QtCore.Qt.UserRole, pct)               # % bar
        item.setData(1, SORT_ROLE, pct)
        item.setData(2, SORT_ROLE, row.total_memory)
        item.setData(3, SORT_ROLE, row.new_memory if row.new_memory is not None else -1)
        item.setData(4, SORT_ROLE, row.unique_memory if row.unique_memory is not None else -1)
        item.setData(5, SORT_ROLE, -1)
        # A structural row can carry a scope too. Edge-group rows are scope "group", so they
        # come out violet like every other group. Rows with no colour -- the categories, the
        # owner rows, and everything scope "internal" -- keep the DEFAULT text colour, so
        # nothing is set on them.
        colour = Style.Color.SCOPE_COLORS.get(row.scope)
        if colour is not None:
            brush = QtGui.QBrush(QtGui.QColor(*colour))
            for c in range(len(self.COLS)):
                item.setForeground(c, brush)

        # Bold marks a row that allocated new memory ITSELF, so it draws the eye to what
        # this cook actually changed. Only childless rows qualify: a parent is bold the
        # moment any one descendant allocates, which on real geometry is almost always,
        # and "something under here allocated" is not worth an emphasis. The root is the
        # deliberate exception -- _build bolds it unconditionally as the total.
        if not row.children and row.new_memory:
            font = item.font(0)
            font.setBold(True)
            for c in range(len(self.COLS)):
                item.setFont(c, font)
        return item

    def _attr_item(self, attr, report_total, has_own_expander=False):
        """One attribute leaf. Scope is conveyed by row colour, not a name suffix."""
        pct = 100.0 * attr.total_memory / report_total
        item = _AttrItem(
            [attr.name, "", attr.total_label(human_bytes),
             human_bytes(attr.new_memory), human_bytes(attr.unique_memory),
             attr.data_id_label, attr.pages_label, attr.type_label])
        item.setData(1, QtCore.Qt.UserRole, pct)
        if attr.is_tail_initialized:
            item.setToolTip(7, "Registered with the detail's tail-initialize table, so "
                               "appended elements get the default re-asserted.\nThis table "
                               "is what the Unaccounted row measures.")
        # Per-column magnitude keys so header-click sorting is numeric, not by the
        # formatted "1.2 KB" strings.
        item.setData(0, SORT_ROLE, attr.name)
        item.setData(1, SORT_ROLE, pct)
        item.setData(2, SORT_ROLE, attr.total_memory)
        item.setData(3, SORT_ROLE, attr.new_memory)
        item.setData(4, SORT_ROLE, attr.unique_memory)
        item.setData(5, SORT_ROLE, attr.data_id)
        item.setData(7, SORT_ROLE, attr.type_name)
        brush = QtGui.QBrush(QtGui.QColor(*Style.Color.SCOPE_COLORS.get(
            attr.scope, Style.Color.ATTR_DEFAULT_FG)))
        font = item.font(0)
        # Bold means THIS row allocated -- see _section_item for the rule and why a row
        # with children is excluded.
        font.setBold(attr.new_memory != 0 and not has_own_expander)
        for c in range(len(self.COLS)):
            item.setForeground(c, brush)
            item.setFont(c, font)
        # ITALIC = this data id was found on an input, so this node did not change the
        # VALUES. Shown by italicising the id itself rather than a "(inh)" suffix, which made
        # the column noisy. Applied AFTER the loop above, which would otherwise overwrite it,
        # and off a COPY of `font` so the row's bold state is preserved.
        if attr.is_data_id_found_in_inputs:
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
            # Restore the toggle if this attribute was on for a previous node -- the
            # set persists across populate() so state is remembered when the same
            # (owner, scope, name) reappears.
            item.setCheckState(0, QtCore.Qt.Checked if attr.key in self._toggled
                               else QtCore.Qt.Unchecked)
            # UserRole carries the key ONLY here: _on_item_changed keys the bar toggle off
            # it, so putting it on non-checkable rows would fire spurious toggle events.
            item.setData(0, QtCore.Qt.UserRole, attr.key)
        elif not has_own_expander:
            # No checkbox: pad with a blank icon so the name lines up with the
            # checkable rows instead of sitting in the checkbox's position.
            #
            # UNLESS the row carries its own expander arrow (today: <primitive list> with
            # its per-type children). That arrow already sits where the pad would go, so
            # padding as well double-indents the label. Such a row takes the checkbox's
            # place rather than being pushed past it.
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


# ---------------------------------------------------------------------------
# Page grid (lower section)
# ---------------------------------------------------------------------------

class _DiffItem(QtWidgets.QTreeWidgetItem):
    """An attribute row in the diff: sorts on SORT_ROLE for the active column.

    An unknown sorts BELOW every real number rather than beside zero -- "not measured" is
    not "did not change", and letting the two land together is the same conflation the
    dash exists to prevent.
    """

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
    """Renders a diff.DiffRow tree: what changed between the pinned node and the live one.

    A sibling of MemoryReport rather than a mode of it. MemoryReport hard-codes its column
    indices -- the percent delegate on column 1, the data id's sort key on column 5,
    _AttrItem.NO_SORT_COLUMN -- and branching each of those on a mode flag is the kind of
    change that goes wrong silently. Two classes, one fixed column set each.

    Every memory figure here is a DELTA. There are no absolute columns: the absolutes are
    one gesture away, since deselecting shows the pinned node's ordinary report. A percent
    column is gone for good -- a share of the geometry total says nothing about a change.
    """

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

    # A row that exists on one side only, or that is a different attribute wearing the same
    # name, is marked in the NAME'S FONT as well as by its tint. Colour is never the only
    # channel here: the tints are a red/green/violet family, the worst case for a
    # colour-blind reader, and dropping the A and B totals took away the other thing that
    # gave an added row away -- with only deltas on the row, "+151.2 KB" for something that
    # never existed looks exactly like a change.
    #
    # The font rather than a word after the name: an ADDED / REMOVED / REPLACED tag sits
    # right where the eye scans for the attribute and pushes the names out of line, which
    # costs more than the state is worth on a tree read column-first.
    #
    # Strikethrough for a row that is gone needs no explaining. Bold for added collides with
    # nothing -- _build bolds the ROOT, which is a category and can never be added or
    # removed. Italic is free too; MemoryReport uses it on the Data ID column, a different
    # column in a different tree.
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
        # Structural rows -- the three categories, the owner groups, the index-map and
        # group-table rows -- keep their canonical order under every sort, as they do in
        # the single-node report. They are the shape of the breakdown, and ranking
        # "Internal" above "Attributes & Groups" by size makes the tree unreadable.
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

        # The absolutes, as a tooltip on the cell they were subtracted to make. They stop
        # being columns in this mode but they are still the answer to "from what?".
        if row.a_total is not None and row.b_total is not None:
            item.setToolTip(self.COL_DELTA_TOTAL, "%s → %s"
                            % (human_bytes(row.a_total), human_bytes(row.b_total)))

        # Colour the three memory deltas and the page count by direction, each on its own
        # value -- Total can grow while New falls, which is exactly the case worth seeing.
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
        # so "paged primitive list → full primitive list" loses its right half to the panel
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
        for col in range(len(self.COLS)):
            self.resizeColumnToContents(col)
            self.setColumnWidth(col, self.columnWidth(col) + Style.Layout.COL_PAD)

    def _build(self, parent, row, parent_path):
        item = self._item(row)
        path = parent_path + (row.label,)
        # The same identity the report uses, so a selection survives moving between the
        # two bodies -- select a row, deselect the node to read the full report, and the
        # row is still current.
        # The key the JOIN paired this row on, not one recomputed here -- see DiffRow.key.
        # It is the report's key too, so a selection survives moving between the two
        # bodies: select a row, deselect the node to read the full report, and the row is
        # still current.
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


class PageGridWidget(QtWidgets.QAbstractScrollArea):
    """Virtualised wrapped grid of page cards for one owner.

    All spacing / sizing is read from Style.Layout (H_GAP, V_GAP, GRID_BAR_GAP,
    BAR_GAP, LEFT_PAD, ...); this class holds no look-and-feel constants of its own.
    """

    # Emitted on hover with a description of what is under the cursor ("" when
    # nothing). The panel wires this to the lower section's status bar.
    hoverInfo = QtCore.Signal(str)
    # Emitted on hover with the (owner, scope, name) of the attribute bar under the cursor
    # (("", "", "") when not over a bar). The panel wires this to highlight the matching
    # table row (linked highlight, the reverse of set_emphasis). The OWNER is carried
    # because a bar in "sharing" mode can belong to another owner than the grid's.
    barHovered = QtCore.Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        # Same reasoning as MemoryReport: the grid is a canvas sitting on the panel, not a
        # text field, so it takes Window rather than the scroll area's default Base.
        self.viewport().setBackgroundRole(QtGui.QPalette.Window)
        self._bg_cache_rgb = None       # see _bg_u32 / _dim_lut
        self._bg_cache_u32 = 0
        self._dim_lut_cache = None
        self._dim_color_cache = {}      # single packed colours, same blend (see _dim_u32)

        self._decoded = None
        self._mode = "occupancy"           # or "block"
        self._cell_px = Style.Layout.DEFAULT_CELL_PX
        self._bar_attrs = []               # ordered [(owner, scope, name)] keys
        self._page_storage_arrays = []            # aligned with _bar_attrs
        self._emphasis = None              # index into _bar_attrs, or None (no highlight)
        # "sharing" mode only: the SELECTED attribute's per-page memory block identity.
        # Bars come from this instead of the toggles, so the toggle state is ignored in
        # that mode.
        self._sharing = None               # (attr, block_ids, map_indices, mapping) or None
        self._selected_attr = None         # drives the sharing-mode empty-state message
        self._sharing_lut_cache = None
        self._sharing_dim_lut_cache = None
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
        # Block ids identify allocations in ONE report, so a new report (or a new owner
        # under the same report) invalidates a pinned block rather than renaming it.
        self._pinned_block = 0
        self._block_members_cache = (0, None)
        if self._mode == "sharing":
            # Bars come from the selected attribute's peers here, not the toggles. Take
            # the new decoded owner but leave the bar rows alone, or switching owner or
            # toggling a checkbox would wipe them.
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
        """Highlight one attribute bar (dim the rest); None clears. `key` is an
        (owner, scope, name) triple. Cheap repaint only -- the class arrays are
        unchanged."""
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
        self._sharing_dim_lut_cache = None
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
            # Clear the ROWS too, not just the data. Leaving the previous attribute's peers
            # in _bar_attrs made _hover_at take the sharing branch and unpack a None
            # _sharing -- selecting a sharing attribute, then a non-sharing one, then
            # moving the mouse over the grid.
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
        """The memory block id at `page` of the selected attribute, or 0 (no sharing
        data, no page, or a block no other page in the detail reaches)."""
        if self._sharing is None or page is None:
            return 0
        block_ids = self._sharing[1]
        if not (0 <= page < len(block_ids)):
            return 0
        return int(block_ids[page])

    def _block_members(self, block):
        """The selected attribute's pages on `block`, ascending. Cached for the current
        block: the scan is over every page, and hover re-asks for it on every mouse move
        while the cursor stays on one card."""
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

    def _bar_h(self):
        return max(4, self._cell_px)

    def _gutter_w(self):
        return Style.Layout.LEFT_PAD

    def _bars_block_h(self):
        nb = len(self._bar_attrs)
        if nb == 0:
            return 0
        return Style.Layout.GRID_BAR_GAP + nb * self._bar_h() + (nb - 1) * Style.Layout.BAR_GAP

    def _card_h(self):
        return self._grid_px() + self._bars_block_h()

    def _card_w(self):
        return self._grid_px()

    def _cols(self):
        avail = self.viewport().width() - self._gutter_w() - Style.Layout.H_GAP
        step = self._card_w() + Style.Layout.H_GAP
        return max(1, (avail + Style.Layout.H_GAP) // step)

    def _row_stride(self):
        return self._card_h() + Style.Layout.V_GAP

    def _num_pages(self):
        return self._decoded.num_pages if self._decoded else 0

    def _update_scrollbar(self):
        n = self._num_pages()
        if n == 0:
            self.verticalScrollBar().setRange(0, 0)
            return
        cols = self._cols()
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

    def _bg_rgb(self):
        """The grid canvas colour: whatever the current colour scheme paints behind a view.

        Not a constant of ours. The cards and bars carry the meaning; the surface they sit
        on is chrome and belongs to the theme, so the grid follows a scheme change the same
        way the rest of the panel does."""
        pal = self.viewport().palette()
        return pal.color(self.viewport().backgroundRole()).getRgb()[:3]

    def _bg_u32(self):
        """`_bg_rgb` packed for the uint32 band, cached per colour."""
        rgb = self._bg_rgb()
        if rgb != self._bg_cache_rgb:
            self._bg_cache_rgb = rgb
            self._bg_cache_u32 = _u32(rgb + (255,))
            # Everything dimmed blends toward the background, so all four caches go.
            self._dim_lut_cache = None
            self._sharing_dim_lut_cache = None
            self._sharing_ink_cache = None
            self._dim_color_cache = {}
        return self._bg_cache_u32

    def _dim_lut(self):
        """Page-storage colours blended toward the canvas, for un-emphasised bars.

        Recomputed when the theme moves the canvas -- blending toward a stale dark grey on
        a light scheme would make the dimmed bars the loudest thing on screen."""
        self._bg_u32()                      # refreshes the cache / invalidates this one
        if self._dim_lut_cache is None:
            self._dim_lut_cache = _blend_lut32(
                Style.Color.PAGE_STORAGE_LUT, self._bg_cache_rgb + (255,),
                Style.Color.DIM_BLEND)
        return self._dim_lut_cache

    def _dim_u32(self, color):
        """One packed colour blended toward the canvas -- the same treatment `_dim_lut`
        gives the page-storage table, for the per-attribute bar colours, which are
        generated rather than tabulated."""
        self._bg_u32()                      # refreshes the cache / invalidates this one
        dimmed = self._dim_color_cache.get(color)
        if dimmed is None:
            dimmed = int(_blend_packed32(np.uint32(color), self._bg_cache_rgb + (255,),
                                         Style.Color.DIM_BLEND)[0])
            self._dim_color_cache[color] = dimmed
        return dimmed

    def paintEvent(self, event):
        painter = QtGui.QPainter(self.viewport())
        # Whatever Houdini's current colour scheme puts behind a view. The canvas is
        # chrome, not data -- only the cards and bars drawn on it carry meaning.
        painter.fillRect(self.viewport().rect(), QtGui.QColor(*self._bg_rgb()))
        n = self._num_pages()
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

        cols = self._cols()
        stride = self._row_stride()
        scroll_y = self.verticalScrollBar().value()
        vp_h = self.viewport().height()
        first_row = scroll_y // stride
        last_row = (scroll_y + vp_h - 1) // stride
        max_row = (n - 1) // cols
        last_row = min(last_row, max_row)
        if first_row > last_row:
            return

        gutter_w = self._gutter_w()
        cards_w = self.viewport().width() - gutter_w
        band_top = first_row * stride
        band_h = (last_row - first_row + 1) * stride

        # One QImage per frame, borrowing the band buffer (NO copy) -- band stays
        # alive across drawImage, which reads it synchronously into the paint
        # device. This is the interactive hot path (up to a few thousand visible
        # tiles); do not build a copying QImage wrapper here. The band is
        # viewport-bounded, so this stays O(viewport), not O(total pages).
        band = np.full((band_h, cards_w), self._bg_u32(), dtype=np.uint32)
        self._render_band(band, first_row, last_row, cols)
        qimg = QtGui.QImage(band.data, cards_w, band_h, 4 * cards_w,
                            QtGui.QImage.Format_RGBA8888)
        painter.drawImage(gutter_w, band_top - scroll_y, qimg)

    def _sharing_lut(self):
        """uint32 colour per memory block id. Id 0 -- a block no other page in the
        detail reaches -- is the muted grey; the rest come from the block palette."""
        block_ids = self._sharing[1] if self._sharing else None
        size = (int(block_ids.max()) + 1) if (block_ids is not None and len(block_ids)) else 1
        if self._sharing_lut_cache is not None and len(self._sharing_lut_cache) == size:
            return self._sharing_lut_cache
        lut = np.empty(size, dtype=np.uint32)
        lut[0] = Style.Color.SHARING_NONE32
        if size > 1:
            lut[1:] = _block_palette(size - 1, Style.Color.SHARING_SEED)
        self._sharing_lut_cache = lut
        self._sharing_dim_lut_cache = None
        self._sharing_ink_cache = None
        return lut

    def _sharing_dim_lut(self):
        """`_sharing_lut` blended toward the canvas: the tiles of pages that are NOT on
        the highlighted block."""
        lut = self._sharing_lut()
        self._bg_u32()                      # refreshes the cache / invalidates this one
        if self._sharing_dim_lut_cache is None:
            self._sharing_dim_lut_cache = _blend_packed32(
                lut, self._bg_cache_rgb + (255,), Style.Color.DIM_BLEND)
        return self._sharing_dim_lut_cache

    def _sharing_ink(self):
        """``(lit, dim)`` label ink per block id, matching `_sharing_lut` index for index.

        The dim copy is blended toward the canvas by the same DIM_BLEND the tile is, so a
        label recedes exactly as far as the tile under it. Inking a dimmed tile at full
        strength would leave the numbers as loud as before the pin, which is the opposite
        of what the pin is for."""
        lut = self._sharing_lut()
        if self._sharing_ink_cache is None:
            lit = _label_ink(lut)
            dim = _blend_lut32(
                np.concatenate([lit, np.full((len(lit), 1), 255, np.uint8)], axis=1),
                self._bg_cache_rgb + (255,), Style.Color.DIM_BLEND)
            dim = dim.view(np.uint8).reshape(-1, 4)[:, :3]
            self._sharing_ink_cache = (lit, dim)
        return self._sharing_ink_cache

    def _draw_pinned_outline(self, grids, block_ids, pinned):
        """Ring the pinned block's tiles in the theme's accent, in place.

        Dimming everything else already says which tiles are on the block, but it says it
        by ABSENCE -- and on a grid where most tiles are off-block anyway, the eye reads
        the wash, not the two tiles that escaped it. The ring is the positive half of the
        same signal.

        The colour is asked of the theme, never pinned: `pluto_primary` is one of the
        three HSV values each of the 52 schemes is generated from, so it moves with the
        scheme, and the fallback covers a UI that publishes no palette at all."""
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
        """Stamp each tile with its block id, in place, over the finished tile colours.

        Colour alone stops separating the blocks well before the counts real geometry
        reaches: the palette is one Oklch ring, so N blocks are N hues on a circumference
        of 0.75 dE, and past roughly 32 of them neighbouring ids are the same colour. The
        label is what the status line already calls the block -- in the same base -- so a
        tile and the card under the cursor read as the same fact.

        Vectorised over the whole band rather than per tile: the glyphs are laid out and
        stamped one PLACE at a time across every page that has one, so the Python cost is
        the label length, not the page count. Tiles are laid out in a loop right after
        this and a per-tile text pass there would put a few thousand QPainter calls in the
        paint path."""
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
        """Why the sharing view is blank. The reasons are different problems and a single
        "nothing to show" would leave the user guessing which one they have."""
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
                    f"{human_bytes(attr.intra_detail_sharing_memory)} in its "
                    f"value table, not its pages")
        # Shares real bytes, has pages, is not a value-table type, and still produced no
        # per-page peers. Nothing here knows why, and saying so is the only honest answer --
        # the alternative is to guess, which is the mistake this branch exists to stop.
        return (f"{attr.name} shares "
                f"{human_bytes(attr.intra_detail_sharing_memory)}, but which "
                f"pages could not be determined")

    def _render_band(self, band, first_row, last_row, cols):
        """Composite the visible cards into the uint32 band image (numpy only)."""
        n = self._num_pages()
        cell = self._cell_px
        grid_px = self._grid_px()
        bar_h = self._bar_h()
        stride = self._row_stride()
        band_top = first_row * stride

        p0 = first_row * cols
        p1 = min((last_row + 1) * cols, n)

        # Card tops: 32x32 uint32 cells upscaled by cell px (only the visible
        # pages, so the work is bounded by the viewport, not the total map).
        if self._mode == "sharing":
            # One flat colour per page: WHICH memory block it sits on. The card is not
            # per-slot here -- the block is a property of the whole page. Two pages of one
            # colour are one allocation, whether they are pages 0 and 8 of this attribute
            # or one page each of two attributes.
            block_ids = self._sharing[1][p0:p1].astype(np.int64)
            lut = self._sharing_lut()
            ids = np.clip(block_ids, 0, len(lut) - 1)
            active = self._pinned_block
            if active:
                # Highlight: the pages on the PINNED block keep their colour, everything
                # else goes toward the canvas. One np.where over the visible band.
                flat = np.where(block_ids == active, lut[ids], self._sharing_dim_lut()[ids])
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

        # At higher zoom, draw cell separators (1-2 px) at each cell's leading
        # edge so individual 32x32 slots are easy to distinguish.
        #
        # Never in sharing mode. The block is a property of the WHOLE page there, so the
        # tile is one flat colour and the 32x32 lattice divides it into slots that differ
        # in nothing -- it draws a distinction the mode does not make, and it does it
        # across the label.
        if label_ids is None and cell >= Style.Layout.CELL_GRIDLINE_MIN:
            edge_px = 2 if cell >= Style.Layout.CELL_GRIDLINE_THICK else 1
            edge = (np.arange(grid_px) % cell) < edge_px
            grids[:, edge, :] = Style.Color.GRIDLINE32
            grids[:, :, edge] = Style.Color.GRIDLINE32

        # Before the tiles are placed, so each is one vectorised pass over the band. The
        # ring goes on first: it is the tile's edge and the label is its middle, and
        # drawing the label last keeps that true if a tile is ever too small for both.
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
                # selected row is never one of them and the emphasis is always None.
                off_block = bool(active) and int(block_ids[page]) != active
                for bar_idx, key in enumerate(self._bar_attrs):
                    if bar_idx not in members:
                        band[bar_y:bar_y + bar_h, x:x + grid_px] = self._bg_u32()
                        bar_y += bar_h + Style.Layout.BAR_GAP
                        continue
                    color = self._attr_colors.get(key, Style.Color.SHARING_NONE32)
                    if off_block:
                        color = self._dim_u32(color)
                    band[bar_y:bar_y + bar_h, x:x + grid_px] = color
                    bar_y += bar_h + Style.Layout.BAR_GAP
            else:
                for bar_idx, codes in enumerate(self._page_storage_arrays):
                    code = codes[page]
                    lut = (Style.Color.PAGE_STORAGE_LUT32
                           if (emphasized is None or bar_idx == emphasized)
                           else self._dim_lut())
                    band[bar_y:bar_y + bar_h, x:x + grid_px] = \
                        lut[code] if code != PS_NONE else self._bg_u32()
                    bar_y += bar_h + Style.Layout.BAR_GAP

    # -- interaction --------------------------------------------------------

    _PAGE_STORAGE_LABEL = {PS_CONSTANT: "constant", PS_SHARED: "shared",
                           PS_HARDENED: "hardened", PS_UNKNOWN: "unknown (no hardness API)",
                           PS_CONSTANT_SHARED: "constant, shared"}

    def _page_offsets(self, page):
        """``[first - last]``, the offsets `page` covers -- so a card can be read against
        an element number without doing the arithmetic."""
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, self._decoded.offset_size) - 1
        return f"[{start} - {max(start, end)}]"

    # How many block-mates the hover names before it gives up and counts them: a 256-way
    # self-merge puts every page of the source on one block, and the status line is one
    # line.
    _MAX_NAMED_BLOCK_MATES = 6

    def _block_mates_text(self, page):
        """``block 3, also on pages 8, 16`` for the hovered page.

        "" only when there is no selected attribute to answer for. A page whose block
        nothing else reaches gets the same wording as the legend's grey swatch rather than
        an empty string: the mode measured it, and silence would read as unanswered."""
        if self._sharing is None:
            return ""
        block = self._block_at(page)
        if block == 0:
            return "on a block no other page uses"
        # Base 36, the same form the tile is stamped with, so the card names the block the
        # reader is looking at rather than a second identifier for it. The PAGE numbers
        # below stay decimal: those are real ordinals into the index map.
        label = _base36(block)
        others = [int(p) for p in self._block_members(block) if int(p) != page]
        if not others:
            # The block IS shared -- the id says so -- but by another attribute, not by a
            # second page of this one. Naming the peers is the bars' job.
            return f"block {label}"
        named = ", ".join(str(p) for p in others[:self._MAX_NAMED_BLOCK_MATES])
        if len(others) > self._MAX_NAMED_BLOCK_MATES:
            named += f", +{len(others) - self._MAX_NAMED_BLOCK_MATES} more"
        return f"block {label}, also on pages {named}"

    def _hover_at(self, pos):
        """What is under the cursor, as ``(info_str, bar_idx, page)``. ``bar_idx`` is
        the index into ``_bar_attrs`` when the cursor is over an attribute bar, else
        None. ``page`` is the card under the cursor whether or not a bar is, so the
        caller can drive the block highlight from a hover anywhere on the card. Computed
        once so mouseMoveEvent can drive the status line, the barHovered link and the
        highlight together."""
        decoded = self._decoded
        if decoded is None or decoded.num_pages == 0:
            return "", None, None
        grid_px = self._grid_px()
        stride = self._row_stride()
        cols = self._cols()
        x = pos.x() - self._gutter_w()
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
        step = self._bar_h() + Style.Layout.BAR_GAP
        bar_idx = bar_offset // step
        if bar_offset < 0 or bar_idx >= len(self._bar_attrs) or (bar_offset % step) >= self._bar_h():
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
        """Click a page to highlight its memory block: every other page on that block
        keeps its colour and the rest dim. It stays until it is cleared, so the mouse is
        free to move to the report rows or to another attribute."""
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
        # One panel-wide stylesheet for typography; it cascades to the tagged children
        # (objectNames below) as they are added. It sets no chrome colour -- see
        # Style.PANEL_QSS.
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
        # Pin: capture the displayed node's report as the A side, then compare whatever is
        # selected against it. Deliberately does NOT touch the pause state -- the two are
        # different axes, and un-pausing on the user's behalf would fire a report on
        # whichever node happened to be selected, which is the cost Pause exists to avoid.
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

        # Reload + Pause, copying the Node Info window's node toolbar (NodeToolbar.qml):
        # Reload sits to the LEFT of Pause and exists only while paused -- with updates
        # frozen it is the way to force one.
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

        # Instanced warning sits at the very top, just under the title.
        self._instanced_label = QtWidgets.QLabel("")
        self._instanced_label.setWordWrap(True)
        self._instanced_label.setObjectName("instanced")
        self._instanced_label.setVisible(False)
        layout.addWidget(self._instanced_label)

        # A selection made while paused is stashed, not applied, so without this the click
        # has no feedback at all. It used to be carried by an empty-tree placeholder; that
        # space now always holds a real report, so the line lives up here instead.
        self._pending_label = QtWidgets.QLabel("")
        self._pending_label.setObjectName("instanced")   # same amber "needs your attention"
        self._pending_label.setVisible(False)
        layout.addWidget(self._pending_label)

        # Memory section: a single breakdown tree rooted at "Geometry Memory"
        # (= Primitive List + Index Maps + Attributes + Unaccounted). Scope filters
        # apply to the attribute rows and to the lower section's bars.
        self._mem_section = CollapsibleSection("Memory")
        # A coloured swatch next to each scope toggle matches the scope's row
        # colour in the table (reliable regardless of Houdini's stylesheet).
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(QtWidgets.QLabel("Scopes:"))
        self._scope_cbs = {}           # scope name -> its checkbox (see _enabled_scopes)
        self._public_cb = self._scope_toggle(scope_row, "public")
        self._private_cb = self._scope_toggle(scope_row, "private")
        self._groups_cb = self._scope_toggle(scope_row, "groups")
        # The <primitive list> row; on by default. Abbreviated in the UI only -- the
        # scope itself is spelled out, see _SCOPE_FOR_LABEL.
        self._primitive_list_cb = self._scope_toggle(scope_row, "primlist")
        # The container/bookkeeping rows under Internal. A VIEW-ONLY scope -- no attribute
        # has it, and the report knows nothing about it.
        self._internal_cb = self._scope_toggle(scope_row, "internal")
        scope_row.addStretch(1)
        self._mem_report = MemoryReport()
        self._mem_report.toggledAttrsChanged.connect(self._update_grid)
        # Linked highlight: selecting an attribute row emphasizes its page bar.
        self._mem_report.currentItemChanged.connect(self._on_attr_selected)
        # The diff body. Stacked with the report rather than replacing its columns: see
        # DiffReport for why these are two classes.
        self._diff_report = DiffReport()
        self._diff_report.setVisible(False)
        # Shown in place of both when a comparison came back empty. Its own widget so the
        # message is never confused with a tree that failed to build.
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
        sp = self._splitter
        total = sum(sp.sizes()) or sp.height()
        mem_open = self._mem_section.is_open()
        grid_open = self._grid_section.is_open()
        mem_h = self._mem_section.header_height()
        grid_h = self._grid_section.header_height()
        if mem_open and grid_open:
            sp.setSizes(self._open_sizes)
        elif mem_open:
            sp.setSizes([max(total - grid_h, mem_h), grid_h])
        elif grid_open:
            sp.setSizes([mem_h, max(total - mem_h, grid_h)])
        else:
            sp.setSizes([mem_h, grid_h])

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
        cb = QtWidgets.QCheckBox()
        # Every scope defaults on except "internal" -- the container/bookkeeping rows
        # (Attribute Set, Index Maps, Group Tables, Detail Object) are rarely what a user
        # wants to see first; they opt in.
        cb.setChecked(scope != "internal")
        cb.stateChanged.connect(self._on_scope_changed)
        self._scope_cbs[scope] = cb
        # A scope with no colour ("internal") gets a plain label in the default text
        # colour -- matching its rows, which are also left uncoloured.
        colour = Style.Color.SCOPE_COLORS.get(scope)
        text = label if colour is None \
            else f'<span style="color:{_rgb_css(colour)}">{label}</span>'
        lbl = ClickableLabel(text)
        lbl.clicked.connect(cb.toggle)
        layout.addWidget(cb)
        layout.addWidget(lbl)
        layout.addSpacing(6)
        return cb

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
                # setParent(None) removes it from view NOW. deleteLater() alone only
                # schedules destruction for the next event-loop pass, leaving the old
                # label parented and *still painted* -- so the fresh labels overlap
                # it and the doubled strokes read as bold (worse when Follow
                # Selection fires several cook events before the deletes flush).
                w.setParent(None)
                w.deleteLater()

        def add(color, text):
            if color is not None:
                layout.addWidget(self._swatch(color))
            lbl = QtWidgets.QLabel(text)
            lbl.setObjectName("legendLabel")   # coloured by Style.PANEL_QSS
            layout.addWidget(lbl)
            layout.addSpacing(8)

        occ = Style.Color.OCC_LUT
        ps = Style.Color.PAGE_STORAGE_LUT
        mode = self._grid.mode()
        if mode == "sharing":
            # Both channels hand out a colour per thing, so they share one entry rather
            # than repeating the phrase either side of the separator. No swatch: a single
            # square would be as misleading here as it is in block mode. The mode is not
            # named either -- the Mode combo is a few pixels away, and this legend sets the
            # panel's minimum width (its minimumSizeHint is its sizeHint), so every
            # redundant phrase costs real room.
            #
            # The "/ attribute" half is conditional because the bars are: an attribute that
            # only reuses its own blocks draws none, and naming a channel that is not on
            # screen would send the user looking for it.
            add(None, "unique colour per memory block / attribute"
                if self._grid.bar_attrs() else "unique colour per memory block")
            add(Style.Color.SHARING_NONE, "block no other page uses")
        elif mode == "block":
            # No swatch: each block has its own colour, so a single square would
            # be misleading.
            add(None, "contiguous block: unique colour per run")
            add(Style.Color.BLOCK_BG, "vacant / out-of-range")
        else:
            add(occ[ST_ACTIVE], "active")
            add(occ[ST_VACANT], "vacant")
            add(occ[ST_TEMP], "temporary")
            add(occ[ST_OOR], "out-of-range")
        # Attribute bars, only relevant when some bar is shown. They mean something
        # different in sharing mode -- one row per PEER, not a storage class -- so the
        # storage legend would be actively wrong there.
        if self._grid.bar_attrs():
            layout.addWidget(self._vsep())
            layout.addSpacing(8)
            if mode == "sharing":
                # What a FILLED bar means is in the shared entry above; this says what an
                # empty one means. The swatch has to be the real canvas colour, not a
                # literal, or the legend stops matching the grid the moment the theme
                # changes.
                add(self._grid._bg_rgb() + (255,), "bar not on this block")
            else:
                add(ps[PS_CONSTANT], "constant")
                add(ps[PS_SHARED], "shared")
                add(ps[PS_HARDENED], "hardened")
                # The last two are conditional for opposite reasons. Constant-and-shared is
                # a narrow case and the legend stays short when it is absent; unknown must
                # only appear when something on screen actually has unmeasurable pages --
                # an element group or the paged <primitive list>.
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
        # A widget hears about a new application palette as PaletteChange, not as
        # ApplicationPaletteChange -- that one goes to the application object.
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.PaletteChange:
            self._queue_repolish()

    def paintEvent(self, event):
        # The panel cannot rely on being TOLD the scheme changed. QStyleSheetStyle gives
        # every widget it styles an explicit palette, and Qt has no reason to announce an
        # application palette change to a widget whose own resolved palette did not move --
        # so changeEvent above can simply never fire.
        #
        # Checking here instead costs one colour comparison per repaint and needs no
        # application-wide event filter. Such a filter is the obvious alternative and is a
        # bad trade: it runs for every event of every object in Houdini (measured at 2392
        # calls for a single palette change with 300 extra widgets on screen) and drove
        # _repolish ten times over. Houdini repaints everything when the scheme changes, so
        # a repaint is a reliable moment to notice.
        super().paintEvent(event)
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        token = app.palette().color(QtGui.QPalette.Window).rgba()
        if token != self._theme_token:
            self._theme_token = token
            self._queue_repolish()

    def _queue_repolish(self):
        """Collapse a burst of theme notifications into one re-polish.

        A scheme change arrives many times over -- once per widget that reports it -- and
        each re-polish walks the whole subtree. Ten of them froze the application for
        seconds. Deferring to the event loop makes the burst idempotent."""
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
        """Switch the tracked node and move the cook callback onto it. Returns whether
        anything changed, so the caller knows whether to refresh.

        The pause gate is deliberately NOT checked here. Pause means "stop receiving
        updates from the scene unless explicitly requested by the Reload", so the gate
        belongs to the caller: set_node() is the scene talking and defers to it, while
        Reload and un-pause arrive having already decided."""
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
        """Reload: the one explicit request Pause does not gate.

        It ADOPTS whatever was selected while paused before re-reading. Without that it
        re-read the node already on screen, so selecting a node and pressing Reload did
        nothing visible -- which made stay-paused-and-Reload, the workflow for scenes too
        heavy to follow live, impossible to use."""
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
        """Fill the output combo with the node's output label names and reset to output
        0. Signals are blocked so repopulating does not trigger a redundant refresh (the
        caller queues one)."""
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
        # Frame changes are not node events, but a time-dependent node recooks
        # when the playbar frame moves -- so watch the playbar too. The playbar
        # exists only in a graphical Houdini; guard so headless construction
        # (tests, hython) still loads and tears down cleanly.
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
        # Refresh only when the tracked node actually recooks on a frame change.
        # for_last_cook=True reads the last cook's time-dependency without forcing
        # a cook, keeping this non-perturbing; the deferred _refresh then reads the
        # freshly-cooked geometry (report() cooks the node at the current frame).
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
        # Title is just the SOP path -- or both paths in compare mode, since a toggle alone
        # cannot say what is being compared. The memory totals live in the tree below.
        self._header.setText(self._title())
        instanced = [name for name, model in ((self._pinned_path, self._pinned),
                                              (self._title_live(), self._model))
                     if model is not None and model.instanced]
        if instanced:
            # The output detail IS another node's detail, so that node allocated nothing of
            # its own (every data id is found on the shared input) and the provider zeroes
            # its New and Unique figures.
            #
            # In compare mode SAY WHICH SIDE. The diff dashes Δ New and Δ Unique for an
            # instanced side (see diff._join), and a column of dashes needs a reason
            # attached or it reads as a measurement that failed.
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

    # Sub-reports that are all-or-nothing: take the byte arrays out and what is left is
    # not a smaller record, it is one that says sharing WAS measured while holding none of
    # the measurement. memory_block_sharing is None | {...} in the schema, so None is the
    # value that states exactly what stripping did, and every consumer already reads it.
    # Dropping the key instead would be wrong the other way -- AttributeStats requires it.
    #
    # page_details is NOT on this list and must not be: its byte arrays go, but the
    # constant / shared / hardened counts beside them are what the diff's c/s/h column
    # subtracts, and they stay true with the masks gone.
    _STRIP_TO_NONE = ("memory_block_sharing",)

    def _strip_report(self, report):
        """The report without its `bytes` values, for pinning.

        Those are the page bitsets, the per-page counts and the block ids -- everything the
        page grid reads and nothing the diff does. On a 1.44M-point grid they are 2320 KB of
        the report's 2323 KB, and a panel whose subject is memory should not sit on
        megabytes of page masks it will never look at.

        It is also what makes compare mode memory-only: with no page data on the A side
        there is no grid to draw for it, so the section stays hidden in every state rather
        than appearing and vanishing as the user selects and deselects.
        """
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
    #
    # onNodePathChanged only fires when the pane tab lands on a DIFFERENT node; emptying
    # the network selection leaves it pointing at the last one, so the panel never hears
    # about it and keeps comparing a node against itself. That is the whole of the
    # "nothing selected" state, so it needs a signal of its own.
    #
    # ChildSelectionChanged on the enclosing network is that signal, and it is a NODE event
    # -- the same addEventCallback machinery the panel already uses for cooks, so it works
    # in a headless session and can be tested. hou.ui.addSelectionCallback would do the
    # same job, but hou.ui exists only in a graphical Houdini, which makes that path
    # untestable here; this one is not.
    #
    # Watched only while pinned. An un-pinned panel emptying itself on a deselect would be
    # a behaviour change nobody asked for.

    def _watch_network(self, node):
        """Follow selection changes in `node`'s network, dropping any previous watch.

        A None node KEEPS the current watch rather than dropping it. Losing the live node
        is the deselected state, and that is exactly when the watch is still needed -- to
        hear the re-select. Dropping it there made the panel deaf after the first deselect.
        """
        if node is None:
            return
        network = node.parent()
        # `==`, never `is`. hou.Node hands back a NEW Python wrapper on every call --
        # node.parent() is not node.parent() -- so an identity test here never matches and
        # the watch is torn down and rebuilt on every adopt.
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
        """Both directions, because the pane tab reports neither of them here.

        onNodePathChanged fires on a change of PATH. Deselecting does not change it, and
        neither does re-selecting the node it still points at -- so after the panel clears
        its own live node, picking that same node back up produces no event at all and the
        comparison never returns. Both halves have to come from this signal.

        Everything goes through set_node, so Pause still gates it: with updates frozen a
        selection change waits for Reload like any other.
        """
        if not self._alive or self._pinned is None:
            return
        selected = hou.selectedNodes()
        if not selected:
            self.set_node(None)
            return
        # The most recently selected SOP, which is what Houdini treats as current. A
        # selection carrying no SOP at all is someone working in another context; leave
        # the panel where it is rather than blanking it.
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
        # The grid section belongs to the un-pinned panel only. Hidden rather than
        # collapsed: a collapsed header invites opening it, and there is nothing behind it
        # to open. The splitter keeps its remembered sizes, so un-pinning restores the
        # layout the user had.
        if pinned:
            self._watch_network(self._node)
        else:
            self._unwatch_network()
        self._grid_section.setVisible(not pinned)
        self._apply_pin_style()
        # The title says WHAT is being compared, so it has to move with the pin and not
        # wait for the next report -- pinning does not itself trigger one.
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
        """Put the right body on screen for the current pin / selection state.

        Three states, and only the last is a message -- the other two are real reports, so
        an empty tree never has to stand in for an instruction:

          not pinned, or pinned with nothing selected -> an ordinary single-node report
          pinned with a node selected                 -> the diff
          ...and that diff came back empty            -> "No differences."
        """
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
        """Say that a selection is waiting. Only while paused -- unpaused it is applied at
        once and there is nothing to report."""
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
        return {scope for scope, cb in self._scope_cbs.items() if cb.isChecked()}

    def _bar_attrs_for_owner(self, owner):
        """Toggled attributes for `owner`, in the order their rows appear in the memory
        report -- so the Nth bar lines up with the Nth toggled row above it, whatever
        column the tree is sorted by. The tree is the single source of that order (it is
        what the user sees); re-deriving it here is what let the two drift apart."""
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
        decoded = DecodedOwner(self._model.owner_map(owner), owner)
        # Detail-wide, so it is handed over before the bars: the colours must not depend
        # on which owner is shown or which attribute is selected.
        self._grid.set_attr_colors(_attr_color_map(self._model))
        self._grid.set_data(decoded, self._bar_attrs_for_owner(owner))
        self._refresh_legend()      # bar-class entries depend on shown bars
        # set_data cleared any emphasis; re-apply from the current selection so a
        # still-shown attribute stays highlighted (and an invalid one clears).
        self._on_attr_selected(self._mem_report.currentItem(), None)

    def _on_attr_selected(self, current, _previous):
        """Selected attribute row -> emphasize its bar (dim the rest) and outline the rows
        it shares storage with. Clears the emphasis when the row is a header, a different
        owner, or not toggled on."""
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
        """Hovered bar -> outline its report row with a coloured BORDER. That is the only
        cue hover gives: it must NOT dim/undim the other bars -- emphasis is driven purely
        by the SELECTED row (see _on_attr_selected), never by hover."""
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
            # Every other mode draws the TOGGLED attributes. Sharing mode replaced that
            # list with its peer rows, so it has to be rebuilt from the toggles on the way
            # out -- otherwise the bars stay gone until the user re-toggles one.
            # _update_grid() refreshes the legend itself.
            self._update_grid()

    def _sync_sharing_selection(self):
        """Hand the grid the selected attribute for "sharing" mode. The grid ignores this
        in the other modes, where the bars come from the toggles instead."""
        current = self._mem_report.currentItem()
        attr = current.data(0, ATTR_ROLE) if current is not None else None
        self._grid.set_sharing(attr)

    def _on_res_changed(self, _idx):
        self._grid.set_cell_px(self._res_combo.currentData())
