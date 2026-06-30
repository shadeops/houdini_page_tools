#include <PY/PY_CPythonAPI.h>
#include <PY/PY_Python.h>
#include <PY/PY_AutoObject.h>
#include <PY/PY_InterpreterAutoLock.h>
#include <HOM/HOM_Module.h>
#include <OP/OP_Director.h>
#include <UT/UT_SysSpecific.h>
#include <GU/GU_DetailHandle.h>
#include <GU/GU_Detail.h>
#include <SOP/SOP_Node.h>
#include <OP/OP_Context.h>

#include <GA/GA_IndexMap.h>

#include <iostream>

static const char *Doc_GeoPageReport = "do a thing\n";

static PY_PyObject *
createHouException(
    const char *exception_class_name, const char *instance_message,
    PY_PyObject *&exception_class)
{
    exception_class = nullptr;
    PY_AutoObject hou_module(PY_PyImport_ImportModule("hou"));
    PY_PyObject *hou_module_dict = PY_PyModule_GetDict(hou_module);

    exception_class = PY_PyDict_GetItemString(hou_module_dict, exception_class_name);
    if (!exception_class) {
        PY_PyErr_SetString(PY_PyExc_RuntimeError(), "Could not find exception class in hou module");
        return nullptr;
    }
    PY_AutoObject args(PY_Py_BuildValue("(s)", instance_message));
    if (!args) return nullptr;
    return PY_PyObject_Call(exception_class, args, nullptr);
}

GA_AttributeOwner toAttribOwner(const char *owner_str) {
    if (!owner_str) return GA_ATTRIB_INVALID;
    UT_StringRef owner(owner_str);
    if (owner == UT_StringRef("vertex")) return GA_ATTRIB_VERTEX;
    if (owner == UT_StringRef("point")) return GA_ATTRIB_POINT;
    if (owner == UT_StringRef("prim") || owner == "primitive") return GA_ATTRIB_PRIMITIVE;
    if (owner == UT_StringRef("detail") || owner == "global") return GA_ATTRIB_DETAIL;
    return GA_ATTRIB_INVALID;
}

struct PageBits {
    uint32_t bits[32] = {0};
};

struct AttribStats {
    UT_StringHolder name;
    UT_StringHolder scope;
    GA_DataId data_id = GA_INVALID_DATAID;
    bool has_page_details = false;
    bool is_table_hardened = false;
    UT_BitArray constant_pages;
    UT_BitArray hardened_pages;
};

struct PageStatOptions {
    int get_block_ranges = 0;
};

struct StartStop {
    GA_Offset start = 0;
    GA_Offset stop = -1;
};

// SoA since we are going to create a Python object from each one of these.
struct OwnerStats {
    GA_Size offset_size = 0;
    GA_Size index_size = 0;
    GA_Size num_pages = 0;
    bool is_monotonic = false;
    bool is_trivial = false;
    UT_Array<GA_Size> active;
    UT_Array<GA_Size> temporary;
    UT_Array<GA_Size> vacant;
    UT_Array<PageBits> active_bits;
    UT_Array<PageBits> temporary_bits;
    UT_Array<AttribStats> attrib_stats;
    UT_Array<StartStop> full_block_ranges;
};

// uint32_t[32]
// b = 0 to 1023 (inclusive)
// b >> 5 = which uint32_t to write to in the array of 32
// lowest bit == 0 index in page.
inline void setPageBit (uint32_t *mask, int b)       { mask[b >> 5] |= (uint32_t(1) << (b & 31)); }

const char *
scopeName(GA_AttributeScope scope)
{
    switch (scope)
    {
        case GA_SCOPE_PUBLIC: return "public";
        case GA_SCOPE_PRIVATE: return "private";
        case GA_SCOPE_GROUP: return "group";
        default: return "invalid";
    }
}

