import numpy as np

from .style import Style

BITS_PER_BYTE = 8

# Occupancy state codes (index into OCC_LUT).
ST_VACANT, ST_ACTIVE, ST_TEMP, ST_OOR = 0, 1, 2, 3

# Attribute page storage codes (index into PAGE_STORAGE_LUT).
# PS_CONSTANT_SHARED: a constant page whose stored value is refcounted.
PS_CONSTANT, PS_SHARED, PS_HARDENED, PS_UNKNOWN, PS_CONSTANT_SHARED, PS_NONE = \
    0, 1, 2, 3, 4, 255


def _unpack_offset_bits(raw, num_pages, page_size):
    """One-bit-per-offset bitstream -> (num_pages, page_size) uint8 (0/1), bit b at offset b."""
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return bits.reshape(num_pages, page_size)


def _unpack_flags(raw, num_pages):
    """Packed bit-per-page mask -> (num_pages,) bool."""
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return bits[:num_pages].astype(bool)


# Oklab -> linear-sRGB matrices (Bjorn Ottosson). Inlined rather than
# coloraide for paint-path performance; verified in test_page_panel.py.
_OKLAB_TO_LMS = np.array([
    [1.0,  0.3963377774,  0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480]])
_LMS_TO_LINEAR_RGB = np.array([
    [ 4.0767416621, -3.3077115913,  0.2309699292],
    [-1.2684380046,  2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147,  1.7076147010]])

# Golden ratio conjugate: the step of the Weyl sequence hue[i] = start + i*_PHI (mod 1),
# which puts consecutive indices a constant 1 - _PHI turns apart on the ring.
_PHI = (5.0 ** 0.5 - 1.0) / 2.0


def _block_palette(num_blocks, seed=Style.Color.BLOCK_SEED):
    """(num_blocks,) uint32 RGBA of distinct soft colours, stable per index."""
    if num_blocks <= 0:
        return np.zeros(0, dtype=np.uint32)
    start = np.random.default_rng(seed).random()
    hue = np.mod(start + np.arange(num_blocks, dtype=np.float64) * _PHI, 1.0)

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


class DecodedOwner:
    """Decoded, virtualisation-friendly view of one owner's index-map report."""

    def __init__(self, owner_report, owner, page_layout, primitive_list_report=None,
                 attributes_report=None):
        self.owner = owner
        self.num_pages = owner_report["num_pages"]
        self.offset_size = owner_report["offset_size"]
        self.page_size = page_layout["page_size"]
        self.page_dim = int(round(self.page_size ** 0.5))    # the card is page_dim x page_dim
        self._bytes_per_page_offset_bits = self.page_size // BITS_PER_BYTE
        ga_size = np.dtype(f"<i{page_layout['ga_size_in_bytes']}")

        occupancy = owner_report["occupancy"]
        # Raw bit buffers as byte views, sliced per page.
        self._active_raw = occupancy["active_offset_bits"]
        self._temp_raw = occupancy["temporary_offset_bits"]

        self.num_active = np.frombuffer(occupancy["num_active_per_page"], ga_size)
        self.num_temp = np.frombuffer(occupancy["num_temporary_per_page"], ga_size)
        self.num_vacant = np.frombuffer(occupancy["num_vacant_per_page"], ga_size)

        # Contiguous full blocks: half-open [start, end) offset pairs.
        raw_blocks = occupancy.get("full_block_ranges", b"")
        blocks = np.frombuffer(raw_blocks, ga_size).reshape(-1, 2) if raw_blocks \
            else np.zeros((0, 2), ga_size)
        self.block_starts = np.ascontiguousarray(blocks[:, 0])
        self.block_ends = np.ascontiguousarray(blocks[:, 1])
        self._block_palette = _block_palette(len(self.block_starts))

        # Attribute page-storage arrays are decoded lazily + cached (see attribute_page_storage).
        self._attribs = (attributes_report or {}).get("attributes", {})
        self._page_storage_cache = {}
        # Not a GA_Attribute, so the primitive list's page_details sit in the report's own
        # "primitive_list" entry; kept so attribute_page_storage can serve its bar.
        self._primitive_list_page_details = (primitive_list_report or {}).get("page_details")

    # -- per visible-range producers ----------------------------------------

    def occupancy_states(self, p0, p1):
        """(n, page_size) uint8 occupancy codes for pages [p0, p1)."""
        n = p1 - p0
        stride = self._bytes_per_page_offset_bits
        active = _unpack_offset_bits(self._active_raw[p0 * stride:p1 * stride], n,
                                     self.page_size)                          # 0/1
        temp = _unpack_offset_bits(self._temp_raw[p0 * stride:p1 * stride], n,
                                   self.page_size)                            # 0/1
        state = active + (temp << 1)               # 0 vacant, 1 active, 2 temporary
        # Out-of-range slots only fill the tail of the last page, so the per-slot scan runs
        # only when this band reaches it.
        if p1 * self.page_size > self.offset_size:
            first_oor = max(0, self.offset_size - p0 * self.page_size)
            state.reshape(-1)[first_oor:] = ST_OOR
        return state

    def block_colors_u32(self, p0, p1):
        """(n, page_size) uint32 RGBA for pages [p0, p1) in Continuous block mode."""
        n = p1 - p0
        elem_start = p0 * self.page_size
        elem_stop = p1 * self.page_size
        out = np.full(n * self.page_size, Style.Color.BLOCK_BG32, dtype=np.uint32)
        if len(self.block_starts):
            first_block = int(np.searchsorted(self.block_ends, elem_start, side="right"))
            last_block = int(np.searchsorted(self.block_starts, elem_stop, side="left"))
            palette = self._block_palette
            for block in range(first_block, last_block):
                run_start = max(int(self.block_starts[block]), elem_start) - elem_start
                run_stop = min(int(self.block_ends[block]), elem_stop) - elem_start
                if run_stop > run_start:
                    out[run_start:run_stop] = palette[block]
        return out.reshape(n, self.page_size)

    def attribute_page_storage(self, scope, name):
        """(num_pages,) uint8 page-storage code array for one page-detail attribute."""
        key = (scope, name)
        cached = self._page_storage_cache.get(key)
        if cached is not None:
            return cached
        n = self.num_pages
        if scope == "primitive_list":
            page_details = self._primitive_list_page_details
        else:
            attrib = self._attribs.get(scope, {}).get(name)
            page_details = attrib.get("page_details") if attrib else None
        if page_details is None:
            codes = np.full(n, PS_NONE, dtype=np.uint8)
        else:
            constant_mask = _unpack_flags(page_details["constant_page_bits"], n)
            hardened_mask = _unpack_flags(page_details["hardened_page_bits"], n)
            shared_mask = _unpack_flags(page_details["shared_page_bits"], n)
            # Without a hardness API, "neither constant nor hardened" means UNKNOWN, not
            # shared.
            default = (PS_SHARED if page_details["has_hardened_details"]
                       else PS_UNKNOWN)
            codes = np.full(n, default, dtype=np.uint8)
            codes[hardened_mask] = PS_HARDENED
            # Constant last, so it beats the default. Constant-and-shared pages are a subset
            # of constant_mask, so they are split out after it.
            codes[constant_mask] = PS_CONSTANT
            codes[constant_mask & shared_mask] = PS_CONSTANT_SHARED
        self._page_storage_cache[key] = codes
        return codes
