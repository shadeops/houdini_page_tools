// Return dict schema
//
// All *_memory values will be in bytes
//
// {
//  'node_path': str,
//  'is_cooked': bool,
//  'output_index': int,
//  'is_instanced': bool,
//
//  'num_tail_initializers': int,
//
//  'total_memory': int,
//  'new_memory': int,
//  'unique_memory': int,
//
//  'memory': {
//      'attribute_set_total_memory': int,
//      'attribute_set_new_memory': int,
//      'attribute_set_unique_memory': int,
//
//      'attribute_set_overhead_memory': int,
//      'attribute_set_overhead_new_memory': int,
//      'attribute_set_overhead_unique_memory': int,
//
//      'index_maps_total_memory': int,
//      'index_maps_new_memory': int,
//      'index_maps_unique_memory': int,
//
//      'detail_struct_memory': int,           # sizeof(GU_Detail)
//      'detail_struct_new_memory': int,
//      'detail_struct_unique_memory': int,
//
//      # This is the SOP's countMemory report minus all known sources' countMemory
//      # This excess will be the ga_TailInitializeTable which is main(?) used for groups
//      # to set default values for values past the offset_size and constant pages
//      # There is no public interface to countMemory, but we can estimate it from the
//      # number of tail initializers
//
//      'residual_total_memory': int,
//      'residual_new_memory': int,
//      'residual_unique_memory': int,
//
//      'group_tables': {
//          'point | primitive | vertex': {
//              'total_memory': int,
//              'new_memory': int,
//              'unique_memory': int,
//          },
//
//          # Edges aren't store like the other groups, which are attribute based.
//          # They are not page backed and are stored directly in the group table.
//          'edge': {
//              'total_memory': int,
//              'new_memory': int,
//              'unique_memory': int,
//              'groups': {
//                  <edge_group_name> str: {
//                      'total_memory': int,
//                      'new_memory': int,
//                      'unique_memory': int,
//                  },
//              },
//              'name_map_memory': int,
//          },
//      },
//      'group_tables_total_memory': int,
//      'group_tables_new_memory': int,
//      'group_tables_unique_memory': int,
//  },
//
//  'page_size': int,                          # GA_PAGE_SIZE, needs to be 1024
//  'per_page_count_bytes': int,
//  'page_word_bytes': int,                    # num of bytes in a page 'word'
//  'page_occupancy_words_per_page': int,      # uint32 words per page in page_bits masks
//
//  # Index Maps
//  'owners': {
//      'point | primitive | vertex | detail': {
//          'owner': int,                      # enum of owner
//          'offset_size': int,
//          'index_size': int,
//          'num_pages': int,
//
//          'is_monotonic': bool,
//          'is_trivial': bool,
//
//          # These will be 0 if the Index Map is trivial, since it doesn't
//          # need to be allocated on the heap
//
//          'total_memory': int,
//          'new_memory': int,
//          'unique_memory': int,
//
//          'page_mask_words': int,
//          'num_active_per_page': bytes,      # i64 array, one per page
//          'num_temporary_per_page': bytes,   # i64 array
//          'num_vacant_per_page': bytes,      # i64 array
//          'active_page_bits': bytes,         # bitarray
//          'temporary_page_bits': bytes,      # bitarray
//          'full_block_ranges': bytes,        # int64[2] array, [start, end)
//
//          'attributes': {
//              # While primtive lists aren't an attribute they can be page backed
//              # when not a "full representation", so we'll treat them as "meta"
//              # attributes so piggy back on the page counting.
//              'public | private | group | meta': {
//                  <attribute_name> str: {
//                      'type_name': str,      # registered ATI type, e.g. "numeric"
//                      'scope': str,          # 'public | private | group | meta'
//                      'is_meta': bool,
//                      'tuple_size': int,
//
//                      'total_memory': int,
//                      'unique_memory': int,
//                      'new_memory': int,
//
//                      'is_data_id_found_in_inputs': bool,
//
//                      # Only groups seem to be tail_initialized in practice
//                      'is_tail_initialized': bool,
//
//                      'data_id': int,        # -1 when unset
//
//                      'is_full_representation': bool,
//                      'primitive_types': {
//                          <type_name> str: {
//                              'type_id': int,
//                              'count': int,
//                              'total_memory': int,
//                              'new_memory': int,
//                              'unique_memory': int,
//                          },
//                      },
//
//                      'has_page_details': bool,
//                      'has_hardened_page_details': bool,
//                      'is_page_table_hardened': bool,
//                      'num_constant_pages': int,
//                      'num_shared_pages': int,          # always 0 for element groups?
//                      'num_hardened_pages': int,
//
//                      'constant_page_bits': bytes,    # bitarray (one bit per page)
//                      'hardened_page_bits': bytes,    # bitarray (one bit per page)
//                  },
//              },
//          },
//      },
//  },
// }

#include <GA/GA_ATIDict.h>
#include <GA/GA_ATIGroupBool.h>  // Provides GA_ElementGroup
#include <GA/GA_ATINumeric.h>
#include <GA/GA_ATIString.h>
#include <GA/GA_ATITopology.h>
#include <GA/GA_Attribute.h>
#include <GA/GA_AttributeDict.h>
#include <GA/GA_AttributeSet.h>
#include <GA/GA_AttributeType.h>
#include <GA/GA_IndexMap.h>
#include <GA/GA_Iterator.h>
#include <GA/GA_Primitive.h>
#include <GA/GA_PrimitiveList.h>
#include <GA/GA_Range.h>
#include <GA/GA_Types.h>
#include <GU/GU_Detail.h>
#include <GU/GU_DetailHandle.h>
#include <HOM/HOM_Errors.h>
#include <HOM/HOM_Module.h>
#include <OP/OP_Context.h>
#include <OP/OP_Director.h>
#include <OP/OP_Node.h>
#include <PY/PY_AutoObject.h>
#include <PY/PY_CPythonAPI.h>
#include <PY/PY_InterpreterAutoLock.h>
#include <SOP/SOP_Node.h>
#include <SYS/SYS_Types.h>
#include <UT/UT_Array.h>
#include <UT/UT_ArraySet.h>
#include <UT/UT_Assert.h>
#include <UT/UT_BitArray.h>
#include <UT/UT_MemoryCounter.h>
#include <UT/UT_Set.h>
#include <UT/UT_StringHolder.h>

#include <string>

// Our bitarrays assume a page size of 1024, so verify just in case.
static_assert(GA_PAGE_SIZE == 1024, "PageBits assumes GA_PAGE_SIZE == 1024");

