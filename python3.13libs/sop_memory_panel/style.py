import os

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

_STYLE_UNSET = object()


def _u32(color):
    """Pack an (R, G, B, A) tuple as a single RGBA8888 uint32 (little-endian)."""
    return int(np.array(color, np.uint8).view(np.uint32)[0])


def _rgb_css(color):
    return f"rgb({color[0]},{color[1]},{color[2]})"


def human_bytes(num_bytes):
    num_bytes = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0


# ---------------------------------------------------------------------------
# Style -- every "look and feel" value in one namespace
# ---------------------------------------------------------------------------

class Style:
    """Central namespace for the panel's look and feel."""

    class Color:
        # 4-tuple RGBA for uint32 band / LUTs; 3-tuple RGB for text / chrome.

        # Index-map occupancy (index == ST_* code).
        OCC_LUT = np.array(
            [
                [200, 200, 200, 255],   # vacant       -> light gray
                [110, 175, 110, 255],   # active       -> muted green
                [110, 140, 205, 255],   # temporary    -> muted blue
                [ 28,  28,  28, 255],   # out-of-range -> near-black
            ],
            dtype=np.uint8,
        )
        # Attribute page storage (index == PS_* code).
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

        # Grid canvas colour comes from the palette (_background_rgb), not from here.
        BLOCK_BG = (78, 78, 78, 255)    # non-block cells in Continuous block mode
        GRIDLINE = (45, 45, 45, 255)    # cell separators, drawn only at high zoom

        # uint32 variants for the single-image compositing path.
        OCC_LUT32 = OCC_LUT.view(np.uint32).reshape(-1).copy()
        PAGE_STORAGE_LUT32 = PAGE_STORAGE_LUT.view(np.uint32).reshape(-1).copy()
        BLOCK_BG32 = _u32(BLOCK_BG)
        # Block id 0: no other page in the detail reaches this block.
        SHARING_NONE = (86, 86, 86, 255)
        SHARING_SEED = 0x7C41
        # Separate seed so block and attribute palettes don't collide on one card.
        SHARING_ATTR_SEED = 0x1E33

        GRIDLINE32 = _u32(GRIDLINE)
        SHARING_NONE32 = _u32(SHARING_NONE)

        DIM_BLEND = 0.62            # un-emphasised bars blend this far toward canvas

        # Oklch palette ring: L chosen for widest sRGB gamut, C at 94% of the
        # smallest in-gamut chroma across all hues at that L.
        BLOCK_OKLCH_L = 0.75
        BLOCK_OKLCH_C = 0.1199
        BLOCK_SEED = 0x50A6

        # Block-id label ink: two neutrals, switched by tile lightness in Oklab L.
        LABEL_INK_DARK = (24, 24, 24)           # Oklab L 0.209
        LABEL_INK_LIGHT = (242, 242, 242)       # Oklab L 0.961
        LABEL_INK_SWITCH_L = 0.585

        # Scope -> row text colour. Marks data the user can directly create and
        # delete (attributes, groups, edge groups, primitives) -- the same set of
        # rows that go bold when New is nonzero (report.py); every container or
        # bookkeeping row has no entry here and is never bold.
        # Every colour here except edge_group's doubles as a filter-toggle swatch
        # (panel.py's _scope_toggle), so a row's colour also tells you which
        # checkbox controls it. Edge groups break that rule -- they are gated by
        # "Group Tables", not by "Groups" -- so edge_group gets its OWN colour,
        # deliberately different from "group"'s violet: reusing it would visually
        # claim the Groups checkbox filters edge groups too, which it does not.
        SCOPE_COLORS = {
            "public":  (170, 200, 230),     # soft blue
            "private": (228, 205, 165),     # soft amber
            "group":   (205, 178, 222),     # soft violet -- element groups
            "edge_group": (224, 158, 178),  # soft rose -- gated by Group Tables, not Groups
            "primitive_list": (150, 205, 190),  # soft teal -- per-type primitive rows
        }

        # Compare mode delta colours. Every delta also carries an explicit + or -.
        DELTA_UP = (216, 122, 96)       # grew   -- warm
        DELTA_DOWN = (122, 190, 140)    # shrank -- cool
        DELTA_ZERO = (140, 140, 140)    # measured zero, dimmed
        DELTA_UNKNOWN = (130, 130, 130) # unknown — neutral grey, not a direction

        PCT_TRACK = (55, 55, 55)
        PCT_FILL = (70, 130, 200)
        PCT_TEXT = (230, 230, 230)

        ATTR_DEFAULT_FG = (220, 220, 220)
        NO_PAGES_FG = (180, 180, 180)

        INSTANCED_FG = (224, 192, 96)   # amber warning
        HAIRLINE = (85, 85, 85)         # legend swatch border
        # Pause pill fallbacks (used when pluto roles are unavailable).
        PAUSE_ON = (200, 129, 60)       # fill while frozen
        PAUSE_ON_FG = (32, 24, 12)      # label + border
        PAUSE_OFF = (86, 86, 86)        # fill at rest
        PAUSE_OFF_FG = (220, 220, 220)  # label at rest
        HOVER_BORDER = (100, 170, 240)  # hovered-bar row border
        # Peer wash: translucent, painted OVER the finished row — a style may
        # fill PE_PanelItemViewItem opaquely, swallowing anything painted under.
        # Colour is the theme's accent (see Style.peer_wash); this is the fallback.
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

        # Block-id label sizing on sharing-mode tiles (see _glyph_atlas).
        LABEL_PAD = 2
        LABEL_MIN_PX = 8
        LABEL_MAX_FRACTION = 0.42

        PIN_OUTLINE_MAX_PX = 3
        PIN_OUTLINE_PER_PX = 48         # one ring pixel per this much tile

        SWATCH = 14                     # legend swatch square (px)
        COL_PAD = 20                    # extra width added to each fitted column
        PCT_COL_MIN = 72                # minimum width of the % bar column

        SPLITTER_OPEN = (260, 520)      # restored sizes when both sections are open
        PANEL_MARGIN = 4                # root layout contents margin
        LEGEND_MARGINS = (8, 2, 2, 2)   # legend row contents margins

    # -- Widget chrome ----------------------------------------------------------

    # Peer wash and pin outline: the theme's accent (pluto_primary), never
    # highlight (which is what selection uses).
    PEER_ROLE = "pluto_primary"
    PIN_OUTLINE_ROLE = PEER_ROLE

    # Percentage bar: borrows Houdini's slider roles (field/primary).
    PCT_TRACK_ROLE = "pluto_field"
    PCT_FILL_ROLE = "pluto_primary"
    PCT_TRACK_FG_ROLE = "pluto_fieldFg"
    PCT_FILL_FG_ROLE = "pluto_primaryFg"

    @staticmethod
    def role(name, fallback):
        """Pluto theme colour as (r, g, b), or ``fallback`` on older UIs."""
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

    # Pause pill: copies Houdini's MiniToolButton (NodeToolbar.qml).
    PAUSE_OFF_BG_ROLE = "pluto_button"
    PAUSE_OFF_FG_ROLE = "pluto_buttonFg"
    PAUSE_ON_BG_ROLE = "pluto_checkedSurface"
    PAUSE_ON_FG_ROLE = "pluto_checkedSurfaceFg"
    PAUSE_BORDER_PX = 2

    # Phosphor-Fill icon font — same glyphs the Node Info toolbar uses.
    PHOSPHOR_FILL = "$HFS/houdini/fonts/Phosphor-Fill.ttf"
    GLYPH_PAUSE = ""          # PhosphorGlyphs.pause
    GLYPH_RELOAD = ""         # PhosphorGlyphs.arrows_clockwise
    GLYPH_RATIO = 11.0 / 24.0       # MiniToolButton: an 11px glyph in a 24px control
    _phosphor_family = _STYLE_UNSET     # resolved once, then cached (None if unavailable)

    @classmethod
    def phosphor_family(cls):
        """The Phosphor-Fill family name, or None on a build without the font."""
        if cls._phosphor_family is _STYLE_UNSET:
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
