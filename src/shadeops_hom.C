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
    exception_class = NULL;
    PY_AutoObject hou_module(PY_PyImport_ImportModule("hou"));
    PY_PyObject *hou_module_dict = PY_PyModule_GetDict(hou_module);
    
    exception_class = PY_PyDict_GetItemString(hou_module_dict, exception_class_name);
    if (!exception_class) {
        PY_PyErr_SetString(PY_PyExc_RuntimeError(), "Could not find exception class in hou module");
        return NULL;
    }
    PY_AutoObject args(PY_Py_BuildValue("(s)", instance_message));
    if (!args) return NULL;
    return PY_PyObject_Call(exception_class, args, NULL);
}

GA_Size GeoPageStats(const char *sop_path) {
    HOM_AutoLock hom_lock;

    SOP_Node *sop_node = OPgetDirector()->findSOPNode(sop_path);
    if (!sop_node) {
        throw HOM_NodeError("Could not find sop");
    }
   
    OP_Context context{};
    // TODO: Pick output?
    GU_DetailHandle gu_handle = sop_node->getCookedGeoHandle(context);
    if (gu_handle.isNull()) {
        throw HOM_InvalidGeometry("Could not fetch cooked geometry");
    }
    const GU_Detail *gdp = gu_handle.gdp();
    const GA_IndexMap &point_map = gdp->getPointMap();

    std::cout << "Index Size: " << point_map.indexSize() << std::endl;
    std::cout << "Offset Size: " << point_map.offsetSize() << std::endl;
    
    for (
        GA_AttributeDict::iterator it = gdp->getAttributeDict(GA_ATTRIB_POINT).begin(GA_SCOPE_PUBLIC);
        !it.atEnd();
        ++it
    )
    {
        const GA_Attribute *attrib = it.attrib();
        std::cout << attrib->getFullName() << std::endl;
    }

    GA_Offset start, end;
    for (GA_Iterator it(gdp->getPointRange()); it.blockAdvance(start, end); ) {
        std::cout << start << " " << end << std::endl;
    }
    GA_Size num_points = gdp->getNumPoints();
    std::cout << "fetching pts: " << num_points << std::endl;
    
    return num_points;
}

PY_PyObject *Py_GeoPageReport(PY_PyObject *self, PY_PyObject *args) {

    const char *sop_path = nullptr;
    if (!PY_PyArg_ParseTuple(args, "s", &sop_path)) PY_Py_RETURN_NONE;
    if (!sop_path) return NULL;
    
    try {
        GA_Size num_points = GeoPageStats(sop_path);
        PY_PyObject *result = PY_PyLong_FromLong(num_points);
        return result;

    } catch (HOM_Error &error) {
        std::string exception_class_name = UTunmangleClassNameFromTypeIdName(typeid(error).name());
        if (exception_class_name.find("HOM_") == 0)
            exception_class_name = exception_class_name.substr(4);
        PY_PyObject *exception_class;
        PY_AutoObject exception_instance(createHouException(
            exception_class_name.c_str(), error.instanceMessage().c_str(),
            exception_class)
        );
        if (!exception_instance) return NULL;
        PY_PyErr_SetObject(exception_class, exception_instance);
        return NULL;
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
