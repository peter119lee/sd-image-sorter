"""Clearing records for images whose files are gone.

The gallery hides unreadable rows and shows a banner counting them, but the only
action it offered was "Find Moved Files" — which relinks files that *moved*. For
a file that was genuinely deleted that finds nothing, and nothing anywhere let a
user see, select or clear those rows (``include_unreadable=True`` existed in the
data layer and no router ever passed it). So the banner was permanently
unresolvable: hide it, and it returns next session.

Clearing is the missing action, and it is also the one irreversible one, so the
rules it has to obey are:

1. Never touch files. Only rows.
2. Never offer clearing for a location we cannot currently see. An unplugged
   external drive makes every row on it "unreadable" while the files are fine;
   clearing those would destroy tags and ratings for images that still exist.
3. State the cost before asking. Whether user work is attached is a fact in the
   database, not something the UI should guess at or the user should assume.
"""

from __future__ import annotations

import os

import database as db
from services.image_service import ImageService


def _svc() -> ImageService:
    return ImageService()


def _mark_missing(image_id: int, reason: str = "file not found") -> None:
    db.mark_image_unreadable(image_id, reason)


class TestWhatTheSummaryReports:
    def test_a_readable_library_reports_nothing_to_clean(self, test_db):
        db.add_image(path=r"L:\lib\fine.png", filename="fine.png")

        summary = _svc().summarize_missing_images()

        assert summary["total"] == 0
        assert summary["groups"] == []
        assert summary["clearable_total"] == 0
        assert summary["blocked_total"] == 0

    def test_rows_are_grouped_by_the_folder_they_claim_to_live_in(self, test_db, tmp_path):
        gone = tmp_path / "deleted-dataset"
        first = db.add_image(path=str(gone / "a.png"), filename="a.png")
        second = db.add_image(path=str(gone / "b.png"), filename="b.png")
        other = tmp_path / "other-dataset"
        third = db.add_image(path=str(other / "c.png"), filename="c.png")
        for image_id in (first, second, third):
            _mark_missing(image_id)

        summary = _svc().summarize_missing_images()

        assert summary["total"] == 3
        by_location = {group["location"]: group for group in summary["groups"]}
        assert by_location[str(gone)]["count"] == 2
        assert by_location[str(other)]["count"] == 1

    def test_a_folder_we_can_still_see_is_reported_as_the_file_being_gone(
        self, test_db, tmp_path
    ):
        """The folder is readable and still in use, and the file is not in it.

        The sibling file matters: an empty folder is indistinguishable from an
        unmounted mount point, so it is treated as unreachable instead.
        """
        visible = tmp_path / "still-here"
        visible.mkdir()
        (visible / "sibling.png").write_bytes(b"a file that is still here")
        image_id = db.add_image(path=str(visible / "deleted.png"), filename="deleted.png")
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["reason"] == "file_deleted"
        assert group["clearable"] is True

    def test_a_missing_folder_on_a_reachable_disk_is_reported_as_the_folder_being_gone(
        self, test_db, tmp_path
    ):
        """tmp_path exists, so the volume is mounted and the subfolder really was removed."""
        image_id = db.add_image(
            path=str(tmp_path / "removed-folder" / "x.png"), filename="x.png"
        )
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["reason"] == "folder_deleted"
        assert group["clearable"] is True


