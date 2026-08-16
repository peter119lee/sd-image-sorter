"""Tests for the v4.0 Aurora entry-page stats (migration 020 + service + API).

Covers the activity_log migration, record_activity upsert semantics and its
never-raise contract, streak/today aggregation, the added-today / unviewed
watermark counts, the deterministic daily ★5 hero pick with seed re-roll, and
the GET /api/entry/summary endpoint.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import entry_stats_service  # noqa: E402


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _seed_activity(db, day: str, kind: str, count: int) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO activity_log (day, kind, count) VALUES (?, ?, ?) "
        "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count",
        (day, kind, count),
    )
    conn.commit()


def _add_image(db, path: str, filename: str):
    return db.add_image(
        path=path, filename=filename, generator="comfyui", metadata_json="{}",
    )


def _mark_unreadable(db, image_id: int) -> None:
    """Flag a row the way the gallery does when the file no longer opens."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE images SET is_readable = 0, read_error = 'missing' WHERE id = ?",
        (image_id,),
    )
    conn.commit()


class TestMigration020:
    def test_activity_log_table_exists(self, test_db):
        conn = test_db.get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_log'"
        ).fetchone()
        assert row is not None


class TestRecordActivity:
    def test_upsert_accumulates(self, test_db):
        entry_stats_service.record_activity("added", 3)
        entry_stats_service.record_activity("added", 2)
        conn = test_db.get_connection()
        count = conn.execute(
            "SELECT count FROM activity_log WHERE day = ? AND kind = 'added'",
            (_day(0),),
        ).fetchone()[0]
        assert count == 5

    def test_zero_and_negative_are_ignored(self, test_db):
        entry_stats_service.record_activity("added", 0)
        entry_stats_service.record_activity("added", -4)
        conn = test_db.get_connection()
        rows = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        assert rows == 0

    def test_never_raises_without_table(self, test_db):
        conn = test_db.get_connection()
        conn.execute("DROP TABLE activity_log")
        conn.commit()
        entry_stats_service.record_activity("added", 1)  # must not raise


class TestStreakAndToday:
    def test_empty_log_means_zero(self, test_db):
        summary = entry_stats_service.get_entry_summary()
        assert summary["streak_days"] == 0
        assert summary["today_touched"] == 0

    def test_today_only(self, test_db):
        _seed_activity(test_db, _day(0), "tagged", 42)
        summary = entry_stats_service.get_entry_summary()
        assert summary["streak_days"] == 1
        assert summary["today_touched"] == 42

    def test_streak_ending_yesterday_still_counts(self, test_db):
        _seed_activity(test_db, _day(-1), "moved", 5)
        _seed_activity(test_db, _day(-2), "moved", 5)
        summary = entry_stats_service.get_entry_summary()
        assert summary["streak_days"] == 2
        assert summary["today_touched"] == 0

    def test_gap_breaks_streak(self, test_db):
        _seed_activity(test_db, _day(0), "added", 1)
        _seed_activity(test_db, _day(-3), "added", 9)
        summary = entry_stats_service.get_entry_summary()
        assert summary["streak_days"] == 1

    def test_multi_kind_days_sum(self, test_db):
        _seed_activity(test_db, _day(0), "added", 2)
        _seed_activity(test_db, _day(0), "rated", 3)
        summary = entry_stats_service.get_entry_summary()
        assert summary["today_touched"] == 5


class TestLibraryCounts:
    def test_totals_and_added_today(self, test_db):
        _add_image(test_db, "/lib/a.png", "a.png")
        old = _add_image(test_db, "/lib/b.png", "b.png")
        conn = test_db.get_connection()
        conn.execute(
            "UPDATE images SET indexed_at = '2000-01-01 00:00:00' WHERE id = ?",
            (old,),
        )
        conn.commit()
        summary = entry_stats_service.get_entry_summary()
        assert summary["library_total"] == 2
        assert summary["added_today"] == 1

    def test_unviewed_uses_watermark(self, test_db):
        _add_image(test_db, "/lib/seen.png", "seen.png")
        watermark = entry_stats_service.get_entry_summary()["server_now"]
        fresh = _add_image(test_db, "/lib/new.png", "new.png")
        conn = test_db.get_connection()
        conn.execute(
            "UPDATE images SET indexed_at = '2099-01-01 00:00:00' WHERE id = ?",
            (fresh,),
        )
        conn.commit()
        summary = entry_stats_service.get_entry_summary(last_seen=watermark)
        assert summary["unviewed"] == 1

    def test_malformed_watermark_is_not_an_error(self, test_db):
        summary = entry_stats_service.get_entry_summary(last_seen="not-a-date")
        assert summary["unviewed"] == 0


