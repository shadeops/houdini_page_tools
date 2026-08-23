// clang-format off
// Return dict schema
//
// All *_memory values will be in bytes
// All bitpacking assumes little endian
//
// {
//  'node_path': str,
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
//      # all the attributes in the detail, deduplicated
//      'attributes_total_memory': int,
//      'attributes_new_memory': int,
//      'attributes_unique_memory': int,
//
//      'primitive_list_total_memory': int,
//      'primitive_list_new_memory': int,
//      'primitive_list_unique_memory': int,
//
//      'attribute_set_total_memory': int,
//      'attribute_set_new_memory': int,
//      'attribute_set_unique_memory': int,
//
//      'index_maps_total_memory': int,
//      'index_maps_new_memory': int,
//      'index_maps_unique_memory': int,
//
//      'gu_detail_total_memory': int,
//      'gu_detail_new_memory': int,
//      'gu_detail_unique_memory': int,
//
//      # This is the SOP's countMemory report minus the various measured countMemory stats
//      # that are accessible.
//      # There is no interface for countMemory in ga_TailInitializeTable
//      # So if our totals of the parts don't match Houdini's total count it is likely due
//      # to the uncounted tailInitializers.
//      # If we encounter residuals > 0, 'num_tail_initializers' is zero
//      # and no groups have been modified or deleted that is unexpected and there might be
//      # an accounting bug. Additionally if residuals are < 0 that also points to an accounting
//      # bug that should be reported.
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
//          # Edges aren't stored like the other groups, which are attribute based.
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
//              'name_map_total_memory': int,
//              'name_map_new_memory': int,
//              'name_map_unique_memory': int,
//          },
//      },
//      'group_tables_total_memory': int,
//      'group_tables_new_memory': int,
//      'group_tables_unique_memory': int,
//  },
//
//  'page_size': int,                          # GA_PAGE_SIZE, needs to be 1024
//  'per_page_count_bytes': int,               # bytes per entry in num_[active|temporary|vacant]_per_page
//  'page_word_bytes': int,                    # word size of the per-page masks
//  'page_occupancy_words_per_page': int,      # uint32 words per page in the occupancy masks
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
//          'index_map_total_memory': int,
//          'index_map_new_memory': int,
//          'index_map_unique_memory': int,
//
//          'attributes_total_memory': int,
//          'attributes_new_memory': int,
//          'attributes_unique_memory': int,
//
//          'page_mask_words': int,
//          'num_active_per_page': bytes,      # i64 array, one element per page
//          'num_temporary_per_page': bytes,   # i64 array, one element per page
//          'num_vacant_per_page': bytes,      # i64 array, one element per page
//          'active_page_bits': bytes,         # bitarray, one bit per page offset, uint32[32]
//          'temporary_page_bits': bytes,      # bitarray, one bit per page offset, uint32[32]
//          'full_block_ranges': bytes,        # int64[2] array, [start, end)
//
//          'attributes': {
//              'public | private | group': {
//                  <attribute_name> str: {
//                      'type_name': str,      # registered ATI type, e.g. "numeric"
//                      'scope': str,          # 'public | private | group'
//                      'tuple_size': int,
//
//                      'total_memory': int,
//                      'unique_memory': int,
//                      'new_memory': int,
//
//                      # Attributes can share their pages across the same attribute
//                      # or other attributes (even in different owners)
//                      # We use this to reconcile the totals
//                      'intra_detail_sharing_memory': int,
//
//                      # We can keep track of what pages are shared with each other.
//                      # To do so we need to track of who has an interest in the
//                      # shared page.
//                      # Always sorted
//                      'shares_with_attrib_keys': [
//                          {'owner': str, 'scope': str, 'name': str},
//                      ],
//
//                     # Provides a mini database of which pages are shared with whom
//                      'memory_block_sharing': None | {
//                          # These are internal block ids that will be used to sharing
//                          # matching.
//                          'memory_block_ids': bytes,           # uint32 per page
//                          # A index into "shares_with_mapping"
//                          'shares_with_mapping_indices': bytes,  # uint32 per page
//                          # A list of indices of matching attribute keys
//                          # Inner list of shares_with_attrib_key indices are sorted
//                          'shares_with_mapping': [[shares_with_attrib_key index, ...], ...],
//                      },
//
//                      'is_data_id_found_in_inputs': bool,
//
//                      # Only groups seem to be tail_initialized in practice
//                      'is_tail_initialized': bool,
//
//                      'data_id': int,        # -1 when unset
//
//                      # While almost all attributes are backed by pages, many attribute
//                      # types do not expose their page tables via a public interface,
//                      # so we can not count them. (Array, blob, index-pair attributes for example)
//                      # We only provide page details for those types with a public interface.
//                      'page_details': None | {
//                          # Some apis, like groups, don't provide details on if the pages
//                          # are hardened or not. In these cases we can only know if they are
//                          # "constant" or not. So if a page isn't constant and
//                          # "has_hardened_details" is false, then the shared/hardened pages/bits
//                          # are in an "unknown" state.
//                          'has_hardened_details': bool,
//
//                          'is_page_table_hardened': bool,
//
//                          # Our total page counts equal the sum of these three fields
//                          # if has_hardened_details is true
//                          'num_constant_pages': int,
//                          'num_shared_pages': int,
//                          'num_hardened_pages': int,
//
//                          # This is a special case of a "constant" page where it is
//                          # also shared under certain conditions (a tuple at least
//                          # sizeof(PageTableEntry) wide, and not all zero).
//                          'num_constant_shared_pages': int,
//
//                          'constant_page_bits': bytes,    # bitarray (one bit per page)
//                          # if has_hardened_details is false the values will all be 0
//                          # and a page bit will either be constant or unknown
//                          'hardened_page_bits': bytes,    # bitarray (one bit per page)
//                          'shared_page_bits': bytes,      # bitarray (one bit per page)
//                      },
//                  },
//              },
//          },
//      },
//  },
//
//  'primitive_list': {
//      'total_memory': int,
//      'new_memory': int,
//      'unique_memory': int,
//
//      'data_id': int,        # -1 when unset
//      'is_data_id_found_in_inputs': bool,
//
//      # When full representation is false the primitive list is backed by a page array.
//      'is_full_representation': bool,
//      # This is only populated when is_full_representation is true
//      'primitive_types': {
//          <type_name> str: {
//              'type_id': int,
//              'count': int,
//              'total_memory': int,
//              'new_memory': int,
//              'unique_memory': int,
//          },
//      },
//      'page_details': None | { ... },      # same page details as attributes
//  },
// }
// clang-format on

// Needs to be included first per comments in the header
#include <PY/PY_CPythonAPI.h>

#include <GA/GA_ATIDict.h>
#include <GA/GA_ATIGroupBool.h>
#include <GA/GA_ATINumeric.h>
#include <GA/GA_ATIString.h>
#include <GA/GA_ATITopology.h>
#include <GA/GA_Attribute.h>
#include <GA/GA_AttributeDict.h>
#include <GA/GA_AttributeSet.h>
#include <GA/GA_AttributeType.h>
#include <GA/GA_EdgeGroup.h>
#include <GA/GA_EdgeGroupTable.h>
#include <GA/GA_ElementGroupTable.h>
#include <GA/GA_IndexMap.h>
#include <GA/GA_Iterator.h>
#include <GA/GA_PageArray.h>
#include <GA/GA_Primitive.h>
#include <GA/GA_PrimitiveFactory.h>
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
#include <PY/PY_InterpreterAutoLock.h>
#include <SOP/SOP_Node.h>
#include <SYS/SYS_Types.h>
#include <UT/UT_Array.h>
#include <UT/UT_ArrayMap.h>
#include <UT/UT_ArraySet.h>
#include <UT/UT_Assert.h>
#include <UT/UT_BitArray.h>
#include <UT/UT_MemoryCounter.h>
#include <UT/UT_Set.h>
#include <UT/UT_Storage.h>
#include <UT/UT_StringHolder.h>
#include <UT/UT_WorkBuffer.h>

#include <string>

// Our bitarrays assume a page size of 1024, so verify just in case.
static_assert(GA_PAGE_SIZE == 1024, "PageBits assumes GA_PAGE_SIZE == 1024");

namespace page_tools {

// 1024 bits = GA_PAGE_SIZE
// packed as 32 x uint32.
// page offset -> word (page_offset>>5)
// bit(page_offset&31).
struct PageBits {
    uint32 bits[32] = {0};
};

inline void
setPageBit(uint32* mask, GA_Size page_offset) {
    mask[page_offset >> 5] |= (static_cast<uint32>(1) << (page_offset & 31));
}

// NOTE:
//  Occasionally define GA_STRICT_TYPES to ensure we aren't mixing GA_Offset
//  and GA_Index incorrectly. (Normally both an exint)

// Measurements from the UT_MemoryCounters
struct MemoryCounts {
    int64 total_bytes  = 0;  // getFullCount()
    int64 new_bytes    = 0;  // getCount()
    int64 unique_bytes = 0;  // getUniqueCount()

    // Zeroes every field but total_bytes.
    void zeroNonTotal() {
        new_bytes    = 0;
        unique_bytes = 0;
    }

    MemoryCounts& operator+=(const MemoryCounts& that) {
        total_bytes  += that.total_bytes;
        new_bytes    += that.new_bytes;
        unique_bytes += that.unique_bytes;
        return *this;
    }

