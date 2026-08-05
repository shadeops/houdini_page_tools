"""Model layer for the ``_page_tools`` memory report -- no Qt, no widgets.

This is the ONLY module that knows the report dict's keys (the schema is documented in
``SOP_Memory_Report.md``). ``panel.py`` renders what this produces and never
indexes the report itself.

The split exists because the accounting *is* the product here: which bytes belong to
which structure, and why the columns reconcile, is the whole subject of
``SOP_Memory_Analysis.md`` / ``SOP_Memory_Sources.md``. Keeping it out of a QTreeWidget
means it can be tested headlessly, without a QApplication (see tests/test_page_model.py).

* ``AttributeStats``  -- one attribute row.
* ``PrimitiveList``   -- the GA_PrimitiveList row; not an attribute, so not one of those.
* ``Row``         -- one node of the breakdown tree; pure data, no Qt.
* ``MemoryModel`` -- parses one report; ``breakdown()`` builds the tree.

Bytes stay ints throughout; byte *formatting* is the view's job.
"""

# Owner display order across the panel (tree owner groups + page-grid owner combo).
# Point first, then Primitive, as those are the most commonly inspected.
OWNERS = ("point", "primitive", "vertex", "detail")

# The four group tables GA_Detail owns (report key memory["group_tables"]); display
# order. There is NO detail table (getElementGroupTable takes only point/vertex/
# primitive) and NO breakpoint table. "edge" has no owner -- GA_EdgeGroup is not a
# GA_Attribute -- so it never appears as an attribute row, only here.
GROUP_TABLES = ("point", "primitive", "vertex", "edge")

# Display scopes: what colours a row and which toggle hides it.
#
# The first three are attribute scopes the report emits. The last two are VIEW-ONLY, with
# no counterpart in the report: "primitive_list" tags the one GA_PrimitiveList row, which
# the report keeps outside "owners" entirely, and "internal" tags the container/bookkeeping
# rows under Internal. The provider knows about neither.
SCOPES = ("public", "private", "group", "primitive_list", "internal")


def map_kind(owner_map):
    """State of a GA_IndexMap, for the Type column of its Index Maps row: trivial,
    monotonic or non-monotonic. What each means, and why non-monotonic on a cooked SOP is
    a red flag: SOP_Memory_Viewer.md section 4."""
    if owner_map.get("is_trivial"):
        return "trivial"
    return "monotonic" if owner_map.get("is_monotonic") else "non-monotonic"


class PrimTypeStats:
    """One entry of ``report["primitive_list"]["full_representation"]["primitive_types"]``:
    what one primitive TYPE (Poly / Volume / VDB / PackedGeometry / ...) contributes to
    that row."""

    __slots__ = ("name", "type_id", "count",
                 "total_memory", "new_memory", "unique_memory")

    def __init__(self, name, raw):
        self.name = name
        self.type_id = int(raw["type_id"])
        self.count = int(raw["count"])
        self.total_memory = int(raw["total_memory"])
        self.new_memory = int(raw["new_memory"])
        self.unique_memory = int(raw["unique_memory"])

    @property
    def count_label(self):
        return f"{self.count:,} prim" + ("" if self.count == 1 else "s")