class TestHeroPick:
    def test_no_five_star_means_no_hero(self, test_db):
        _add_image(test_db, "/lib/plain.png", "plain.png")
        assert entry_stats_service.get_entry_summary()["hero"] is None

    def test_daily_pick_is_deterministic(self, test_db):
        for n in range(3):
            iid = _add_image(test_db, f"/lib/h{n}.png", f"h{n}.png")
            test_db.set_user_rating(iid, 5)
        first = entry_stats_service.get_entry_summary()["hero"]
        second = entry_stats_service.get_entry_summary()["hero"]
        assert first == second
        assert first["pool"] == 3

    def test_seed_rerolls_within_pool(self, test_db):
        ids = []
        for n in range(3):
            iid = _add_image(test_db, f"/lib/r{n}.png", f"r{n}.png")
            test_db.set_user_rating(iid, 5)
            ids.append(iid)
        picks = {
            entry_stats_service.get_entry_summary(hero_seed=seed)["hero"]["id"]
            for seed in range(3)
        }
        assert picks == set(ids)

    def test_unreadable_five_star_is_never_picked(self, test_db):
        gone = _add_image(test_db, "/lib/gone.png", "gone.png")
        test_db.set_user_rating(gone, 5)
        _mark_unreadable(test_db, gone)
        kept = _add_image(test_db, "/lib/kept.png", "kept.png")
        test_db.set_user_rating(kept, 5)

        for seed in range(4):
            hero = entry_stats_service.get_entry_summary(hero_seed=seed)["hero"]
            assert hero["id"] == kept
            assert hero["pool"] == 1

    def test_all_five_stars_unreadable_means_no_hero(self, test_db):
        gone = _add_image(test_db, "/lib/gone.png", "gone.png")
        test_db.set_user_rating(gone, 5)
        _mark_unreadable(test_db, gone)
        assert entry_stats_service.get_entry_summary()["hero"] is None


class TestEntrySummaryEndpoint:
    def test_endpoint_shape(self, test_client):
        response = test_client.get("/api/entry/summary")
        assert response.status_code == 200
        body = response.json()
        for key in (
            "library_total", "added_today", "unviewed",
            "streak_days", "today_touched", "hero", "server_now",
        ):
            assert key in body

    def test_endpoint_rejects_bad_seed(self, test_client):
        response = test_client.get("/api/entry/summary?hero_seed=-2")
        # The app's global validation handler maps FastAPI 422s to 400.
        assert response.status_code == 400


class TestHeroPool:
    def test_starred_lead_then_newest(self, test_db):
        plain_ids = []
        for n in range(4):
            plain_ids.append(_add_image(test_db, f"/lib/p{n}.png", f"p{n}.png"))
        starred_id = _add_image(test_db, "/lib/star.png", "star.png")
        test_db.set_user_rating(starred_id, 5)

        pool = entry_stats_service.get_hero_pool(limit=3)

        assert pool["ids"][0] == starred_id
        assert pool["starred"] == 1
        assert pool["total"] == 3
        # Non-starred fill is newest-first.
        assert pool["ids"][1:] == sorted(plain_ids, reverse=True)[:2]

    def test_empty_library(self, test_db):
        pool = entry_stats_service.get_hero_pool()
        assert pool == {"ids": [], "starred": 0, "total": 0}

    def test_unreadable_rows_are_excluded(self, test_db):
        """A library whose newest rows are all missing files must still
        surface the older readable ones, otherwise the entry-page cover
        renders as a black void."""
        readable = [
            _add_image(test_db, f"/lib/ok{n}.png", f"ok{n}.png") for n in range(3)
        ]
        for n in range(5):
            dead = _add_image(test_db, f"/lib/dead{n}.png", f"dead{n}.png")
            _mark_unreadable(test_db, dead)

        pool = entry_stats_service.get_hero_pool(limit=10)

        assert pool["ids"] == sorted(readable, reverse=True)
        assert pool["total"] == 3

    def test_unreadable_starred_row_is_not_counted(self, test_db):
        gone = _add_image(test_db, "/lib/gone.png", "gone.png")
        test_db.set_user_rating(gone, 5)
        _mark_unreadable(test_db, gone)
        alive = _add_image(test_db, "/lib/alive.png", "alive.png")

        pool = entry_stats_service.get_hero_pool(limit=10)

        assert pool["ids"] == [alive]
        assert pool["starred"] == 0

    def test_endpoint_shape_and_limit_validation(self, test_client):
        response = test_client.get("/api/entry/hero-pool?limit=5")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"ids", "starred", "total"}

        # Global validation handler maps FastAPI 422s to 400.
        assert test_client.get("/api/entry/hero-pool?limit=0").status_code == 400
        assert test_client.get("/api/entry/hero-pool?limit=999").status_code == 400
