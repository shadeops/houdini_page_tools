import array
import page_tools
import hou

def prep_page_report(occupancy, num_pages, page_layout):

    assert page_layout["ga_size_in_bytes"] == array.array("q").itemsize
    assert array.array("i").itemsize == 4       # VEX ints: the // 32 below assumes 32 bits
    # The *_offset_bits are unpacked into 32-bit VEX ints, page_size bits per page.
    vex_ints_per_page = page_layout["page_size"] // 32

    for k in (
        "num_active_per_page",
        "num_vacant_per_page",
        "num_temporary_per_page",
    ):
        tmp = array.array("q")
        tmp.frombytes(occupancy[k])
        assert len(tmp) == num_pages
        del occupancy[k]
        occupancy[k] = tmp

    for k in (
        "full_block_ranges",
    ):
        if k not in occupancy:
            continue
        tmp = array.array("q")
        tmp.frombytes(occupancy[k])
        del occupancy[k]
        occupancy[k] = tmp

    for k in (
        "temporary_offset_bits",
        "active_offset_bits",
    ):
        tmp = array.array("i")
        tmp.frombytes(occupancy[k])
        assert len(tmp) == num_pages * vex_ints_per_page
        del occupancy[k]
        occupancy[k] = tmp

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

    return occupancy

def page_report_as_attribs(sop, geo, owner="point", include_public=True, include_private=False, include_groups=False):

    full_report = page_tools.report(sop)

    index_map_report = full_report["index_maps"]["owners"][owner]
    page_layout = full_report["page_layout"]
    attributes = full_report["attribute_set"]["owners"][owner]["attributes"]

    num_pages_atr = geo.addAttrib(hou.attribType.Global, "num_pages", 0)
    geo.setGlobalAttribValue(num_pages_atr, index_map_report["num_pages"])

    if index_map_report["num_pages"] == 0:
        # empty geometry
        return

    num_pages = index_map_report["num_pages"]
    occupancy = prep_page_report(index_map_report["occupancy"], num_pages, page_layout)
    vex_ints_per_page = page_layout["page_size"] // 32
    # The *_page_bits masks, truncated to the 32-bit VEX ints that hold num_pages bits.
    vex_ints_per_mask = -(-num_pages // 32)
    mask_bytes = vex_ints_per_mask * array.array("i").itemsize

    active_bits = geo.addArrayAttrib(hou.attribType.Global, "active_bits", hou.attribData.Int,
                                     vex_ints_per_page)
    geo.setGlobalAttribValue(active_bits, occupancy["active_offset_bits"])

    temporary_bits = geo.addArrayAttrib(hou.attribType.Global, "temporary_bits", hou.attribData.Int,
                                        vex_ints_per_page)
    geo.setGlobalAttribValue(temporary_bits, occupancy["temporary_offset_bits"])

    offset_size = geo.addAttrib(hou.attribType.Global, "offset_size", 0)
    geo.setGlobalAttribValue(offset_size, index_map_report["offset_size"])

    index_size = geo.addAttrib(hou.attribType.Global, "index_size", 0)
    geo.setGlobalAttribValue(index_size, index_map_report["index_size"])

    monotonic_map = geo.addAttrib(hou.attribType.Global, "monotonic_map", 0)
    geo.setGlobalAttribValue(monotonic_map, index_map_report["is_monotonic"])

    trivial_map = geo.addAttrib(hou.attribType.Global, "trivial_map", 0)
    geo.setGlobalAttribValue(trivial_map, index_map_report["is_trivial"])

    owner_atr = geo.addAttrib(hou.attribType.Global, "owner", "")
    geo.setGlobalAttribValue(owner_atr, owner)

    attrib_names = []
    attrib_ids = []
    page_info = []
    constant_pages = array.array("i")
    hardened_pages = array.array("i")
    # padding for attributes that don't have page data available
    empty_pages = array.array("i", [0,] * vex_ints_per_mask )
    scope_filter = (
        "public" if include_public else None,
        "private" if include_private else None,
        "group" if include_groups else None,
    )
    attribs_reported = 0
    for scope, attribs in attributes.items():
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
                # The report packs these as 64-bit ints so it is possible there can be an extra
                # 4 bytes of data if num_pages % 64 is between [1 and 32]
                t.frombytes(page_details["constant_page_bits"][:mask_bytes])
                constant_pages.extend(t)
                t = array.array("i")
                t.frombytes(page_details["hardened_page_bits"][:mask_bytes])
                hardened_pages.extend(t)

    attrib_names_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_names", hou.attribData.String, 1)
    geo.setGlobalAttribValue(attrib_names_atr, attrib_names)

    attrib_ids_atr = geo.addArrayAttrib(hou.attribType.Global, "attrib_ids", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(attrib_ids_atr, attrib_ids)

    constant_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "constant_pages", hou.attribData.Int, vex_ints_per_mask)
    geo.setGlobalAttribValue(constant_pages_atr, constant_pages)

    hardened_pages_atr = geo.addArrayAttrib(hou.attribType.Global, "hardened_pages", hou.attribData.Int, vex_ints_per_mask)
    geo.setGlobalAttribValue(hardened_pages_atr, hardened_pages)

    page_info_atr = geo.addArrayAttrib(hou.attribType.Global, "page_info", hou.attribData.Int, 1)
    geo.setGlobalAttribValue(page_info_atr, page_info)

    attribs_reported_atr = geo.addAttrib(hou.attribType.Global, "attribs_reported", 0)
    geo.setGlobalAttribValue(attribs_reported_atr, attribs_reported)

    page_words_atr = geo.addAttrib(hou.attribType.Global, "page_words", 0)
    geo.setGlobalAttribValue(page_words_atr, vex_ints_per_mask)

    if "full_block_ranges" in occupancy:
        full_block_ranges_atr = geo.addArrayAttrib(hou.attribType.Global, "full_block_ranges", hou.attribData.Int, 2)
        geo.setGlobalAttribValue(full_block_ranges_atr, occupancy["full_block_ranges"])

