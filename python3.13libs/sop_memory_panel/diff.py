"""Two ``MemoryModel`` breakdown trees, joined into one tree of differences. NO Qt.

    from sop_memory_panel.diff import diff_models, prune
    root = prune(diff_models(MemoryModel(report_a), MemoryModel(report_b)))

Rows are paired by ``model.row_key`` -- the same identity the panel already remembers a
selection and a collapsed section by. That is what makes the join free: an attribute's
(owner, scope, name) is stable across reports, so two SOPs' attributes match up with no
positional guessing.

NEW AND UNIQUE DO NOT APPLY TO AN INSTANCED DETAIL, on either side: the provider zeroes them
because the node owns nothing, so a delta taken from them means nothing. Those columns come
back as None -- the same dash an unreadable page table gets.

THE DELTA IS THE ONLY THING COMPUTED HERE, and it is B - A over two figures the provider
measured. Nothing is re-summed from children and nothing is summed over the rows that
survive pruning -- the same rule model.py works under, for the same reason: an owner row
legitimately reports less than its children add up to, because it counts a shared block
once and each sharer reports it in full.

A DELTA AGAINST AN UNKNOWN IS UNKNOWN, NEVER ZERO. The provider leaves the page split
unmeasurable in two distinct ways, and both have to survive the subtraction; see csh().
"""

from .model import OWNERS, PrimitiveList, row_key

# Between two paired values. Display only -- nothing parses these back.
ARROW = " → "


def csh(attr):
    """One row's (constant, shared, hardened) page counts, with None wherever the provider
    did not measure the number.

    Two different absences, which is why this cannot return zeros:

      * no page_details at all -- array, blob and index-pair attributes have private page
        tables, so nothing about their pages is readable. All three slots are unknown.
      * has_hardened_details false -- element groups and a paged primitive list expose page
        CONSTANCY but not hardness, so their non-constant pages are unknown rather than
        shared. Only the first slot is known.

    Mirrors AttributeStats.pages_label, which draws exactly this distinction as "-".
    """
    if attr is None or not attr.has_page_details:
        return (None, None, None)
    # A GA_PrimitiveList never exposes hardness either, and reports it through the class
    # rather than the flag -- PrimitiveList.pages_label is likewise hardcoded to "c/-/-".
    if isinstance(attr, PrimitiveList) or not attr.has_hardened_page_details:
        return (attr.num_constant_pages, None, None)
    return (attr.num_constant_pages, attr.num_shared_pages, attr.num_hardened_pages)


def page_count(row, owner_pages):
    """How many pages this row's data spans, or None when the row is not about pages.

    Straight from the index map's `num_pages`, NEVER the c/s/h sum. Those three counts only
    partition the pages when has_hardened_details is true -- a 4-page owner reports 2/0/0
    for a group, the other two pages being unknown -- so summing them understates, and
    understates silently.

    Two kinds of row have one. An OWNER row (a group under Attributes & Groups, or its
    index map under Internal) is about the map itself, which is where a page count belongs.
    An attribute row takes its owner's count, because that is what an attribute's pages ARE
    -- it repeats down the group, and that repetition is what lets each row's Δ c/s/h be
    checked against it in place.
    """
    if row is None:
        return None
    if row.owner is not None:
        return owner_pages.get(row.owner)
    if row.attr is None or not row.attr.has_page_details:
        return None
    return owner_pages.get(row.attr.key[0])


def sub(b, a):
    """B - A, propagating unknown: either side unknown makes the answer unknown."""
    if b is None or a is None:
        return None
    return b - a


def type_of(attr):
    """The identity-bearing part of an attribute's type.

    Tuple size is in it because numeric[3] -> numeric[1] is a different attribute too, and
    is_tail_initialized is NOT: that is a fact about how the attribute is filled, not about
    what it is, and it rides on type_label for display.
    """
    if attr is None:
        return None
    return (attr.type_name, getattr(attr, "tuple_size", 0))


def pair(a, b, fmt=str):
    """One value when the sides agree, "A → B" when they differ, the lone value when only
    one side has it."""
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
    """One row of the joined tree.

    `a_total` / `b_total` are not columns -- the view shows deltas only -- but they are the
    tooltip on the delta cell, which is where an absolute belongs once it stops being a
    column of its own.
    """

    __slots__ = ("label", "key", "scope", "status", "depth", "note", "order", "is_attr",
                 "a_total", "b_total", "d_total", "d_new", "d_unique",
                 "d_pages", "d_csh", "data_id", "type_str", "children")

    def __init__(self, label, key, scope=None, note="", order=0, is_attr=False):
        self.label = label
        # Carried from Row so the view can keep the structural rows in their canonical
        # order under any sort, exactly as the single-node report does: the three
        # categories and the owner groups are the shape of the breakdown, not data to rank.
        self.order = order
        # Whether this row IS an attribute (or the <primitive list>), as opposed to a
        # structural row. The view sorts one and pins the other.
        self.is_attr = is_attr
        # The identity the row was PAIRED on, carried through so the view keys a selection
        # and a collapsed section off the same value the join used. Recomputing it from the
        # label path in the view would lose the attribute form, and two attributes in one
        # owner can share a name across scopes.
        self.key = key
        self.scope = scope
        # Display-only annotation carried across from Row.note (Unaccounted's
        # tail-initializer count). Never structural: `label` is still the row's identity.
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
        """Depth-first over this row and every descendant."""
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


