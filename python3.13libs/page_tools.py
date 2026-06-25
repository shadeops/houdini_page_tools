import array
import pathlib

import hou

def build_extensions():
    import inlinecpp
    print("Installing inlinecpp extensions")
    def get_C_src(name):
        with open(f"{pathlib.Path(__file__).parent}/cpp_sources/{name}.C", "r") as f:
            return f.read()

    # There is a bug in inlinecpp.extendClass, so we need to create a library
    # and extend it ourselves.
    page_tools_mod = inlinecpp.createLibrary(
        "page_tools",
        includes="""
#include <iostream>
#include <GU/GU_Detail.h>
""",
        function_sources=[
            get_C_src("compress_pages"),
            get_C_src("defrag_geo"),
        ],
    )

    def _make_wrapper(function):
        def _CPPFunctionWrapper(*args, **kwargs):
            return function(*args, **kwargs)
        return _CPPFunctionWrapper
    for function in page_tools_mod._functions:
        setattr(hou.Geometry, function.name, _make_wrapper(function))


#import shadeops_hom
def prep_page_report(report):

    for k in (
        "num_active_in_page",
        "num_vacant_in_page",
        "num_temporary_in_page",
    ):
        tmp = array.array("L")
        tmp.frombytes(report[k])
        assert len(tmp) == report["num_pages"]
        del report[k]
        report[k] = tmp

    for k in [
        "temporary_bits",
        "active_bits",
    ]:
        tmp = array.array("I")
        tmp.frombytes(report[k])
        assert len(tmp) == report["num_pages"] * 32
        del report[k]
        report[k] = tmp

    for attrib, stats in report["attrib_stats"].items():
        for page_type in ("constant_pages", "hardened_pages"):
            if stats[page_type]:
                # These are stored as exint / u64, but we'll pick I (u32) here instead of L
                # Since these are just bit masks it doesn't really matter so long as we offset
                # to the right integer. (This is because VEX defaults to 32bits)
                tmp = array.array("I")
                tmp.frombytes(stats[page_type])
                del stats[page_type]
                stats[page_type] = tmp

    return report

def page_report_to_attribs(geo, report, skip_public=False, skip_private=True, skip_group=True):

    prep_page_report(report)

    active_bits = geo.addArrayAttrib(hou.attribType.Global, "active_bits", hou.attribData.Int, 32)
    geo.setGlobalAttribValue(active_bits, report["active_bits"])

    temporary_bits = geo.addArrayAttrib(hou.attribType.Global, "temporary_bits", hou.attribData.Int, 32)
    geo.setGlobalAttribValue(temporary_bits, report["temporary_bits"])

    num_pages = geo.addAttrib(hou.attribType.Global, "num_pages", 0)
    geo.setGlobalAttribValue(num_pages, report["num_pages"])

    offset_size = geo.addAttrib(hou.attribType.Global, "offset_size", 0)
    geo.setGlobalAttribValue(offset_size, report["offset_size"])

    index_size = geo.addAttrib(hou.attribType.Global, "index_size", 0)
    geo.setGlobalAttribValue(index_size, report["index_size"])

    monotonic_map = geo.addAttrib(hou.attribType.Global, "monotonic_map", 0)
    geo.setGlobalAttribValue(monotonic_map, report["monotonic_map"])

    trivial_map = geo.addAttrib(hou.attribType.Global, "trivial_map", 0)
    geo.setGlobalAttribValue(trivial_map, report["trivial_map"])

    owner = geo.addAttrib(hou.attribType.Global, "owner", "")
    geo.setGlobalAttribValue(owner, report["attrib_owner"])

    attrib_names = []
    attrib_ids = []
    page_info = []
    constant_pages = array.array("I")
    hardened_pages = array.array("I")
    attribs_reported = 0
    # padding for attributes that don't have page data available
    empty_pages = array.array("I", [0,] * report["page_words"] )
    for k,v in report["attrib_stats"].items():
        if skip_private and v["scope"] == "private":
            continue
        if skip_group and v["scope"] == "group":
            continue
        if skip_public and v["scope"] == "public":
            continue
        attribs_reported += 1
        attrib_names.append(k)
        attrib_ids.append(v["data_id"])
        if v["constant_pages"] is None:
            page_info.append(0)
            constant_pages.extend(empty_pages)
            hardened_pages.extend(empty_pages)
        else:
            page_info.append(1)
            constant_pages.extend(v["constant_pages"])
            hardened_pages.extend(v["hardened_pages"])

    attrib_names_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_names", hou.attribData.String, 1)
    geo.setGlobalAttribValue(attrib_names_atr, attrib_names)

    attrib_ids_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_ids", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(attrib_ids_atr, attrib_ids)

    # We double the array size here because constant_page_words is with respect to a exint (u64) but
    # we are converting the array to u32[2] for VEX reasons.
    constant_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "constant_pages", hou.attribData.Int, report["page_words"]*2)
    geo.setGlobalAttribValue(constant_pages_atr, constant_pages)

    hardened_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "hardened_pages", hou.attribData.Int, report["page_words"]*2)
    geo.setGlobalAttribValue(hardened_pages_atr, hardened_pages)

    page_info_atr = geo.addArrayAttrib(hou.attribType.Global, "page_info", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(page_info_atr, page_info)

    attribs_reported_atr = geo.addAttrib(hou.attribType.Global, "attribs_reported", 0)
    geo.setGlobalAttribValue(attribs_reported_atr, attribs_reported)

    page_words_atr = geo.addAttrib(hou.attribType.Global, "page_words", 0)
    geo.setGlobalAttribValue(page_words_atr, report["page_words"]*2)

