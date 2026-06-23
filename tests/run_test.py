import hou
import array
import pprint
import shadeops_hom

# Two attributes can share the same data ID even when they don't share
# the same internal data pages or even fragmentation patterns.  Note
# however that the data ID is not necessarily changed for topology
# changes, so it is necessary to also compare topology data IDs in order
# to be absolutely sure that two attributes sharing the same data ID
# represent the same data (taking into account defragmentation).

def convert_report(report):
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
            tmp = array.array("L")
            tmp.frombytes(stats["constant_pages"])
            del stats["constant_pages"]
            stats["constant_pages"] = tmp

    return report

geo = hou.node("/obj").createNode("geo")

pts = geo.createNode("pointgenerate")
pts.parm("npts").set(2048)
pts.cook(force=True)

blast = geo.createNode("blast")
blast.setFirstInput(pts)
blast.parm("group").set("1")
blast.parm("grouptype").set(3)

delete = geo.createNode("delete")
delete.setFirstInput(pts)
delete.parm("group").set("1")
delete.parm("entity").set(1)

print("Point Generate")
path = pts.path()
page_report = convert_report(shadeops_hom.geo_page_report(path, "point"))
pprint.pp(page_report)

print("Blast")
path = blast.path()
page_report = convert_report(shadeops_hom.geo_page_report(path, "point"))
pprint.pp(page_report)

print("Delete")
path = delete.path()
page_report = convert_report(shadeops_hom.geo_page_report(path, "point"))
pprint.pp(page_report)

try:
    shadeops_hom.geo_page_report("blah", "point")
except hou.NodeError:
    pass

print("Success")

