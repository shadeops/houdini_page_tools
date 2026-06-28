import hou

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

    def _update_geo(self):
        if not self.node:
            return
        new_geo = hou.Geometry()
        report_geo = hou.Geometry()
        if self.node.needsToCook():
            return
            #self.node.cook(force=True)
        report = shadeops_hom.geo_page_report(self.node.path(), "point", True)
        page_tools.page_report_to_attribs(report_geo, report, False, True, True)
        self.invokegraph.execute(new_geo, [self.graph_geo, report_geo, self.opts_geo])
        report_geo.clear()
        self.geo.setGeometry(new_geo)
        self.geo.show(True)

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

    def _on_selection(self, selection):
        sops = [i for i in selection if isinstance(i, hou.SopNode)]
        current_sop = sops[-1] if sops else None
        self._set_node(current_sop)

    def _set_node(self, node):
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
            self._update_geo()
        else:
            self.geo.show(False)
        self.scene_viewer.curViewport().draw()

    def _on_node_event(self, **kwargs):
        if kwargs.get("event_type") == hou.nodeEventType.BeingDeleted:
            self.node = None
        self._update_geo()
        self.scene_viewer.curViewport().draw()


def createViewerStateTemplate():
    template = hou.ViewerStateTemplate(
        "geo_page_viewer", "Geo Page Viewer", hou.sopNodeTypeCategory()
    )
    template.bindFactory(SOPPageViewerState)
    template.bindIcon("hicon:/SVGIcons.index?BUTTONS_grid_small.svg")
    return template