    MemoryCounts& operator+=(int64 bytes) {
        total_bytes  += bytes;
        new_bytes    += bytes;
        unique_bytes += bytes;
        return *this;
    }

    MemoryCounts& operator-=(const MemoryCounts& that) {
        total_bytes  -= that.total_bytes;
        new_bytes    -= that.new_bytes;
        unique_bytes -= that.unique_bytes;
        return *this;
    }
};

inline MemoryCounts
operator-(MemoryCounts lhs, const MemoryCounts& rhs) {
    lhs -= rhs;
    return lhs;
}

template <typename CounterT>
static MemoryCounts
memoryCountsFromCounter(const CounterT& counter) {
    MemoryCounts counts;
    counts.total_bytes  = static_cast<int64>(counter.getFullCount());
    counts.new_bytes    = static_cast<int64>(counter.getCount());
    counts.unique_bytes = static_cast<int64>(counter.getUniqueCount());
    return counts;
}

struct PrimTypeStats {
    UT_StringHolder type_name;
    int             type_id = -1;
    GA_Size         count   = 0;
    MemoryCounts    memory;
};

static const int32 INVALID_TRACKER_INDEX = -1;

struct SharesWithAttribKey {
    GA_AttributeOwner owner = GA_ATTRIB_POINT;
    GA_AttributeScope scope = GA_SCOPE_PUBLIC;
    UT_StringHolder   name;

    int32             tracker_index = INVALID_TRACKER_INDEX;

    bool              operator<(const SharesWithAttribKey& that) const {
        if (owner != that.owner) return owner < that.owner;
        if (scope != that.scope) return scope < that.scope;
        return name < that.name;
    }
};

struct PageStats {
    // If false, no page details available and all other fields are invalid
    // and should be left at 0.
    bool        has_details               = false;
    bool        has_hardened_details      = false;
    bool        is_page_table_hardened    = false;

    GA_Size     num_constant_pages        = 0;
    GA_Size     num_shared_pages          = 0;
    GA_Size     num_hardened_pages        = 0;
    GA_Size     num_constant_shared_pages = 0;

    UT_BitArray constant_page_bits;
    UT_BitArray hardened_page_bits;
    UT_BitArray shared_page_bits;
};

struct AttributeStats {
    UT_StringHolder               name;
    GA_AttributeOwner             owner = GA_ATTRIB_POINT;
    GA_AttributeScope             scope = GA_SCOPE_PUBLIC;

    UT_StringHolder               type_name;
    int                           tuple_size = 0;

    MemoryCounts                  memory;
    int64                         intra_detail_sharing_memory = 0;

    UT_Array<SharesWithAttribKey> shares_with_attrib_keys;

    bool                          has_memory_block_sharing = false;
    UT_Array<int32>               memory_block_ids;
    UT_Array<int32>               shares_with_mapping_indices;
    UT_Array<UT_Array<exint>>     shares_with_mapping;

    GA_DataId                     data_id                    = GA_INVALID_DATAID;
    bool                          is_data_id_found_in_inputs = false;
    bool                          is_tail_initialized        = false;

    PageStats                     page_stats;

    UT_Array<const void*>         memory_block_pointers;
};

struct PrimitiveListStats {
    MemoryCounts            memory;
    GA_DataId               data_id                    = GA_INVALID_DATAID;
    bool                    is_data_id_found_in_inputs = false;
    bool                    is_full_representation     = false;
    UT_Array<PrimTypeStats> prim_types;
    PageStats               page_stats;
};

struct OwnerStats {
    GA_AttributeOwner owner       = GA_ATTRIB_POINT;
    GA_Offset         offset_size = GA_Offset(0);
    GA_Index          index_size  = GA_Index(0);
    GA_Size           num_pages   = 0;

    MemoryCounts      index_map_memory;

    // De-duplicated, measured by this owner's DedupedMemoryCounts rather than summed
    // back out of the attribute rows.
    MemoryCounts             attribs_memory;

    bool                     is_monotonic = false;
    bool                     is_trivial   = false;

    UT_Array<GA_Size>        num_active_per_page;
    UT_Array<GA_Size>        num_temporary_per_page;
    UT_Array<GA_Size>        num_vacant_per_page;
    UT_Array<PageBits>       active_page_bits;
    UT_Array<PageBits>       temporary_page_bits;

    UT_Array<GA_Offset>      full_block_ranges;

    UT_Array<AttributeStats> attribs;
};

static const int               ELEMENT_GROUP_TABLE_N                             = 3;
static const GA_AttributeOwner ELEMENT_GROUP_TABLE_OWNERS[ELEMENT_GROUP_TABLE_N] = {
    GA_ATTRIB_POINT,
    GA_ATTRIB_PRIMITIVE,
    GA_ATTRIB_VERTEX
};

struct DetailStats {
    UT_StringHolder node_path;

    int             output_index = 0;
    bool            is_instanced = false;

    //   total_memory =
    //       attributes_total_memory
    //       + primitive_list_total_memory
    //       + attribute_set_total_memory
    //       + index_maps_total_memory
    //       + group_tables_total_memory
    //       + gu_detail_total_memory
    //       + residual_total_memory

    MemoryCounts       memory;
    MemoryCounts       attribs_memory;        // deduplicated memory counts
    MemoryCounts       attribute_set_memory;  // GA_AttributeSet overhead
    MemoryCounts       primitive_list_memory;
    MemoryCounts       index_maps_memory;

    PrimitiveListStats primitive_list;

    struct EdgeGroupStats {
        UT_StringHolder name;
        MemoryCounts    memory;
    };

    struct EdgeGroupTableStats {
        MemoryCounts             memory;
        MemoryCounts             name_map_memory;
        UT_Array<EdgeGroupStats> groups;
    };

    MemoryCounts        element_group_table_memory[ELEMENT_GROUP_TABLE_N];
    EdgeGroupTableStats edge_group_table;
    MemoryCounts        group_tables_memory;
    MemoryCounts        gu_detail_memory;
    MemoryCounts        residual_memory;

    GA_Size             num_tail_initializers = 0;

    OwnerStats          owner_stats[GA_ATTRIB_OWNER_N];
};

// What one shared memory block accumulates during the counting pass.
struct SharedMemoryBlock {
    int64 size                            = 0;
    int32 sharing_set_id                  = -1;

    int32 attrib_pass                     = -1;
    int32 attrib_visits                   = 0;             // times tracker visited the attribute
    int32 owner_visits[GA_ATTRIB_OWNER_N] = {0, 0, 0, 0};  // times tracker visited the owner type
    int32 all_attrib_visits               = 0;             // total times tracker visited

    bool  is_new                          = false;
};

struct MemoryBlockPageUse {
    int32 pages          = 0;
    int32 sharing_set_id = -1;
    int32 block_id       = 0;
};

}  // namespace page_tools

// Required because UT::DefaultClearer's primary template is declared without a definition
namespace UT {
template <>
struct DefaultClearer<page_tools::SharedMemoryBlock> {
    static void clear(page_tools::SharedMemoryBlock& v) { v = page_tools::SharedMemoryBlock(); }
    static bool isClear(const page_tools::SharedMemoryBlock& v) { return v.all_attrib_visits == 0; }
    static void clearConstruct(page_tools::SharedMemoryBlock* p) { clear(*p); }
    static const bool clearNeedsDestruction = false;
};

template <>
struct DefaultClearer<page_tools::MemoryBlockPageUse> {
    static void clear(page_tools::MemoryBlockPageUse& v) { v = page_tools::MemoryBlockPageUse(); }
    static bool isClear(const page_tools::MemoryBlockPageUse& v) { return v.pages == 0; }
    static void clearConstruct(page_tools::MemoryBlockPageUse* p) { clear(*p); }
    static const bool clearNeedsDestruction = false;
};
}  // namespace UT

namespace page_tools {

struct DedupedMemoryCounts : MemoryCounts {
    void visit(exint visits, size_t size, exint refcount, bool is_new) {
        if (visits == 1) {
            total_bytes += static_cast<int64>(size);
            if (is_new) new_bytes += static_cast<int64>(size);
        }
        if (visits == refcount) unique_bytes += static_cast<int64>(size);
    }
};

// Tracks the shared memory block usage across attributes. Use to deduplicate and record
// sharing among attributes.
struct MemoryBlockTracker {
    MemoryBlockTracker(const UT::ArraySet<const void*>& avoid) : avoid_set(avoid) {}

    // To keep track of all the attributes visited we store lookup keys in a continuous
    // array. The lookup key allows us to distinguish the different attributes in
    // the DetailStats.owner_stats[owner] arrays
    // (This results in a weak coupling with DetailStats)
    struct AttribLookupKey {
        GA_AttributeOwner owner = GA_ATTRIB_POINT;
        exint             index = 0;
    };

    DedupedMemoryCounts       all_attrib_memory;
    DedupedMemoryCounts       owners_attrib_memory[GA_ATTRIB_OWNER_N];

    UT_Array<UT_Array<int32>> sharing_sets;
    UT_Array<AttribLookupKey> attrib_keys;

    int32                     nextAttributePass() { return ++last_attrib_pass; }

    int32                     registerAttribute(GA_AttributeOwner owner_id, exint index) {
        UT_ASSERT_MSG(attrib_keys.size() <= SYS_INT32_MAX, "more attributes than an int32 holds");
        const int32 tracker_index = static_cast<int32>(attrib_keys.size());
        attrib_keys.append();
        AttribLookupKey& attrib_key = attrib_keys.last();
        attrib_key.owner            = owner_id;
        attrib_key.index            = index;
        return tracker_index;
    }