class TestTheUnpluggedDriveGuard:
    """The case that makes a one-click "clear all" a data-loss bug."""

    def test_a_location_with_no_visible_ancestor_is_never_offered_for_clearing(
        self, test_db
    ):
        image_id = db.add_image(
            path=r"Q:\external-backup\photos\keep.png", filename="keep.png"
        )
        _mark_missing(image_id)

        summary = _svc().summarize_missing_images()
        group = summary["groups"][0]

        assert group["reason"] == "location_unreachable"
        assert group["clearable"] is False
        assert summary["blocked_total"] == 1
        assert summary["clearable_total"] == 0

    def test_a_folder_we_can_see_but_not_read_is_not_offered_for_clearing(
        self, test_db, tmp_path, monkeypatch
    ):
        """Permission-denied is not absence. The files are there; we just cannot look.

        Walking up would find a readable ancestor and call it `folder_deleted`,
        which would offer to clear rows for files that exist.
        """
        locked = tmp_path / "locked"
        locked.mkdir()
        image_id = db.add_image(path=str(locked / "a.png"), filename="a.png")
        _mark_missing(image_id)

        real_access = os.access

        def deny_locked(path, mode, **kwargs):
            if str(path) == str(locked):
                return False
            return real_access(path, mode, **kwargs)

        monkeypatch.setattr(os, "access", deny_locked)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["reason"] == "location_unreachable"
        assert group["clearable"] is False

    def test_an_empty_folder_is_not_offered_for_clearing(self, test_db, tmp_path):
        """An empty directory cannot be told apart from an unmounted mount point.

        On Linux an unmounted `/mnt/usb` is exactly this: a directory that
        exists, is readable, and holds nothing. Refusing here costs the user a
        banner they can resolve by reconnecting and re-scanning; clearing here
        costs them tags and ratings for files that were never gone.
        """
        empty = tmp_path / "empty-mountpoint"
        empty.mkdir()
        image_id = db.add_image(path=str(empty / "a.png"), filename="a.png")
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["reason"] == "location_unreachable"
        assert group["clearable"] is False

    def test_a_folder_still_holding_other_files_is_clearable(self, test_db, tmp_path):
        """The counterpart: a live folder proves the volume is really mounted."""
        folder = tmp_path / "live-folder"
        folder.mkdir()
        (folder / "sibling.png").write_bytes(b"still here")
        image_id = db.add_image(path=str(folder / "gone.png"), filename="gone.png")
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["reason"] == "file_deleted"
        assert group["clearable"] is True

    def test_a_row_with_no_usable_path_is_never_offered_for_clearing(self, test_db):
        """A blank path resolves to the working directory, which is not its home."""
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO images (path, filename, is_readable) VALUES ('', 'orphan.png', 0)"
            )

        summary = _svc().summarize_missing_images()

        assert summary["total"] == 1
        assert summary["clearable_total"] == 0
        assert summary["groups"][0]["reason"] == "location_unreachable"

    def test_clearing_an_unreachable_location_is_refused_and_removes_nothing(
        self, test_db
    ):
        image_id = db.add_image(
            path=r"Q:\external-backup\photos\keep.png", filename="keep.png"
        )
        _mark_missing(image_id)

        result = _svc().clear_missing_images(location=r"Q:\external-backup\photos")

        assert result["removed"] == 0
        assert result["status"] == "refused"
        assert result["reason"] == "location_unreachable"
        assert db.get_image_by_id(image_id) is not None

    def test_clearing_everything_skips_unreachable_locations_instead_of_failing(
        self, test_db, tmp_path
    ):
        """A mixed library must still be cleanable, without touching the offline part."""
        reachable = db.add_image(
            path=str(tmp_path / "gone" / "a.png"), filename="a.png"
        )
        offline = db.add_image(
            path=r"Q:\external\b.png", filename="b.png"
        )
        _mark_missing(reachable)
        _mark_missing(offline)

        result = _svc().clear_missing_images(location=None)

        assert result["removed"] == 1
        assert result["skipped_unreachable"] == 1
        assert db.get_image_by_id(reachable) is None
        assert db.get_image_by_id(offline) is not None


class TestTheCostIsMeasuredNotAssumed:
    def test_a_group_with_no_user_work_says_so(self, test_db, tmp_path):
        image_id = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["with_tags"] == 0
        assert group["with_rating"] == 0
        assert group["in_collection"] == 0
        assert group["user_work_total"] == 0

    def test_tags_on_a_missing_row_are_counted_as_work_that_would_be_lost(
        self, test_db, tmp_path
    ):
        """Tags survive a file going missing, so clearing the row is what loses them.

        This only became true once ``mark_image_unreadable`` stopped deleting
        tags (see test_unreadable_preserves_user_work.py). Before that the count
        was structurally always zero and this reading was worthless.
        """
        image_id = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])
        _mark_missing(image_id)

        summary = _svc().summarize_missing_images()

        assert summary["groups"][0]["with_tags"] == 1
        assert summary["groups"][0]["user_work_total"] == 1
        assert summary["user_work_total"] == 1

    def test_a_star_rating_counts_as_work_that_would_be_lost(self, test_db, tmp_path):
        image_id = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        db.set_user_rating(image_id, 5)
        _mark_missing(image_id)

        group = _svc().summarize_missing_images()["groups"][0]

        assert group["with_rating"] == 1
        assert group["user_work_total"] == 1

    def test_the_cost_of_clearing_excludes_work_on_locations_that_cannot_be_cleared(
        self, test_db, tmp_path
    ):
        """Warning about work that the action will not touch is a false warning.

        The blocked rows keep their tags precisely because they are not cleared,
        so quoting the library-wide total in a confirmation frightens the user
        about a loss that cannot happen — and in the reverse case would hide a
        real one.
        """
        safe = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        _mark_missing(safe)

        offline = db.add_image(path=r"Q:\external\tagged.png", filename="tagged.png")
        db.add_tags(offline, [{"tag": "1girl", "confidence": 0.9}])
        db.set_user_rating(offline, 5)
        _mark_missing(offline)

        summary = _svc().summarize_missing_images()

        # The library-wide figure still reports everything affected.
        assert summary["user_work_total"] == 1
        # What clearing would actually cost is zero: the tagged row is blocked.
        assert summary["clearable_user_work_total"] == 0

    def test_the_cost_of_clearing_counts_work_inside_clearable_locations(
        self, test_db, tmp_path
    ):
        safe = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        db.add_tags(safe, [{"tag": "1girl", "confidence": 0.9}])
        _mark_missing(safe)

        summary = _svc().summarize_missing_images()

        assert summary["user_work_total"] == 1
        assert summary["clearable_user_work_total"] == 1


