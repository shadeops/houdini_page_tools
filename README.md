# SOP Memory Reporting Tools for Houdini

This repo contains some tools for analyzing how Houdini's SOP memory works.
The core of the tool set is a [C++ HOM extension](src/_page_tools.C).

With the reporting provided by the `_page_tools.report()` function additional visualizers are built.


# Quick Start
Assuming you are in a Houdini environment with a C++ compiler available -
* Clone the repo `git clone https://github.com/shadeops/houdini_page_tools`
* Build the extension with `hcustom -i houdini_page_tools/python3.13libs houdini_page_tools/src/_page_tools.C`
* Set the Houdini Path to include the root of the repo. `export HOUDINI_PATH="$PWD/houdini_page_tools:&"
* Launch Houdini
* ```
  import page_tools
  page_tools.report(hou.node("/obj/geo1/grid1").path())
  ```
  
The above assumes Houdini 22, if using a different version you'll need to change python3.13libs to match your
version of Houdini's python libs directory. Also, instead of setting the HOUDINI_PATH you can place the path
in your `houdini.env` or make a Houdini package.

When building, if you get an warning about `__builtin_memcmp` exceeds maximum size, you can silence it with
```
HCUSTOM_CFLAGS='-Wno-stringop-overread' hcustom -i hfs_pages/python3.13libs hfs_pages/src/_page_tools.C
```
This happens with very new versions of gcc that have a bit more warning rigour than the versions SideFX is using. 


# The Tools

## `_page_tools` HOM Extension
This adds a new module called `_page_tools` which can be imported. It provides a single function `report`.
```
>>> import _page_tools
>>> help(_page_tools)
Help on module _page_tools:

NAME
    _page_tools

FUNCTIONS
    report(...)
        report(node_path, output_index=<view output>) -> dict
```
This returns a large Python dictionary, the schema for it can be found in the header of [the source code](src/_page_tools.C)

## `page_tools` Python Module
The `page_tools` module is a simple wrapper around `_page_tools` that also adds two additional functions to `hou.Geometry` via
inlinecpp.

The two functions added:
### `hou.Geometry.defragment(fill_holes: bool)`
Defragment the pages within the Geometry.

### `hou.Geometry.compress_pages()`
Try to compress pages into constant pages.

## Geo Page Viewer State
A custom viewer state that tracks the currently selected geometry and displays a visual representation of the geometry page occupancy.
Can be accessed through the *Sop Page Tools* Shelf Set
