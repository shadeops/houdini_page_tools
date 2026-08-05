import array
import pathlib

import _page_tools

import hou

report = _page_tools.report

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


def prep_page_report(report):

    assert array.array("l").itemsize == 8
    assert array.array("I").itemsize == 4

    for k in (
        "num_active_per_page",
        "num_vacant_per_page",
        "num_temporary_per_page",
    ):
        tmp = array.array("l")
        tmp.frombytes(report[k])
        assert len(tmp) == report["num_pages"]
        del report[k]
        report[k] = tmp

    for k in (
        "full_block_ranges",
    ):
        if k not in report:
            continue
        tmp = array.array("l")
        tmp.frombytes(report[k])
        del report[k]
        report[k] = tmp

    for k in (
        "temporary_page_bits",
        "active_page_bits",
    ):
        tmp = array.array("I")
        tmp.frombytes(report[k])
        assert len(tmp) == report["num_pages"] * 32
        del report[k]
        report[k] = tmp

    #for attrib, stats in report["attrib_stats"].items():
    #    for page_type in ("constant_pages", "hardened_pages"):
    #        if stats[page_type]:
    #            # These are stored as exint / u64, but we'll pick I (u32) here instead of L
    #            # Since these are just bit masks it doesn't really matter so long as we offset
    #            # to the right integer. (This is because VEX defaults to 32bits)
    #            tmp = array.array("I")
    #            tmp.frombytes(stats[page_type])
    #            del stats[page_type]
    #            stats[page_type] = tmp

    return report

def page_report_to_attribs(geo, detail_report, owner="point", include_public=True, include_private=False, include_groups=False):

    report = detail_report["owners"][owner]

    num_pages = geo.addAttrib(hou.attribType.Global, "num_pages", 0)
    geo.setGlobalAttribValue(num_pages, report["num_pages"])

    if report["num_pages"] == 0:
        # empty geometry
        return

    prep_page_report(report)

    active_bits = geo.addArrayAttrib(hou.attribType.Global, "active_bits", hou.attribData.Int, 32)
    geo.setGlobalAttribValue(active_bits, report["active_page_bits"])

    temporary_bits = geo.addArrayAttrib(hou.attribType.Global, "temporary_bits", hou.attribData.Int, 32)
    geo.setGlobalAttribValue(temporary_bits, report["temporary_page_bits"])

    offset_size = geo.addAttrib(hou.attribType.Global, "offset_size", 0)
    geo.setGlobalAttribValue(offset_size, report["offset_size"])

    index_size = geo.addAttrib(hou.attribType.Global, "index_size", 0)
    geo.setGlobalAttribValue(index_size, report["index_size"])

    monotonic_map = geo.addAttrib(hou.attribType.Global, "monotonic_map", 0)
    geo.setGlobalAttribValue(monotonic_map, report["is_monotonic"])

    trivial_map = geo.addAttrib(hou.attribType.Global, "trivial_map", 0)
    geo.setGlobalAttribValue(trivial_map, report["is_trivial"])

    owner_atr = geo.addAttrib(hou.attribType.Global, "owner", "")
    geo.setGlobalAttribValue(owner_atr, owner)

    attrib_names = []
    attrib_ids = []
    page_info = []
    constant_pages = array.array("i")
    hardened_pages = array.array("i")
    # padding for attributes that don't have page data available
    empty_pages = array.array("i", [0,] * report["page_mask_words"] )
    scope_filter = (
        "public" if include_public else None,
        "private" if include_private else None,
        "group" if include_groups else None,
    )
    attribs_reported = 0
    for scope, attribs in report["attributes"].items():
        if scope not in scope_filter:
            continue
        for k,v in attribs.items():
            attribs_reported += 1
            attrib_names.append(k)
            attrib_ids.append(v["data_id"])
            page_details = v["page_details"]
            if page_details is None:
                page_info.append(0)
                constant_pages.extend(empty_pages)
                hardened_pages.extend(empty_pages)
            else:
                page_info.append(1)
                t = array.array("i")
                t.frombytes(page_details["constant_page_bits"])
                constant_pages.extend(t)
                t = array.array("i")
                t.frombytes(page_details["hardened_page_bits"])
                hardened_pages.extend(t)

    attrib_names_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_names", hou.attribData.String, 1)
    geo.setGlobalAttribValue(attrib_names_atr, attrib_names)

    attrib_ids_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_ids", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(attrib_ids_atr, attrib_ids)

    # We double the array size here because constant_page_words is with respect to a exint (u64) but
    # we are converting the array to u32[2] for VEX reasons.
    constant_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "constant_pages", hou.attribData.Int, report["page_mask_words"]*2)
    geo.setGlobalAttribValue(constant_pages_atr, constant_pages)

    hardened_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "hardened_pages", hou.attribData.Int, report["page_mask_words"]*2)
    geo.setGlobalAttribValue(hardened_pages_atr, hardened_pages)

    page_info_atr = geo.addArrayAttrib(hou.attribType.Global, "page_info", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(page_info_atr, page_info)

    attribs_reported_atr = geo.addAttrib(hou.attribType.Global, "attribs_reported", 0)
    geo.setGlobalAttribValue(attribs_reported_atr, attribs_reported)

    page_words_atr = geo.addAttrib(hou.attribType.Global, "page_words", 0)
    geo.setGlobalAttribValue(page_words_atr, report["page_mask_words"]*2)

    if "full_block_ranges" in report:
        full_block_ranges_atr = geo.addArrayAttrib(hou.attribType.Global, "full_block_ranges", hou.attribData.Int, 2)
        geo.setGlobalAttribValue(full_block_ranges_atr, report["full_block_ranges"])

