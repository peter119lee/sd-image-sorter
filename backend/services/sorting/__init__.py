"""Decomposed SortingService method mixins (split from services/sorting_service.py).

Import through services.sorting_service — the compatibility facade and the
single monkeypatch surface — for existing code. Submodule map (2026-07): state / scan / move / batch_move /
session_state / session / workbench / library. This __init__ stays
import-free so package load order cannot create facade<->mixin cycles.
"""