    // Returns true when this attribute has already visited the block, matching
    // UT_MemoryCounter::countShared
    bool countSharedVisit(
        size_t            size,
        exint             refcount,
        const void*       block_pointer,
        GA_AttributeOwner owner_id,
        int32             attrib_pass,
        int32             tracker_index,
        MemoryCounts&     attrib_shared_memory
    ) {
        SharedMemoryBlock& block = memory_blocks[block_pointer];

        if (block.all_attrib_visits == 0) block.is_new = (avoid_set.count(block_pointer) == 0);
        block.size = static_cast<int64>(size);

        if (block.attrib_pass != attrib_pass) {
            block.attrib_pass   = attrib_pass;
            block.attrib_visits = 0;
        }
        ++block.attrib_visits;

        const bool already_visited = block.attrib_visits > 1;
        if (!already_visited) {
            attrib_shared_memory.total_bytes += static_cast<int64>(size);
            if (block.is_new) attrib_shared_memory.new_bytes += static_cast<int64>(size);
            block.sharing_set_id = addToSharingSet(block.sharing_set_id, tracker_index);
        } else if (block.attrib_visits == refcount) {
            attrib_shared_memory.unique_bytes += static_cast<int64>(size);
        }

        ++block.all_attrib_visits;
        all_attrib_memory.visit(block.all_attrib_visits, size, refcount, block.is_new);

        ++block.owner_visits[owner_id];
        owners_attrib_memory[owner_id].visit(
            block.owner_visits[owner_id], size, refcount, block.is_new
        );

        return already_visited;
    }

    void addUnshared(int64 bytes, GA_AttributeOwner owner_id) {
        all_attrib_memory              += bytes;
        owners_attrib_memory[owner_id] += bytes;
    }

    int32 addToSharingSet(int32 sharing_set_id, int32 tracker_index) {
        // Two int32s fill the uint64 exactly, so the pair packs without collision and
        // needs no hash for a pair type. Each half goes through uint32 to keep it to its
        // own 32 bits. The extra step is due to Houdini 21 using C++ 17 where left shifting
        // negative numbers is undefined.
        const uint64 key = (static_cast<uint64>(static_cast<uint32>(sharing_set_id)) << 32) |
                           static_cast<uint32>(tracker_index);
        auto         it  = sharing_set_transitions.find(key);
        if (it != sharing_set_transitions.end()) return it->second;

        UT_Array<int32> next_sharing_set;
        if (sharing_set_id >= 0) next_sharing_set = sharing_sets(sharing_set_id);
        if (next_sharing_set.find(tracker_index) < 0) {
            next_sharing_set.append(tracker_index);
            next_sharing_set.sort();
        }

        const int32 new_id = static_cast<int32>(sharing_sets.size());
        sharing_sets.append(next_sharing_set);
        sharing_set_transitions[key] = new_id;
        return new_id;
    }

    const UT::ArraySet<const void*>&             avoid_set;
    UT::ArrayMap<const void*, SharedMemoryBlock> memory_blocks;
    UT::ArrayMap<uint64, int32>                  sharing_set_transitions;
    int32                                        last_attrib_pass = -1;
};

// Measures one attribute like UT_MemoryCounterNewSafe does, while feeding the
// memory-block tracker.
class MemoryCounterRecorder : public UT_MemoryCounter {
   public:
    MemoryCounterRecorder(MemoryBlockTracker& tracker, int32 tracker_index, GA_AttributeOwner owner)
        : UT_MemoryCounter(/*countshared*/ true, /*countunshared*/ true),
          myTracker(tracker),
          myTrackerIndex(tracker_index),
          myOwner(owner),
          myAttribPass(tracker.nextAttributePass()) {}

    // Adapted from UT_MemoryCounterNewSafe::countShared
    bool countShared(size_t size, exint refcount, const void* block_pointer) override {
        UT_ASSERT_P(refcount > 0);
        UT_ASSERT_P(block_pointer);

        if (refcount == 1) {
            UT_MemoryCounter::countUnshared(size);
            return false;
        }

        return myTracker.countSharedVisit(
            size,
            refcount,
            block_pointer,
            myOwner,
            myAttribPass,
            myTrackerIndex,
            myAttribSharedMemory
        );
    }

    size_t getCount() const override {
        return UT_MemoryCounter::getCount() + static_cast<size_t>(myAttribSharedMemory.new_bytes);
    }
    // Adapted from UT_MemoryCounterNewSafe::getFullCount
    int64 getFullCount() const {
        return static_cast<int64>(UT_MemoryCounter::getCount()) + myAttribSharedMemory.total_bytes;
    }
    // Adapted from UT_MemoryCounterNewSafe::getUniqueCount
    int64 getUniqueCount() const {
        return static_cast<int64>(UT_MemoryCounter::getCount()) + myAttribSharedMemory.unique_bytes;
    }

    int64 getUnsharedCount() const { return static_cast<int64>(UT_MemoryCounter::getCount()); }