class AttributeStats:
    """One ``report["owners"][owner]["attributes"][scope][name]`` entry."""

    __slots__ = ("owner", "scope", "name",
                 "total_memory", "new_memory", "unique_memory",
                 "intra_detail_sharing_memory", "shares_with_attrib_keys",
                 "has_memory_block_sharing", "memory_block_ids",
                 "shares_with_mapping_indices", "shares_with_mapping",
                 "data_id", "is_data_id_found_in_inputs", "is_tail_initialized",
                 "type_name", "tuple_size",
                 "has_page_details", "has_hardened_page_details",
                 "num_constant_pages", "num_shared_pages", "num_hardened_pages",
                 "num_constant_shared_pages")

    def __init__(self, owner, scope, name, raw):
        self.owner = owner
        self.scope = scope
        self.name = name
        self.total_memory = int(raw["total_memory"])
        self.new_memory = int(raw["new_memory"])
        self.unique_memory = int(raw["unique_memory"])
        # Bytes another attribute IN THIS DETAIL also references -- a COW sibling. Not the
        # same as sharing with an input (total - new): in the two-table case an attribute
        # can have every page shared upstream and 0 here. Every sharer reports the block in
        # full, so these do NOT sum to the bytes saved; the owner's aggregate is what
        # reconciles. Display only.
        self.intra_detail_sharing_memory = int(raw["intra_detail_sharing_memory"])
        # Peers as (owner, scope, name) triples, directly comparable against `key`.
        # Symmetric and complete: three attributes over one block each list the other two.
        self.shares_with_attrib_keys = [(p["owner"], p["scope"], p["name"])
                                for p in raw["shares_with_attrib_keys"]]
        # WHICH memory block each page is on, and who else is on it. None when the page
        # table cannot be read, or when no block of this attribute is reached from a second
        # page -- the nesting carries the condition, as with page_details.
        #
        # `memory_block_ids` is one uint32 per page. Two pages are on one allocation
        # exactly when their ids are equal and non-zero, across attributes and owners as
        # well as within one array; 0 means no other page in the detail reaches that
        # block, which is NOT the same as unshared (a page shared only with an input reads
        # 0). `shares_with_mapping_indices` is one uint32 per page indexing
        # `shares_with_mapping`, whose entries are LISTS of positions in
        # `shares_with_attrib_keys` -- one block can carry several attributes. Entry 0
        # is always empty.
        #
        # The relation is block identity, so it holds between pages at DIFFERENT indices
        # and between attributes on different owners.
        pmb = raw["memory_block_sharing"]
        self.has_memory_block_sharing = pmb is not None
        self.memory_block_ids = pmb["memory_block_ids"] if pmb else b""
        self.shares_with_mapping_indices = (
            pmb["shares_with_mapping_indices"] if pmb else b"")
        self.shares_with_mapping = (
            [list(e) for e in pmb["shares_with_mapping"]] if pmb else [])
        self.data_id = int(raw["data_id"])
        self.is_data_id_found_in_inputs = bool(raw["is_data_id_found_in_inputs"])
        # Registered with the detail's tail-initialize table -- the structure the
        # Unaccounted row IS. Only element groups with constant pages set it.
        self.is_tail_initialized = bool(raw.get("is_tail_initialized"))
        self.type_name = raw["type_name"]
        self.tuple_size = int(raw.get("tuple_size", 0))
        # page_details is a sub dict, or None when the attribute's pages cannot be READ
        # -- which is not the same as it having none. Array, blob and index-pair
        # attributes are page-backed with private page tables; only blind data and a
        # <primitive list> in full representation are genuinely pageless. The nesting
        # carries the condition, so nothing below is a "sometimes present" key.
        page_details = raw["page_details"]
        self.has_page_details = page_details is not None
        pd = page_details or {}
        # Whether the hardened/shared SPLIT is knowable. False for element groups and the
        # paged <primitive list>: those expose page constancy but no hardness, so their
        # non-constant pages are UNKNOWN. Reading "neither constant nor hardened" as
        # "shared" there would be a claim the provider never made.
        self.has_hardened_page_details = bool(pd.get("has_hardened_details", True))
        self.num_constant_pages = int(pd.get("num_constant_pages", 0))
        self.num_shared_pages = int(pd.get("num_shared_pages", 0))
        self.num_hardened_pages = int(pd.get("num_hardened_pages", 0))
        # A subset of num_constant_pages, not a fourth class: a constant page whose single
        # stored value is refcounted because it neither fits inline nor is zero. The page
        # API cannot see this, so the three counts above still read as a partition.
        self.num_constant_shared_pages = int(pd.get("num_constant_shared_pages", 0))
    @property
    def key(self):
        """Identity across refreshes and across nodes -- what the panel's bar toggle
        is keyed on, so a toggle survives a recook and a node switch."""
        return (self.owner, self.scope, self.name)

    @property
    def type_label(self):
        # A tail-initialized attribute is marked here rather than on the name: the name
        # column stays scannable, and this is a fact about the attribute's storage, which
        # is what this column is for.
        base = f"{self.type_name}[{self.tuple_size}]"
        return base + " (tail-init)" if self.is_tail_initialized else base

    @property
    def pages_label(self):
        """The Pages c/s/h cell. Keeps three slots so the column stays aligned and each
        number sits under its header letter."""
        if not self.has_page_details:
            return "-"
        if not self.has_hardened_page_details:
            return f"{self.num_constant_pages}/-/-"
        return f"{self.num_constant_pages}/{self.num_shared_pages}/{self.num_hardened_pages}"

    def total_label(self, fmt):
        """The Total Memory cell: the attribute's own bytes, plus how many of them a
        sibling attribute also references."""
        if not self.intra_detail_sharing_memory:
            return fmt(self.total_memory)
        return f"{fmt(self.total_memory)} ({fmt(self.intra_detail_sharing_memory)} shared)"

    @property
    def data_id_label(self):
        # Just the id. `is_data_id_found_in_inputs` -- this id was found on an input, so
        # this node did not change the values -- used to be spelled out here as a "(inh)"
        # suffix, which made the column noisy. The view renders the id ITALIC instead,
        # reading the flag directly.
        #
        # It stays an annotation on the Data ID and NOT on New: it is a statement about the
        # values, not the memory: a renamed or hardened copy keeps its source's data id
        # (so the flag is True) yet has New > 0.
        return str(self.data_id) if self.data_id >= 0 else "-"


