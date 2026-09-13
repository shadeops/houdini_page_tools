"""Accounting model for the ``_page_tools`` memory report — no Qt.

The only module that reads the report dict's keys (schema: ``SOP_Memory_Report.md``).
``panel.py`` renders what this produces and never indexes the report itself.

Classes: ``AttributeStats``, ``PrimitiveList``, ``Row``, ``MemoryModel``.
"""

# Point first, then Primitive — the most commonly inspected.
OWNERS = ("point", "primitive", "vertex", "detail")

# The four group tables GA_Detail owns. "edge" has no GA owner — GA_EdgeGroup is
# GA_Group only, not GA_Attribute — so it never appears as an attribute row.
GROUP_TABLES = ("point", "primitive", "vertex", "edge")

# One checkbox each, in this order (matches the root branches they belong to):
# "attribute_set" gates Attribute Set's owner rows and Data Structure Overhead;
# "public"/"private"/"group" are attribute scopes the report emits, filtering
# individual leaves WITHIN those owner rows; the last three are VIEW-ONLY branch
# tokens the provider knows nothing about, each gating one root branch's CHILDREN
# (never the branch row itself). "edge_group" is deliberately absent — it colours a
# row without ever hiding one.
SCOPES = ("attribute_set", "public", "private", "group", "primitive_list",
          "index_maps", "group_tables")

# Root branches under "Geometry Memory", in display order — one per
# GA_Detail::countMemory() contributor. Each _build_* method takes its own order= from
# this tuple, so reordering the tree is a one-line edit here.
ROOT_BRANCHES = ("attribute_set", "primitive_list", "index_maps", "group_tables",
                 "detail_object", "unaccounted")


def map_kind(owner_map):
    """Index map state for the Type column: trivial, monotonic, or non-monotonic."""
    if owner_map.get("is_trivial"):
        return "trivial"
    return "monotonic" if owner_map.get("is_monotonic") else "non-monotonic"


def counts(memory):
    """A report node's ``memory`` dict -> (total, new, unique). Every node in the
    report carries its figure this way, so this one accessor reads any of them."""
    return (memory.get("total", 0), memory.get("new", 0), memory.get("unique", 0))


class PrimTypeStats:
    """One primitive TYPE's contribution to the primitive list row."""

    __slots__ = ("name", "count",
                 "total_memory", "new_memory", "unique_memory")

    def __init__(self, name, raw):
        self.name = name
        self.count = raw["count"]
        self.total_memory, self.new_memory, self.unique_memory = counts(raw["memory"])

    @property
    def count_label(self):
        return f"{self.count:,} prim" + ("" if self.count == 1 else "s")


