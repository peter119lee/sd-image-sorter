"""Decomposed CensorService method mixins (split from services/censor_service.py).

Import through services.censor_service — the compatibility facade and the
single monkeypatch surface — for existing code. Submodule map (2026-07): mask_cache / detection / output_io /
edit_ops / edit_mask_geometry / sam3_ops. This __init__ stays import-free so package load order
cannot create facade<->mixin cycles.
"""
