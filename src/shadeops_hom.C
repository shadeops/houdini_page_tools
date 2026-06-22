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

// SoA since we are going to create a Python object from each onne of these.
struct AttribStats {
    GA_Size offset_size = 0;
    GA_Size num_pages = 0;
    UT_Array<GA_Size> active;
    UT_Array<GA_Size> temporary;
    UT_Array<GA_Size> vacant;
    UT_Array<PageBits> active_bits;
    UT_Array<PageBits> temp_bits;
};

// uint32_t[32]
// b = 0 to 1023 (inclusive)
// b >> 5 = which uint32_t to write to in the array of 32
// lowest bit == 0 index in page.
inline void setPageBit (uint32_t *mask, int b)       { mask[b >> 5] |= (uint32_t(1) << (b & 31)); }

void GeoPageStats(const char *sop_path, const char *owner_str, AttribStats &out) {
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

    const GA_Size num_pages = GA_Size(index_map.offsetSize() + GA_PAGE_SIZE - 1) >> GA_PAGE_BITS;
    out.num_pages = num_pages;
    out.offset_size = index_map.offsetSize();

    out.active.setSize(out.num_pages);
    out.temporary.setSize(out.num_pages);
    out.vacant.setSize(out.num_pages);
    out.active_bits.setSize(out.num_pages);
    out.temp_bits.setSize(out.num_pages);

    for (GA_Size cur_page = 0; cur_page < num_pages; ++cur_page) {

        const GA_Size page_start = cur_page << GA_PAGE_BITS;
        const GA_Size page_end = (cur_page+1) << GA_PAGE_BITS;

        GA_Size &active = out.active[cur_page];
        GA_Size &temporary = out.temporary[cur_page];
        GA_Size &vacant = out.vacant[cur_page];
        PageBits &active_bits = out.active_bits[cur_page];
        PageBits &temp_bits = out.temp_bits[cur_page];

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
                setPageBit(temp_bits.bits, index_in_page);
                temporary += 1;
            } else {
                vacant += 1;
            }
        }
    }

    //for (
    //    GA_AttributeDict::iterator it = gdp->getAttributeDict(owner).begin(GA_SCOPE_PUBLIC);
    //    !it.atEnd();
    //    ++it
    //)
    //{
    //    const GA_Attribute *attrib = it.attrib();
    //    std::cout << "\t" << attrib->getFullName() << std::endl;

    //    GA_Offset start, end;

    //    for (GA_Iterator it(gdp->getPointRange()); it.blockAdvance(start, end); ) {
    //        std::cout << start << " " << end << std::endl;
    //    }
    //}

    return; 
}

bool dictThief(PY_PyObject *d, const char *key, PY_PyObject *val) {
    if (!val) return false;
    const int ret = PY_PyDict_SetItemString(d, key, val);
    PY_Py_DECREF(val);
    return ret == 0;
}

PY_PyObject *Py_GeoPageReport(PY_PyObject *self, PY_PyObject *args) {

    const char *sop_path = nullptr;
    const char *attrib_owner = nullptr;
    if (!PY_PyArg_ParseTuple(args, "ss", &sop_path, &attrib_owner)) PY_Py_RETURN_NONE;
    if (!sop_path) return nullptr;
    if (!attrib_owner) return nullptr;
    try {
        AttribStats stats;
        GeoPageStats(sop_path, attrib_owner, stats);

        PY_PyObject *d = PY_PyDict_New();
        if (!d) return nullptr;

        bool ret = 1;
        ret = ret && dictThief(d, "attrib_owner", PY_PyString_FromString(attrib_owner));
        ret = ret && dictThief(d, "num_pages", PY_PyLong_FromLongLong(stats.num_pages));
        ret = ret && dictThief(d, "offset_size", PY_PyLong_FromLongLong(stats.offset_size));
        ret = ret && dictThief(d, "size_of_ga_size", PY_PyLong_FromLongLong(sizeof(GA_Size)));
        // These should be PY_PyBytes_FromStringAndSize
        //ret = ret && dictThief(d, "num_active_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.active.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        //ret = ret && dictThief(d, "num_temporary_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.temporary.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        //ret = ret && dictThief(d, "num_vacant_in_page", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(stats.vacant.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_active_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.active.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_temporary_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.temporary.getRawArray()), sizeof(GA_Size)*stats.num_pages));
        ret = ret && dictThief(d, "num_vacant_in_page", PY_Py_BuildValue("y#", reinterpret_cast<const char *>(stats.vacant.getRawArray()), sizeof(GA_Size)*stats.num_pages));
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
