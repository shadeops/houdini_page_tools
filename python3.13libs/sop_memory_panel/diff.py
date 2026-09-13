"""Two MemoryModel breakdowns joined into a tree of differences. No Qt.

Rules: the delta is B - A over provider-measured figures, never re-summed from
children. Unknown on either side makes the slot unknown. An instanced side
empties New and Unique (the provider zeroes them — "does not apply", not "zero").
"""

from .model import OWNERS, PrimitiveList, data_id_text, row_key

ARROW = " → "


def constant_shared_hardened(attrib):
    """(constant, shared, hardened) page counts, with None for unmeasurable slots.

    No page_details at all (array/blob/index-pair): all three unknown.
    has_hardened_details false (element groups, paged prim list): only constant known.
    """
    if attrib is None or not attrib.has_page_details:
        return (None, None, None)
    if isinstance(attrib, PrimitiveList) or not attrib.has_hardened_page_details:
        return (attrib.num_constant_pages, None, None)
    return (attrib.num_constant_pages, attrib.num_shared_pages, attrib.num_hardened_pages)


def page_count(row, owner_pages):
    """How many pages this row's data spans, or None when not applicable.

    From the index map's num_pages, NEVER the c/s/h sum — those only partition
    when has_hardened_details is true.
    """
    if row is None:
        return None
    if row.owner is not None:
        return owner_pages.get(row.owner)
    if row.attrib is None or not row.attrib.has_page_details:
        return None
    return owner_pages.get(row.attrib.key[0])


def delta_or_unknown(b, a):
    """B - A, propagating unknown."""
    if b is None or a is None:
        return None
    return b - a


def type_of(attrib):
    """Identity-bearing type: (type_name, tuple_size)."""
    if attrib is None:
        return None
    return (attrib.type_name, getattr(attrib, "tuple_size", 0))


def pair(a, b, fmt=str):
    """One value when equal, "A → B" when they differ."""
    if a is None and b is None:
        return ""
    if a is None:
        return fmt(b)
    if b is None:
        return fmt(a)
    return fmt(a) if a == b else fmt(a) + ARROW + fmt(b)


class DiffRow:
    """One row of the joined tree."""

    __slots__ = ("label", "key", "scope", "status", "note", "order", "is_attrib",
                 "a_total", "b_total", "d_total", "d_new", "d_unique",
                 "d_pages", "d_csh", "data_id", "type_str", "children")

    def __init__(self, label, key, scope=None, note="", order=0, is_attrib=False):
        self.label = label
        self.order = order
        self.is_attrib = is_attrib
        self.key = key
        self.scope = scope
        self.note = note
        self.status = "same"
        self.a_total = self.b_total = None
        self.d_total = self.d_new = self.d_unique = 0
        self.d_pages = None
        self.d_csh = (None, None, None)
        self.data_id = ""
        self.type_str = ""
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, label):
        for row in self.walk():
            if row.label == label:
                return row
        return None

    def __repr__(self):
        return ("DiffRow(%r, %s, d_total=%d, children=%d)"
                % (self.label, self.status, self.d_total, len(self.children)))


def _join(row_a, row_b, path, pages_a, pages_b, is_either_side_instanced):
    src = row_b if row_b is not None else row_a
    diff_row = DiffRow(src.label, row_key(src, path), src.scope, src.note, src.order,
                       (row_a.attrib if row_a is not None else row_b.attrib) is not None)
    attrib_a = row_a.attrib if row_a is not None else None
    attrib_b = row_b.attrib if row_b is not None else None

    diff_row.a_total = row_a.total_memory if row_a is not None else None
    diff_row.b_total = row_b.total_memory if row_b is not None else None
    values_a = ((row_a.total_memory, row_a.new_memory or 0, row_a.unique_memory or 0)
                if row_a is not None else (0, 0, 0))
    values_b = ((row_b.total_memory, row_b.new_memory or 0, row_b.unique_memory or 0)
                if row_b is not None else (0, 0, 0))
    diff_row.d_total = values_b[0] - values_a[0]
    if is_either_side_instanced:
        diff_row.d_new = diff_row.d_unique = None
    else:
        diff_row.d_new, diff_row.d_unique = values_b[1] - values_a[1], values_b[2] - values_a[2]

    csh_a = (0, 0, 0) if row_a is None else constant_shared_hardened(attrib_a)
    csh_b = (0, 0, 0) if row_b is None else constant_shared_hardened(attrib_b)
    diff_row.d_csh = tuple(delta_or_unknown(y, x) for x, y in zip(csh_a, csh_b))
    page_count_a = 0 if row_a is None else page_count(row_a, pages_a)
    page_count_b = 0 if row_b is None else page_count(row_b, pages_b)
    diff_row.d_pages = delta_or_unknown(page_count_b, page_count_a)

    data_id_a = row_a.data_id if row_a is not None else None
    data_id_b = row_b.data_id if row_b is not None else None
    diff_row.data_id = pair(data_id_a, data_id_b, data_id_text)
    type_a = (row_a.type_str or None) if row_a is not None else None
    type_b = (row_b.type_str or None) if row_b is not None else None
    diff_row.type_str = pair(type_a, type_b)

    if row_a is None:
        diff_row.status = "added"
    elif row_b is None:
        diff_row.status = "removed"
    elif attrib_a is not None and attrib_b is not None and type_of(attrib_a) != type_of(attrib_b):
        diff_row.status = "replaced"
    elif (diff_row.d_total or diff_row.d_new or diff_row.d_unique or diff_row.d_pages
          or any(diff_row.d_csh) or ARROW in diff_row.type_str):
        diff_row.status = "changed"
    # A changed data_id alone does NOT retain a row — data ids are a per-detail
    # counter, so rebuilding renumbers every attribute.

    child_path = path + (src.label,)
    kids_a = {row_key(c, child_path): c for c in (row_a.children if row_a is not None else ())}
    seen = set()
    for c in (row_b.children if row_b is not None else ()):
        key = row_key(c, child_path)
        seen.add(key)
        diff_row.children.append(
            _join(kids_a.get(key), c, child_path, pages_a, pages_b, is_either_side_instanced))
    for c in (row_a.children if row_a is not None else ()):
        key = row_key(c, child_path)
        if key not in seen:
            diff_row.children.append(
                _join(c, None, child_path, pages_a, pages_b, is_either_side_instanced))
    return diff_row


def diff_models(model_a, model_b, scopes=None):
    """The unpruned joined tree, or None when either side has no breakdown."""
    if not model_a or not model_b:
        return None
    root_a = model_a.breakdown(scopes)
    root_b = model_b.breakdown(scopes)
    if root_a is None or root_b is None:
        return None
    pages_a = {o: model_a.owner_map(o).get("num_pages", 0) for o in OWNERS}
    pages_b = {o: model_b.owner_map(o).get("num_pages", 0) for o in OWNERS}
    # Either side instanced makes both New/Unique unknown.
    is_either_side_instanced = model_a.is_instanced or model_b.is_instanced
    return _join(root_a, root_b, (), pages_a, pages_b, is_either_side_instanced)


def prune(row):
    """Drop unchanged rows, keeping ancestors of anything that differs."""
    if row is None:
        return None
    row.children = [k for k in (prune(c) for c in row.children) if k is not None]
    if row.status != "same" or row.children:
        return row
    return None