class PrimitiveList:
    """``report["primitive_list"]`` -- the GA_PrimitiveList.

    Not a GA_Attribute, so it has no name, owner, scope or tuple size in the report, and
    none of the sharing fields: no block of it is ever reached from an attribute. The
    panel still draws it as a row under Primitive, which is why it carries the same label
    properties an AttributeStats does.
    """

    NAME = "<primitive list>"

    __slots__ = ("total_memory", "new_memory", "unique_memory",
                 "data_id", "is_data_id_found_in_inputs",
                 "is_full_representation", "prim_types",
                 "overhead_total_memory", "overhead_new_memory", "overhead_unique_memory",
                 "has_page_details", "has_hardened_page_details",
                 "num_constant_pages", "num_shared_pages", "num_hardened_pages",
                 "num_constant_shared_pages")

    def __init__(self, raw):
        raw = raw or {}
        self.total_memory = int(raw.get("total_memory", 0))
        self.new_memory = int(raw.get("new_memory", 0))
        self.unique_memory = int(raw.get("unique_memory", 0))
        self.data_id = int(raw.get("data_id", -1))
        self.is_data_id_found_in_inputs = bool(raw.get("is_data_id_found_in_inputs"))
        # The provider splits by primitive type ONLY in full representation -- a compact
        # list has no primitive objects to measure, and materialising them would perturb
        # the geometry it is measuring. The nesting carries that condition:
        # full_representation is None for a paged list, so nothing below is a
        # "sometimes present" key, and an empty prim_types cannot be read as "zero".
        self.is_full_representation = bool(raw.get("is_full_representation"))
        full = raw.get("full_representation") or {}
        self.prim_types = [PrimTypeStats(n, d)
                           for n, d in full.get("primitive_types", {}).items()]
        self.prim_types.sort(key=lambda t: (-t.total_memory, t.name))
        # What the list costs on top of the primitives it holds. The provider computes it
        # and deliberately does not break it down, so nothing here may describe what is
        # inside it -- see the primitive_list schema in src/_page_tools.C. The type rows
        # plus this row equal total_memory, which is the reason the field exists. 0 for a
        # paged list, where the primitives cannot be separated out at all.
        overhead = full.get("data_structure_overhead") or {}
        self.overhead_total_memory = int(overhead.get("total_memory", 0))
        self.overhead_new_memory = int(overhead.get("new_memory", 0))
        self.overhead_unique_memory = int(overhead.get("unique_memory", 0))

        page_details = raw.get("page_details")
        self.has_page_details = page_details is not None
        pd = page_details or {}
        self.has_hardened_page_details = bool(pd.get("has_hardened_details", False))
        self.num_constant_pages = int(pd.get("num_constant_pages", 0))
        self.num_shared_pages = int(pd.get("num_shared_pages", 0))
        self.num_hardened_pages = int(pd.get("num_hardened_pages", 0))
        self.num_constant_shared_pages = int(pd.get("num_constant_shared_pages", 0))

    def __bool__(self):
        """False for a report that has no primitive list at all."""
        return self.has_page_details or bool(self.prim_types) or self.total_memory > 0

    @property
    def name(self):
        return self.NAME

    @property
    def scope(self):
        """A VIEW-ONLY scope -- see SCOPES. The report gives this row no scope, because it
        is not in the attribute dict that scopes partition."""
        return "primitive_list"

    @property
    def key(self):
        """Shaped like AttributeStats.key so the panel can key a toggle on either."""
        return ("primitive", self.scope, self.NAME)

    @property
    def type_label(self):
        # Which way GA_PrimitiveList is storing its primitives, because that is what the
        # rest of the row means: PAGED gets page-class bars and no per-type split (there
        # are no primitive objects), FULL gets the split and no bars (there is no page
        # array). Reporting the bare "primitivelist" said nothing and left the reader to
        # infer the state from which columns happened to be filled.
        return ("full" if self.is_full_representation else "paged") + " primitive list"

    @property
    def pages_label(self):
        """The Pages c/s/h cell. A prim list never exposes hardness, so the last two are
        always unknown -- and "constant" here means equal-length contiguous vertex lists,
        not one repeated value (GA_PrimitiveList.h:162)."""
        if not self.has_page_details:
            return "-"
        return f"{self.num_constant_pages}/-/-"

    def total_label(self, fmt):
        """No sibling shares a block with it, so there is never a shared annotation."""
        return fmt(self.total_memory)

    @property
    def data_id_label(self):
        return str(self.data_id) if self.data_id >= 0 else "-"

    # The view draws this row in the same table as the attributes, so it answers the same
    # questions. These four are constants rather than report keys because the answer can
    # only ever be no: a GA_PrimitiveList cannot register a tail initializer (only element
    # groups do) and shares no memory block with anything in the detail, so it never
    # enters the block-sharing mode that reads the three fields behind that flag.
    is_tail_initialized = False
    has_memory_block_sharing = False
    intra_detail_sharing_memory = 0
    shares_with_attrib_keys = ()

    # The Type column sorts on this while displaying type_label. The report no longer
    # carries it -- with its own key there is no type to distinguish it from.
    type_name = "primitivelist"


