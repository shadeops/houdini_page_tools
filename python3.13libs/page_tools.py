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