class TestClearingIsScopedAndKeepsFiles:
    def test_clearing_one_location_leaves_the_other_locations_alone(
        self, test_db, tmp_path
    ):
        target = tmp_path / "clear-me"
        keep = tmp_path / "keep-me"
        doomed = db.add_image(path=str(target / "a.png"), filename="a.png")
        spared = db.add_image(path=str(keep / "b.png"), filename="b.png")
        _mark_missing(doomed)
        _mark_missing(spared)

        result = _svc().clear_missing_images(location=str(target))

        assert result["removed"] == 1
        assert db.get_image_by_id(doomed) is None
        assert db.get_image_by_id(spared) is not None

    def test_clearing_never_removes_a_readable_row_in_the_same_folder(
        self, test_db, tmp_path
    ):
        """A folder can hold both a live image and a record for a deleted one."""
        folder = tmp_path / "mixed"
        folder.mkdir()
        (folder / "live.png").write_bytes(b"a real file on disk")
        live = db.add_image(path=str(folder / "live.png"), filename="live.png")
        dead = db.add_image(path=str(folder / "dead.png"), filename="dead.png")
        _mark_missing(dead)

        result = _svc().clear_missing_images(location=str(folder))

        assert result["removed"] == 1
        assert db.get_image_by_id(dead) is None
        assert db.get_image_by_id(live) is not None

    def test_clearing_does_not_delete_any_file_from_disk(self, test_db, tmp_path):
        """The record is stale, but if a file is there it must survive untouched."""
        folder = tmp_path / "mixed"
        folder.mkdir()
        bystander = folder / "bystander.png"
        bystander.write_bytes(b"not an image, but a real file")
        db.add_image(path=str(bystander), filename="bystander.png")
        dead = db.add_image(path=str(folder / "dead.png"), filename="dead.png")
        _mark_missing(dead)

        _svc().clear_missing_images(location=str(folder))

        assert bystander.exists()
        assert bystander.read_bytes() == b"not an image, but a real file"

    def test_clearing_an_unknown_location_removes_nothing(self, test_db, tmp_path):
        image_id = db.add_image(path=str(tmp_path / "gone" / "a.png"), filename="a.png")
        _mark_missing(image_id)

        result = _svc().clear_missing_images(location=str(tmp_path / "never-indexed"))

        assert result["removed"] == 0
        assert result["status"] == "nothing_to_clear"
        assert db.get_image_by_id(image_id) is not None


class TestOverHttp:
    """The frontend reaches this over HTTP, so the wire contract is tested too."""

    def test_the_summary_endpoint_is_not_shadowed_by_the_image_id_route(
        self, test_client
    ):
        """`/api/images/missing-summary` must not be parsed as an image id."""
        response = test_client.get("/api/images/missing-summary")

        assert response.status_code == 200
        body = response.json()
        for key in (
            "total",
            "clearable_total",
            "blocked_total",
            "user_work_total",
            "groups",
        ):
            assert key in body

    def test_clearing_reports_what_it_removed(self, test_client, tmp_path):
        image_id = test_client.test_db.add_image(
            path=str(tmp_path / "gone" / "a.png"), filename="a.png"
        )
        test_client.test_db.mark_image_unreadable(image_id, "File not found")

        response = test_client.post(
            "/api/images/missing/clear", json={"location": str(tmp_path / "gone")}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cleared"
        assert body["removed"] == 1
        assert body["permanent_delete"] is False

    def test_clearing_an_offline_location_is_refused_over_http_too(
        self, test_client
    ):
        image_id = test_client.test_db.add_image(
            path=r"Q:\offline\a.png", filename="a.png"
        )
        test_client.test_db.mark_image_unreadable(image_id, "File not found")

        response = test_client.post(
            "/api/images/missing/clear", json={"location": r"Q:\offline"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "refused"
        assert body["removed"] == 0
        assert test_client.test_db.get_image_by_id(image_id) is not None