namespace page_tools {

// 1024 bits = GA_PAGE_SIZE
// packed as 32 x uint32.
// slot -> word (slot>>5)
// bit(slot&31).
struct PageBits {
    uint32 bits[32] = {0};
};

inline void setPageBit(uint32* mask, GA_Size slot) {
    mask[slot >> 5] |= (static_cast<uint32>(1) << (slot & 31));
}

// NOTE:
//  Occasionally define GA_STRICT_TYPES to ensure we aren't mixing GA_Offset
//  and GA_Index incorrectly. (Normally both ar exint)

struct PrimTypeStats {
    UT_StringHolder type_name;
    int             type_id       = -1;
    GA_Size         count         = 0;
    int64           total_memory  = 0;
    int64           new_memory    = 0;
    int64           unique_memory = 0;
};

struct AttributeStats {
    UT_StringHolder   name;
    GA_AttributeOwner owner         = GA_ATTRIB_POINT;
    GA_AttributeScope scope         = GA_SCOPE_PUBLIC;
    // "meta" isn't a Houdini concept, it is just a reporting hack so we can reuse
    // AttributeStats for the primitive list info since it shares many of the same
    // details
    bool                    is_meta = false;
    UT_StringHolder         type_name;
    int                     tuple_size                 = 0;

    int64                   total_memory               = 0;
    int64                   new_memory                 = 0;
    int64                   unique_memory              = 0;

    GA_DataId               data_id                    = GA_INVALID_DATAID;
    bool                    is_data_id_found_in_inputs = false;
    bool                    is_tail_initialized        = false;

    bool                    has_page_details           = false;
    bool                    has_hardened_page_details  = false;
    bool                    is_page_table_hardened     = false;

    GA_Size                 num_constant_pages         = 0;
    GA_Size                 num_shared_pages           = 0;
    GA_Size                 num_hardened_pages         = 0;

    UT_BitArray             constant_page_bits;
    UT_BitArray             hardened_page_bits;

    bool                    is_full_representation = false;

    UT_Array<PrimTypeStats> prim_types;
};

struct OwnerStats {
    GA_AttributeOwner        owner         = GA_ATTRIB_POINT;
    GA_Offset                offset_size   = GA_Offset(0);
    GA_Index                 index_size    = GA_Index(0);
    GA_Size                  num_pages     = 0;

    int64                    total_memory  = 0;
    int64                    new_memory    = 0;
    int64                    unique_memory = 0;

    bool                     is_monotonic  = false;
    bool                     is_trivial    = false;

    UT_Array<GA_Size>        num_active_per_page;
    UT_Array<GA_Size>        num_temporary_per_page;
    UT_Array<GA_Size>        num_vacant_per_page;
    UT_Array<PageBits>       active_page_bits;
    UT_Array<PageBits>       temporary_page_bits;

    UT_Array<GA_Offset>      full_block_ranges;

    UT_Array<AttributeStats> attributes;
};

enum GroupTableId {
    GROUP_TABLE_POINT = 0,
    GROUP_TABLE_PRIMITIVE,
    GROUP_TABLE_VERTEX,
    GROUP_TABLE_EDGE,
    GROUP_TABLE_N
};

static const char* const       GROUP_TABLE_NAMES[GROUP_TABLE_N] = {"point", "primitive", "vertex",
                                                                   "edge"};

static const GA_AttributeOwner GROUP_TABLE_OWNERS[GROUP_TABLE_EDGE] = {
    GA_ATTRIB_POINT, GA_ATTRIB_PRIMITIVE, GA_ATTRIB_VERTEX};
static_assert(GROUP_TABLE_EDGE == 3, "GROUP_TABLE_OWNERS lists the 3 element-group tables");

struct DetailStats {
    UT_StringHolder node_path;

    bool            is_cooked                  = false;
    int             output_index               = 0;
    bool            is_instanced               = false;

    //   total_memory =
    //       attribute total_memory
    //       + <primitive list>
    //       + attribute_set_overhead_memory
    //       + index_maps_total_memory
    //       + group_tables_total_memory
    //       + detail_struct_memory
    //       + residual_total_memory

    int64 total_memory                         = 0;
    int64 new_memory                           = 0;
    int64 unique_memory                        = 0;

    int64 attribute_set_total_memory           = 0;
    int64 attribute_set_new_memory             = 0;
    int64 attribute_set_unique_memory          = 0;

    int64 attribute_set_overhead_memory        = 0;
    int64 attribute_set_overhead_new_memory    = 0;
    int64 attribute_set_overhead_unique_memory = 0;

    int64 index_maps_total_memory              = 0;
    int64 index_maps_new_memory                = 0;
    int64 index_maps_unique_memory             = 0;

    struct GroupStats {
        UT_StringHolder name;
        int64           total_memory  = 0;
        int64           new_memory    = 0;
        int64           unique_memory = 0;
    };

    struct GroupTableStats {
        int64                total_memory    = 0;
        int64                new_memory      = 0;
        int64                unique_memory   = 0;

        int64                name_map_memory = 0;
        UT_Array<GroupStats> groups;
    };

    GroupTableStats group_tables[GROUP_TABLE_N];
    int64           group_tables_total_memory   = 0;
    int64           group_tables_new_memory     = 0;
    int64           group_tables_unique_memory  = 0;

    int64           detail_struct_memory        = 0;
    int64           detail_struct_new_memory    = 0;
    int64           detail_struct_unique_memory = 0;

    int64           residual_total_memory       = 0;
    int64           residual_new_memory         = 0;
    int64           residual_unique_memory      = 0;

    GA_Size         num_tail_initializers       = 0;

