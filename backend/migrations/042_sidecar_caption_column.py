"""Migration 042: give sidecar-derived caption text its own images column.

``.txt`` / ``.json`` sidecars sitting next to an image almost always hold a
Danbooru-style tag list written by a human or a tagger, not the SD generation
prompt that produced the image. That text used to be stored in
``images.prompt``, which made it searchable but destroyed the meaning of the
field — Prompt Lab statistics were silently computed over other people's tags
and the user could no longer tell which rows record what actually generated
the image.

``sidecar_caption`` holds it instead. The column is deliberately additive:
this migration only runs ``ALTER TABLE ... ADD COLUMN`` when the column is
absent and never reads, rewrites or clears an existing row, so the first run
against a populated production database leaves every stored value untouched
and simply makes the new column read NULL everywhere. Existing rows are
repopulated on demand by the settings-page recovery job, never automatically.

No index: the column is only ever matched with ``LIKE '%term%'`` by the
gallery search, which a b-tree index cannot serve.
"""
from __future__ import annotations

import sqlite3

from migrations._schema_common import table_exists


VERSION = 42
NAME = "sidecar_caption_column"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def apply(conn: sqlite3.Connection) -> None:
    """Add the sidecar_caption column to images (idempotent, additive)."""
    if not table_exists(conn, "images"):
        return
    if _column_exists(conn, "images", "sidecar_caption"):
        return
    conn.execute("ALTER TABLE images ADD COLUMN sidecar_caption TEXT")