class AttributeStats:
    """One ``report["attribute_set"]["owners"][owner]["attributes"][scope][name]``
    entry."""

    __slots__ = ("owner", "scope", "name",
                 "total_memory", "new_memory", "unique_memory",
                 "intra_detail_memory_sharing", "shares_with_attrib_keys",
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
        self.total_memory, self.new_memory, self.unique_memory = counts(raw["memory"])
        # Every sharer reports the block in full, so these do NOT sum to bytes
        # saved; the owner's aggregate is what reconciles.
        self.intra_detail_memory_sharing = raw["intra_detail_memory_sharing"]
        self.shares_with_attrib_keys = [(p["owner"], p["scope"], p["name"])
                                for p in raw["shares_with_attrib_keys"]]
        # memory_block_ids: one uint32 per page. Two pages on one allocation
        # exactly when their ids are equal and non-zero; 0 means no other page
        # in the detail reaches that block (NOT the same as unshared).
        block_sharing = raw["memory_block_sharing"]
        self.has_memory_block_sharing = block_sharing is not None
        self.memory_block_ids = block_sharing["memory_block_ids"] if block_sharing else b""
        self.shares_with_mapping_indices = (
            block_sharing["shares_with_mapping_indices"] if block_sharing else b"")
        self.shares_with_mapping = (
            [list(e) for e in block_sharing["shares_with_mapping"]] if block_sharing else [])
        self.data_id = raw["data_id"]
        self.is_data_id_found_in_inputs = raw["is_data_id_found_in_inputs"]
        self.is_tail_initialized = raw["is_tail_initialized"]
        self.type_name = raw["type_name"]
        self.tuple_size = raw.get("tuple_size", 0)
        # None when the attribute's pages cannot be read (array, blob,
        # index-pair) — not the same as having no pages.
        page_details = raw["page_details"]
        self.has_page_details = page_details is not None
        page_detail = page_details or {}
        # False for element groups and the paged primitive list: they expose
        # constancy but not hardness, so non-constant pages are UNKNOWN.
        self.has_hardened_page_details = page_detail.get("has_hardened_details", True)
        self.num_constant_pages = page_detail.get("num_constant_pages", 0)
        self.num_shared_pages = page_detail.get("num_shared_pages", 0)
        self.num_hardened_pages = page_detail.get("num_hardened_pages", 0)
        # Subset of num_constant_pages: a constant page whose stored value is
        # refcounted because it is too wide for inline storage.
        self.num_constant_shared_pages = page_detail.get("num_constant_shared_pages", 0)

    @property
    def key(self):
        return (self.owner, self.scope, self.name)

    @property
    def type_label(self):
        base = (self.type_name if self.tuple_size == 1
                else f"{self.type_name}[{self.tuple_size}]")
        return base + " (tail-init)" if self.is_tail_initialized else base

    @property
    def pages_label(self):
        if not self.has_page_details:
            return "-"
        if not self.has_hardened_page_details:
            return f"{self.num_constant_pages}/-/-"
        return f"{self.num_constant_pages}/{self.num_shared_pages}/{self.num_hardened_pages}"

    def total_label(self, fmt):
        if not self.intra_detail_memory_sharing:
            return fmt(self.total_memory)
        return f"{fmt(self.total_memory)} ({fmt(self.intra_detail_memory_sharing)} shared)"

    @property
    def data_id_label(self):
        # is_data_id_found_in_inputs is about the VALUES, not the memory: a
        # hardened copy keeps its source's data id yet has New > 0. The view
        # renders the flag as italic, not as a suffix.
        return str(self.data_id) if self.data_id >= 0 else "-"


class PrimitiveList:
    """The GA_PrimitiveList row — not a GA_Attribute, so it has no owner, scope, or
    tuple size. Carries the same label properties as AttributeStats so the panel can
    draw it in the same table."""

    NAME = "Primitive List"

    __slots__ = ("total_memory", "new_memory", "unique_memory",
                 "data_id", "is_data_id_found_in_inputs",
                 "is_full_representation", "prim_types",
                 "overhead_total_memory", "overhead_new_memory", "overhead_unique_memory",
                 "has_page_details", "has_hardened_page_details",
                 "num_constant_pages", "num_shared_pages", "num_hardened_pages",
                 "num_constant_shared_pages")

    def __init__(self, raw):
        raw = raw or {}
        self.total_memory, self.new_memory, self.unique_memory = counts(
            raw.get("memory", {}))
        self.data_id = raw.get("data_id", -1)
        self.is_data_id_found_in_inputs = raw.get("is_data_id_found_in_inputs", False)
        # data_structure_overhead/primitive_types are None for a paged list — there are
        # no primitive objects to measure, and materialising them would perturb the
        # geometry.
        self.is_full_representation = raw.get("is_full_representation", False)
        self.prim_types = [PrimTypeStats(n, d)
                           for n, d in (raw.get("primitive_types") or {}).items()]
        self.prim_types.sort(key=lambda t: (-t.total_memory, t.name))
        overhead = raw.get("data_structure_overhead") or {}
        self.overhead_total_memory, self.overhead_new_memory, self.overhead_unique_memory = (
            counts(overhead.get("memory", {})))

        page_details = raw.get("page_details")
        self.has_page_details = page_details is not None
        page_detail = page_details or {}
        self.has_hardened_page_details = page_detail.get("has_hardened_details", False)
        self.num_constant_pages = page_detail.get("num_constant_pages", 0)
        self.num_shared_pages = page_detail.get("num_shared_pages", 0)
        self.num_hardened_pages = page_detail.get("num_hardened_pages", 0)
        self.num_constant_shared_pages = page_detail.get("num_constant_shared_pages", 0)

    def __bool__(self):
        return self.has_page_details or bool(self.prim_types) or self.total_memory > 0

    @property
    def name(self):
        return self.NAME

    @property
    def scope(self):
        """View-only scope — the report gives this row no scope."""
        return "primitive_list"

    @property
    def key(self):
        return ("primitive", self.scope, self.NAME)

    @property
    def type_label(self):
        return "full representation" if self.is_full_representation else "paged primitive list"

    @property
    def pages_label(self):
        # A prim list never exposes hardness — "constant" here means
        # equal-length contiguous vertex lists (GA_PrimitiveList.h:162).
        if not self.has_page_details:
            return "-"
        return f"{self.num_constant_pages}/-/-"

    def total_label(self, fmt):
        return fmt(self.total_memory)

    @property
    def data_id_label(self):
        return str(self.data_id) if self.data_id >= 0 else "-"

    is_tail_initialized = False
    has_memory_block_sharing = False
    intra_detail_memory_sharing = 0
    shares_with_attrib_keys = ()
    type_name = "primitivelist"


class EdgeGroupStats:
    """One ``report["group_tables"]["tables"]["edge"]["groups"]`` entry."""

    __slots__ = ("name", "total_memory", "new_memory", "unique_memory",
                 "data_id", "is_data_id_found_in_inputs")

    def __init__(self, name, raw):
        self.name = name
        self.total_memory, self.new_memory, self.unique_memory = counts(
            raw.get("memory", {}))
        self.data_id = raw["data_id"]
        self.is_data_id_found_in_inputs = raw["is_data_id_found_in_inputs"]

    @property
    def key(self):
        return ("edgegroup", self.scope, self.name)

    @property
    def scope(self):
        # Its own scope, not "group": a GA_EdgeGroup is not a GA_Attribute, so the
        # Groups checkbox (which filters attribute leaves) must not reach it. Its own
        # colour too, deliberately different from an element group's violet -- that
        # colour doubles as the Groups checkbox's swatch, and reusing it here would
        # visually (mis)imply the Groups toggle controls edge groups.
        return "edge_group"

    @property
    def pages_label(self):
        return "-"

    def total_label(self, fmt):
        return fmt(self.total_memory)

    @property
    def data_id_label(self):
        return str(self.data_id) if self.data_id >= 0 else "-"

    is_tail_initialized = False
    has_memory_block_sharing = False
    intra_detail_memory_sharing = 0
    shares_with_attrib_keys = ()
    has_page_details = False
    type_label = "edgegroup"
    type_name = "edgegroup"


class Row:
    """One node of the breakdown tree."""

    __slots__ = ("label", "total_memory", "new_memory", "unique_memory",
                 "order", "type_str", "type_hidden", "data_id", "data_id_inherited",
                 "data_id_hidden", "attr", "scope", "note", "collapsed", "owner",
                 "children")

    def __init__(self, label, total_memory, new_memory=None, unique_memory=None,
                 order=0, type_str="", type_hidden=False, data_id=None,
                 data_id_inherited=False, data_id_hidden=False, attr=None,
                 scope=None, note="", collapsed=False, owner=None):
        self.label = label
        # Which GA owner a STRUCTURAL row is about — attribute leaves carry it
        # on .attr instead.
        self.owner = owner
        # Display-only annotation; never structural — label stays the identity.
        self.note = note
        self.total_memory = int(total_memory)
        self.new_memory = None if new_memory is None else int(new_memory)
        self.unique_memory = None if unique_memory is None else int(unique_memory)
        self.order = order
        self.type_str = type_str
        # An attribute leaf normally shows its .attr's own type label. The Primitive
        # List's "Paged Vertex List" child is the one exception -- the list's
        # representation label belongs on the branch root instead (see
        # _build_primitive_list), so this row asks the view to blank the Type column
        # despite carrying an attr.
        self.type_hidden = type_hidden
        # data_id/data_id_inherited: for a STRUCTURAL row that names one underlying
        # object without being checkbox-capable (the Primitive List branch in full
        # representation, where the checkbox lives on a child instead) -- an attribute
        # leaf carries the same information on .attr instead, and this stays unset.
        self.data_id = data_id
        self.data_id_inherited = data_id_inherited
        # An attribute leaf normally shows its .attr's own data id. The Primitive
        # List's "Paged Vertex List" child is the one exception -- the list's data id
        # belongs on the branch root instead (see _build_primitive_list), so this row
        # asks the view to blank the Data ID column despite carrying an attr.
        self.data_id_hidden = data_id_hidden
        self.attr = attr
        # Colour only — it marks a row as data the user creates and deletes
        # (attributes, groups, edge groups, primitives), which is also what makes the
        # row bold when New is nonzero. None for every container and bookkeeping row.
        # It no longer names a toggle: branch filtering happens at construction time,
        # in each _build_* method, and never reads this.
        self.scope = scope
        self.collapsed = collapsed
        self.children = []

    def add(self, child):
        self.children.append(child)
        return child

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
        return (f"Row({self.label!r}, total={self.total_memory}, "
                f"new={self.new_memory}, unique={self.unique_memory}, "
                f"children={len(self.children)})")


def row_key(row, parent_path):
    """Row identity as a tuple: ("attr", owner, scope, name) for attribute leaves,
    ("path", label, ...) for structural rows."""
    if row.attr is not None:
        return ("attr",) + tuple(row.attr.key)
    return ("path",) + tuple(parent_path) + (row.label,)


class MemoryModel:
    """One ``page_tools.report()`` dict, parsed."""

    def __init__(self, report):
        self._report = report or {}
        self._index_map_owners = self._report.get("index_maps", {}).get("owners", {})
        self._attrib_set_owners = self._report.get("attribute_set", {}).get("owners", {})

        self.node = self._report.get("node_path", "")
        self.instanced = self._report.get("is_instanced", False)

        # GU_Detail::countMemory() — NOT getMemoryUsage(), which is blind to
        # element-group memory (SOP_Memory_Groups.md).
        # Authoritative detail-level counts from one UT_MemoryCounterNewSafe pass,
        # NOT a sum of per-attribute rows.
        self.total_memory, self.new_memory, self.unique_memory = counts(
            self._report.get("memory", {}))
        # Names the Unaccounted row; never sizes it — the count can be 0 with
        # the table still allocated.
        self.num_tail_initializers = self._report.get("num_tail_initializers", 0)

        self.primitive_list = PrimitiveList(self._report.get("primitive_list"))

        self._attrs = [AttributeStats(owner, scope, name, raw)
                       for owner in OWNERS
                       for scope, scope_attrs
                       in self._attrib_set_owners.get(owner, {}).get("attributes", {}).items()
                       for name, raw in scope_attrs.items()]
        self._attrs_by_owner = {}
        for attr in self._attrs:
            self._attrs_by_owner.setdefault(attr.owner, []).append(attr)
        # Stable position per key — the view indexes a palette with it so an
        # attribute keeps one colour across every owner and selection.
        self._attr_positions = {key: i for i, key
                                in enumerate(sorted(a.key for a in self._attrs))}

    def __bool__(self):
        return bool(self._report)

    def owner_map(self, owner):
        """The index-map sub-report for one owner (feeds DecodedOwner) -- layout and
        occupancy only, not attributes; see attribute_set_owner()."""
        return self._index_map_owners.get(owner, {})

    def attribute_set_owner(self, owner):
        """One owner's attribute_set node -- payload memory + the attributes dict
        (feeds DecodedOwner's attribute page-storage lookups)."""
        return self._attrib_set_owners.get(owner, {})

    def raw_primitive_list(self):
        """The report's own primitive_list sub-dict (also feeds DecodedOwner) --
        distinct from self.primitive_list, which is model.py's parsed PrimitiveList."""
        return self._report.get("primitive_list")

    def attrs(self, owner=None, scopes=None):
        source = self._attrs if owner is None else self._attrs_by_owner.get(owner, [])
        return [a for a in source if scopes is None or a.scope in scopes]

    def attr_position(self, key):
        return self._attr_positions.get(tuple(key))

    def num_attr_positions(self):
        return len(self._attr_positions)

    def owner_attr_memory(self, owner):
        """De-duplicated totals straight from the provider."""
        return counts(self.attribute_set_owner(owner).get("memory", {}))

    # -- the breakdown ------------------------------------------------------

    def breakdown(self, scopes=None):
        """Geometry Memory -> six direct branches, one per
        GA_Detail::countMemory() contributor (Attribute Set, Primitive List, Index
        Maps, Group Tables, Detail Object, Unaccounted) in ROOT_BRANCHES order,
        summing to the root EXACTLY in all three columns. Every branch row is always
        built; a branch's own SCOPES token (where it has one) gates only that
        branch's CHILDREN, never the branch row itself."""
        if not self._report:
            return None
        if scopes is None:
            scopes = set(SCOPES)

        root = Row("Geometry Memory", self.total_memory, self.new_memory,
                   self.unique_memory)
        root.add(self._build_attribute_set(scopes))
        root.add(self._build_primitive_list(scopes))
        root.add(self._build_index_maps(scopes))
        root.add(self._build_group_tables(scopes))
        root.add(self._build_detail_object())
        root.add(self._build_unaccounted())
        return root

    def _build_attribute_set(self, scopes):
        """GA_AttributeSet in full: the owner rows (real attribute/group payload) plus
        the container's own bookkeeping, LAST, as "Data Structure Overhead". Gated by
        its own coarse "attribute_set" toggle like every other branch's children; the
        finer Public/Private/Groups toggles filter individual leaves
        WITHIN the owner rows, once "attribute_set" itself has let the owners through."""
        attribute_set = self._report.get("attribute_set", {})
        # The provider's own de-duplicated total — payload plus overhead already summed
        # in, not re-derived here (provider owns the accounting).
        total, new, unique = counts(attribute_set.get("memory", {}))
        attr_set = Row("Attribute Set", total, new, unique,
                       order=ROOT_BRANCHES.index("attribute_set"))
        if "attribute_set" in scopes:
            for owner_idx, owner in enumerate(OWNERS):
                attrs = self.attrs(owner)
                if not attrs:
                    continue
                # Reported, not summed — summing over-counts shared blocks.
                owner_total, owner_new, owner_unique = self.owner_attr_memory(owner)
                owner_row = attr_set.add(
                    Row(owner.capitalize(), owner_total, owner_new, owner_unique,
                        order=owner_idx, owner=owner))
                for attr in attrs:
                    if attr.scope in scopes:
                        owner_row.add(
                            Row(attr.name, attr.total_memory, attr.new_memory,
                                attr.unique_memory, type_str=attr.type_label,
                                data_id=attr.data_id, attr=attr, scope=attr.scope))

            # The container's own side-table bytes — bookkeeping, not user-created
            # data, so scope=None (no colour, never bold).
            overhead_total, overhead_new, overhead_unique = counts(
                attribute_set.get("data_structure_overhead", {}).get("memory", {}))
            attr_set.add(Row("Data Structure Overhead", overhead_total, overhead_new,
                             overhead_unique, order=len(OWNERS)))

        return attr_set

    def _build_primitive_list(self, scopes):
        """GA_PrimitiveList always has at least one child once its toggle is on: the
        full-representation per-type rows plus Data Structure Overhead, or — for a
        paged (compact) list — the single "Paged Vertex List" row, which (not this
        branch row) carries the checkbox and page-detail info. The branch root always
        carries the list's own data id and representation label — no child names the
        list as a whole in the full-representation case, and the compact case's one
        child leaves its own Data ID column blank rather than repeat the root's."""
        prim_list = self.primitive_list
        full = bool(prim_list.prim_types)
        row = Row(prim_list.name, prim_list.total_memory, prim_list.new_memory,
                  prim_list.unique_memory, order=ROOT_BRANCHES.index("primitive_list"),
                  type_str=prim_list.type_label,
                  data_id=prim_list.data_id,
                  data_id_inherited=prim_list.is_data_id_found_in_inputs)
        if "primitive_list" in scopes:
            if full:
                for type_idx, ptype in enumerate(prim_list.prim_types):
                    row.add(Row(ptype.name, ptype.total_memory, ptype.new_memory,
                                ptype.unique_memory, order=type_idx,
                                type_str=ptype.count_label, scope="primitive_list"))
                # The list's own side tables, not a primitive — bookkeeping, so
                # scope=None (see Attribute Set's identical overhead row above).
                row.add(Row("Data Structure Overhead", prim_list.overhead_total_memory,
                            prim_list.overhead_new_memory,
                            prim_list.overhead_unique_memory,
                            order=len(prim_list.prim_types)))
            else:
                # data_id_hidden/type_hidden: the list's data id and representation
                # label are shown on the branch root above, not repeated here, even
                # though this row carries .attr=prim_list.
                row.add(Row("Paged Vertex List", prim_list.total_memory,
                            prim_list.new_memory, prim_list.unique_memory, order=0,
                            data_id_hidden=True, type_hidden=True,
                            attr=prim_list, scope="primitive_list"))
        return row

    def _build_index_maps(self, scopes):
        """The four GA_Index_Map owners. Never individually zero-skipped, even at
        0 B: each carries a trivial/monotonic classification (map_kind) that is
        meaningful on its own regardless of byte size, unlike a group table's rows
        (see _build_group_tables), which are pure byte counts."""
        index_maps = self._report.get("index_maps", {})
        total, new, unique = counts(index_maps.get("memory", {}))
        row = Row("Index Maps", total, new, unique,
                  order=ROOT_BRANCHES.index("index_maps"))
        if "index_maps" in scopes:
            for owner_idx, owner in enumerate(OWNERS):
                owner_map_report = self.owner_map(owner)
                o_total, o_new, o_unique = counts(owner_map_report.get("memory", {}))
                row.add(Row(owner.capitalize(), o_total, o_new, o_unique,
                            order=owner_idx, type_str=map_kind(owner_map_report),
                            owner=owner))
        return row

    def _build_group_tables(self, scopes):
        """Point/Primitive/Vertex/Edge group tables. Unlike Index Maps, an individual
        table still disappears at 0 B even with the toggle on: a table's row is a pure
        byte count with no other classification to preserve, so there is nothing
        informative left to show. Edge groups are real geometry data, individually
        broken out under Edge; they and the table's own remainder (its Name Map, named
        "Data Structure Overhead" for consistency with Attribute Set's and Primitive
        List's identical bookkeeping rows) sum back to the table's total, the same
        "children plus one remainder row" shape those two use. Edge groups are shown whenever
        Group Tables' children are shown — they are not separately gated by "Groups",
        which only filters ELEMENT-group leaves under Attribute Set."""
        group_tables = self._report.get("group_tables", {})
        tables = group_tables.get("tables", {})
        total, new, unique = counts(group_tables.get("memory", {}))
        row = Row("Group Tables", total, new, unique,
                  order=ROOT_BRANCHES.index("group_tables"))
        if "group_tables" in scopes:
            for table_idx, tname in enumerate(GROUP_TABLES):
                table = tables.get(tname, {})
                table_total, table_new, table_unique = counts(table.get("memory", {}))
                if not (table_total or table_new or table_unique):
                    continue
                table_row = row.add(Row(tname.capitalize(), table_total, table_new,
                                        table_unique, order=table_idx))
                if tname != "edge":
                    continue
                edge_groups = sorted(table.get("groups", {}).items())
                if not edge_groups:
                    continue
                for group_idx, (gname, g) in enumerate(edge_groups):
                    edge_group = EdgeGroupStats(gname, g)
                    table_row.add(Row(edge_group.name, edge_group.total_memory,
                                      edge_group.new_memory, edge_group.unique_memory,
                                      order=group_idx, type_str=edge_group.type_label,
                                      data_id=edge_group.data_id, attr=edge_group,
                                      scope=edge_group.scope))
                overhead_total, overhead_new, overhead_unique = counts(
                    table.get("data_structure_overhead", {}).get("memory", {}))
                table_row.add(Row("Data Structure Overhead",
                                  overhead_total, overhead_new, overhead_unique,
                                  order=len(edge_groups)))
        return row

    def _build_detail_object(self):
        """GU_Detail's own sizeof — always present, unconditionally: a GU_Detail
        cannot be 0 B, so unlike every other branch there is no zero-check and no
        toggle at all."""
        total, new, unique = counts(self._report.get("detail_object", {}).get("memory", {}))
        return Row("Detail Object", total, new, unique,
                   order=ROOT_BRANCHES.index("detail_object"))

    def _build_unaccounted(self):
        # Taken from the provider's residual figure, not re-derived — so the sum is a
        # real, falsifiable claim. What lands here is GA_Detail::myTailInitializers
        # (private, no size getter — can only ever be a remainder).
        total, new, unique = counts(self._report.get("unaccounted", {}).get("memory", {}))
        # num_tail_initializers names the row but must NOT gate it: the bucket
        # array outlives the last registration (SOP_Memory_Sources.md §4).
        note = ""
        if self.num_tail_initializers:
            note = "tail initializers: %d" % self.num_tail_initializers
        return Row("Unaccounted", total, new, unique,
                   order=ROOT_BRANCHES.index("unaccounted"), note=note)