   private:
    MemoryBlockTracker& myTracker;
    int32               myTrackerIndex;
    GA_AttributeOwner   myOwner;
    int32               myAttribPass;
    MemoryCounts        myAttribSharedMemory;
};

// Hack for checking out of range outputs on nodes.
static const int               OUTPUT_FROM_VIEW = 0x7fffffff;

static const GA_AttributeOwner ALL_OWNERS[GA_ATTRIB_OWNER_N] =
    {GA_ATTRIB_VERTEX, GA_ATTRIB_POINT, GA_ATTRIB_PRIMITIVE, GA_ATTRIB_GLOBAL};

static void
gatherInputDataIds(const GU_Detail* in_gdp, UT_Set<GA_DataId>& data_ids) {
    for (GA_AttributeOwner owner : ALL_OWNERS) {
        const GA_AttributeDict& dict = in_gdp->getAttributeDict(owner);

        for (GA_AttributeDict::iterator it = dict.begin(); !it.atEnd(); ++it) {
            const GA_Attribute* attrib = it.attrib();
            if (!attrib) continue;

            const GA_DataId data_id = attrib->getDataId();
            if (data_id != GA_INVALID_DATAID) data_ids.insert(data_id);
        }
    }

    const GA_DataId prim_list_id = in_gdp->getPrimitiveList().getDataId();
    if (prim_list_id != GA_INVALID_DATAID) data_ids.insert(prim_list_id);
}

// GA_IndexMap has no page-count accessor, so derive it the way UT_PageArray::numPages does.
static GA_Size
numPagesForIndexMap(const GA_IndexMap& index_map) {
    return (GA_Size(index_map.offsetSize()) + GA_PAGE_SIZE - 1) >> GA_PAGE_BITS;
}

// Heeding warning in UT_PageArray and casting to the correct type
//  /// Returns a pointer to the data of a page.
//  /// WARNING: DO NOT call this if DATA_T is void!  The pointer returned
//  ///          can depend on the type.
template <typename PageArrayT>
static const void*
pageDataPointer(const PageArrayT& page_array, GA_Size page) {
    const UT_PageNum page_num(page);
    switch (GAStorageToUTStorage(page_array.getStorage())) {
        // This abhorrent C++ syntax is "The template disambiguator for dependent names"
        // clang-format off
        case UT_Storage::INT8:      return page_array.template castType<int8>().getPageData(page_num);
        case UT_Storage::UINT8:     return page_array.template castType<uint8>().getPageData(page_num);
        case UT_Storage::INT16:     return page_array.template castType<int16>().getPageData(page_num);
        case UT_Storage::INT32:     return page_array.template castType<int32>().getPageData(page_num);
        case UT_Storage::INT64:     return page_array.template castType<int64>().getPageData(page_num);
        case UT_Storage::REAL16:    return page_array.template castType<fpreal16>().getPageData(page_num);
        case UT_Storage::REAL32:    return page_array.template castType<fpreal32>().getPageData(page_num);
        case UT_Storage::REAL64:    return page_array.template castType<fpreal64>().getPageData(page_num);
        case UT_Storage::INVALID:   break;
            // clang-format on
    }
    UT_ASSERT_MSG(false, "pageDataPointer called on an array with no readable storage");
    return nullptr;
}

template <typename PageArrayT>
static void
pageStorageFromArray(
    const PageArrayT&         page_array,
    GA_Size                   num_pages,
    const MemoryBlockTracker& tracker,
    PageStats&                page_stats,
    UT_Array<const void*>&    memory_block_pointers
) {
    page_stats.has_details            = true;
    page_stats.has_hardened_details   = true;
    page_stats.is_page_table_hardened = page_array.isTableHardened();
    page_stats.constant_page_bits.setSize(num_pages);
    page_stats.hardened_page_bits.setSize(num_pages);
    page_stats.shared_page_bits.setSize(num_pages);
    memory_block_pointers.setSize(num_pages);
    memory_block_pointers.constant(nullptr);

    for (GA_Size page = 0; page < num_pages; ++page) {
        const bool  is_constant   = page_array.isPageConstant(GA_PageNum(page));
        const void* block_pointer = nullptr;

        bool        is_shared     = false;
        if (is_constant) {
            block_pointer = pageDataPointer(page_array, page);
            is_shared = block_pointer != nullptr && tracker.memory_blocks.count(block_pointer) != 0;
        } else {
            is_shared = !page_array.isPageHard(GA_PageNum(page));
            if (is_shared) block_pointer = pageDataPointer(page_array, page);
        }
        memory_block_pointers(page) = block_pointer;

        page_stats.constant_page_bits.setBitFast(page, is_constant);
        page_stats.shared_page_bits.setBitFast(page, is_shared);

        // Instead of calling isPageHard, we'll do the same exact logic to avoid having to relookup
        // the page in UT_PageArray.isPageHard
        // page_stats.hardened_page_bits.setBitFast(page, page_array.isPageHard(GA_PageNum(page));
        page_stats.hardened_page_bits.setBitFast(page, !is_constant && !is_shared);

        // Kept mutually exclusive, so the three still sum to num_pages. Constant-and-shared
        // is reported alongside as a subset of the constant count, not as a fourth class.
        if (is_constant)
            ++page_stats.num_constant_pages;
        else if (is_shared)
            ++page_stats.num_shared_pages;
        else
            ++page_stats.num_hardened_pages;
        // In some cases, a page can be constant and shared. This only happens if the tuple
        // the page holds is at least sizeof(PageTableEntry) wide and not all 0 values.
        // In these cases a constant page needs to allocate on the heap and thus can be shared.
        if (is_constant && is_shared) ++page_stats.num_constant_shared_pages;
    }
}

static void
gatherPageStorage(
    const GA_Attribute*       attrib,
    GA_Size                   num_pages,
    const MemoryBlockTracker& tracker,
    PageStats&                page_stats,
    UT_Array<const void*>&    memory_block_pointers
) {
    if (const GA_ATINumeric* n = GA_ATINumeric::cast(attrib)) {
        pageStorageFromArray(n->getData(), num_pages, tracker, page_stats, memory_block_pointers);
    } else if (const GA_ATITopology* t = GA_ATITopology::cast(attrib)) {
        pageStorageFromArray(t->getData(), num_pages, tracker, page_stats, memory_block_pointers);
    } else if (const GA_ATIString* s = GA_ATIString::cast(attrib)) {
        pageStorageFromArray(
            s->getHandleData(), num_pages, tracker, page_stats, memory_block_pointers
        );
    } else if (const GA_ATIDict* d = GA_ATIDict::cast(attrib)) {
        pageStorageFromArray(
            d->getHandleData(), num_pages, tracker, page_stats, memory_block_pointers
        );
    } else if (const GA_ATIGroupBool* g = GA_ATIGroupBool::cast(attrib)) {
        page_stats.has_details            = true;
        page_stats.is_page_table_hardened = false;
        page_stats.constant_page_bits.setSize(num_pages);

        // There is no public API to determine if pages are hardened or shared for a group.
        // So we set to the num_pages and rely on them all being initialized to 0.
        page_stats.has_hardened_details = false;
        page_stats.hardened_page_bits.setSize(num_pages);
        page_stats.shared_page_bits.setSize(num_pages);

        for (GA_Size page = 0; page < num_pages; ++page) {
            const bool is_constant = g->isPageConstant(GA_PageNum(page));
            page_stats.constant_page_bits.setBitFast(page, is_constant);
            if (is_constant) ++page_stats.num_constant_pages;
        }
    } else {
        page_stats.has_details = false;
    }
}

static void
gatherAttributeStats(
    const GA_Attribute*      out_attrib,
    const UT_Set<GA_DataId>& input_data_ids,
    MemoryBlockTracker&      tracker,
    int32                    tracker_index,
    GA_Size                  num_pages,
    AttributeStats&          attrib_stats
) {
    // Instead of using the UT_MemoryCounterNewSafe for gather memory counts we instead use
    // a specialized version which does the same thing as UT_MemoryCounterNewSafe but also
    // records who is using which memory blocks. This allows us to map what blocks of memory
    // are shared with other attributes. (Including self sharing.)
    MemoryCounterRecorder counter(tracker, tracker_index, attrib_stats.owner);
    out_attrib->countMemory(counter, /*inclusive*/ true);
    tracker.addUnshared(counter.getUnsharedCount(), attrib_stats.owner);

    attrib_stats.memory  = memoryCountsFromCounter(counter);

    attrib_stats.data_id = out_attrib->getDataId();

    attrib_stats.is_data_id_found_in_inputs =
        (attrib_stats.data_id != GA_INVALID_DATAID &&
         input_data_ids.count(attrib_stats.data_id) != 0);

    gatherPageStorage(
        out_attrib, num_pages, tracker, attrib_stats.page_stats, attrib_stats.memory_block_pointers
    );
}

static void
gatherPrimitiveTypeStats(
    const GU_Detail*                 gdp,
    const UT::ArraySet<const void*>& avoid,
    UT_Array<PrimTypeStats>&         prim_types
) {
    // We need to maintain a single counter and run every prim through it, instead
    // of creating a new counter for each prim. This is because two prims might share
    // the same reference and using a new counter per run could double count.
    // However, using a single counter makes tracking each primitive's contribution more
    // involved since we need to calculate the difference from the previous step.
    // In other words, if we grew by N bytes during a iteration, then we knew our current
    // primitive wasn't shared with a previous prim.
    UT_MemoryCounterNewSafe counter(avoid);

    // Primitive type ids are derived at run time from 0, so we can reference into
    // an array instead of making a Set.
    UT_Array<exint> type_id_to_index;
    type_id_to_index.appendMultiple(exint(-1), gdp->getPrimitiveFactory().getPrimTypeCount());
    MemoryCounts previous;

    for (GA_Iterator it(gdp->getPrimitiveRange()); !it.atEnd(); ++it) {
        const GA_Primitive* prim = gdp->getPrimitive(*it);
        if (!prim) continue;

        const int type_id         = prim->getTypeId().get();
        exint     prim_type_index = type_id_to_index(type_id);
        if (prim_type_index < 0) {
            prim_type_index                       = prim_types.append();
            type_id_to_index(type_id)             = prim_type_index;
            prim_types(prim_type_index).type_name = prim->getTypeName();
            prim_types(prim_type_index).type_id   = type_id;
        }

        prim->countMemory(counter);

        PrimTypeStats& stats = prim_types(prim_type_index);
        ++stats.count;
        const MemoryCounts running  = memoryCountsFromCounter(counter);
        stats.memory               += running - previous;
        previous                    = running;
    }
}

static void
gatherPrimitiveListStats(
    const GU_Detail*                 gdp,
    const UT::ArraySet<const void*>& avoid,
    const UT_Set<GA_DataId>&         input_data_ids,
    PrimitiveListStats&              prim_list_stats
) {
    const GA_PrimitiveList& prim_list = gdp->getPrimitiveList();
    const GA_Size           num_pages = numPagesForIndexMap(gdp->getIndexMap(GA_ATTRIB_PRIMITIVE));

    UT_MemoryCounterNewSafe counter(avoid);
    prim_list.countMemory(counter, /*inclusive*/ false);
    prim_list_stats.memory  = memoryCountsFromCounter(counter);

    prim_list_stats.data_id = prim_list.getDataId();

    prim_list_stats.is_data_id_found_in_inputs =
        (prim_list_stats.data_id != GA_INVALID_DATAID &&
         input_data_ids.count(prim_list_stats.data_id) != 0);

    prim_list_stats.is_full_representation = prim_list.isFullRepresentation();
    if (prim_list_stats.is_full_representation)
        gatherPrimitiveTypeStats(gdp, avoid, prim_list_stats.prim_types);

    PageStats& page_stats  = prim_list_stats.page_stats;

    page_stats.has_details = !prim_list_stats.is_full_representation;

    if (!page_stats.has_details) return;

    page_stats.has_hardened_details   = false;
    page_stats.is_page_table_hardened = false;
    page_stats.constant_page_bits.setSize(num_pages);

    // Similar to the groups, there isn't a public API for the pageDataHandle
    // so we just leave them initialized to 0
    page_stats.hardened_page_bits.setSize(num_pages);
    page_stats.shared_page_bits.setSize(num_pages);

    // Constant for primitive lists means the vertex lists all have the same count
    // and are contiguous, so one representative list stands in for the whole page.
    for (GA_Size page = 0; page < num_pages; ++page) {
        const bool is_const = prim_list.isVertexListPageConstant(GA_PageNum(page));
        page_stats.constant_page_bits.setBitFast(page, is_const);
        if (is_const) ++page_stats.num_constant_pages;
    }
}

// Per-owner index-map stats + per-page occupancy (counts + intra-page bitmasks).
static void
gatherOwnerStats(
    const GU_Detail*                 gdp,
    GA_AttributeOwner                owner,
    const UT::ArraySet<const void*>& avoid,
    OwnerStats&                      owner_stats
) {
    const GA_IndexMap& index_map = gdp->getIndexMap(owner);
    owner_stats.owner            = owner;
    owner_stats.offset_size      = index_map.offsetSize();
    owner_stats.index_size       = index_map.indexSize();

    UT_MemoryCounterNewSafe counter(avoid);
    index_map.countMemory(counter, /*inclusive*/ false);

    owner_stats.index_map_memory = memoryCountsFromCounter(counter);
    owner_stats.is_monotonic     = index_map.isMonotonicMap();
    owner_stats.is_trivial       = index_map.isTrivialMap();

    const GA_Size num_pages      = numPagesForIndexMap(index_map);
    owner_stats.num_pages        = num_pages;
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
            for (GA_Offset offset = page_start; offset < page_end; ++offset) {
                if (index_map.isOffsetActive(offset)) {
                    setPageBit(active_page_bits.bits, GAgetPageOff(offset));
                    ++active;
                } else if (index_map.isOffsetTransient(offset)) {
                    setPageBit(temporary_page_bits.bits, GAgetPageOff(offset));
                    ++temporary;
                } else {
                    ++vacant;
                }
            }
            owner_stats.num_active_per_page[page]    = active;
            owner_stats.num_temporary_per_page[page] = temporary;
            owner_stats.num_vacant_per_page[page]    = vacant;
        }