    OwnerStats      owner_stats[GA_ATTRIB_OWNER_N];
};

// Hack for checking out of range outputs on nodes.
static const int               OUTPUT_FROM_VIEW              = 0x7fffffff;

static const GA_AttributeOwner ALL_OWNERS[GA_ATTRIB_OWNER_N] = {
    GA_ATTRIB_VERTEX, GA_ATTRIB_POINT, GA_ATTRIB_PRIMITIVE, GA_ATTRIB_GLOBAL};

static void gatherInputDataIds(const GU_Detail* in_gdp, UT_Set<GA_DataId>& data_ids) {
    for (GA_AttributeOwner owner : ALL_OWNERS) {
        const GA_AttributeDict& dict = in_gdp->getAttributeDict(owner);

        for (GA_AttributeDict::iterator it = dict.begin(GA_SCOPE_INVALID); !it.atEnd(); ++it) {
            const GA_Attribute* attrib = it.attrib();
            if (!attrib) continue;

            const GA_DataId data_id = attrib->getDataId();
            if (data_id != GA_INVALID_DATAID) data_ids.insert(data_id);
        }
    }

    const GA_DataId prim_list_id = in_gdp->getPrimitiveList().getDataId();
    if (prim_list_id != GA_INVALID_DATAID) data_ids.insert(prim_list_id);
}

static void gatherAttributeStats(const GA_Attribute*              out_attr,
                                 const UT::ArraySet<const void*>& avoid,
                                 const UT_Set<GA_DataId>&         input_data_ids,
                                 AttributeStats&                  attrib_stats) {
    UT_MemoryCounterNewSafe counter(avoid);
    out_attr->countMemory(counter, /*inclusive*/ true);

    attrib_stats.total_memory               = counter.getFullCount();
    attrib_stats.unique_memory              = counter.getUniqueCount();
    attrib_stats.new_memory                 = counter.getCount();

    attrib_stats.data_id                    = out_attr->getDataId();
    attrib_stats.is_data_id_found_in_inputs = (attrib_stats.data_id != GA_INVALID_DATAID &&
                                               input_data_ids.count(attrib_stats.data_id) != 0);
}

template <typename PageArrayT>
static void pageStorageFromArray(const PageArrayT& page_array,
                                 GA_Size           num_pages,
                                 AttributeStats&   attrib_stats) {
    attrib_stats.has_page_details       = true;
    attrib_stats.is_page_table_hardened = page_array.isTableHardened();
    attrib_stats.constant_page_bits.setSize(num_pages);
    attrib_stats.hardened_page_bits.setSize(num_pages);

    for (GA_Size page = 0; page < num_pages; ++page) {
        const bool is_constant = page_array.isPageConstant(GA_PageNum(page));
        const bool is_hard     = page_array.isPageHard(GA_PageNum(page));

        attrib_stats.constant_page_bits.setBitFast(page, is_constant);
        attrib_stats.hardened_page_bits.setBitFast(page, is_hard);

        if (is_constant)
            ++attrib_stats.num_constant_pages;
        else if (is_hard)
            ++attrib_stats.num_hardened_pages;
        else
            ++attrib_stats.num_shared_pages;
    }
}

static void gatherPageStorage(const GA_Attribute* attrib,
                              GA_Size             num_pages,
                              AttributeStats&     attrib_stats) {
    if (const GA_ATINumeric* n = GA_ATINumeric::cast(attrib)) {
        pageStorageFromArray(n->getData(), num_pages, attrib_stats);
    } else if (const GA_ATITopology* t = GA_ATITopology::cast(attrib)) {
        pageStorageFromArray(t->getData(), num_pages, attrib_stats);
    } else if (const GA_ATIString* s = GA_ATIString::cast(attrib)) {
        pageStorageFromArray(s->getHandleData(), num_pages, attrib_stats);
    } else if (const GA_ATIDict* d = GA_ATIDict::cast(attrib)) {
        pageStorageFromArray(d->getHandleData(), num_pages, attrib_stats);
    } else if (const GA_ATIGroupBool* g = GA_ATIGroupBool::cast(attrib)) {
        // There isn't a public API to get the page data handle for groups
        // The best we can do is fetch the constant page bits
        attrib_stats.has_page_details          = true;
        attrib_stats.has_hardened_page_details = false;
        attrib_stats.is_page_table_hardened    = false;
        attrib_stats.constant_page_bits.setSize(num_pages);
        attrib_stats.hardened_page_bits.setSize(num_pages);  // should initialize to 0
        for (GA_Size page = 0; page < num_pages; ++page) {
            const bool is_constant = g->isPageConstant(GA_PageNum(page));
            attrib_stats.constant_page_bits.setBitFast(page, is_constant);
            if (is_constant) ++attrib_stats.num_constant_pages;
        }
    } else {
        attrib_stats.has_page_details = false;
    }
}

static void gatherPrimitiveTypeStats(const GU_Detail*                 gdp,
                                     const UT::ArraySet<const void*>& avoid,
                                     bool                             is_instanced,
                                     UT_Array<PrimTypeStats>&         prim_types) {
    // We need to maintain a single counter and run every prim through it. We do this
    // instead of creating a new counter for eac prim. This is because two prims
    // might share the same reference and using a single counter prevent double counting.
    // However this makes tracking each primitive's contribution more involved since we
    // need to calculate the difference from the previous step. In other words, if we
    // grew by 10 bytes, then we knew that primitive wasn't shared with a previous prim.
    UT_MemoryCounterNewSafe counter(avoid);

    // Primitive type ids are derived at run time from 0, so we can reference into
    // an array instead of making a Set.
    UT_Array<exint> type_id_to_index;
    type_id_to_index.appendMultiple(exint(-1), gdp->getPrimitiveFactory().getPrimTypeCount());
    int64 prev_full_mem   = 0;
    int64 prev_new_mem    = 0;
    int64 prev_unique_mem = 0;

    for (GA_Iterator it(gdp->getPrimitiveRange()); !it.atEnd(); ++it) {
        const GA_Primitive* prim = gdp->getPrimitive(*it);
        if (!prim) continue;

        const int type_id = prim->getTypeId().get();
        exint     index   = type_id_to_index(type_id);
        if (index < 0) {
            index                       = prim_types.append();
            type_id_to_index(type_id)   = index;
            prim_types(index).type_name = prim->getTypeName();
            prim_types(index).type_id   = type_id;
        }

        prim->countMemory(counter);

        PrimTypeStats& stats = prim_types(index);
        ++stats.count;
        const int64 full_mem  = static_cast<int64>(counter.getFullCount());
        stats.total_memory   += full_mem - prev_full_mem;
        prev_full_mem         = full_mem;
        if (!is_instanced) {
            const int64 new_mem     = static_cast<int64>(counter.getCount());
            const int64 unique_mem  = static_cast<int64>(counter.getUniqueCount());
            stats.new_memory       += new_mem - prev_new_mem;
            stats.unique_memory    += unique_mem - prev_unique_mem;
            prev_new_mem            = new_mem;
            prev_unique_mem         = unique_mem;
        }
    }
}

static void gatherPrimitiveListMeta(const GU_Detail*                 gdp,
                                    GA_Size                          num_pages,
                                    const UT::ArraySet<const void*>& avoid,
                                    const UT_Set<GA_DataId>&         input_data_ids,
                                    bool                             is_instanced,
                                    AttributeStats&                  attrib_stats) {
    attrib_stats.name                 = "<primitive list>";
    attrib_stats.owner                = GA_ATTRIB_PRIMITIVE;
    attrib_stats.is_meta              = true;
    attrib_stats.type_name            = "primitivelist";
    attrib_stats.tuple_size           = 0;

    const GA_PrimitiveList& prim_list = gdp->getPrimitiveList();

    UT_MemoryCounterNewSafe counter(avoid);
    prim_list.countMemory(counter, /*inclusive*/ false);
    attrib_stats.total_memory = static_cast<int64>(counter.getFullCount());
    if (!is_instanced) {
        attrib_stats.new_memory    = static_cast<int64>(counter.getCount());
        attrib_stats.unique_memory = static_cast<int64>(counter.getUniqueCount());
    }

    attrib_stats.data_id = prim_list.getDataId();
    attrib_stats.is_data_id_found_in_inputs =
        is_instanced || (attrib_stats.data_id != GA_INVALID_DATAID &&
                         input_data_ids.count(attrib_stats.data_id) != 0);

    attrib_stats.is_full_representation = prim_list.isFullRepresentation();
    if (attrib_stats.is_full_representation)
        gatherPrimitiveTypeStats(gdp, avoid, is_instanced, attrib_stats.prim_types);

    attrib_stats.has_page_details = !attrib_stats.is_full_representation;
    if (!attrib_stats.has_page_details) return;

    attrib_stats.has_hardened_page_details = false;
    attrib_stats.is_page_table_hardened    = false;
    attrib_stats.constant_page_bits.setSize(num_pages);
    // Similar to the groups, there isn't a public API for the pageDataHandle
    // so we just set the hardend page bits to 0
    attrib_stats.hardened_page_bits.setSize(num_pages);
    for (GA_Size page = 0; page < num_pages; ++page) {
        const bool is_const = prim_list.isVertexListPageConstant(GA_PageNum(page));
        attrib_stats.constant_page_bits.setBitFast(page, is_const);
        if (is_const) ++attrib_stats.num_constant_pages;
    }
}

// Per-owner index-map stats + per-page occupancy (counts + intra-page bitmasks).
static void gatherOwnerStats(const GU_Detail*                 gdp,
                             GA_AttributeOwner                owner,
                             bool                             want_block_ranges,
                             const UT::ArraySet<const void*>& avoid,
                             bool                             instanced,
                             OwnerStats&                      owner_stats) {
    const GA_IndexMap& index_map = gdp->getIndexMap(owner);
    owner_stats.owner            = owner;
    owner_stats.offset_size      = index_map.offsetSize();
    owner_stats.index_size       = index_map.indexSize();

    UT_MemoryCounterNewSafe counter(avoid);
    index_map.countMemory(counter, /*inclusive*/ false);
    owner_stats.total_memory = static_cast<int64>(counter.getFullCount());
    if (instanced) {
        owner_stats.new_memory    = 0;
        owner_stats.unique_memory = 0;
    } else {
        owner_stats.new_memory    = static_cast<int64>(counter.getCount());
        owner_stats.unique_memory = static_cast<int64>(counter.getUniqueCount());
    }
    owner_stats.is_monotonic = index_map.isMonotonicMap();
    owner_stats.is_trivial   = index_map.isTrivialMap();

    const GA_Size num_pages  = (GA_Size(index_map.offsetSize()) + GA_PAGE_SIZE - 1) >> GA_PAGE_BITS;
    owner_stats.num_pages    = num_pages;
    owner_stats.num_active_per_page.setSize(num_pages);
    owner_stats.num_temporary_per_page.setSize(num_pages);
    owner_stats.num_vacant_per_page.setSize(num_pages);
    owner_stats.active_page_bits.setSize(num_pages);
    owner_stats.temporary_page_bits.setSize(num_pages);

    if (owner_stats.is_trivial) {
        for (GA_Size page = 0; page < num_pages; ++page) {
            const GA_Offset page_start = GA_Offset(page << GA_PAGE_BITS);
            const GA_Offset page_end   = GAgetPageBoundary(page_start, index_map.offsetSize());
            const GA_Size   page_count = page_end - page_start;
            owner_stats.num_active_per_page[page]    = page_count;
            owner_stats.num_temporary_per_page[page] = 0;
            owner_stats.num_vacant_per_page[page]    = 0;

            uint32*       bits                       = owner_stats.active_page_bits[page].bits;
            const GA_Size full_words                 = page_count >> 5;
            for (GA_Size word = 0; word < full_words; ++word)
                bits[word] = ~static_cast<uint32>(0);

            const GA_Size tail_bits = page_count & 31;
            if (tail_bits) bits[full_words] = (static_cast<uint32>(1) << tail_bits) - 1;
        }
    } else
        for (GA_Size page = 0; page < num_pages; ++page) {
            const GA_Offset page_start = GA_Offset(page << GA_PAGE_BITS);
            const GA_Offset page_end   = GAgetPageBoundary(page_start, index_map.offsetSize());
            GA_Size         active     = 0;
            GA_Size         temporary  = 0;
            GA_Size         vacant     = 0;
            PageBits&       active_page_bits    = owner_stats.active_page_bits[page];
            PageBits&       temporary_page_bits = owner_stats.temporary_page_bits[page];
            for (GA_Offset page_offset = page_start; page_offset < page_end; ++page_offset) {
                if (index_map.isOffsetActive(page_offset)) {
                    setPageBit(active_page_bits.bits, GAgetPageOff(page_offset));
                    ++active;
                } else if (index_map.isOffsetTransient(page_offset)) {
                    setPageBit(temporary_page_bits.bits, GAgetPageOff(page_offset));
                    ++temporary;
                } else {
                    ++vacant;
                }
            }
            owner_stats.num_active_per_page[page]    = active;
            owner_stats.num_temporary_per_page[page] = temporary;
            owner_stats.num_vacant_per_page[page]    = vacant;
        }

    if (want_block_ranges) {
        GA_Range  range(index_map);
        GA_Offset start;
        GA_Offset end;
        for (GA_Iterator it(range); it.fullBlockAdvance(start, end);) {
            owner_stats.full_block_ranges.append(start);
            owner_stats.full_block_ranges.append(end);
        }
    }
}

static void gatherSources(SOP_Node*                   sop,
                          const GU_Detail*            out_gdp,
                          const OP_Context&           context,
                          int                         out_idx,
                          DetailStats&                report,
                          UT_Array<const GU_Detail*>& sources) {
    if (SOP_Node* internal_sop = sop->getOutputSop(out_idx, /*fallback_to_display_render*/ true)) {
        // We fetch the cached geo and don't force a cook. The assumption is
        // our current node cooked any needed depencencies already.
        const GU_Detail* internal_gdp = internal_sop->getLastGeo();
        if (internal_gdp && internal_gdp == out_gdp) {
            report.is_instanced = true;
        }
    }

    if (!report.is_instanced) {
        const unsigned num_inputs = sop->nInputs();
        sources.setCapacityIfNeeded(num_inputs);
        for (unsigned input_index = 0; input_index < num_inputs; ++input_index) {
            // gather the various sources, if the input gdp matches ours, then we can assume
            // it is an instance and exit out. Otherwise add it the list of sources to look at
            // for the avoid set.
            const GU_Detail* source_gdp =
                sop->getInputLastGeo(static_cast<int>(input_index), context.getTime());
            if (!source_gdp) continue;
            if (source_gdp == out_gdp) {
                report.is_instanced = true;
                break;
            }
            if (sources.find(source_gdp) < 0) sources.append(source_gdp);
        }
    }

    if (!report.is_instanced) {
        // Similar to above but now we do the same for the extra inputs
        // This can lead to false positives, like if a parameter references a node
        // but never uses the geometry. But since are unable to look inside a node
        // this is the best we can do.
        // TODO: maybe we can via a parm interest vs data interest?
        OP_NodeList extra_nodes;
        sop->getExtraInputNodes(extra_nodes,
                                /*remove_duplicates*/ true,
                                /*data_interest*/ true,
                                /*parm_interest*/ true,
                                /*flag_interest*/ false);
        sources.setCapacityIfNeeded(sources.size() + extra_nodes.size());
        for (exint extra_index = 0; extra_index < extra_nodes.size(); ++extra_index) {
            SOP_Node* extra_sop = CAST_SOPNODE(extra_nodes(extra_index));
            if (!extra_sop || extra_sop == sop) continue;
            const GU_Detail* source_gdp = extra_sop->getLastGeo();
            if (!source_gdp) continue;
            if (source_gdp == out_gdp) {
                report.is_instanced = true;
                break;
            }
            if (sources.find(source_gdp) < 0) sources.append(source_gdp);
        }
    }

    if (report.is_instanced) sources.clear();
}

static void reconcileTotals(DetailStats& report) {
    int64 group_attr_total_memory  = 0;
    int64 group_attr_new_memory    = 0;
    int64 group_attr_unique_memory = 0;
    int64 covered_total_memory     = 0;
    int64 covered_new_memory       = 0;
    int64 covered_unique_memory    = 0;

    int64 all_attrs_total_memory   = 0;
    int64 all_attrs_new_memory     = 0;
    int64 all_attrs_unique_memory  = 0;

    for (GA_AttributeOwner owner : ALL_OWNERS) {
        for (const AttributeStats& attr : report.owner_stats[owner].attributes) {
            all_attrs_total_memory  += attr.total_memory;
            all_attrs_new_memory    += attr.new_memory;
            all_attrs_unique_memory += attr.unique_memory;

            if (attr.is_meta) continue;

            if (attr.scope == GA_SCOPE_GROUP) {
                group_attr_total_memory  += attr.total_memory;
                group_attr_new_memory    += attr.new_memory;
                group_attr_unique_memory += attr.unique_memory;
            } else {
                covered_total_memory  += attr.total_memory;
                covered_new_memory    += attr.new_memory;
                covered_unique_memory += attr.unique_memory;
            }
        }
    }
    report.attribute_set_total_memory = report.attribute_set_total_memory - group_attr_total_memory;
    UT_ASSERT_MSG(report.attribute_set_total_memory < 0, "attribute_set_total_memory less than 0");

    report.attribute_set_new_memory = report.attribute_set_new_memory - group_attr_new_memory;
    UT_ASSERT_MSG(report.attribute_set_new_memory < 0, "attribute_set_new_memory less than 0");

    report.attribute_set_unique_memory =
        report.attribute_set_unique_memory - group_attr_unique_memory;
    UT_ASSERT_MSG(report.attribute_set_unique_memory < 0,
                  "attribute_set_unique_memory less than 0");

    report.attribute_set_overhead_memory = report.attribute_set_total_memory - covered_total_memory;
    report.attribute_set_overhead_new_memory = report.attribute_set_new_memory - covered_new_memory;
    report.attribute_set_overhead_unique_memory =
        report.attribute_set_unique_memory - covered_unique_memory;

    report.detail_struct_new_memory    = report.is_instanced ? 0 : report.detail_struct_memory;
    report.detail_struct_unique_memory = report.detail_struct_new_memory;

    // The residual is the discrepancy between what we can derive and Houdini's own internal
    // APIs. Currently the only discrepancy source seems to be from the tail initializers
    // for the group attributes.

    report.residual_total_memory       = report.total_memory - all_attrs_total_memory -
                                   report.attribute_set_overhead_memory -
                                   report.index_maps_total_memory -
                                   report.group_tables_total_memory - report.detail_struct_memory;

    report.residual_new_memory = report.new_memory - all_attrs_new_memory -
                                 report.attribute_set_overhead_new_memory -
                                 report.index_maps_new_memory - report.group_tables_new_memory -
                                 report.detail_struct_new_memory;

    report.residual_unique_memory =
        report.unique_memory - all_attrs_unique_memory -
        report.attribute_set_overhead_unique_memory - report.index_maps_unique_memory -
        report.group_tables_unique_memory - report.detail_struct_unique_memory;
}

// Throws HOM_OperationFailed if node_path does not resolve to a cookable SOP or
// the cook yields no geometry. The Python wrapper (in *_ToCompile.C) translates
// that into a hou.OperationFailed, mirroring the HDK HOM sample's convention of
// having the worker throw a typed HOM_Error rather than return a status code.
void gatherDetailStats(const char*  node_path,
                       bool         want_block_ranges,
                       int          output_index,
                       DetailStats& report) {
    HOM_AutoLock hom_lock;

    report.node_path = node_path;

    OP_Node* op_node = OPgetDirector()->findNode(node_path);
    if (!op_node) throw HOM_OperationFailed("Could not find a node at the given path");
    SOP_Node* sop = CAST_SOPNODE(op_node);
    if (!sop) throw HOM_OperationFailed("Node is not a SOP");

    OP_Context context(CHgetEvalTime());

    // Figure out what output index we want to evaluate. If it is outside
    // the standard range assume it is the "default" output for view.
    const int out_idx              = (output_index >= -128 && output_index <= 127)
                                         ? output_index
                                         : static_cast<int>(sop->getOutputForView());
    report.output_index            = out_idx;

    GU_DetailHandle  output_handle = sop->cookOutput(context, out_idx, /*interests*/ nullptr);
    const GU_Detail* out_gdp       = output_handle.gdp();
    if (!out_gdp) throw HOM_OperationFailed("Node produced no cooked geometry");

    report.is_cooked            = true;

    report.detail_struct_memory = static_cast<int64>(sizeof(GU_Detail));

    UT_Array<const GU_Detail*> sources;
    gatherSources(sop, out_gdp, context, out_idx, report, sources);

    // The avoid set to separate new geometry data from geometry that is shared with
    // the inputs.
    UT::ArraySet<const void*> avoid;
    UT_Set<GA_DataId>         input_data_ids;
    if (!report.is_instanced) {
        UT_MemoryCounterGather gather(avoid);
        for (const GU_Detail* source_gdp : sources) {
            source_gdp->countMemory(gather, /*inclusive*/ true);
            gatherInputDataIds(source_gdp, input_data_ids);
        }
    }

    {
        UT_MemoryCounterNewSafe attr_counter(avoid);
        out_gdp->getAttributes().countMemory(attr_counter, /*inclusive*/ false);
        report.attribute_set_total_memory = static_cast<int64>(attr_counter.getFullCount());

        if (report.is_instanced) {
            report.attribute_set_new_memory    = 0;
            report.attribute_set_unique_memory = 0;
        } else {
            report.attribute_set_new_memory    = static_cast<int64>(attr_counter.getCount());
            report.attribute_set_unique_memory = static_cast<int64>(attr_counter.getUniqueCount());
        }
    }

    for (int i = 0; i < GROUP_TABLE_N; ++i) {
        const GA_GroupTable& table =
            (i == GROUP_TABLE_EDGE) ? static_cast<const GA_GroupTable&>(out_gdp->edgeGroups())
                                    : static_cast<const GA_GroupTable&>(
                                          out_gdp->getElementGroupTable(GROUP_TABLE_OWNERS[i]));

        DetailStats::GroupTableStats& table_stats = report.group_tables[i];

        UT_MemoryCounterNewSafe       counter(avoid);
        table.countMemory(counter, /*inclusive*/ false);
        table_stats.total_memory = static_cast<int64>(counter.getFullCount());
        if (!report.is_instanced) {
            table_stats.new_memory    = static_cast<int64>(counter.getCount());
            table_stats.unique_memory = static_cast<int64>(counter.getUniqueCount());
        }

        if (i == GROUP_TABLE_EDGE) {
            // Group edges aren't attributes so they have to be handled differently
            const GA_EdgeGroupTable& edge_table = out_gdp->edgeGroups();
            table_stats.groups.setCapacityIfNeeded(edge_table.entries());

            int64 groups_total = 0;
            for (GA_EdgeGroupTable::iterator it = edge_table.beginTraverse();
                 it != edge_table.endTraverse(); ++it) {
                const GA_EdgeGroup* group = it.group();
                if (!group) continue;

                DetailStats::GroupStats& group_stats =
                    table_stats.groups[table_stats.groups.append()];
                group_stats.name = it.name();

                UT_MemoryCounterNewSafe group_counter(avoid);
                group->countMemory(group_counter, /*inclusive*/ true);
                group_stats.total_memory = static_cast<int64>(group_counter.getFullCount());
                if (!report.is_instanced) {
                    group_stats.new_memory    = static_cast<int64>(group_counter.getCount());
                    group_stats.unique_memory = static_cast<int64>(group_counter.getUniqueCount());
                }
                groups_total += group_stats.total_memory;
            }

            table_stats.name_map_memory = table_stats.total_memory - groups_total;
            UT_ASSERT_MSG(table_stats.name_map_memory < 0, "name_map_memory less than 0");
        }

        report.group_tables_total_memory  += table_stats.total_memory;
        report.group_tables_new_memory    += table_stats.new_memory;
        report.group_tables_unique_memory += table_stats.unique_memory;
    }

    {
        UT_MemoryCounterNewSafe counter(avoid);
        out_gdp->countMemory(counter, /*inclusive*/ true);
        report.total_memory = static_cast<int64>(counter.getFullCount());
        if (!report.is_instanced) {
            report.new_memory    = static_cast<int64>(counter.getCount());
            report.unique_memory = static_cast<int64>(counter.getUniqueCount());
        }
    }

    for (GA_AttributeOwner owner : ALL_OWNERS) {
        OwnerStats& owner_stats = report.owner_stats[owner];
        gatherOwnerStats(out_gdp, owner, want_block_ranges, avoid, report.is_instanced,
                         owner_stats);
        report.index_maps_total_memory    += owner_stats.total_memory;
        report.index_maps_new_memory      += owner_stats.new_memory;
        report.index_maps_unique_memory   += owner_stats.unique_memory;
        const GA_Size           num_pages  = owner_stats.num_pages;

        const GA_AttributeDict& dict       = out_gdp->getAttributeDict(owner);
        owner_stats.attributes.setSize(dict.entries());

        exint attr_index = 0;
        for (GA_AttributeDict::iterator it = dict.begin(GA_SCOPE_INVALID); !it.atEnd(); ++it) {
            const GA_Attribute* out_attr = it.attrib();
            UT_ASSERT(out_attr);
            if (!out_attr) continue;

            AttributeStats& attrib_stats     = owner_stats.attributes[attr_index++];
            attrib_stats.name                = out_attr->getName();
            attrib_stats.owner               = owner;
            attrib_stats.scope               = out_attr->getScope();
            attrib_stats.type_name           = out_attr->getType().getTypeName();
            attrib_stats.tuple_size          = out_attr->getTupleSize();

            attrib_stats.is_tail_initialized = out_attr->isTailInitialization();
            if (attrib_stats.is_tail_initialized) report.num_tail_initializers++;

            if (report.is_instanced) {
                UT_MemoryCounterFullSafe full_counter;
                out_attr->countMemory(full_counter, /*inclusive*/ true);
                attrib_stats.total_memory = static_cast<int64>(full_counter.getFullCount());
                attrib_stats.data_id      = out_attr->getDataId();
                attrib_stats.is_data_id_found_in_inputs = true;
            } else {
                gatherAttributeStats(out_attr, avoid, input_data_ids, attrib_stats);
            }
            gatherPageStorage(out_attr, num_pages, attrib_stats);
        }
        owner_stats.attributes.setSize(attr_index);

        if (owner == GA_ATTRIB_PRIMITIVE) {
            AttributeStats& meta = owner_stats.attributes[owner_stats.attributes.append()];
            gatherPrimitiveListMeta(out_gdp, num_pages, avoid, input_data_ids, report.is_instanced,
                                    meta);
        }
    }

    reconcileTotals(report);
}

static void setStr(PY_PyObject* d, const char* k, const char* v) {
    PY_AutoObject o(PY_Py_BuildValue("s", v ? v : ""));
    PY_PyDict_SetItemString(d, k, o);
}

static void setI64(PY_PyObject* d, const char* k, int64 v) {
    PY_AutoObject o(PY_PyLong_FromLongLong((long long)v));
    PY_PyDict_SetItemString(d, k, o);
}

static void setBool(PY_PyObject* d, const char* k, bool v) {
    PY_PyDict_SetItemString(d, k, v ? PY_Py_True() : PY_Py_False());
}

static void setObjSteal(PY_PyObject* d, const char* k, PY_PyObject* o_raw) {
    PY_AutoObject o(o_raw);
    if (!o) return;
    PY_PyDict_SetItemString(d, k, o);
}

// We need to use PY_Py_BuildValue since the Houdini PY headers don't
// expose all the various build bytes helpers.
static PY_PyObject* bytesFromRaw(const void* data, size_t nbytes) {
    const char* cp = nbytes ? reinterpret_cast<const char*>(data) : "";
    return PY_Py_BuildValue("y#", cp, static_cast<PY_Py_ssize_t>(nbytes));
}

static const char* ownerLabel(GA_AttributeOwner owner) {
    switch (owner) {
        case GA_ATTRIB_POINT:     return "point";
        case GA_ATTRIB_VERTEX:    return "vertex";
        case GA_ATTRIB_PRIMITIVE: return "primitive";
        case GA_ATTRIB_GLOBAL:    return "detail";
        default:                  return "unknown";
    }
}

static const char* scopeLabel(GA_AttributeScope scope) {
    switch (scope) {
        case GA_SCOPE_PUBLIC:  return "public";
        case GA_SCOPE_PRIVATE: return "private";
        case GA_SCOPE_GROUP:   return "group";
        default:               return "invalid";
    }
}

static PY_PyObject* pyDictFromAttributeStats(const AttributeStats& attrib_stats) {
    PY_PyObject* d = PY_PyDict_New();
    if (!d) return nullptr;

    setStr(d, "type_name", attrib_stats.type_name.c_str());
    setStr(d, "scope", attrib_stats.is_meta ? "meta" : scopeLabel(attrib_stats.scope));
    setBool(d, "is_meta", attrib_stats.is_meta);
    setI64(d, "tuple_size", attrib_stats.tuple_size);
    setI64(d, "total_memory", attrib_stats.total_memory);
    setI64(d, "unique_memory", attrib_stats.unique_memory);
    setI64(d, "new_memory", attrib_stats.new_memory);
    setBool(d, "is_data_id_found_in_inputs", attrib_stats.is_data_id_found_in_inputs);
    setBool(d, "is_tail_initialized", attrib_stats.is_tail_initialized);
    setI64(d, "data_id", attrib_stats.data_id);
    setBool(d, "has_page_details", attrib_stats.has_page_details);
    if (attrib_stats.is_meta) {
        setBool(d, "is_full_representation", attrib_stats.is_full_representation);

        PY_AutoObject types(PY_PyDict_New());
        for (const PrimTypeStats& type_stats : attrib_stats.prim_types) {
            if (!types) break;
            PY_PyObject* row = PY_PyDict_New();
            if (!row) break;
            setI64(row, "type_id", type_stats.type_id);
            setI64(row, "count", type_stats.count);
            setI64(row, "total_memory", type_stats.total_memory);
            setI64(row, "new_memory", type_stats.new_memory);
            setI64(row, "unique_memory", type_stats.unique_memory);
            setObjSteal(types, type_stats.type_name.c_str(), row);
        }
        if (types) PY_PyDict_SetItemString(d, "primitive_types", types);
    }
    if (attrib_stats.has_page_details) {
        setBool(d, "has_hardened_page_details", attrib_stats.has_hardened_page_details);
        setBool(d, "is_page_table_hardened", attrib_stats.is_page_table_hardened);
        setI64(d, "num_constant_pages", attrib_stats.num_constant_pages);
        setI64(d, "num_shared_pages", attrib_stats.num_shared_pages);
        setI64(d, "num_hardened_pages", attrib_stats.num_hardened_pages);

        setObjSteal(
            d, "constant_page_bits",
            bytesFromRaw(attrib_stats.constant_page_bits.data(),
                         sizeof(UT_BitArray::BlockType) *
                             UT_BitArray::numWords(attrib_stats.constant_page_bits.size())));

        setObjSteal(
            d, "hardened_page_bits",
            bytesFromRaw(attrib_stats.hardened_page_bits.data(),
                         sizeof(UT_BitArray::BlockType) *
                             UT_BitArray::numWords(attrib_stats.hardened_page_bits.size())));
    }
    return d;
}

static PY_PyObject* pyDictFromOwnerStats(const OwnerStats& owner_stats, bool want_block_ranges) {
    PY_AutoObject d(PY_PyDict_New());
    if (!d) return nullptr;
    setI64(d, "owner", owner_stats.owner);
    setI64(d, "offset_size", owner_stats.offset_size);
    setI64(d, "index_size", owner_stats.index_size);
    setI64(d, "num_pages", owner_stats.num_pages);
    setI64(d, "total_memory", owner_stats.total_memory);
    setI64(d, "new_memory", owner_stats.new_memory);
    setI64(d, "unique_memory", owner_stats.unique_memory);
    setBool(d, "is_monotonic", owner_stats.is_monotonic);
    setBool(d, "is_trivial", owner_stats.is_trivial);
    setI64(d, "page_mask_words", UT_BitArray::numWords(owner_stats.num_pages));

    setObjSteal(d, "num_active_per_page",
                bytesFromRaw(owner_stats.num_active_per_page.getRawArray(),
                             sizeof(GA_Size) * owner_stats.num_active_per_page.size()));
    setObjSteal(d, "num_temporary_per_page",
                bytesFromRaw(owner_stats.num_temporary_per_page.getRawArray(),
                             sizeof(GA_Size) * owner_stats.num_temporary_per_page.size()));
    setObjSteal(d, "num_vacant_per_page",
                bytesFromRaw(owner_stats.num_vacant_per_page.getRawArray(),
                             sizeof(GA_Size) * owner_stats.num_vacant_per_page.size()));

    setObjSteal(d, "active_page_bits",
                bytesFromRaw(owner_stats.active_page_bits.getRawArray(),
                             sizeof(PageBits) * owner_stats.active_page_bits.size()));
    setObjSteal(d, "temporary_page_bits",
                bytesFromRaw(owner_stats.temporary_page_bits.getRawArray(),
                             sizeof(PageBits) * owner_stats.temporary_page_bits.size()));

    PY_AutoObject attribs_dict(PY_PyDict_New());
    if (!attribs_dict) return nullptr;

    for (const AttributeStats& attrib_stats : owner_stats.attributes) {
        const char*  scope_label = attrib_stats.is_meta ? "meta" : scopeLabel(attrib_stats.scope);

        PY_PyObject* scope_dict  = PY_PyDict_GetItemString(attribs_dict, scope_label);
        if (!scope_dict) {
            PY_AutoObject created(PY_PyDict_New());
            if (!created) return nullptr;

            PY_PyDict_SetItemString(attribs_dict, scope_label, created);
            scope_dict = created;
        }

        PY_AutoObject attr_dict(pyDictFromAttributeStats(attrib_stats));
        if (!attr_dict) return nullptr;

        PY_PyDict_SetItemString(scope_dict, attrib_stats.name.c_str(), attr_dict);
    }
    PY_PyDict_SetItemString(d, "attributes", attribs_dict);

    if (want_block_ranges)
        setObjSteal(d, "full_block_ranges",
                    bytesFromRaw(owner_stats.full_block_ranges.getRawArray(),
                                 sizeof(GA_Offset) * owner_stats.full_block_ranges.size()));
    PY_Py_INCREF(d);
    return d;
}

PY_PyObject* pyDictFromDetailStats(const DetailStats& report, bool want_block_ranges) {
    PY_AutoObject top(PY_PyDict_New());
    if (!top) return nullptr;

    setStr(top, "node_path", report.node_path.c_str());
    setBool(top, "is_cooked", report.is_cooked);
    setI64(top, "output_index", report.output_index);
    setBool(top, "is_instanced", report.is_instanced);

    setI64(top, "num_tail_initializers", report.num_tail_initializers);

    setI64(top, "total_memory", report.total_memory);
    setI64(top, "new_memory", report.new_memory);
    setI64(top, "unique_memory", report.unique_memory);

    {
        PY_AutoObject memory(PY_PyDict_New());
        if (!memory) return nullptr;
        setI64(memory, "attribute_set_total_memory", report.attribute_set_total_memory);
        setI64(memory, "attribute_set_new_memory", report.attribute_set_new_memory);
        setI64(memory, "attribute_set_unique_memory", report.attribute_set_unique_memory);
        setI64(memory, "attribute_set_overhead_memory", report.attribute_set_overhead_memory);
        setI64(memory, "attribute_set_overhead_new_memory",
               report.attribute_set_overhead_new_memory);
        setI64(memory, "attribute_set_overhead_unique_memory",
               report.attribute_set_overhead_unique_memory);
        setI64(memory, "index_maps_total_memory", report.index_maps_total_memory);
        setI64(memory, "index_maps_new_memory", report.index_maps_new_memory);
        setI64(memory, "index_maps_unique_memory", report.index_maps_unique_memory);
        setI64(memory, "detail_struct_memory", report.detail_struct_memory);
        setI64(memory, "detail_struct_new_memory", report.detail_struct_new_memory);
        setI64(memory, "detail_struct_unique_memory", report.detail_struct_unique_memory);
        setI64(memory, "residual_total_memory", report.residual_total_memory);
        setI64(memory, "residual_new_memory", report.residual_new_memory);
        setI64(memory, "residual_unique_memory", report.residual_unique_memory);

        {
            PY_AutoObject group_tables(PY_PyDict_New());
            if (!group_tables) return nullptr;
            for (int i = 0; i < GROUP_TABLE_N; ++i) {
                const DetailStats::GroupTableStats& table_stats = report.group_tables[i];
                PY_AutoObject                       d(PY_PyDict_New());
                if (!d) return nullptr;
                setI64(d, "total_memory", table_stats.total_memory);
                setI64(d, "new_memory", table_stats.new_memory);
                setI64(d, "unique_memory", table_stats.unique_memory);

                if (i == GROUP_TABLE_EDGE) {
                    setI64(d, "name_map_memory", table_stats.name_map_memory);
                    PY_AutoObject groups(PY_PyDict_New());
                    if (!groups) return nullptr;
                    for (const DetailStats::GroupStats& g : table_stats.groups) {
                        PY_AutoObject gd(PY_PyDict_New());
                        if (!gd) return nullptr;
                        setI64(gd, "total_memory", g.total_memory);
                        setI64(gd, "new_memory", g.new_memory);
                        setI64(gd, "unique_memory", g.unique_memory);
                        PY_PyDict_SetItemString(groups, g.name.c_str(), gd);
                    }
                    PY_PyDict_SetItemString(d, "groups", groups);
                }
                PY_PyDict_SetItemString(group_tables, GROUP_TABLE_NAMES[i], d);
            }
            PY_PyDict_SetItemString(memory, "group_tables", group_tables);
        }
        setI64(memory, "group_tables_total_memory", report.group_tables_total_memory);
        setI64(memory, "group_tables_new_memory", report.group_tables_new_memory);
        setI64(memory, "group_tables_unique_memory", report.group_tables_unique_memory);
        PY_PyDict_SetItemString(top, "memory", memory);
    }

    setI64(top, "page_size", GA_PAGE_SIZE);
    setI64(top, "per_page_count_bytes", static_cast<int64>(sizeof(GA_Size)));
    setI64(top, "page_word_bytes", static_cast<int64>(sizeof(UT_BitArray::BlockType)));
    setI64(top, "page_occupancy_words_per_page",
           static_cast<int64>(sizeof(PageBits) / sizeof(uint32)));

    PY_AutoObject owners(PY_PyDict_New());
    if (!owners) return nullptr;

    for (GA_AttributeOwner owner : ALL_OWNERS) {
        const OwnerStats& owner_stats = report.owner_stats[owner];
        setObjSteal(owners, ownerLabel(owner),
                    pyDictFromOwnerStats(owner_stats, want_block_ranges));
    }
    PY_PyDict_SetItemString(top, "owners", owners);

    PY_Py_INCREF(top);
    return top;
}

PY_PyObject* gatherGeometryStats(const char* node_path, bool block_ranges, int output_index) {
    DetailStats report;
    gatherDetailStats(node_path, block_ranges, output_index, report);
    return pyDictFromDetailStats(report, block_ranges);
}

}  // namespace page_tools

