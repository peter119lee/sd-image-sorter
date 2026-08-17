"""Decomposed tag-export helper modules (split from services/tag_export_service.py).

Import through services.tag_export_service — the compatibility facade and the
single monkeypatch surface (tag_export_service.db.<fn> /
tag_export_service.count_selection_token_ids) — for existing code.
Submodule map (2026-07): selection /
captions / sidecars / preview. This __init__ stays import-free so package
load order cannot create facade<->submodule cycles.
"""
