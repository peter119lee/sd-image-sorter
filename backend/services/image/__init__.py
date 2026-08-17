"""Decomposed ImageService method mixins (split from services/image_service.py).

Import through services.image_service — the compatibility facade and the
single monkeypatch surface — for existing code. Submodule map (2026-07): _constants / _filters / reconnect /
repair / gallery / jobs_delete / jobs_remove / selection / serving. This
__init__ stays import-free so package load order cannot create
facade<->mixin cycles.
"""
