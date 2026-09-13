from PySide6 import QtCore

import hou

SELECTION_EVENTS = (hou.nodeEventType.ChildSelectionChanged,)

COOK_EVENTS = (
    hou.nodeEventType.ParmTupleChanged,
    hou.nodeEventType.InputDataChanged,
    hou.nodeEventType.InputRewired,
    hou.nodeEventType.BeingDeleted,
)

_UNSET = object()               # "no node change was deferred while paused" sentinel


class SopNodeTracker(QtCore.QObject):
    nodeChanged = QtCore.Signal()
    pendingNodeChanged = QtCore.Signal()
    refreshNeeded = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.node = None
        self.watched_network = None     # network whose selection we follow while pinned
        self.is_watching_selection = False
        self.is_alive = True            # cleared by teardown(); guards deferred work
        self.is_paused = False          # frozen: ignore selection/cook/frame events
        self.pending_node = _UNSET      # node selected while paused, applied on resume
        self._playbar_cb_added = False
        self._add_playbar_callback()

    def _adopt_node(self, node):
        """Switch the tracked node and move the cook callback. Returns True if changed.

        Does NOT check the pause gate — that belongs to the caller."""
        if node is self.node:
            return False
        self._remove_cook_callback()
        self.node = node if isinstance(node, hou.SopNode) else None
        self.nodeChanged.emit()        # new node -> its output labels, reset to output 0
        if self.node is not None:
            self.node.addEventCallback(COOK_EVENTS, self._on_node_event)
        # Deselection is a property of the NETWORK, not the node, so the watch follows the
        # live node from one network to the next.
        self._watch_network(self.node)
        return True

    def _remove_cook_callback(self):
        if self.node is not None:
            try:
                self.node.removeEventCallback(COOK_EVENTS, self._on_node_event)
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass

    def _adopt_pending_node(self):
        pending, self.pending_node = self.pending_node, _UNSET
        if pending is not _UNSET:
            self._adopt_node(pending)

    def has_pending_node(self):
        return (self.is_paused and self.pending_node is not _UNSET
                and self.pending_node is not self.node)

    def set_node(self, node):
        # The scene talking: gated by Pause. Frozen, remember the latest selection but
        # don't switch or refresh -- Reload or resuming applies it.
        if self.is_paused:
            self.pending_node = node
            self.pendingNodeChanged.emit()
            return
        if self._adopt_node(node):
            self.refreshNeeded.emit()

    def reload(self):
        """Reload: adopts any pending selection, then refreshes. Not pause-gated."""
        self._adopt_pending_node()
        self.pendingNodeChanged.emit()
        self.refreshNeeded.emit()

    def set_paused(self, paused):
        # Pause gates the three event sources (selection, cook, frame). Resuming applies
        # whatever node was selected while paused, then refreshes to catch up.
        self.is_paused = bool(paused)
        if not self.is_paused:
            # _adopt_node rather than set_node: the pause flag is already cleared, and going
            # through the gate again only reads as correct by accident.
            self._adopt_pending_node()
            self.refreshNeeded.emit()

    def set_watching_selection(self, is_watching_selection):
        self.is_watching_selection = is_watching_selection
        if is_watching_selection:
            self._watch_network(self.node)
        else:
            self._unwatch_network()

    def teardown(self):
        self.is_alive = False
        self._remove_playbar_callback()
        self._unwatch_network()
        self._remove_cook_callback()
        self.node = None

    def _on_node_event(self, **kwargs):
        if kwargs.get("event_type") == hou.nodeEventType.BeingDeleted:
            self.node = None        # always drop a deleted node's ref, even while paused
        if self.is_paused:
            return                  # frozen: ignore cooks (parm/input changes)
        self.refreshNeeded.emit()

    # -- frame / time-dependency --------------------------------------------

    def _add_playbar_callback(self):
        # hou.playbar exists only in graphical Houdini.
        try:
            hou.playbar.addEventCallback(self._on_frame_change)
            self._playbar_cb_added = True
        except (AttributeError, hou.Error):
            self._playbar_cb_added = False

    def _remove_playbar_callback(self):
        if not self._playbar_cb_added:
            return
        try:
            hou.playbar.removeEventCallback(self._on_frame_change)
        except (AttributeError, hou.Error):
            pass
        self._playbar_cb_added = False

    def _on_frame_change(self, event_type, frame):
        # for_last_cook=True reads dependency without forcing a cook.
        if event_type != hou.playbarEvent.FrameChanged:
            return
        if not self.is_alive or self.node is None or self.is_paused:
            return
        try:
            if self.node.isTimeDependent(for_last_cook=True):
                self.refreshNeeded.emit()
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass

    # -- deselection ---------------------------------------------------------
    # ChildSelectionChanged on the network catches deselect and re-select, which
    # onNodePathChanged misses; hou.ui.addSelectionCallback would too, but is GUI-only.

    def _watch_network(self, node):
        """Follow selection changes in `node`'s network. None keeps the current watch."""
        if node is None:
            return
        network = node.parent()
        # `==`, not `is` — hou.Node creates a new wrapper on every call.
        if network == self.watched_network:
            return
        self._unwatch_network()
        if not self.is_watching_selection:
            return
        try:
            network.addEventCallback(SELECTION_EVENTS, self._on_network_selection)
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            return
        self.watched_network = network

    def _unwatch_network(self):
        if self.watched_network is None:
            return
        try:
            self.watched_network.removeEventCallback(SELECTION_EVENTS,
                                                     self._on_network_selection)
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass
        self.watched_network = None

    def _on_network_selection(self, **kwargs):
        """Handle deselect and re-select (onNodePathChanged fires for neither)."""
        if not self.is_alive or not self.is_watching_selection:
            return
        selected = hou.selectedNodes()
        if not selected:
            self.set_node(None)
            return
        for node in reversed(selected):
            if isinstance(node, hou.SopNode):
                self.set_node(node)
                return
