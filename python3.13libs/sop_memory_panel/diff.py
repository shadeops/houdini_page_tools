"""Two MemoryModel breakdowns joined into a tree of differences. No Qt.

Rules: the delta is B - A over provider-measured figures, never re-summed from
children. Unknown on either side makes the slot unknown. An instanced side
empties New and Unique (the provider zeroes them — "does not apply", not "zero").
"""

from .model import OWNERS, PrimitiveList, row_key

ARROW = " → "


def constant_shared_hardened(attr):
    """(constant, shared, hardened) page counts, with None for unmeasurable slots.

    No page_details at all (array/blob/index-pair): all three unknown.
    has_hardened_details false (element groups, paged prim list): only constant known.
    """
    if attr is None or not attr.has_page_details:
        return (None, None, None)
    if isinstance(attr, PrimitiveList) or not attr.has_hardened_page_details:
        return (attr.num_constant_pages, None, None)
    return (attr.num_constant_pages, attr.num_shared_pages, attr.num_hardened_pages)


def page_count(row, owner_pages):
    """How many pages this row's data spans, or None when not applicable.

    From the index map's num_pages, NEVER the c/s/h sum — those only partition
    when has_hardened_details is true.
    """
    if row is None:
        return None
    if row.owner is not None:
        return owner_pages.get(row.owner)
    if row.attr is None or not row.attr.has_page_details:
        return None
    return owner_pages.get(row.attr.key[0])


def delta_or_unknown(b, a):
    """B - A, propagating unknown."""
    if b is None or a is None:
        return None
    return b - a


def type_of(attr):
    """Identity-bearing type: (type_name, tuple_size)."""
    if attr is None:
        return None
    return (attr.type_name, getattr(attr, "tuple_size", 0))


def pair(a, b, fmt=str):
    """One value when equal, "A → B" when they differ."""
    if a is None and b is None:
        return ""
    if a is None:
        return fmt(b)
    if b is None:
        return fmt(a)
    return fmt(a) if a == b else fmt(a) + ARROW + fmt(b)


def _data_id(value):
    return str(value) if value >= 0 else "-"


class DiffRow:
    """One row of the joined tree."""

    __slots__ = ("label", "key", "scope", "status", "depth", "note", "order", "is_attr",
                 "a_total", "b_total", "d_total", "d_new", "d_unique",
                 "d_pages", "d_csh", "data_id", "type_str", "children")

    def __init__(self, label, key, scope=None, note="", order=0, is_attr=False):
        self.label = label
        self.order = order
        self.is_attr = is_attr
        self.key = key
        self.scope = scope
        self.note = note
        self.status = "same"
        self.depth = 0
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


def _join(row_a, row_b, path, pages_a, pages_b, instanced):
    src = row_b if row_b is not None else row_a
    out = DiffRow(src.label, row_key(src, path), src.scope, src.note,
                  src.order, (row_a.attr if row_a is not None else row_b.attr) is not None)
    attr_a = row_a.attr if row_a is not None else None
    attr_b = row_b.attr if row_b is not None else None

    out.a_total = row_a.total_memory if row_a is not None else None
    out.b_total = row_b.total_memory if row_b is not None else None
    values_a = ((row_a.total_memory, row_a.new_memory or 0, row_a.unique_memory or 0)
                if row_a is not None else (0, 0, 0))
    values_b = ((row_b.total_memory, row_b.new_memory or 0, row_b.unique_memory or 0)
                if row_b is not None else (0, 0, 0))
    out.d_total = values_b[0] - values_a[0]
    if instanced:
        out.d_new = out.d_unique = None
    else:
        out.d_new, out.d_unique = values_b[1] - values_a[1], values_b[2] - values_a[2]

    csh_a = (0, 0, 0) if row_a is None else constant_shared_hardened(attr_a)
    csh_b = (0, 0, 0) if row_b is None else constant_shared_hardened(attr_b)
    out.d_csh = tuple(delta_or_unknown(y, x) for x, y in zip(csh_a, csh_b))
    page_count_a = 0 if row_a is None else page_count(row_a, pages_a)
    page_count_b = 0 if row_b is None else page_count(row_b, pages_b)
    out.d_pages = delta_or_unknown(page_count_b, page_count_a)

    # Paged Vertex List shares its .attr with the Primitive List branch root, which is
    # where that data id is shown instead -- data_id_hidden marks this row as the one
    # to blank rather than read from the shared attr.
    if (row_a is not None and row_a.data_id_hidden) or (row_b is not None and row_b.data_id_hidden):
        out.data_id = ""
    else:
        data_id_a = attr_a.data_id if attr_a is not None else (row_a.data_id if row_a is not None else None)
        data_id_b = attr_b.data_id if attr_b is not None else (row_b.data_id if row_b is not None else None)
        out.data_id = pair(data_id_a, data_id_b, _data_id)
    # type_hidden mirrors data_id_hidden above -- the same Paged Vertex List row also
    # blanks its Type column, since the representation label is shown on the root.
    if (row_a is not None and row_a.type_hidden) or (row_b is not None and row_b.type_hidden):
        out.type_str = ""
    else:
        type_a = attr_a.type_label if attr_a is not None else (row_a.type_str or None if row_a is not None else None)
        type_b = attr_b.type_label if attr_b is not None else (row_b.type_str or None if row_b is not None else None)
        out.type_str = pair(type_a, type_b)

    if row_a is None:
        out.status = "added"
    elif row_b is None:
        out.status = "removed"
    elif attr_a is not None and attr_b is not None and type_of(attr_a) != type_of(attr_b):
        out.status = "replaced"
    elif (out.d_total or out.d_new or out.d_unique or out.d_pages
          or any(out.d_csh) or ARROW in out.type_str):
        out.status = "changed"
    # A changed data_id alone does NOT retain a row — data ids are a per-detail
    # counter, so rebuilding renumbers every attribute.

    child_path = path + (src.label,)
    kids_a = {row_key(c, child_path): c for c in (row_a.children if row_a is not None else ())}
    seen = set()
    for c in (row_b.children if row_b is not None else ()):
        key = row_key(c, child_path)
        seen.add(key)
        out.children.append(
            _join(kids_a.get(key), c, child_path, pages_a, pages_b, instanced))
    for c in (row_a.children if row_a is not None else ()):
        key = row_key(c, child_path)
        if key not in seen:
            out.children.append(
                _join(c, None, child_path, pages_a, pages_b, instanced))
    return out


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
    instanced = model_a.instanced or model_b.instanced
    return set_depth(_join(root_a, root_b, (), pages_a, pages_b, instanced))


def prune(row):
    """Drop unchanged rows, keeping ancestors of anything that differs."""
    if row is None:
        return None
    row.children = [k for k in (prune(c) for c in row.children) if k is not None]
    if row.status != "same" or row.children:
        return row
    return None


def set_depth(row, depth=0):
    if row is not None:
        row.depth = depth
        for child in row.children:
            set_depth(child, depth + 1)
    return row