class Row:
    """One node of the breakdown tree."""

    __slots__ = ("label", "total_memory", "new_memory", "unique_memory",
                 "order", "type_str", "data_id", "attr", "scope", "note", "collapsed",
                 "owner", "children")

    def __init__(self, label, total_memory, new_memory=None, unique_memory=None,
                 order=0, type_str="", data_id=None, attr=None, scope=None, note="",
                 collapsed=False, owner=None):
        self.label = label
        # Which GA owner a STRUCTURAL row is about ("point", "vertex", ...), on the owner
        # groups and the per-owner index maps. Attribute leaves carry it on `attr` instead.
        # It is data, not display: the owner's page count belongs to whoever asks, and
        # recovering it from a row LABEL would be a lookup by rendered text.
        self.owner = owner
        # Optional display-only annotation. NEVER structural: `label` stays the row's
        # identity (callers and tests key off it), and nothing is ever derived from this.
        self.note = note
        self.total_memory = int(total_memory)
        self.new_memory = None if new_memory is None else int(new_memory)
        self.unique_memory = None if unique_memory is None else int(unique_memory)
        self.order = order
        self.type_str = type_str
        self.data_id = data_id
        self.attr = attr
        # The row's DISPLAY scope: what colours it and which toggle hides it. Broader than
        # an attribute's scope, because structural rows have one too:
        #
        #   attribute leaves        -- attr.scope (public / private / group)
        #   the <primitive list>    -- "primitive_list" (not a GA_Attribute, and the
        #                              report gives it no scope of its own)
        #   edge-group rows         -- "group" (they ARE groups, even though GA_EdgeGroup
        #                              is not a GA_Attribute)
        #   rows UNDER Internal     -- "internal"
        #   everything else         -- None: never filtered, never coloured (the root, the
        #                              three categories, owner rows, the Edges section)
        self.scope = scope
        # Display-only: the view starts this row closed. Structural, never accounting --
        # a collapsed row's children are still in the tree and still counted.
        self.collapsed = collapsed
        self.children = []

    def add(self, child):
        self.children.append(child)
        return child

    def walk(self):
        """Depth-first over this row and every descendant."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, label):
        """First row in the subtree whose label matches (tests / lookups)."""
        for row in self.walk():
            if row.label == label:
                return row
        return None

    def __repr__(self):
        return (f"Row({self.label!r}, total={self.total_memory}, "
                f"new={self.new_memory}, unique={self.unique_memory}, "
                f"children={len(self.children)})")


def row_key(row, parent_path):
    """One breakdown row's IDENTITY, as a tuple. Two forms:

        ("attr", owner, scope, name)   attribute leaves, keyed like the bar toggles
        ("path", label, label, ...)    structural rows, by their position in the tree

    Structural rows have no key of their own, but ``Row.label`` IS their identity (`note`
    is display-only) and labels are unique among siblings, so the label path down from the
    root names the row. Attribute leaves take their attribute key instead: it is stable
    wherever the row ends up, and two attributes in one owner group can share a name across
    scopes, which a label path could not tell apart.

    It lives here rather than in the view because there are now two consumers -- the panel
    remembers a selection and a collapsed section by it, and the diff PAIRS ROWS BETWEEN TWO
    REPORTS by it. A second, separately-typed copy of an identity is a copy that drifts.
    """
    if row.attr is not None:
        return ("attr",) + tuple(row.attr.key)
    return ("path",) + tuple(parent_path) + (row.label,)


class MemoryModel:
    """One ``page_tools.report()`` dict, parsed."""

    def __init__(self, report):
        self._report = report or {}
        self._owners = self._report.get("owners", {})
        self._mem = self._report.get("memory", {})

        self.node = self._report.get("node_path", "")
        self.instanced = bool(self._report.get("is_instanced"))

        # The geometry total (and the % denominator): GU_Detail::countMemory()'s full
        # count -- the same measurement Houdini's own SOP info tree reports.
        #
        # The provider no longer emits a getMemoryUsage figure at all. It used to (as
        # "total_memory"), and this line used to fall back to it. That API is BLIND TO
        # ELEMENT-GROUP MEMORY -- verified against RSS, it reported 75 KB for a detail
        # holding 57 MB of point-group bitsets, which made the root read smaller than its
        # own children and drove Unaccounted negative. See SOP_Memory_Groups.md.
        self.total_memory = int(self._report.get("total_memory", 0))
        # Whole-geometry New / Unique: the authoritative detail-level counts (ONE
        # UT_MemoryCounterNewSafe pass over the whole detail), NOT a sum of the
        # per-attribute rows.
        self.new_memory = int(self._report.get("new_memory", 0))
        self.unique_memory = int(self._report.get("unique_memory", 0))
        # How many attributes are registered as tail initializers. NAMES the Unaccounted
        # row; never sizes it -- the count can be 0 with the table still allocated.
        self.num_tail_initializers = int(self._report.get("num_tail_initializers", 0))

        # Its own top-level key, not an entry of any owner's attribute dict: it is not a
        # GA_Attribute. The panel still shows it under Primitive -- see breakdown().
        self.primitive_list = PrimitiveList(self._report.get("primitive_list"))

        self._attrs = [AttributeStats(owner, scope, name, raw)
                       for owner in OWNERS
                       for scope, scope_attrs
                       in self._owners.get(owner, {}).get("attributes", {}).items()
                       for name, raw in scope_attrs.items()]
        # Bucketed once so an owner-filtered attrs() query (breakdown()'s per-owner loop
        # calls it once per owner) doesn't rescan every attribute across every owner.
        self._attrs_by_owner = {}
        for attr in self._attrs:
            self._attrs_by_owner.setdefault(attr.owner, []).append(attr)
        # A stable position per attribute key, detail-wide. The view indexes a palette
        # with it so an attribute keeps one colour across every owner and every
        # selection. Sorted rather than taken in report order so the position -- and so
        # the colour -- depends only on WHICH attributes exist, not on the order the
        # provider happened to walk them in.
        self._attr_positions = {key: i for i, key
                                in enumerate(sorted(a.key for a in self._attrs))}

    def __bool__(self):
        return bool(self._report)

    # -- raw sub-reports ----------------------------------------------------

    def owner_map(self, owner):
        """The index-map sub-report for one owner (page bitsets, per-page counts,
        block ranges). Feeds the view's DecodedOwner."""
        return self._owners.get(owner, {})

    # -- attributes ---------------------------------------------------------

    def attrs(self, owner=None, scopes=None):
        """Every attribute, optionally narrowed to one owner and/or a set of scopes."""
        source = self._attrs if owner is None else self._attrs_by_owner.get(owner, [])
        return [a for a in source if scopes is None or a.scope in scopes]

    def attr_position(self, key):
        """Index of one (owner, scope, name) key in the detail's stable attribute order,
        or None when no such attribute is in this report."""
        return self._attr_positions.get(tuple(key))

    def num_attr_positions(self):
        """How many keys `attr_position` can return -- the size a per-attribute palette
        has to be to cover the detail."""
        return len(self._attr_positions)

    def owner_attr_memory(self, owner):
        """The owner's attributes as three de-duplicated totals, straight from the
        provider."""
        o = self.owner_map(owner)
        return (int(o.get("attributes_total_memory", 0)),
                int(o.get("attributes_new_memory", 0)),
                int(o.get("attributes_unique_memory", 0)))

    # -- the breakdown ------------------------------------------------------

    def breakdown(self, scopes=None):
        """The tree the panel renders: Geometry Memory -> Attributes & Groups / Internal
        / Unaccounted, which sum to it EXACTLY in all three columns."""
        if not self._report:
            return None
        if scopes is None:
            scopes = set(SCOPES)
        mem = self._mem

        root = Row("Geometry Memory", self.total_memory, self.new_memory,
                   self.unique_memory)

        # --- Attributes & Groups -------------------------------------------------
        # The element data, grouped by owner. Element GROUPS belong here alongside
        # ordinary attributes because a GA_ElementGroup (== GA_ATIGroupBool) derives
        # from BOTH GA_Attribute and GA_Group: it shares the owner's GA_IndexMap and
        # the same page layout. The <primitive list> is drawn under Primitive but is NOT
        # an attribute -- the provider gives it its own term, so its bytes are added in
        # here the same way the edge groups' are. EDGE groups get their own section here
        # too (see below). The container bookkeeping that holds all of this lives under
        # Internal.
        attr_parent = Row("Attributes & Groups", 0, 0, 0, order=0)
        root.add(attr_parent)
        plist = self.primitive_list
        for owner_idx, owner in enumerate(OWNERS):
            attrs = self.attrs(owner)
            show_plist = owner == "primitive" and bool(plist)
            if not attrs and not show_plist:   # only owners with something to show
                continue
            # Reported, not summed. Summing the children over-counts any block two of
            # them share -- see owner_attr_memory(). Adding the prim list cannot
            # double-count: it is a separate term and shares no block with an attribute.
            owner_total, owner_new_mem, owner_unique_mem = self.owner_attr_memory(owner)
            if show_plist:
                owner_total += plist.total_memory
                owner_new_mem += plist.new_memory
                owner_unique_mem += plist.unique_memory
            owner_row = attr_parent.add(
                Row(owner.capitalize(), owner_total, owner_new_mem, owner_unique_mem,
                    order=owner_idx, owner=owner))
            for attr in attrs:
                if attr.scope in scopes:       # filter the ROWS, never the totals
                    owner_row.add(
                        Row(attr.name, attr.total_memory, attr.new_memory,
                            attr.unique_memory, type_str=attr.type_label,
                            data_id=attr.data_id, attr=attr, scope=attr.scope))
            if show_plist and plist.scope in scopes:
                plist_row = owner_row.add(
                    Row(plist.name, plist.total_memory, plist.new_memory,
                        plist.unique_memory, type_str=plist.type_label,
                        data_id=plist.data_id, attr=plist, scope=plist.scope,
                        # The per-type breakdown starts CLOSED: it is detail about one
                        # row, and on mixed geometry it is long.
                        collapsed=bool(plist.prim_types)))
                # Per primitive type, then what the list costs on top of them. Together
                # they sum to the parent EXACTLY in all three columns; the provider
                # computes the overhead, so nothing here is derived.
                for ti, ptype in enumerate(plist.prim_types):
                    plist_row.add(Row(ptype.name, ptype.total_memory,
                                      ptype.new_memory, ptype.unique_memory,
                                      order=ti, type_str=ptype.count_label,
                                      scope=plist.scope))
                # Last, and only when there are type rows to be overhead ON TOP OF: a
                # paged list reports 0 here because its primitives cannot be separated
                # out, and a lone "0" row would read as "this list has no overhead".
                if plist.prim_types:
                    plist_row.add(Row("Data Structure Overhead",
                                      plist.overhead_total_memory,
                                      plist.overhead_new_memory,
                                      plist.overhead_unique_memory,
                                      # No type_str: the report says what this figure is
                                      # WORTH, never what it is made of, and the view may
                                      # not fill that in from somewhere else.
                                      order=len(plist.prim_types),
                                      scope=plist.scope))

        # EDGES -- a section alongside the owners, even though "edge" is not a GA owner.
        # A GA_EdgeGroup is not a GA_Attribute (GA_Group only: no owner, no index map), so
        # the PROVIDER accounts for it inside the edge group TABLE. But it is real geometry
        # data -- often the largest thing in the tree -- not container bookkeeping, so it is
        # shown here with the other groups rather than buried under Internal. Its rows carry
        # scope "group": violet, and hidden by the groups toggle like any other group.
        #
        # The bytes move with the rows: Internal > Group Tables > Edge is reduced to the
        # table's name->group map (see below), so the two categories still sum to the total.
        edge_groups = sorted(mem.get("group_tables", {}).get("edge", {})
                             .get("groups", {}).items())
        edges_total = sum(int(g.get("total_memory", 0)) for _n, g in edge_groups)
        edges_new = sum(int(g.get("new_memory", 0)) for _n, g in edge_groups)
        edges_unique = sum(int(g.get("unique_memory", 0)) for _n, g in edge_groups)
        if edge_groups:
            edges_row = attr_parent.add(
                Row("Edges", edges_total, edges_new, edges_unique, order=len(OWNERS)))
            for gi, (gname, g) in enumerate(edge_groups):
                if "group" not in scopes:      # filter the ROWS, never the totals
                    continue
                edges_row.add(Row(gname,
                                  int(g.get("total_memory", 0)),
                                  int(g.get("new_memory", 0)),
                                  int(g.get("unique_memory", 0)),
                                  order=gi, type_str="edgegroup", scope="group"))

        # Totals over EVERY row, never the visible ones (see the docstring). The attribute
        # part is the provider's detail-wide de-duplicated figure -- NOT a sum of the owner
        # rows, which would double-count anything shared across owners. The primitive list
        # and the edge groups are not attributes and are accounted separately, so adding
        # them here is safe arithmetic.
        attr_parent.total_memory = (int(mem.get("attributes_total_memory", 0))
                                    + int(mem.get("primitive_list_total_memory", 0))
                                    + edges_total)
        attr_parent.new_memory = (int(mem.get("attributes_new_memory", 0))
                                  + int(mem.get("primitive_list_new_memory", 0))
                                  + edges_new)
        attr_parent.unique_memory = (int(mem.get("attributes_unique_memory", 0))
                                     + int(mem.get("primitive_list_unique_memory", 0))
                                     + edges_unique)

        # --- Internal ------------------------------------------------------------
        # The container / bookkeeping structures that hold the data above: the
        # GA_AttributeSet's own overhead, the GA_IndexMaps, the four group tables, and
        # the GU_Detail struct itself.
        internal = Row("Internal", 0, 0, 0, order=1)
        root.add(internal)

        # Attribute Set: the GA_AttributeSet container's own bookkeeping -- the part
        # attributable to no single attribute (~0.5 KB, scaling with the attribute COUNT
        # and with hash capacity). Like group_tables_*, the key is the CONTAINER's bytes;
        # its members are attributes_*_memory and the per-attribute rows.
        # The provider derives it, because knowing which scopes the subtraction must span
        # is schema knowledge: every scope INCLUDING group, since GA_AttributeSet's counter
        # walks group-scope attributes too, so the groups cancel and only the bookkeeping
        # is left. The <primitive list> is not a member -- it is the GA_PrimitiveList.
        # Subtracting the wrong set drives this row negative.
        # Every row UNDER Internal carries scope "internal": the toggle hides them all, and
        # they take the default text colour. The Internal row ITSELF is not tagged -- it is a
        # top-level category and always shows, with its total intact.
        aset_total = int(mem.get("attribute_set_total_memory", 0))
        aset_new = int(mem.get("attribute_set_new_memory", 0))
        aset_unique = int(mem.get("attribute_set_unique_memory", 0))
        show_internal = "internal" in scopes
        if show_internal:
            internal.add(Row("Attribute Set", aset_total, aset_new, aset_unique,
                             order=0, scope="internal"))

        # Index Maps + one child per owner (they sum exactly to the parent). New = bytes
        # not COW-shared with an input; Unique = refcount==1 bytes, the same block-level
        # count Houdini uses.
        im_total = int(mem.get("index_maps_total_memory", 0))
        im_new = int(mem.get("index_maps_new_memory", 0))
        im_unique = int(mem.get("index_maps_unique_memory", 0))
        if show_internal:
            im_parent = internal.add(Row("Index Maps", im_total, im_new, im_unique,
                                         order=1, scope="internal"))
            for oi, owner in enumerate(OWNERS):
                imo = self.owner_map(owner)
                # The Type column describes the map's compactness -- which is exactly what
                # its memory measures. A trivial map (offset == index) is implicit and owns
                # NO heap, so it reads 0 B; only holes/fragmentation force a real allocation.
                # (Its 112 B sizeof lives in the Detail Object row, not here.)
                im_parent.add(Row(owner.capitalize(),
                                  int(imo.get("index_map_total_memory", 0)),
                                  int(imo.get("index_map_new_memory", 0)),
                                  int(imo.get("index_map_unique_memory", 0)),
                                  order=oi, type_str=map_kind(imo), scope="internal",
                                  owner=owner))

        # Group Tables: the four GA_Detail owns (point / primitive / vertex element group
        # tables + the edge group table). ALL FOUR are map overhead only here.
        #
        # For point/primitive/vertex that is what the table holds anyway -- the group bitsets
        # are the group-scope attribute rows above.
        #
        # For EDGE the provider's table total also includes the edge groups themselves
        # (GA_EdgeGroup is not an attribute, so the table owns them). Those bytes are shown
        # under Attributes & Groups > Edges instead, so this row is reduced to the table's
        # name->group map, which the provider reports as name_map_*_memory. The bytes move
        # with the rows: Attributes & Groups gains exactly what Group Tables gives up, and the
        # categories still sum to the total.
        gt = mem.get("group_tables", {})
        edge_tbl = gt.get("edge", {})
        edge_tbl_total = int(edge_tbl.get("total_memory", 0))
        edge_name_map = int(edge_tbl.get("name_map_total_memory", 0))
        edge_name_map_new = int(edge_tbl.get("name_map_new_memory", 0))
        edge_name_map_unique = int(edge_tbl.get("name_map_unique_memory", 0))

        # Group Tables' totals EXCLUDE the edge groups, which now live under Attributes.
        gt_total = int(mem.get("group_tables_total_memory", 0)) - edges_total
        gt_new_mem = int(mem.get("group_tables_new_memory", 0)) - edges_new
        gt_unique_mem = int(mem.get("group_tables_unique_memory", 0)) - edges_unique
        if show_internal and (gt_total or gt_new_mem or gt_unique_mem):
            gt_parent = internal.add(
                Row("Group Tables", gt_total, gt_new_mem, gt_unique_mem,
                    order=2, scope="internal"))
            for table_idx, tname in enumerate(GROUP_TABLES):
                table = gt.get(tname, {})
                if tname == "edge":            # the map only -- the groups moved out
                    table_total, table_new, table_unique = (
                        edge_name_map, edge_name_map_new, edge_name_map_unique)
                else:
                    table_total = int(table.get("total_memory", 0))
                    table_new = int(table.get("new_memory", 0))
                    table_unique = int(table.get("unique_memory", 0))
                if not (table_total or table_new or table_unique):
                    continue                   # skip empty tables
                gt_parent.add(Row(tname.capitalize(), table_total, table_new, table_unique,
                                  order=table_idx, scope="internal"))

        # Detail Object: sizeof(GU_Detail) -- the detail's own C++ struct, with every
        # contained member's sizeof inside it. The whole-detail counter (inclusive=true)
        # adds it exactly once and every category above is counted inclusive=false (heap
        # only), so this is precisely the leftover -- but it is a KNOWN, exact quantity
        # (1496 B), so it gets its own row instead of being dumped into Unaccounted.
        # New/Unique: the provider zeroes them when the detail is instanced (the node
        # allocated nothing of its own).
        ds_total = int(mem.get("gu_detail_total_memory", 0))
        ds_new = int(mem.get("gu_detail_new_memory", 0))
        ds_unique = int(mem.get("gu_detail_unique_memory", 0))
        if show_internal and ds_total:
            internal.add(Row("Detail Object", ds_total, ds_new, ds_unique,
                             order=3, scope="internal"))

        # Internal's totals come from the VALUES, never from summing internal.children --
        # those children are filterable now, and summing them would zero this row the moment
        # the "internal" toggle is switched off. That is the exact bug this project already
        # hit once (totals summed from the VISIBLE rows, with Unaccounted silently swallowing
        # the difference). Guarded by test_scope_filter_does_not_change_accounting.
        internal.total_memory = aset_total + im_total + gt_total + ds_total
        internal.new_memory = aset_new + im_new + gt_new_mem + ds_new
        internal.unique_memory = aset_unique + im_unique + gt_unique_mem + ds_unique

        # --- Unaccounted ---------------------------------------------------------
        # The remainder in each column, TAKEN FROM THE PROVIDER -- not re-derived here.
        # The provider owns the accounting; this module renders it. Subtracting for
        # ourselves would make the three columns sum to the root *by construction*,
        # which sounds reassuring but is the opposite: any error in the provider would
        # be silently swept into this row instead of showing up. Consuming `residual*`
        # means the sum is a real, falsifiable claim -- and it is what the tests assert.
        #
        # What lands here is the ga_TailInitializeTable (GA_Detail::myTailInitializers).
        # That is now verified, not inferred: GA_Detail::countMemory counts sizeof, the 4
        # index maps, the prim list, the attribute set, the 4 group tables, GA_Topology and
        # then this table -- and GA_Topology::countMemory adds NOTHING when called with
        # inclusive=false (which is how the detail calls it), so this is the only term left.
        # It is PRIVATE with no size getter, so it can only ever be a remainder.
        #
        # `num_tail_initializers` names it but must NOT gate it: the table's per-owner bucket
        # array outlives the last registration, so a Group Delete leaves the count at 0 with
        # ~6.5 KB still allocated. Label off the COUNT, never the bytes.
        # See SOP_Memory_Sources.md §4.
        # The LABEL stays exactly "Unaccounted" -- it is the row's structural identity and
        # the tests key the categories off it. The count rides along as `note`, a pure
        # display annotation the view may append.
        note = ""
        if self.num_tail_initializers:
            note = "tail initializers: %d" % self.num_tail_initializers
        root.add(Row("Unaccounted",
                     int(mem.get("residual_total_memory", 0)),
                     int(mem.get("residual_new_memory", 0)),
                     int(mem.get("residual_unique_memory", 0)),
                     order=2, note=note))
        return root
