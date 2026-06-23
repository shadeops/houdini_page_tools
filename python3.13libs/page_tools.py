import hou
import os
from pathlib import Path

def build_extensions():
    import inlinecpp
    print("Installing inlinecpp extensions")
    def get_C_src(name):
        with open(f"{Path(__file__).parent}/cpp_sources/{name}.C", "r") as f:
            return f.read()

    inlinecpp.extendClass(
        hou.Geometry,
        "page_tools",
        includes="#include <iostream>",
        function_sources=[
            get_C_src("attrib_report"),
            get_C_src("compress_pages"),
            get_C_src("defrag_geo"),
            get_C_src("page_inspection"),
        ],
    )


import array
import shadeops_hom

def prep_page_report(report):

    for k in [
        "num_active_in_page",
        "num_vacant_in_page",
        "num_temporary_in_page",
    ]:
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
        if stats["constant_pages"]:
            # These are stored as exint / u64, but we'll pick I (u32) here instead of L
            # Since these are just bit masks it doesn't really matter so long as we offset
            # to the right integer. (This is because VEX defaults to 32bits)
            tmp = array.array("I")
            tmp.frombytes(stats["constant_pages"])
            del stats["constant_pages"]
            stats["constant_pages"] = tmp

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
    constant_pages = array.array("I")
    for k,v in report["attrib_stats"].items():
        if skip_private and v["scope"] == "private":
            continue
        if skip_group and v["scope"] == "group":
            continue
        if skip_public and v["scope"] == "public":
            continue
        attrib_names.append(k)
        attrib_ids.append(v["data_id"])
        constant_pages.extend(v["constant_pages"])

    attrib_names_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_names", hou.attribData.String, 1)
    geo.setGlobalAttribValue(attrib_names_atr, attrib_names)

    attrib_ids_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_ids", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(attrib_ids_atr, attrib_ids)

    # We double the array size here because constant_page_words is with respect to a exint (u64) but
    # we are converting the array to u32[2] for VEX reasons.
    constant_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "constant_pages", hou.attribData.Int, report["constant_page_words"]*2)
    geo.setGlobalAttribValue(constant_pages_atr, constant_pages)

