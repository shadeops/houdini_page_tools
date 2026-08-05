"""SOP Memory Python Panel.

``panel`` holds the Qt widgets, ``model`` the report-to-rows translation, and ``diff``
joins two of those trees into one of differences (no Qt either). The report
itself comes from the ``_page_tools`` HDK extension via ``page_tools``, which stays
outside this package because the viewer state uses it too.

The panel class is reached as ``panel.SopMemoryPanel`` rather than re-exported here:
``python_panels/page_tools.pypanel`` runs ``toolutils.safe_reload`` on these modules, and
a name bound at import time would still point at the pre-reload class.
"""

__all__ = ["diff", "model", "panel"]
