"""Decomposed images-router endpoint groups (split from routers/images.py).

Import through routers.images — the facade owns the ONE shared ``router``,
the DI provider (get/set_image_service), the upload-constant patch seam, and
the model re-exports — and the facade's import sequence IS the route
registration order (the single-segment static GET routes selection-chunk /
repair-candidates / count must register before GET /api/images/{image_id}).
Submodule map (2026-07): models /
listing / selection / repair / counting / detail / export / jobs / item_ops /
serving. This __init__ stays import-free so package load order cannot create
facade<->part cycles.
"""
