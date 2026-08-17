"""Migration 044: record what *format* a sidecar caption is in.

``sidecar_caption`` (migration 042) holds text a human or another tool wrote in
a ``.txt``/``.json`` beside the image. That text arrives in two genuinely
different formats: Danbooru-style comma-separated tag lists, and
natural-language prose. Downstream features have to tell them apart — Dataset
Maker picks a caption template, and prompt conversion for a
natural-language-first target model must not hand it a booru tag dump.

Owner decision: do NOT add another text column for this. The existing text
columns are split by **provenance** (``prompt`` = the generator's own,
``ai_caption`` = this app's tagger, ``nl_caption`` = this app's VLM,
``sidecar_caption`` = someone else's file), which is one clean axis.
Tags-versus-prose is a *different* axis — format — and format, unlike
provenance, can be derived from the text. So it is recorded as one small marker
on the existing column: ``tags`` / ``natural`` / ``mixed`` / ``unknown``
(``caption_format.CAPTION_FORMATS``). NULL means "no sidecar text, or not yet
classified"; ``'unknown'`` means "there is text and the classifier would not
guess". The marker only ever decides how text is presented or converted — no
code path may use it to discard, truncate or refuse text.

The column is deliberately additive: this migration only runs
``ALTER TABLE ... ADD COLUMN`` when the column is absent and never reads,
rewrites or clears an existing row, so the first run against a populated
production database leaves every stored value untouched and simply makes the new
column read NULL everywhere. Existing rows are classified on their next scan (a
sidecar edit is already noticed by the ``sidecar_fingerprint`` gate from
migration 043) or when the user runs the settings-page recovery job. There is
deliberately no backfill: rewriting rows in the user's real library needs their
authority, and nothing is lost by waiting.

No index: the marker is only ever read back with the row it belongs to, never
searched, grouped or ordered by. Search must always match the caption *text*,
never this marker.
"""
from __future__ import annotations

import sqlite3

from migrations._schema_common import table_exists


VERSION = 44
NAME = "sidecar_caption_format_column"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def apply(conn: sqlite3.Connection) -> None:
    """Add the sidecar_caption_format column to images (idempotent, additive)."""
    if not table_exists(conn, "images"):
        return
    if _column_exists(conn, "images", "sidecar_caption_format"):
        return
    conn.execute("ALTER TABLE images ADD COLUMN sidecar_caption_format TEXT")