    GA_Range  range(index_map);
    GA_Offset start;
    GA_Offset end;
    for (GA_Iterator it(range); it.fullBlockAdvance(start, end);) {
        owner_stats.full_block_ranges.append(start);
        owner_stats.full_block_ranges.append(end);
    }
}

static void
gatherSources(
    SOP_Node*                   sop,
    const GU_Detail*            out_gdp,
    const OP_Context&           context,
    int                         out_idx,
    DetailStats&                report,
    UT_Array<const GU_Detail*>& sources
) {
    if (SOP_Node* internal_sop = sop->getOutputSop(out_idx, /*fallback_to_display_render*/ true)) {
        // We fetch the cached geo and don't force a cook. The assumption is
        // our current node cooked any needed dependencies already.
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
        // but never uses the geometry. But since we are unable to look inside a node
        // this is the best we can do.
        // TODO: maybe we can via a parm interest vs data interest?
        OP_NodeList extra_nodes;
        sop->getExtraInputNodes(
            extra_nodes,
            /*remove_duplicates*/ true,
            /*data_interest*/ true,
            /*parm_interest*/ true,
            /*flag_interest*/ false
        );
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

// Turns the recorded blocks into the per-attribute sharing figures. Runs once, after every
// attribute of every owner has been measured.
static void
resolveSharing(const MemoryBlockTracker& tracker, DetailStats& report) {
    const int32 num_attribs = static_cast<int32>(tracker.attrib_keys.size());
    if (num_attribs == 0) return;

    // An array of "sets of tracker indices". One element in the array for each attribute.
    // The ArraySet holds the tracker indices of the other attributes it shares a memory
    // block with.
    // Note: int64 is being used here, because when using int32 it tripped UBSan's alignment
    //       checks.
    UT_Array<UT::ArraySet<int64>> shares_with;
    shares_with.setSize(num_attribs);

    // First pass loops over the memory blocks and gathers all the memory block sharing
    // between attributes and tallies the intra detail shared memory for an attribute.
    for (auto&& entry : tracker.memory_blocks) {
        const SharedMemoryBlock& block = entry.second;
        if (block.sharing_set_id < 0) continue;
        const UT_Array<int32>& sharing_set = tracker.sharing_sets(block.sharing_set_id);
        if (sharing_set.size() < 2) continue;

        for (exint i = 0; i < sharing_set.size(); ++i) {
            const MemoryBlockTracker::AttribLookupKey& attrib_lookup_key =
                tracker.attrib_keys(sharing_set(i));

            AttributeStats& stats =
                report.owner_stats[attrib_lookup_key.owner].attribs[attrib_lookup_key.index];

            stats.intra_detail_sharing_memory += block.size;

            for (exint j = 0; j < sharing_set.size(); ++j)
                if (i != j) shares_with(sharing_set(i)).insert(sharing_set(j));
        }
    }

    // Second pass takes the populated shares_with and populates the AttributeStats in the
    // report with the sharing information.
    for (int32 tracker_index = 0; tracker_index < num_attribs; ++tracker_index) {
        if (shares_with(tracker_index).empty()) continue;

        const MemoryBlockTracker::AttribLookupKey& attrib_lookup_key =
            tracker.attrib_keys(tracker_index);

        AttributeStats& stats =
            report.owner_stats[attrib_lookup_key.owner].attribs[attrib_lookup_key.index];

        stats.shares_with_attrib_keys.setCapacityIfNeeded(shares_with(tracker_index).size());

        for (int64 shares_with_tracker_index : shares_with(tracker_index)) {
            const MemoryBlockTracker::AttribLookupKey& shares_with_lookup_key =
                tracker.attrib_keys(shares_with_tracker_index);
            const AttributeStats& shares_with_stats =
                report.owner_stats[shares_with_lookup_key.owner]
                    .attribs[shares_with_lookup_key.index];

            UT_Array<SharesWithAttribKey>& keys = stats.shares_with_attrib_keys;
            keys.append();
            SharesWithAttribKey& key = keys.last();
            key.owner                = shares_with_stats.owner;
            key.scope                = shares_with_stats.scope;
            key.name                 = shares_with_stats.name;
            key.tracker_index        = static_cast<int32>(shares_with_tracker_index);
        }
        // We sort so the order is stable
        stats.shares_with_attrib_keys.sort();
    }
}

// Determine which pages sit on the same memory block as which others. This allows us to
// know which pages are shared across all the attributes in the geometry detail.
// This is done keeping in mind that ref count may be greater than 1 due to sharing
// with the node's inputs.
static void
resolveMemoryBlockSharing(const MemoryBlockTracker& tracker, DetailStats& report) {
    // We run two passes.
    // In the first pass, for each memory block we count how many pages use the memory block
    // based on what the tracker found.
    UT::ArrayMap<const void*, MemoryBlockPageUse> block_use;
    for (GA_AttributeOwner owner : ALL_OWNERS) {
        for (const AttributeStats& attrib_stats : report.owner_stats[owner].attribs) {
            for (const void* block_pointer : attrib_stats.memory_block_pointers) {
                if (!block_pointer) continue;
                auto tracked = tracker.memory_blocks.find(block_pointer);
                if (tracked == tracker.memory_blocks.end()) continue;
                MemoryBlockPageUse& page_use = block_use[block_pointer];
                ++page_use.pages;
                page_use.sharing_set_id = tracked->second.sharing_set_id;
            }
        }
    }
    if (block_use.empty()) return;

    // In the second pass, now that we know the totals from the first pass, we can assign
    // ids to the memory blocks. We iterate on the owners and attribs list instead of by block.
    // Iterating this way keeps the order stable, so rerunning will produce the same ids.
    int32 next_block_id = 0;
    for (GA_AttributeOwner owner : ALL_OWNERS) {
        OwnerStats&   owner_stats = report.owner_stats[owner];
        const GA_Size num_pages   = owner_stats.num_pages;
        if (num_pages <= 0) continue;

        for (AttributeStats& attrib_stats : owner_stats.attribs) {
            if (attrib_stats.memory_block_pointers.isEmpty()) continue;

            UT_Array<int32>           block_ids;
            UT_Array<int32>           mapping_index;
            UT_Array<UT_Array<exint>> mapping;
            block_ids.setSize(num_pages);
            block_ids.constant(0);
            mapping_index.setSize(num_pages);
            mapping_index.constant(0);
            // First entry is always an empty mapping (no sharing by other attributes)
            mapping.setSize(1);
            bool            has_shared_page = false;
            UT_Array<exint> shares_with_key_indices;

            for (GA_Size page = 0; page < num_pages; ++page) {
                const void* block_pointer = attrib_stats.memory_block_pointers(page);
                if (!block_pointer) continue;
                auto used = block_use.find(block_pointer);

                // If less than two this block isn't shared by multiple pages
                // in the same geometry detail so we can continue on, leaving the id at 0.
                if (used == block_use.end() || used->second.pages < 2) continue;

                MemoryBlockPageUse& page_use = used->second;
                if (page_use.block_id == 0) page_use.block_id = ++next_block_id;
                block_ids(page) = page_use.block_id;
                has_shared_page = true;

                shares_with_key_indices.clear();
                if (page_use.sharing_set_id >= 0) {
                    const UT_Array<int32>& sharer_indices =
                        tracker.sharing_sets(page_use.sharing_set_id);
                    // Built in key order so we can take the easy uniqueSortedFind approach
                    for (exint i = 0; i < attrib_stats.shares_with_attrib_keys.size(); ++i)
                        if (sharer_indices.uniqueSortedFind(
                                attrib_stats.shares_with_attrib_keys(i).tracker_index
                            ) >= 0)
                            shares_with_key_indices.append(i);
                }
                if (shares_with_key_indices.isEmpty()) continue;

                int32 mapping_entry_index = -1;
                // We start at 1, since 0 is the index of the empty mapping
                for (exint s = 1; s < mapping.size(); ++s)
                    if (mapping(s) == shares_with_key_indices) {
                        mapping_entry_index = static_cast<int32>(s);
                        break;
                    }
                if (mapping_entry_index < 0) {
                    mapping_entry_index = static_cast<int32>(mapping.size());
                    mapping.append(shares_with_key_indices);
                }
                mapping_index(page) = mapping_entry_index;
            }

            if (!has_shared_page) continue;
            attrib_stats.has_memory_block_sharing    = true;
            attrib_stats.memory_block_ids            = std::move(block_ids);
            attrib_stats.shares_with_mapping_indices = std::move(mapping_index);
            attrib_stats.shares_with_mapping         = std::move(mapping);
        }
    }
}

// Zeroes every new/unique figure, because an instanced detail owns nothing. Applied once
// after all figures are in place.
static void
clearOwnershipForInstanced(DetailStats& report) {
    if (!report.is_instanced) return;

    for (MemoryCounts* counts : {
             &report.memory,
             &report.attribs_memory,
             &report.primitive_list_memory,
             &report.attribute_set_memory,
             &report.index_maps_memory,
             &report.group_tables_memory,
             &report.gu_detail_memory,
             &report.residual_memory,
             &report.edge_group_table.memory,
             &report.edge_group_table.name_map_memory,
             &report.primitive_list.memory,
         })
        counts->zeroNonTotal();

    for (int i = 0; i < ELEMENT_GROUP_TABLE_N; ++i)
        report.element_group_table_memory[i].zeroNonTotal();

    for (DetailStats::EdgeGroupStats& group_stats : report.edge_group_table.groups)
        group_stats.memory.zeroNonTotal();

    report.primitive_list.memory.zeroNonTotal();
    report.primitive_list.is_data_id_found_in_inputs = true;
    for (PrimTypeStats& type_stats : report.primitive_list.prim_types)
        type_stats.memory.zeroNonTotal();

    for (GA_AttributeOwner owner : ALL_OWNERS) {
        OwnerStats& owner_stats = report.owner_stats[owner];
        owner_stats.index_map_memory.zeroNonTotal();
        owner_stats.attribs_memory.zeroNonTotal();
        for (AttributeStats& attrib_stats : owner_stats.attribs) {
            attrib_stats.memory.zeroNonTotal();
            attrib_stats.is_data_id_found_in_inputs = true;
        }
    }
}

// Houdini's getOutputForView returns an int8, so we use this check to see if output_index
// is valid or not. This allows us to use an out of range default, OUTPUT_FROM_VIEW,
// to specify a default behavior, i.e. getOutputForView()
static bool
isExplicitOutputIndex(int output_index) {
    return output_index >= SYS_INT8_MIN && output_index <= SYS_INT8_MAX;
}

// The memory of a geometry is made up of:
//  * The GU_Detail data structure
//  * Attribute Set, which keeps track of the attributes.
//  * Attribute Data, the values and bookkeeping of the attributes
//  * Primitive Lists
//  * Group Tables
//      * Normal groups are stored as attributes.
//      * Edge Groups
//  * Index Maps, data structures which hold the mapping of offsets to indices
//  * Unaccounted Residual, generally tail initializers used when grouping all elements.
void
gatherDetailStats(const char* node_path, int output_index, DetailStats& report) {
    HOM_AutoLock hom_lock;

    report.node_path = node_path;

    OP_Node* op_node = OPgetDirector()->findNode(node_path);
    if (!op_node) throw HOM_OperationFailed("Could not find a node at the given path");

    SOP_Node* sop = CAST_SOPNODE(op_node);
    if (!sop) throw HOM_OperationFailed("Node is not a SOP");

    OP_Context context(CHgetEvalTime());

    const int  out_idx =
        (isExplicitOutputIndex(output_index) ? output_index
                                             : static_cast<int>(sop->getOutputForView()));
    report.output_index            = out_idx;

    GU_DetailHandle  output_handle = sop->cookOutput(context, out_idx, /*interests*/ nullptr);
    const GU_Detail* out_gdp       = output_handle.gdp();
    if (!out_gdp) throw HOM_OperationFailed("Node produced no cooked geometry");

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

    // MEASURE TOTAL MEMORY OF THE DETAIL
    {
        // NOTE: It is important that we do this measurement first.
        //       Certain operations can perturb / modify the underlying data structures
        //       which would cause the report to be different. We want to ensure we don't
        //       perturb anything. Measuring this first acts as a baseline from which
        //       we'll subtract from to obtain a residual.
        UT_MemoryCounterNewSafe counter(avoid);
        out_gdp->countMemory(counter, /*inclusive*/ true);
        report.memory = memoryCountsFromCounter(counter);
    }

    // MEASURE THE GU_DETAIL DATA STRUCTURE
    // The GU_Detail is always owned by the node unless instanced.
    report.gu_detail_memory += static_cast<int64>(sizeof(GU_Detail));

    // MEASURE THE GROUP TABLES FOR POINT / VERTEX / PRIMITIVES
    for (int i = 0; i < ELEMENT_GROUP_TABLE_N; ++i) {
        UT_MemoryCounterNewSafe counter(avoid);
        out_gdp->getElementGroupTable(ELEMENT_GROUP_TABLE_OWNERS[i])
            .countMemory(counter, /*inclusive*/ false);

        report.element_group_table_memory[i]  = memoryCountsFromCounter(counter);
        report.group_tables_memory           += report.element_group_table_memory[i];
    }

    // MEASURE THE GROUP TABLES FOR EDGES
    {
        const GA_EdgeGroupTable&          edge_table       = out_gdp->edgeGroups();
        DetailStats::EdgeGroupTableStats& edge_table_stats = report.edge_group_table;

        UT_MemoryCounterNewSafe           counter(avoid);
        edge_table.countMemory(counter, /*inclusive*/ false);
        edge_table_stats.memory = memoryCountsFromCounter(counter);

        edge_table_stats.groups.setCapacityIfNeeded(edge_table.entries());

        MemoryCounts edge_groups_total;
        for (GA_EdgeGroupTable::iterator it = edge_table.beginTraverse();
             it != edge_table.endTraverse();
             ++it) {
            const GA_EdgeGroup* edge_group = it.group();
            if (!edge_group) continue;

            edge_table_stats.groups.append();
            DetailStats::EdgeGroupStats& edge_group_stats = edge_table_stats.groups.last();
            edge_group_stats.name                         = it.name();

            UT_MemoryCounterNewSafe edge_group_counter(avoid);
            edge_group->countMemory(edge_group_counter, /*inclusive*/ true);
            edge_group_stats.memory  = memoryCountsFromCounter(edge_group_counter);
            edge_groups_total       += edge_group_stats.memory;
        }
        // No accessor reports the name map, so derive it from the total
        edge_table_stats.name_map_memory = edge_table_stats.memory - edge_groups_total;
        UT_ASSERT_MSG(edge_table_stats.name_map_memory.total_bytes >= 0, "name_map total < 0");
        UT_ASSERT_MSG(edge_table_stats.name_map_memory.new_bytes >= 0, "name_map new < 0");
        UT_ASSERT_MSG(edge_table_stats.name_map_memory.unique_bytes >= 0, "name_map unique < 0");

        report.group_tables_memory += edge_table_stats.memory;
    }

    // MEASURE PRIMITIVE LIST MEMORY USAGE
    gatherPrimitiveListStats(out_gdp, avoid, input_data_ids, report.primitive_list);
    report.primitive_list_memory = report.primitive_list.memory;

    MemoryBlockTracker tracker(avoid);

    // MEASURE ATTRIBUTE MEMORY USAGE
    for (GA_AttributeOwner owner : ALL_OWNERS) {
        OwnerStats& owner_stats = report.owner_stats[owner];
        gatherOwnerStats(out_gdp, owner, avoid, owner_stats);
        report.index_maps_memory          += owner_stats.index_map_memory;
        const GA_Size           num_pages  = owner_stats.num_pages;

        const GA_AttributeDict& dict       = out_gdp->getAttributeDict(owner);
        owner_stats.attribs.setSize(dict.entries());

        exint attrib_index = 0;
        for (GA_AttributeDict::iterator it = dict.begin(); !it.atEnd(); ++it) {
            const GA_Attribute* out_attrib = it.attrib();
            UT_ASSERT(out_attrib);
            if (!out_attrib) continue;

            AttributeStats& attrib_stats     = owner_stats.attribs[attrib_index];
            attrib_stats.name                = out_attrib->getName();
            attrib_stats.owner               = owner;
            attrib_stats.scope               = out_attrib->getScope();
            attrib_stats.type_name           = out_attrib->getType().getTypeName();
            attrib_stats.tuple_size          = out_attrib->getTupleSize();

            attrib_stats.is_tail_initialized = out_attrib->isTailInitialization();
            if (attrib_stats.is_tail_initialized) ++report.num_tail_initializers;

            // Registered before measuring so the counter can ascribe the shared blocks it
            // sees to this attribute.
            const int32 tracker_index = tracker.registerAttribute(owner, attrib_index);

            gatherAttributeStats(
                out_attrib, input_data_ids, tracker, tracker_index, num_pages, attrib_stats
            );

            ++attrib_index;
        }
        owner_stats.attribs.setSize(attrib_index);
        owner_stats.attribs_memory = tracker.owners_attrib_memory[owner];
    }
    report.attribs_memory = tracker.all_attrib_memory;

    resolveSharing(tracker, report);
    resolveMemoryBlockSharing(tracker, report);

    // The page pointers exist only to get from the page pass to the block pass. Released
    // here rather than left on the report, where nothing reads them and every attribute
    // would carry eight bytes per page until the report is destroyed.
    for (GA_AttributeOwner owner : ALL_OWNERS)
        for (AttributeStats& attrib_stats : report.owner_stats[owner].attribs)
            attrib_stats.memory_block_pointers.clear();

    // MEASURE ATTRIBUTE SET MEMORY OVERHEAD
    // No accessor reports the attribute set's own bookkeeping, so it is what is left of a
    // measurement of the whole set once the members the tracker accumulated are taken out.
    {
        UT_MemoryCounterNewSafe attrib_counter(avoid);
        out_gdp->getAttributes().countMemory(attrib_counter, /*inclusive*/ false);
        report.attribute_set_memory =
            memoryCountsFromCounter(attrib_counter) - tracker.all_attrib_memory;
    }

    // The residual is the discrepancy between what we can derive and Houdini's own internal
    // APIs. Currently the only discrepancy source seems to be from the tail initializers
    // for the group attributes.
    report.residual_memory  = report.memory;
    report.residual_memory -= report.attribs_memory;
    report.residual_memory -= report.primitive_list_memory;
    report.residual_memory -= report.attribute_set_memory;
    report.residual_memory -= report.index_maps_memory;
    report.residual_memory -= report.group_tables_memory;
    report.residual_memory -= report.gu_detail_memory;

    // When we are instanced, we shouldn't report new or unique. This function clears them.
    // We could have multiple if instanced / else statements, but there isn't measurable
    // performance to be gained by doing so. So it is easier to just clear the values.
    clearOwnershipForInstanced(report);
}

static void
setStr(PY_PyObject* d, const char* k, const char* v) {
    PY_AutoObject o(PY_Py_BuildValue("s", v ? v : ""));
    PY_PyDict_SetItemString(d, k, o);
}

static void
setI64(PY_PyObject* d, const char* k, int64 v) {
    PY_AutoObject o(PY_PyLong_FromLongLong((long long)v));
    PY_PyDict_SetItemString(d, k, o);
}

// Helper to set three dictionary keys for our MemoryCounts
static void
setMemoryCounts(PY_PyObject* d, const char* prefix, const MemoryCounts& counts) {
    static const char* const fields[3] = {"total", "new", "unique"};
    const int64 values[3] = {counts.total_bytes, counts.new_bytes, counts.unique_bytes};

    for (int i = 0; i < 3; ++i) {
        UT_WorkBuffer key;
        if (prefix && prefix[0])
            key.sprintf("%s_%s_memory", prefix, fields[i]);
        else
            key.sprintf("%s_memory", fields[i]);
        setI64(d, key.buffer(), values[i]);
    }
}

static void
setBool(PY_PyObject* d, const char* k, bool v) {
    PY_PyDict_SetItemString(d, k, v ? PY_Py_True() : PY_Py_False());
}

static void
setObjSteal(PY_PyObject* d, const char* k, PY_PyObject* o_raw) {
    PY_AutoObject o(o_raw);
    if (!o) return;
    PY_PyDict_SetItemString(d, k, o);
}

// We need to use PY_Py_BuildValue since the Houdini PY headers don't
// expose all the various build bytes helpers.
static PY_PyObject*
bytesFromRaw(const void* data, size_t nbytes) {
    constexpr const char* empty_bytes = "";
    const char*           bytes       = nbytes ? reinterpret_cast<const char*>(data) : empty_bytes;
    return PY_Py_BuildValue("y#", bytes, static_cast<PY_Py_ssize_t>(nbytes));
}

static const char*
ownerLabel(GA_AttributeOwner owner) {
    switch (owner) {
        case GA_ATTRIB_POINT:     return "point";
        case GA_ATTRIB_VERTEX:    return "vertex";
        case GA_ATTRIB_PRIMITIVE: return "primitive";
        case GA_ATTRIB_GLOBAL:    return "detail";
        default:                  return "unknown";
    }
}

static const char*
scopeLabel(GA_AttributeScope scope) {
    switch (scope) {
        case GA_SCOPE_PUBLIC:  return "public";
        case GA_SCOPE_PRIVATE: return "private";
        case GA_SCOPE_GROUP:   return "group";
        default:               return "invalid";
    }
}

static void
setPageBits(PY_PyObject* d, const char* key, const UT_BitArray& bits) {
    setObjSteal(
        d,
        key,
        bytesFromRaw(
            bits.data(), sizeof(UT_BitArray::BlockType) * UT_BitArray::numWords(bits.size())
        )
    );
}

// Sets 'page_details' to the dict, or to None when the pages could not be read. The nesting
// is what carries that condition, so no field below is a sometimes-present key.
static void
setPageDetails(PY_PyObject* d, const PageStats& page_stats) {
    if (!page_stats.has_details) {
        PY_PyDict_SetItemString(d, "page_details", PY_Py_None());
        return;
    }

    PY_AutoObject page_details(PY_PyDict_New());
    if (!page_details) return;

    setBool(page_details, "has_hardened_details", page_stats.has_hardened_details);
    setBool(page_details, "is_page_table_hardened", page_stats.is_page_table_hardened);
    setI64(page_details, "num_constant_pages", page_stats.num_constant_pages);
    setI64(page_details, "num_shared_pages", page_stats.num_shared_pages);
    setI64(page_details, "num_hardened_pages", page_stats.num_hardened_pages);
    setI64(page_details, "num_constant_shared_pages", page_stats.num_constant_shared_pages);

    setPageBits(page_details, "constant_page_bits", page_stats.constant_page_bits);
    setPageBits(page_details, "hardened_page_bits", page_stats.hardened_page_bits);
    setPageBits(page_details, "shared_page_bits", page_stats.shared_page_bits);

    PY_PyDict_SetItemString(d, "page_details", page_details);
}

static PY_PyObject*
pyDictFromAttributeStats(const AttributeStats& attrib_stats) {
    PY_PyObject* d = PY_PyDict_New();
    if (!d) return nullptr;

    setStr(d, "type_name", attrib_stats.type_name.c_str());
    setStr(d, "scope", scopeLabel(attrib_stats.scope));
    setI64(d, "tuple_size", attrib_stats.tuple_size);
    setMemoryCounts(d, "", attrib_stats.memory);
    setI64(d, "intra_detail_sharing_memory", attrib_stats.intra_detail_sharing_memory);
    setBool(d, "is_data_id_found_in_inputs", attrib_stats.is_data_id_found_in_inputs);
    setBool(d, "is_tail_initialized", attrib_stats.is_tail_initialized);
    setI64(d, "data_id", attrib_stats.data_id);

    PY_AutoObject shares_with(PY_PyList_New(0));
    if (shares_with) {
        for (const SharesWithAttribKey& key : attrib_stats.shares_with_attrib_keys) {
            PY_PyObject* row = PY_PyDict_New();
            if (!row) break;
            setStr(row, "owner", ownerLabel(key.owner));
            setStr(row, "scope", scopeLabel(key.scope));
            setStr(row, "name", key.name.c_str());
            PY_PyList_Append(shares_with, row);
            PY_Py_DECREF(row);
        }
        PY_PyDict_SetItemString(d, "shares_with_attrib_keys", shares_with);
    }

    if (attrib_stats.has_memory_block_sharing) {
        PY_AutoObject memory_block_sharing(PY_PyDict_New());
        if (memory_block_sharing) {
            UT_Array<uint32> block_ids;
            block_ids.setSize(attrib_stats.memory_block_ids.size());

            for (exint i = 0; i < attrib_stats.memory_block_ids.size(); ++i)
                block_ids(i) = static_cast<uint32>(attrib_stats.memory_block_ids(i));

            setObjSteal(
                memory_block_sharing,
                "memory_block_ids",
                bytesFromRaw(block_ids.array(), block_ids.size() * sizeof(uint32))
            );

            UT_Array<uint32> mapping_indices;
            mapping_indices.setSize(attrib_stats.shares_with_mapping_indices.size());

            for (exint i = 0; i < attrib_stats.shares_with_mapping_indices.size(); ++i)
                mapping_indices(i) =
                    static_cast<uint32>(attrib_stats.shares_with_mapping_indices(i));

            setObjSteal(
                memory_block_sharing,
                "shares_with_mapping_indices",
                bytesFromRaw(mapping_indices.array(), mapping_indices.size() * sizeof(uint32))
            );

            PY_AutoObject mapping(PY_PyList_New(0));
            if (mapping) {
                for (const UT_Array<exint>& entry : attrib_stats.shares_with_mapping) {
                    PY_PyObject* row = PY_PyList_New(0);
                    if (!row) break;
                    for (exint shares_with_key_index : entry) {
                        PY_AutoObject v(
                            PY_Py_BuildValue("i", static_cast<int>(shares_with_key_index))
                        );
                        if (v) PY_PyList_Append(row, v);
                    }
                    PY_PyList_Append(mapping, row);
                    PY_Py_DECREF(row);
                }
                PY_PyDict_SetItemString(memory_block_sharing, "shares_with_mapping", mapping);
            }
            PY_PyDict_SetItemString(d, "memory_block_sharing", memory_block_sharing);
        }
    } else {
        PY_PyDict_SetItemString(d, "memory_block_sharing", PY_Py_None());
    }

    setPageDetails(d, attrib_stats.page_stats);
    return d;
}

static PY_PyObject*
pyDictFromPrimitiveListStats(const PrimitiveListStats& prim_list_stats) {
    PY_PyObject* d = PY_PyDict_New();
    if (!d) return nullptr;

    setMemoryCounts(d, "", prim_list_stats.memory);
    setI64(d, "data_id", prim_list_stats.data_id);
    setBool(d, "is_data_id_found_in_inputs", prim_list_stats.is_data_id_found_in_inputs);
    setBool(d, "is_full_representation", prim_list_stats.is_full_representation);

    PY_AutoObject types(PY_PyDict_New());
    for (const PrimTypeStats& type_stats : prim_list_stats.prim_types) {
        if (!types) break;
        PY_PyObject* row = PY_PyDict_New();
        if (!row) break;
        setI64(row, "type_id", type_stats.type_id);
        setI64(row, "count", type_stats.count);
        setMemoryCounts(row, "", type_stats.memory);
        setObjSteal(types, type_stats.type_name.c_str(), row);
    }
    if (types) PY_PyDict_SetItemString(d, "primitive_types", types);

    setPageDetails(d, prim_list_stats.page_stats);
    return d;
}

static PY_PyObject*
pyDictFromOwnerStats(const OwnerStats& owner_stats) {
    PY_AutoObject d(PY_PyDict_New());
    if (!d) return nullptr;
    setI64(d, "owner", owner_stats.owner);
    setI64(d, "offset_size", owner_stats.offset_size);
    setI64(d, "index_size", owner_stats.index_size);
    setI64(d, "num_pages", owner_stats.num_pages);
    setMemoryCounts(d, "index_map", owner_stats.index_map_memory);
    setMemoryCounts(d, "attributes", owner_stats.attribs_memory);
    setBool(d, "is_monotonic", owner_stats.is_monotonic);
    setBool(d, "is_trivial", owner_stats.is_trivial);
    setI64(d, "page_mask_words", UT_BitArray::numWords(owner_stats.num_pages));

    setObjSteal(
        d,
        "num_active_per_page",
        bytesFromRaw(
            owner_stats.num_active_per_page.getRawArray(),
            sizeof(GA_Size) * owner_stats.num_active_per_page.size()
        )
    );
    setObjSteal(
        d,
        "num_temporary_per_page",
        bytesFromRaw(
            owner_stats.num_temporary_per_page.getRawArray(),
            sizeof(GA_Size) * owner_stats.num_temporary_per_page.size()
        )
    );
    setObjSteal(
        d,
        "num_vacant_per_page",
        bytesFromRaw(
            owner_stats.num_vacant_per_page.getRawArray(),
            sizeof(GA_Size) * owner_stats.num_vacant_per_page.size()
        )
    );

    setObjSteal(
        d,
        "active_page_bits",
        bytesFromRaw(
            owner_stats.active_page_bits.getRawArray(),
            sizeof(PageBits) * owner_stats.active_page_bits.size()
        )
    );
    setObjSteal(
        d,
        "temporary_page_bits",
        bytesFromRaw(
            owner_stats.temporary_page_bits.getRawArray(),
            sizeof(PageBits) * owner_stats.temporary_page_bits.size()
        )
    );

    PY_AutoObject attribs_dict(PY_PyDict_New());
    if (!attribs_dict) return nullptr;

    for (const AttributeStats& attrib_stats : owner_stats.attribs) {
        const char*  scope_label = scopeLabel(attrib_stats.scope);

        PY_PyObject* scope_dict  = PY_PyDict_GetItemString(attribs_dict, scope_label);
        if (!scope_dict) {
            PY_AutoObject created(PY_PyDict_New());
            if (!created) return nullptr;

            PY_PyDict_SetItemString(attribs_dict, scope_label, created);
            scope_dict = created;
        }

        PY_AutoObject attrib_dict(pyDictFromAttributeStats(attrib_stats));
        if (!attrib_dict) return nullptr;

        PY_PyDict_SetItemString(scope_dict, attrib_stats.name.c_str(), attrib_dict);
    }
    PY_PyDict_SetItemString(d, "attributes", attribs_dict);

    setObjSteal(
        d,
        "full_block_ranges",
        bytesFromRaw(
            owner_stats.full_block_ranges.getRawArray(),
            sizeof(GA_Offset) * owner_stats.full_block_ranges.size()
        )
    );
    PY_Py_INCREF(d);
    return d;
}

PY_PyObject*
pyDictFromDetailStats(const DetailStats& report) {
    PY_AutoObject top(PY_PyDict_New());
    if (!top) return nullptr;

    setStr(top, "node_path", report.node_path.c_str());
    setI64(top, "output_index", report.output_index);
    setBool(top, "is_instanced", report.is_instanced);

    setI64(top, "num_tail_initializers", report.num_tail_initializers);

    setMemoryCounts(top, "", report.memory);

    {
        PY_AutoObject memory(PY_PyDict_New());
        if (!memory) return nullptr;
        setMemoryCounts(memory, "attributes", report.attribs_memory);
        setMemoryCounts(memory, "primitive_list", report.primitive_list_memory);
        setMemoryCounts(memory, "attribute_set", report.attribute_set_memory);
        setMemoryCounts(memory, "index_maps", report.index_maps_memory);
        setMemoryCounts(memory, "gu_detail", report.gu_detail_memory);
        setMemoryCounts(memory, "residual", report.residual_memory);

        {
            PY_AutoObject group_tables(PY_PyDict_New());
            if (!group_tables) return nullptr;
            for (int i = 0; i < ELEMENT_GROUP_TABLE_N; ++i) {
                PY_AutoObject d(PY_PyDict_New());
                if (!d) return nullptr;
                setMemoryCounts(d, "", report.element_group_table_memory[i]);
                PY_PyDict_SetItemString(group_tables, ownerLabel(ELEMENT_GROUP_TABLE_OWNERS[i]), d);
            }

            {
                const DetailStats::EdgeGroupTableStats& edge_table_stats = report.edge_group_table;
                PY_AutoObject                           d(PY_PyDict_New());
                if (!d) return nullptr;
                setMemoryCounts(d, "", edge_table_stats.memory);
                setMemoryCounts(d, "name_map", edge_table_stats.name_map_memory);

                PY_AutoObject edge_groups(PY_PyDict_New());
                if (!edge_groups) return nullptr;
                for (const DetailStats::EdgeGroupStats& g : edge_table_stats.groups) {
                    PY_AutoObject gd(PY_PyDict_New());
                    if (!gd) return nullptr;
                    setMemoryCounts(gd, "", g.memory);
                    PY_PyDict_SetItemString(edge_groups, g.name.c_str(), gd);
                }
                PY_PyDict_SetItemString(d, "groups", edge_groups);
                PY_PyDict_SetItemString(group_tables, "edge", d);
            }
            PY_PyDict_SetItemString(memory, "group_tables", group_tables);
        }
        setMemoryCounts(memory, "group_tables", report.group_tables_memory);
        PY_PyDict_SetItemString(top, "memory", memory);
    }

    setI64(top, "page_size", GA_PAGE_SIZE);
    setI64(top, "per_page_count_bytes", static_cast<int64>(sizeof(GA_Size)));
    setI64(top, "page_word_bytes", static_cast<int64>(sizeof(UT_BitArray::BlockType)));
    setI64(
        top, "page_occupancy_words_per_page", static_cast<int64>(sizeof(PageBits) / sizeof(uint32))
    );

    PY_AutoObject owners(PY_PyDict_New());
    if (!owners) return nullptr;

    for (GA_AttributeOwner owner : ALL_OWNERS) {
        const OwnerStats& owner_stats = report.owner_stats[owner];
        setObjSteal(owners, ownerLabel(owner), pyDictFromOwnerStats(owner_stats));
    }
    PY_PyDict_SetItemString(top, "owners", owners);

    setObjSteal(top, "primitive_list", pyDictFromPrimitiveListStats(report.primitive_list));

    PY_Py_INCREF(top);
    return top;
}

PY_PyObject*
gatherGeometryStats(const char* node_path, int output_index) {
    DetailStats report;
    gatherDetailStats(node_path, output_index, report);
    return pyDictFromDetailStats(report);
}

}  // namespace page_tools

// Exception handling, see $HT/samples/HOM/_hdk_sample_hom_extensions.C
static PY_PyObject*
createHouException(
    const char*   exception_class_name,
    const char*   instance_message,
    PY_PyObject*& exception_class
) {
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
static PY_PyObject*
report_Wrapper(PY_PyObject* /*self*/, PY_PyObject* args) {
    const char* node_path    = nullptr;
    int         output_index = page_tools::OUTPUT_FROM_VIEW;
    if (!PY_PyArg_ParseTuple(args, "s|i", &node_path, &output_index)) return nullptr;

    try {
        return page_tools::gatherGeometryStats(node_path, output_index);
    } catch (HOM_Error& error) {
        // The _hdk_sample_hom_extensions.C uses UTunmangleClassNameFromTypeIdName
        // to get the exception class name, but I believe we can also fetch it this
        // way which looks a bit more straight forward.
        const std::string exception_class_name = error.exceptionTypeName();

        PY_PyObject*      exception_class      = nullptr;
        PY_AutoObject     exception_instance(createHouException(
            exception_class_name.c_str(), error.instanceMessage().c_str(), exception_class
        ));
        if (!exception_instance) return nullptr;
        PY_PyErr_SetObject(exception_class, exception_instance);
        return nullptr;
    } catch (...) {
        PY_PyErr_SetString(
            PY_PyExc_RuntimeError(), "Unexpected C++ exception in _page_tools.report()"
        );
        return nullptr;
    }
}

// Main entry point, refer to $HT/samples/HOM/_hdk_sample_hom_extensions.C
PY_PyMODINIT_FUNC
PyInit__page_tools(void) {
    PY_PyObject* module = nullptr;
    {
        PY_InterpreterAutoLock interpreter_auto_lock;

        static PY_PyMethodDef  methods[] = {
            {"report",
             report_Wrapper,
             PY_METH_VARARGS(),
             "report(node_path, output_index=<view output>) -> dict"},
            {nullptr, nullptr, 0, nullptr}
        };

        module = PY_Py_InitModule("_page_tools", methods);
    }
    return module;
}
