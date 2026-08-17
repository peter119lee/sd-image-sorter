"""Decomposed dataset-export service modules (split from services/dataset_export_service.py).

Import through services.dataset_export_service — the compatibility facade, the
job-registry home, and the single monkeypatch surface — for existing code.
Submodule map (2026-07): _constants /
models / planning / captions / artifacts / engine. This __init__ stays
import-free so package load order cannot create facade<->submodule cycles.
"""
