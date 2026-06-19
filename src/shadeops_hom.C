#include <PY/PY_CPythonAPI.h>
#include <PY/PY_Python.h>
#include <PY/PY_AutoObject.h>
#include <PY/PY_InterpreterAutoLock.h>
#include <HOM/HOM_Module.h>
#include <UT/UT_DSOVersion.h>
#include <OP/OP_Director.h>
#include <UT/UT_SysSpecific.h>
#include <GU/GU_DetailHandle.h>
#include <GU/GU_Detail.h>

#include <iostream>

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

static const char *Doc_GeoPageReport = "do a thing\n";

PY_PyObject *Py_GeoPageReport(PY_PyObject *self, PY_PyObject *args) {

    HOM_AutoLock hom_lock;
    PY_InterpreterAutoLock interpreter_auto_lock;

    PY_PyObject *hou_geometry = nullptr;
    if (!PY_PyArg_ParseTuple(args, "O", &hou_geometry)) PY_Py_RETURN_NONE;

    PY_AutoObject hou_module(PY_PyImport_ImportModule("hou"));
    PY_PyObject *hou_module_dict = PY_PyModule_GetDict(hou_module);
    PY_PyObject *hou_geometry_class = PY_PyDict_GetItemString(hou_module_dict, "Geometry");
    if (!hou_geometry_class) {
        PY_PyErr_SetString(PY_PyExc_RuntimeError(), "hou.Geometry not defined");
        return NULL;
    }
    if (!PY_PyObject_TypeCheck(hou_geometry, (PY_PyTypeObject *)hou_geometry_class)) {
        PY_PyErr_SetString(PY_PyExc_TypeError(), "Not a hou.Geometry");
        return NULL;

    }
    try {
        // TODO -  Fast Call?
        //PY_PyObject *py_guhandle = PY_PyObject_CallMethod(hou_geometry, "_guDetailHandle", NULL);
        PY_AutoObject py_guhandle(
            PY_PyObject_CallMethod(hou_geometry, "_guDetailHandle", NULL)
        );
        if (!py_guhandle) return NULL;
        
        PY_AutoObject swig_ptr(PY_PyObject_CallMethod(py_guhandle, "_asVoidPointer", NULL));
        if (!swig_ptr) return NULL;

        PY_AutoObject gdp_address(PY_PyObject_CallMethod(swig_ptr, "__int__", NULL));
        if (!gdp_address) return NULL;
        
        const GU_Detail *gdp = static_cast<const GU_Detail *>(PY_PyLong_AsVoidPtr(gdp_address));
        if (!gdp && PY_PyErr_Occurred()) return NULL;
       
        GA_Size num_points = gdp->getNumPoints();
        std::cout << num_points << std::endl; 
        PY_PyObject *result = PY_PyLong_FromLongLong(num_points);
        PY_AutoObject(PY_PyObject_CallMethod(py_guhandle, "destroy", NULL));
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