// Exception handling, see $HT/samples/HOM/_hdk_sample_hom_extensions.C
static PY_PyObject* createHouException(const char*   exception_class_name,
                                       const char*   instance_message,
                                       PY_PyObject*& exception_class) {
    exception_class = nullptr;

    PY_AutoObject hou_module(PY_PyImport_ImportModule("hou"));
    if (!hou_module) return nullptr;

    PY_PyObject* hou_module_dict = PY_PyModule_GetDict(hou_module);
    exception_class              = PY_PyDict_GetItemString(hou_module_dict, exception_class_name);
    if (!exception_class) {
        PY_PyErr_SetString(PY_PyExc_RuntimeError(), "Could not find exception class in hou module");
        return nullptr;
    }

    PY_AutoObject args(PY_Py_BuildValue("(s)", instance_message));
    if (!args) return nullptr;
    return PY_PyObject_Call(exception_class, args, /*kwargs=*/nullptr);
}

// Wrapper to handle exceptions, see $HT/samples/HOM/_hdk_sample_hom_extensions.C
static PY_PyObject* report_Wrapper(PY_PyObject* /*self*/, PY_PyObject* args) {
    const char* node_path    = nullptr;
    int         block_ranges = 0;  // 'p' (0/1)
    int         output_index = page_tools::OUTPUT_FROM_VIEW;
    if (!PY_PyArg_ParseTuple(args, "s|pi", &node_path, &block_ranges, &output_index))
        return nullptr;

    try {
        return page_tools::gatherGeometryStats(node_path, block_ranges != 0, output_index);
    } catch (HOM_Error& error) {
        // The _hdk_sample_hom_extensions.C uses UTunmangleClassNameFromTypeIdName
        // to get the exeption class name, but I believe we can also fetch it this
        // way which looks a bit more straight forward.
        const std::string exception_class_name = error.exceptionTypeName();

        PY_PyObject*      exception_class      = nullptr;
        PY_AutoObject     exception_instance(createHouException(
            exception_class_name.c_str(), error.instanceMessage().c_str(), exception_class));
        if (!exception_instance) return nullptr;
        PY_PyErr_SetObject(exception_class, exception_instance);
        return nullptr;
    } catch (...) {
        PY_PyErr_SetString(PY_PyExc_RuntimeError(),
                           "Unexpected C++ exception in _page_tools.report()");
        return nullptr;
    }
}

// Main entry point, refer to $HT/samples/HOM/_hdk_sample_hom_extensions.C
PY_PyMODINIT_FUNC PyInit__page_tools(void) {
    PY_PyObject* module = nullptr;
    {
        PY_InterpreterAutoLock interpreter_auto_lock;

        static PY_PyMethodDef  methods[] = {
            {"report", report_Wrapper, PY_METH_VARARGS(),
              "report(node_path, block_ranges=False, output_index=<view flag>) -> "},
            {nullptr, nullptr, 0, nullptr}};

        module = PY_Py_InitModule("_page_tools", methods);
    }
    return module;
}
