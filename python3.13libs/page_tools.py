import hou

import _page_tools


def report(sop_node: hou.SopNode, output_index: int | None = None) -> dict:
    if not isinstance(sop_node, hou.SopNode):
        raise hou.InvalidNodeType(f"{sop_node} is not a hou.SopNode")
    if output_index is None:
        return _page_tools.report(sop_node.path())
    else:
        return _page_tools.report(sop_node.path(), output_index)

def add_page_tools_extensions() -> None:
    """Adds two custom functions to hou.Geometry

    hou.Geometry.compress_pages() -> None
        Iterate through public attributes trying to constant compress them.

    hou.Geometry.defragment(fill_holes: bool) -> None
        Try to defragement the geometry's index maps.
    """

    import inlinecpp

    # There is a bug in inlinecpp.extendClass, so we need to create a library
    # and extend it ourselves.  [https://www.sidefx.com/bugs/bug/158014]
    page_tools_mod = inlinecpp.createLibrary(
        "page_tools",
        includes="""
#include <GU/GU_Detail.h>
""",
        function_sources=[
"""
void compress_pages(GU_Detail *gdp) {
    for (
        GA_AttributeDict::iterator it = gdp->getAttributeDict(GA_ATTRIB_POINT).begin(GA_SCOPE_PUBLIC);
        !it.atEnd();
        ++it
    )
    {
        GA_Attribute *attrib = it.attrib();
        attrib->tryCompressAllPages();
    }
}

""",
"""
bool defragment(GU_Detail *gdp, bool fill_holes) {
    UT_Options defrag_opts;
    defrag_opts.setOptionB("removeholes", fill_holes);
    return gdp->defragment(&defrag_opts);
}
"""
        ],
    )
    def _make_wrapper(function):
        def _CPPFunctionWrapper(*args, **kwargs):
            return function(*args, **kwargs)
        return _CPPFunctionWrapper
    for function in page_tools_mod._functions:
        setattr(hou.Geometry, function.name, _make_wrapper(function))


