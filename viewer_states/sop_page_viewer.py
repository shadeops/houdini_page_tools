import hou
import hdefereval

# No true "cooked" event exists; these are what drive a SOP recook.
COOK_EVENTS = (
    hou.nodeEventType.ParmTupleChanged,
    hou.nodeEventType.InputDataChanged,
    hou.nodeEventType.InputRewired,
    hou.nodeEventType.BeingDeleted,
)

import shadeops_hom
import page_tools


class SOPPageViewerState(object):
    def __init__(self, state_name, scene_viewer):
        self.state_name = state_name
        self.scene_viewer = scene_viewer
        self.node = None
        self.geo = hou.SimpleDrawable(self.scene_viewer, hou.Geometry(), "page_view")
        self.invokegraph = hou.sopNodeTypeCategory().nodeVerbs()["invokegraph"]
        self.invokegraph.setParms(
            {
                "method": 1,
                "inputgroup": "inputs",
            }
        )
        self.graph_geo = hou.Geometry()
        self.graph_geo.loadFromFile("page_viewer_graph.bgeo.sc")
        self.opts_geo = hou.Geometry()
        self.opts_geo.addAttrib(hou.attribType.Global, "display_blocks", 0)
        self.geo.enable(True)
        self.geo.setXray(False)
        self.geo.setDisplayMode(hou.drawableDisplayMode.CurrentViewportMode)
        self.geo.setVisibleInAllViewports()
        self.geo.setUseWireframeColor(False)
        self.geo.setOutlineOnly(False)
        self.geo.setDrawOutline(False)

        self.geo_page_owner = "point"
        self.geo_page_public = True
        self.geo_page_private = False
        self.geo_page_groups = False


    def onGenerate(self, kwargs):
        kwargs["state_flags"]["exit_on_node_select"] = False
        self.scene_viewer.setPromptMessage("Select SOP Node")
        hou.ui.addSelectionCallback(self._on_selection)
        initial_sop = self.scene_viewer.currentNode()
        self._set_node(initial_sop if isinstance(initial_sop, hou.SopNode) else None)

    def onExit(self, kwargs):
        hou.ui.removeSelectionCallback(self._on_selection)
        self._set_node(None)
        self.scene_viewer.clearPromptMessage()

    def onMenuAction(self, kwargs):
        self.geo_page_owner = kwargs.get("geo_page_owner", self.geo_page_owner)
        self.geo_page_public = kwargs.get("geo_page_public", self.geo_page_public)
        self.geo_page_private = kwargs.get("geo_page_private", self.geo_page_private)
        self.geo_page_groups = kwargs.get("geo_page_groups", self.geo_page_groups)
        self.opts_geo.setGlobalAttribValue(
            "display_blocks",
            0 if kwargs.get("geo_page_display", "status") == "status" else 1,
        )
        self._update_geo()

    def _update_geo(self):
        #self.log(f"update_geo {self.node}")
        if not self.node:
            return
        new_geo = hou.Geometry()
        report_geo = hou.Geometry()
        report = shadeops_hom.geo_page_report(
            self.node.path(),
            self.geo_page_owner,
            True,
        )
        page_tools.page_report_to_attribs(
            report_geo,
            report,
            self.geo_page_public,
            self.geo_page_private,
            self.geo_page_groups,
        )
        self.invokegraph.execute(new_geo, [self.graph_geo, report_geo, self.opts_geo])
        report_geo.clear()
        self.geo.setGeometry(new_geo)
        self.geo.show(True)
        self.scene_viewer.curViewport().draw()


    def _on_selection(self, selection):
        #self.log("on selection")
        sops = [i for i in selection if isinstance(i, hou.SopNode)]
        current_sop = sops[-1] if sops else None
        self._set_node(current_sop)

    def _request_update(self):
        #self.log(f"request update {self.node}")
        self._update_geo()


    def _set_node(self, node):
        #self.log(f"set node {node}")
        if node is self.node:
            return
        if self.node:
            try:
                self.node.removeEventCallback(COOK_EVENTS, self._on_node_event)
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
        self.node = node
        if node:
            node.addEventCallback(COOK_EVENTS, self._on_node_event)
            hdefereval.executeDeferred(self._request_update)

    def _on_node_event(self, **kwargs):
        if kwargs.get("event_type") == hou.nodeEventType.BeingDeleted:
            self.node = None
        hdefereval.executeDeferred(self._request_update)


def createViewerStateTemplate():
    template = hou.ViewerStateTemplate(
        "geo_page_viewer", "Geo Page Viewer", hou.sopNodeTypeCategory()
    )
    template.bindFactory(SOPPageViewerState)
    template.bindIcon("hicon:/SVGIcons.index?BUTTONS_grid_small.svg")

    menu = hou.ViewerStateMenu("geo_page_menu", "Page Viewer Options")
    menu.addRadioStrip("geo_page_owner", "Owner", "point")
    menu.addRadioStripItem("geo_page_owner", "vertex", "Vertex")
    menu.addRadioStripItem("geo_page_owner", "point", "Point")
    menu.addRadioStripItem("geo_page_owner", "prim", "Primitive")
    menu.addRadioStripItem("geo_page_owner", "detail", "Detail")
    menu.addSeparator()
    menu.addRadioStrip("geo_page_display", "Page Display", "status")
    menu.addRadioStripItem("geo_page_display", "status", "Page Status")
    menu.addRadioStripItem("geo_page_display", "blocks", "Page Blocks")
    menu.addSeparator()
    menu.addToggleItem("geo_page_public", "Public Attributes", True)
    menu.addToggleItem("geo_page_private", "Private Attributes", False)
    menu.addToggleItem("geo_page_groups", "Groups", False)
    template.bindMenu(menu)

    return template
