import base64
import gzip
import json

import hou

import _page_tools


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


def report(sop_node: hou.SopNode, output_index: int | None = None) -> dict:
    if not isinstance(sop_node, hou.SopNode):
        raise hou.InvalidNodeType(f"{sop_node} is not a hou.SopNode")
    if output_index is None:
        return _page_tools.report(sop_node.path())
    else:
        return _page_tools.report(sop_node.path(), output_index)


class PageDecoder(json.JSONDecoder):
    _page_byte_data = {
        # attribute_set/owners/<owner>/attributes/<scope>/<name>/memory_block_sharing
        "memory_block_ids",
        "shares_with_mapping_indices",

        # primitive_list/page_details
        # attribute_set/owners/<owner>/attributes/<scope>/<name>/page_details
        "constant_page_bits",
        "hardened_page_bits",
        "shared_page_bits",

        # index_maps/owners/<owner>/occupancy
        "num_active_per_page",
        "num_temporary_per_page",
        "num_vacant_per_page",
        "active_page_bits",
        "temporary_page_bits",
        "full_block_ranges",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self._page_object_decoder, *args, **kwargs)

    def _page_object_decoder(self, d):
        for k in ( d.keys() & self._page_byte_data):
            d[k] = gzip.decompress(base64.b64decode(d[k]))
        return d


class PageEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, bytes):
            return base64.b64encode(gzip.compress(o)).decode("ascii")
        return super().default(o)


def export_page_report(sop_node, file_path, page_info=True):

    report_dict = report(sop_node)

    if not page_info:
        for owner_report in report_dict["attribute_set"]["owners"].values():
            for attrib_scope in owner_report["attributes"].values():
                for attrib in attrib_scope.values():
                    del attrib["shares_with_attrib_keys"]
                    del attrib["memory_block_sharing"]
                    if page_report := attrib["page_details"]:
                        del page_report["constant_page_bits"]
                        del page_report["hardened_page_bits"]
                        del page_report["shared_page_bits"]

        for index_map_report in report_dict["index_maps"]["owners"].values():
            del index_map_report["occupancy"]

        if page_report := report_dict["primitive_list"]["page_details"]:
            del page_report["constant_page_bits"]
            del page_report["hardened_page_bits"]
            del page_report["shared_page_bits"]

    with open(file_path, "w") as f:
        json.dump(report_dict, f, cls=PageEncoder, indent=2)


def load_page_report(file_path):
    with open(file_path, "r") as f:
        return json.load(f, cls=PageDecoder)


