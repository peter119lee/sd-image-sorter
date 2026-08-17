"""Decomposed dataset-session service modules (split from services/dataset_session_service.py).

Import through services.dataset_session_service — the compatibility facade,
the home of the REBIND dir-global pairs (_SCAN_DIR/_get_scan_dir and
_UPLOAD_DIR/_get_upload_dir), and the single monkeypatch surface — for
existing code. Submodule map (2026-07):
ids_and_items / manifest_store / allowlist / scan / upload. This __init__
stays import-free so package load order cannot create facade<->submodule
cycles.
"""