def _join(ra, rb, path, pages_a, pages_b, instanced):
    src = rb if rb is not None else ra
    out = DiffRow(src.label, row_key(src, path), src.scope, src.note,
                  src.order, (ra.attr if ra is not None else rb.attr) is not None)
    aa = ra.attr if ra is not None else None
    ab = rb.attr if rb is not None else None

    out.a_total = ra.total_memory if ra is not None else None
    out.b_total = rb.total_memory if rb is not None else None
    # A side that is not there contributes zero, which is a measurement: the attribute does
    # not exist, so it holds no bytes. That is unrelated to the unknowns below.
    za = ((ra.total_memory, ra.new_memory or 0, ra.unique_memory or 0)
          if ra is not None else (0, 0, 0))
    zb = ((rb.total_memory, rb.new_memory or 0, rb.unique_memory or 0)
          if rb is not None else (0, 0, 0))
    out.d_total = zb[0] - za[0]
    if instanced:
        # An instanced detail IS another node's, so the provider zeroes its New and Unique
        # on every row -- the node allocated nothing and owns nothing. Those zeroes are
        # "does not apply", not measurements, and subtracting them yields a number that
        # means nothing at all. Dash, on the same rule as an unreadable page table: the
        # column has no answer, and a 0 would claim one.
        out.d_new = out.d_unique = None
    else:
        out.d_new, out.d_unique = zb[1] - za[1], zb[2] - za[2]

    ca = (0, 0, 0) if ra is None else csh(aa)
    cb = (0, 0, 0) if rb is None else csh(ab)
    out.d_csh = tuple(sub(y, x) for x, y in zip(ca, cb))
    pa = 0 if ra is None else page_count(ra, pages_a)
    pb = 0 if rb is None else page_count(rb, pages_b)
    out.d_pages = sub(pb, pa)

    # Structural rows carry a data id and a type through Row rather than an attribute --
    # the index-map rows put "trivial" / "monotonic" in type_str, which is where a map
    # going fragmented becomes visible.
    ida = aa.data_id if aa is not None else (ra.data_id if ra is not None else None)
    idb = ab.data_id if ab is not None else (rb.data_id if rb is not None else None)
    out.data_id = pair(ida, idb, _data_id)
    ta = aa.type_label if aa is not None else (ra.type_str or None if ra is not None else None)
    tb = ab.type_label if ab is not None else (rb.type_str or None if rb is not None else None)
    out.type_str = pair(ta, tb)

    if ra is None:
        out.status = "added"
    elif rb is None:
        out.status = "removed"
    elif aa is not None and ab is not None and type_of(aa) != type_of(ab):
        # Same (owner, scope, name), different attribute entirely -- numeric in A, string
        # in B. Kept PAIRED rather than split into a removed row and an added one: the name
        # persisting is the thing a reader is scanning for, and splitting hides it.
        out.status = "replaced"
    elif (out.d_total or out.d_new or out.d_unique or out.d_pages
          or any(out.d_csh) or ARROW in out.type_str):
        out.status = "changed"
    # A changed data_id does NOT reach that test. Data ids are a per-detail counter, so any
    # node that rebuilds its geometry renumbers every attribute in it -- on grid -> blast
    # that kept six rows whose memory, pages and page split were all identical. The column
    # still shows "A → B" on rows kept for a real reason; it just cannot keep one itself.

    child_path = path + (src.label,)
    kids_a = {row_key(c, child_path): c for c in (ra.children if ra is not None else ())}
    seen = set()
    # B's order, so the tree reads as the geometry the user is looking AT; A-only rows are
    # appended after, under the parent they belonged to.
    for c in (rb.children if rb is not None else ()):
        key = row_key(c, child_path)
        seen.add(key)
        out.children.append(
            _join(kids_a.get(key), c, child_path, pages_a, pages_b, instanced))
    for c in (ra.children if ra is not None else ()):
        key = row_key(c, child_path)
        if key not in seen:
            out.children.append(
                _join(c, None, child_path, pages_a, pages_b, instanced))
    return out


def diff_models(model_a, model_b, scopes=None):
    """The UNPRUNED joined tree, or None when either side has no breakdown.

    `scopes` goes to both breakdowns, never one: a row hidden on one side only would read
    as removed. It hides rows and changes no total, exactly as it does for one model, so a
    parent's delta still accounts for children the filter dropped.
    """
    if not model_a or not model_b:
        return None
    root_a = model_a.breakdown(scopes)
    root_b = model_b.breakdown(scopes)
    if root_a is None or root_b is None:
        return None
    pages_a = {o: int(model_a.owner_map(o).get("num_pages", 0)) for o in OWNERS}
    pages_b = {o: int(model_b.owner_map(o).get("num_pages", 0)) for o in OWNERS}
    # Either side instanced is enough: the delta needs both, and one side reporting "does
    # not apply" leaves nothing to subtract from.
    instanced = bool(model_a.instanced or model_b.instanced)
    return set_depth(_join(root_a, root_b, (), pages_a, pages_b, instanced))


def prune(row):
    """Drop rows with no difference, keeping the tree that leads to the ones that have.

    A row survives when it differs OR when any descendant does. An ancestor whose own delta
    is zero still survives: two children can offset (+5 KB and -5 KB) and the path down to
    them has to exist. Returns None when nothing anywhere differs.
    """
    if row is None:
        return None
    row.children = [k for k in (prune(c) for c in row.children) if k is not None]
    if row.status != "same" or row.children:
        return row
    return None


def set_depth(row, depth=0):
    """Stamp each row with its depth, for the view's indent. Re-run after prune(): dropping
    a row never changes a survivor's depth today, but nothing here guarantees that."""
    if row is not None:
        row.depth = depth
        for child in row.children:
            set_depth(child, depth + 1)
    return row
