"""Entry-page stats (v4.0 Aurora — backend API delta #4).

Aggregates the numbers the entry page (#11a) renders:
- identity block: streak of consecutive active days + images touched today,
- function mosaic: library total, added-today, not-yet-seen counts,
- daily hero: a deterministic ★5 pick that changes once per local day and
  can be re-rolled with a seed offset (换一张).

``record_activity`` is called from the write paths that touch images (scan,
tagging, move, censor save, rating). It must NEVER break the host operation:
every failure is swallowed and logged.
"""
from __future__ import annotations

import logging
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import database as db


logger = logging.getLogger("sd-image-sorter")

# Kinds currently recorded. Open-ended by design; unknown kinds still count
# toward "today touched" and the streak.
KIND_ADDED = "added"
KIND_TAGGED = "tagged"
KIND_MOVED = "moved"
KIND_CENSORED = "censored"
KIND_RATED = "rated"


def _local_day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def record_activity(kind: str, count: int = 1) -> None:
    """UPSERT a daily counter. Never raises (activity logging is best-effort)."""
    if count <= 0:
        return
    try:
        conn = db.get_connection()
        conn.execute(
            """
            INSERT INTO activity_log (day, kind, count) VALUES (?, ?, ?)
            ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count
            """,
            (_local_day(), str(kind), int(count)),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — must never break the host op
        logger.debug("activity_log write skipped (%s x%s): %s", kind, count, exc)


def _streak_and_today(conn) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT day, SUM(count) FROM activity_log GROUP BY day"
    ).fetchall()
    per_day = {str(row[0]): int(row[1] or 0) for row in rows}

    today = datetime.now()
    today_key = _local_day(today)
    today_touched = per_day.get(today_key, 0)

    # Streak of consecutive active days ending today (or yesterday, so the
    # streak doesn't read as 0 before the user has done anything today).
    cursor = today if today_touched > 0 else today - timedelta(days=1)
    streak = 0
    while per_day.get(_local_day(cursor), 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)
    return {"streak_days": streak, "today_touched": today_touched}


def _utc_sqlite_text(moment: datetime) -> str:
    """Format a local datetime the way SQLite CURRENT_TIMESTAMP stores (UTC text)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _pick_hero(conn, hero_seed: int) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, filename FROM images "
        "WHERE user_rating = 5 AND COALESCE(is_readable, 1) = 1 "
        "ORDER BY id"
    ).fetchall()
    if not rows:
        return None
    day_hash = zlib.crc32(_local_day().encode("utf-8"))
    index = (day_hash + int(hero_seed)) % len(rows)
    row = rows[index]
    return {"id": int(row[0]), "filename": str(row[1] or ""), "pool": len(rows)}


def get_hero_pool(limit: int = 60) -> Dict[str, Any]:
    """Image ids for the entry page's slideshow / film-strip display modes.

    ★5-rated images lead (the same pool the daily hero draws from); the rest
    fills with the newest library images so a fresh install with zero ratings
    still gets a living wall. Ids only — the client renders thumbnails.

    Rows whose file no longer opens are excluded: the client turns each id
    straight into a background image, so a dead id renders as a black void.
    """
    conn = db.get_connection()
    capped = max(1, min(int(limit or 60), 200))
    rows = conn.execute(
        """
        SELECT id, (user_rating = 5) AS starred
        FROM images
        WHERE COALESCE(is_readable, 1) = 1
        ORDER BY (user_rating = 5) DESC, indexed_at DESC, id DESC
        LIMIT ?
        """,
        (capped,),
    ).fetchall()
    ids = [int(row[0]) for row in rows]
    starred = sum(1 for row in rows if row[1])
    return {"ids": ids, "starred": starred, "total": len(ids)}


def get_entry_summary(
    last_seen: Optional[str] = None,
    hero_seed: int = 0,
) -> Dict[str, Any]:
    """Aggregate everything the entry page needs in one call."""
    conn = db.get_connection()

    library_total = int(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0])

    local_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    added_today = int(
        conn.execute(
            "SELECT COUNT(*) FROM images WHERE indexed_at >= ?",
            (_utc_sqlite_text(local_midnight),),
        ).fetchone()[0]
    )

    # "还没看过" = indexed after the client's last gallery visit. The client
    # stores the ``server_now`` watermark we hand back, so the comparison uses
    # one clock (the DB's) end to end.
    unviewed = 0
    if last_seen:
        try:
            unviewed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM images WHERE indexed_at > ?",
                    (str(last_seen)[:19],),
                ).fetchone()[0]
            )
        except Exception:  # noqa: BLE001 — malformed watermark is not an error
            unviewed = 0

    summary: Dict[str, Any] = {
        "library_total": library_total,
        "added_today": added_today,
        "unviewed": unviewed,
        "hero": _pick_hero(conn, hero_seed),
        "server_now": str(
            conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        ),
    }
    summary.update(_streak_and_today(conn))
    return summary
