"""artist — decomposition package behind the artist_identifier FILE facade.

Split out of backend/artist_identifier.py (2026-07). The import/patch surface stays on the facade: production code and
tests import and monkeypatch names on ``artist_identifier``; submodules resolve
every facade-owned seam through their lazy ``_facade()`` helper at call time.
Keep this __init__ free of imports so ``importlib.reload(artist_identifier)``
semantics stay trivial (the facade reload must re-run its config reads;
nothing here may cache them).
"""