void GeoPageStats(const char *sop_path, const char *owner_str, PageStatOptions &opts, OwnerStats &out) {
    HOM_AutoLock hom_lock;

    GA_AttributeOwner owner = toAttribOwner(owner_str);
    if (owner == GA_ATTRIB_INVALID) {
        // TOOD switch to ValueError
        throw HOM_NodeError("Invalid attribute owner");
    }
    SOP_Node *sop_node = OPgetDirector()->findSOPNode(sop_path);
    if (!sop_node) {
        throw HOM_NodeError("Could not find sop");
    }
    OP_Context context(CHgetEvalTime()); 
    // TODO: Pick output?
    GU_DetailHandle gu_handle = sop_node->getCookedGeoHandle(context);
    if (gu_handle.isNull()) {
        throw HOM_InvalidGeometry("Could not fetch cooked geometry");
    }
    const GU_Detail *gdp = gu_handle.gdp();
    const GA_IndexMap &index_map = gdp->getIndexMap(owner);

    //std::cout << gdp->getMemoryUsage(true) << "\t" << gdp->getMemoryUsage(false) << std::endl;

    //std::cout << "\t" << "indexMap" << std::endl;
    //std::cout << "\t" << index_map.getMemoryUsage(true) << "\t" << index_map.getMemoryUsage(false) << std::endl;

    const GA_Size num_pages = GA_Size(index_map.offsetSize() + GA_PAGE_SIZE - 1) >> GA_PAGE_BITS;

    out.num_pages = num_pages;
    out.offset_size = index_map.offsetSize();
    out.index_size = index_map.indexSize();
    out.is_monotonic = index_map.isMonotonicMap();
    out.is_trivial = index_map.isTrivialMap();

    out.active.setSize(out.num_pages);
    out.temporary.setSize(out.num_pages);
    out.vacant.setSize(out.num_pages);
    out.active_bits.setSize(out.num_pages);
    out.temporary_bits.setSize(out.num_pages);

    for (GA_Size cur_page = 0; cur_page < num_pages; ++cur_page) {

        const GA_Size page_start = cur_page << GA_PAGE_BITS;
        const GA_Size page_end = (cur_page+1) << GA_PAGE_BITS;

        GA_Size &active = out.active[cur_page];
        GA_Size &temporary = out.temporary[cur_page];
        GA_Size &vacant = out.vacant[cur_page];
        PageBits &active_bits = out.active_bits[cur_page];
        PageBits &temporary_bits = out.temporary_bits[cur_page];

        active = 0;
        temporary = 0;
        vacant = 0;

        for (GA_Size i = page_start; i < SYSmin(page_end, out.offset_size); ++i) {
            const GA_Offset offset(i);

            const int index_in_page = i - page_start;
            if (index_map.isOffsetActive(offset)) {
                setPageBit(active_bits.bits, index_in_page);
                active += 1;
            } else if (index_map.isOffsetTransient(offset)) {
                setPageBit(temporary_bits.bits, index_in_page);
                temporary += 1;
            } else {
                vacant += 1;
            }
        }
    }

    for (
        GA_AttributeDict::iterator it = gdp->getAttributeDict(owner).begin(GA_SCOPE_INVALID);
        !it.atEnd();
        ++it
    )
    {
        const GA_Attribute *attrib = it.attrib();

        AttribStats attrib_stats;
        attrib_stats.name = attrib->getFullName();
        attrib_stats.data_id = attrib->getDataId();
        attrib_stats.scope = scopeName(attrib->getScope());
    
        //std::cout << "\t" << attrib->getFullName() << std::endl;
        //std::cout << "\t" << attrib->getMemoryUsage(true) << "\t" << attrib->getMemoryUsage(false) << std::endl;

        // Not all child classes of GA_Attribute expose APIs for fetching constant page status
        // (most likely not supported(?). For example GA_ATINumericArray and GA_BlobArray don't
        // provide access.
        const GA_ATINumeric *ati_num = GA_ATINumeric::cast(attrib);
        const GA_ATIString *ati_str = GA_ATIString::cast(attrib);
        const GA_ATIDict *ati_dict = GA_ATIDict::cast(attrib);
        const GA_ATITopology *ati_topo = GA_ATITopology::cast(attrib);
        const GA_ATIGroupBool *ati_group = GA_ElementGroup::cast(attrib);

        if (ati_num || ati_str || ati_dict || ati_topo || ati_group) {
            attrib_stats.has_page_details = true;
            attrib_stats.constant_pages.setSize(out.num_pages);
            attrib_stats.hardened_pages.setSize(out.num_pages);
            if (ati_num) {
                const GA_ATINumeric::DataType &data = ati_num->getData();
                attrib_stats.is_table_hardened = data.isTableHardened();
                for (GA_Size cur_page = 0; cur_page < out.num_pages; ++cur_page) {
                    // We can get the information for constant pages from the ATI interface, but not if the page is
                    // hardened or not, so we'll reach for the GA_PageArray methods.
                    //attrib_stats.constant_pages.setBitFast(cur_page, ati_num->isPageConstant(GA_PageNum(cur_page)));
                    attrib_stats.constant_pages.setBitFast(cur_page, data.isPageConstant(GA_PageNum(cur_page)));
                    attrib_stats.hardened_pages.setBitFast(cur_page, data.isPageHard(GA_PageNum(cur_page)));
                }
            } else if (ati_topo) {
                const GA_ATITopology::DataType &data = ati_topo->getData();
                attrib_stats.is_table_hardened = data.isTableHardened();
                for (GA_Size cur_page = 0; cur_page < out.num_pages; ++cur_page) {
                    attrib_stats.constant_pages.setBitFast(cur_page, data.isPageConstant(GA_PageNum(cur_page)));
                    attrib_stats.hardened_pages.setBitFast(cur_page, data.isPageHard(GA_PageNum(cur_page)));
                }
            } else if (ati_str) {
                const GA_ATIString::HandleArrayType &data = ati_str->getHandleData();
                attrib_stats.is_table_hardened = data.isTableHardened();
                for (GA_Size cur_page = 0; cur_page < out.num_pages; ++cur_page) {
                    attrib_stats.constant_pages.setBitFast(cur_page, data.isPageConstant(GA_PageNum(cur_page)));
                    attrib_stats.hardened_pages.setBitFast(cur_page, data.isPageHard(GA_PageNum(cur_page)));
                }
            } else if (ati_dict) {
                const GA_ATIDict::HandleArrayType &data = ati_dict->getHandleData();
                attrib_stats.is_table_hardened = data.isTableHardened();
                for (GA_Size cur_page = 0; cur_page < out.num_pages; ++cur_page) {
                    attrib_stats.constant_pages.setBitFast(cur_page, data.isPageConstant(GA_PageNum(cur_page)));
                    attrib_stats.hardened_pages.setBitFast(cur_page, data.isPageHard(GA_PageNum(cur_page)));
                }
            } else if (ati_group) {
                // Not backed by a UT_PageArray
                for (GA_Size cur_page = 0; cur_page < out.num_pages; ++cur_page) {
                    attrib_stats.constant_pages.setBitFast(cur_page, ati_group->isPageConstant(GA_PageNum(cur_page)));
                    // No API for querying if the page is hardened.
                    attrib_stats.hardened_pages.setBitFast(cur_page, 0);
                }
            }
        }
        out.attrib_stats.append(std::move(attrib_stats));

    }


    if (opts.get_block_ranges) {
        GA_Range range(index_map);
        GA_Offset start, end;
        for (GA_Iterator it(range); it.fullBlockAdvance(start, end); ) {
                out.full_block_ranges.append(StartStop{start,end});
        }
    }

    return;
}

