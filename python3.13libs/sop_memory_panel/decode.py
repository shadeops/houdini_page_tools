import numpy as np

from .style import Style

PAGE_SIZE = 1024
PAGE_DIM = 32                       # 32 x 32 == PAGE_SIZE

# Occupancy state codes (index into OCC_LUT).
ST_VACANT, ST_ACTIVE, ST_TEMP, ST_OOR = 0, 1, 2, 3

# Attribute page storage codes (index into PAGE_STORAGE_LUT).
# PS_CONSTANT_SHARED: a constant page whose stored value is refcounted.
PS_CONSTANT, PS_SHARED, PS_HARDENED, PS_UNKNOWN, PS_CONSTANT_SHARED, PS_NONE = \
    0, 1, 2, 3, 4, 255


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


class DecodedOwner:
    """Decoded, virtualisation-friendly view of one owner's index-map report."""

    def __init__(self, owner_report, owner, primitive_list_report=None, attributes_report=None):
        self.owner = owner
        self.num_pages = owner_report["num_pages"]
        self.offset_size = owner_report["offset_size"]
        self.index_size = owner_report["index_size"]
        self.monotonic = owner_report["is_monotonic"]
        self.trivial = owner_report["is_trivial"]

        occupancy = owner_report["occupancy"]
        n = self.num_pages
        # Raw bit buffers as byte views, reshaped per page (128 bytes/page).
        self._active_raw = occupancy["active_page_bits"]
        self._temp_raw = occupancy["temporary_page_bits"]

        # Per-page occupancy counts (small: 8 bytes/page).
        self.num_active = np.frombuffer(occupancy["num_active_per_page"], np.int64)
        self.num_temp = np.frombuffer(occupancy["num_temporary_per_page"], np.int64)
        self.num_vacant = np.frombuffer(occupancy["num_vacant_per_page"], np.int64)

        # Contiguous full blocks: half-open [start, end) offset pairs.
        raw_blocks = occupancy.get("full_block_ranges", b"")
        blocks = np.frombuffer(raw_blocks, np.int64).reshape(-1, 2) if raw_blocks \
            else np.zeros((0, 2), np.int64)
        self.block_starts = np.ascontiguousarray(blocks[:, 0])
        self.block_ends = np.ascontiguousarray(blocks[:, 1])
        self._block_palette = _block_palette(len(self.block_starts))

        # Attribute page-storage arrays are decoded lazily + cached (see attr_page_storage).
        self._attribs = (attributes_report or {}).get("attributes", {})
        self._page_storage_cache = {}
        # The primitive list's page_details live in the report's own top-level
        # "primitive_list" entry, not inside any owner's "attributes" -- it is not a
        # GA_Attribute. Stashed separately so attr_page_storage can still serve its bar
        # under the (owner="primitive", scope="primitive_list") key report.py hands out.
        self._primitive_list_page_details = (primitive_list_report or {}).get("page_details")

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
        n = self.num_pages
        if scope == "primitive_list":
            page_details = self._primitive_list_page_details
        else:
            attr = self._attribs.get(scope, {}).get(name)
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