bool dictThief(PY_PyObject *d, const char *key, PY_PyObject *val) {
    if (!val) return false;
    const int ret = PY_PyDict_SetItemString(d, key, val);
    PY_Py_DECREF(val);
    return ret == 0;
}

PY_PyObject *Py_GeoPageReport(PY_PyObject *self, PY_PyObject *args) {

    PageStatOptions stat_options;
    const char *sop_path = nullptr;
    const char *attrib_owner = nullptr;
    if (!PY_PyArg_ParseTuple(args, "ss|pp", &sop_path, &attrib_owner, &stat_options.get_block_ranges)) PY_Py_RETURN_NONE;
    if (!sop_path) return nullptr;
    if (!attrib_owner) return nullptr;
    try {
        OwnerStats stats;
        GeoPageStats(sop_path, attrib_owner, stat_options, stats);

        PY_PyObject *d = PY_PyDict_New();
        if (!d) return nullptr;

        bool ret = true;
        ret = ret && dictThief(d, "attrib_owner", PY_PyString_FromString(attrib_owner));
        ret = ret && dictThief(d, "num_pages", PY_PyLong_FromLongLong(stats.num_pages));
        ret = ret && dictThief(d, "offset_size", PY_PyLong_FromLongLong(stats.offset_size));
        ret = ret && dictThief(d, "index_size", PY_PyLong_FromLongLong(stats.index_size));
        ret = ret && PY_PyDict_SetItemString(d, "monotonic_map", stats.is_monotonic ? PY_Py_True() : PY_Py_False());
        ret = ret && PY_PyDict_SetItemString(d, "trivial_map", stats.is_trivial ? PY_Py_True() : PY_Py_False());
        // PY_PyBytes_FromStringAndSize is not in Houdini 22's HDK
        //ret = ret && dictThief(d, "num_active_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.active.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        //ret = ret && dictThief(d, "num_temporary_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.temporary.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        //ret = ret && dictThief(d, "num_vacant_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.vacant.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_active_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.active.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_temporary_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.temporary.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_vacant_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.vacant.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "active_bits", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.active_bits.getRawArray()), sizeof(PageBits)*stats.num_pages));
        ret = ret && dictThief(d, "temporary_bits", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.temporary_bits.getRawArray()), sizeof(PageBits)*stats.num_pages));
        if (stat_options.get_block_ranges) {
            ret = ret && dictThief(d, "full_block_ranges", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.full_block_ranges.getRawArray()), sizeof(StartStop)*stats.full_block_ranges.size()));
        }
        // We explicitly setSize(num_pages) on the UT_BitArrays for each attribute earlier
        ret = ret && dictThief(d, "page_words", PY_PyLong_FromLongLong(UT_BitArray::numWords(stats.num_pages)));

        PY_PyObject *ad = PY_PyDict_New();
        for (exint i = 0; i < stats.attrib_stats.size(); ++i) {
            const AttribStats &astats = stats.attrib_stats[i];
            PY_PyObject *d_astats = PY_PyDict_New();
            dictThief(d_astats, "scope", PY_PyString_FromString(astats.scope));
            dictThief(d_astats, "data_id", PY_PyLong_FromLongLong(astats.data_id));
            PY_PyDict_SetItemString(d_astats, "is_table_hardened", astats.is_table_hardened ? PY_Py_True() : PY_Py_False());
            PY_PyDict_SetItemString(d_astats, "has_page_details", astats.has_page_details ? PY_Py_True() : PY_Py_False());
            if (astats.has_page_details) {
                dictThief(d_astats, "constant_pages",
                PY_Py_BuildValue("y#",
                                    reinterpret_cast<const char *>(astats.constant_pages.data()),
                                    sizeof(UT_BitArray::BlockType) * UT_BitArray::numWords(astats.constant_pages.size())
                                )
                );
                dictThief(d_astats, "hardened_pages",
                PY_Py_BuildValue("y#",
                                    reinterpret_cast<const char *>(astats.hardened_pages.data()),
                                    sizeof(UT_BitArray::BlockType) * UT_BitArray::numWords(astats.hardened_pages.size())
                                )
                );
            } else {
                PY_PyDict_SetItemString(d_astats, "constant_pages", PY_Py_None());
                PY_PyDict_SetItemString(d_astats, "hardened_pages", PY_Py_None());
            }
            dictThief(ad, astats.name.c_str(), d_astats);
        }
        dictThief(d, "attrib_stats", ad);
        return d;

    } catch (HOM_Error &error) {
        std::string exception_class_name = UTunmangleClassNameFromTypeIdName(typeid(error).name());
        if (exception_class_name.find("HOM_") == 0)
            exception_class_name = exception_class_name.substr(4);
        PY_PyObject *exception_class;
        PY_AutoObject exception_instance(createHouException(
            exception_class_name.c_str(), error.instanceMessage().c_str(),
            exception_class)
        );
        if (!exception_instance) return nullptr;
        PY_PyErr_SetObject(exception_class, exception_instance);
        return nullptr;
    }
}

PY_PyMODINIT_FUNC
PyInit_shadeops_hom(void) {
    PY_PyObject *pymodule = nullptr;

    PY_InterpreterAutoLock interpreter_auto_lock;

    static PY_PyMethodDef shadeops_hom_methods[] = {
        {"geo_page_report", Py_GeoPageReport, PY_METH_VARARGS(), Doc_GeoPageReport},
        {nullptr, nullptr, 0, nullptr}
    };

    pymodule = PY_Py_InitModule( "shadeops_hom", shadeops_hom_methods);
    return pymodule;
}
