"""A file we cannot reach right now is not a file that changed.

``mark_image_unreadable`` routed through ``_clear_image_derived_state``, whose
job is to drop data that goes stale *when the source image changes* — a new
fingerprint really does invalidate tags, captions and embeddings.

But it was also called on the "file not found" path, and that is a different
fact. Nothing about the image changed; we simply cannot see it. An unplugged
external drive, a NAS that is asleep, or a folder renamed for five minutes all
produce it. And ``ImageService._filter_and_mark_missing_images`` calls it while
merely *listing the gallery*, so browsing was enough to trigger it.

The consequence was silent and permanent: every tag on those rows was deleted
with no prompt and no notice, and "Find Moved Files" restores the row but cannot
restore the tags. Hours of tagging could vanish because a USB drive was not
plugged in when the gallery loaded.

Content-derived caches (fingerprint, embedding, aesthetic score) are still
cleared, because those must be recomputed from pixels we can no longer read.
What the user typed or chose is kept.
"""

from __future__ import annotations

import database as db
from services.image_service import ImageService


def _tag_names(image_id: int) -> list[str]:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT tag FROM tags WHERE image_id = ? ORDER BY tag", (image_id,)
        ).fetchall()
    return [row["tag"] for row in rows]


class TestMarkingAnImageUnreadableKeepsWhatTheUserMade:
    def test_tags_survive_a_file_going_missing(self, test_db):
        image_id = db.add_image(path=r"L:\lib\tagged.png", filename="tagged.png")
        db.add_tags(
            image_id,
            [
                {"tag": "1girl", "confidence": 0.98},
                {"tag": "silver_hair", "confidence": 0.91},
            ],
        )
        assert _tag_names(image_id) == ["1girl", "silver_hair"]

        db.mark_image_unreadable(image_id, "File not found")

        assert _tag_names(image_id) == ["1girl", "silver_hair"], (
            "an unreachable file is not a changed file; its tags are still true"
        )

    def test_a_star_rating_survives_a_file_going_missing(self, test_db):
        image_id = db.add_image(path=r"L:\lib\rated.png", filename="rated.png")
        db.set_user_rating(image_id, 5)

        db.mark_image_unreadable(image_id, "File not found")

        assert db.get_image_by_id(image_id)["user_rating"] == 5

    def test_the_row_is_still_marked_unreadable(self, test_db):
        """Keeping the tags must not weaken the flag the gallery filters on."""
        image_id = db.add_image(path=r"L:\lib\gone.png", filename="gone.png")
        db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])

        db.mark_image_unreadable(image_id, "File not found")

        row = db.get_image_by_id(image_id)
        assert row["is_readable"] == 0
        assert row["metadata_status"] == "error"
        assert row["read_error"]

    def test_pixel_derived_caches_are_still_cleared(self, test_db):
        """Anything that must be recomputed from pixels we cannot read must go."""
        image_id = db.add_image(path=r"L:\lib\derived.png", filename="derived.png")
        with db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET content_fingerprint = 'abc123',
                    embedding = X'00',
                    aesthetic_score = 7.5,
                    ai_rating = 4,
                    ai_caption = 'a cached caption'
                WHERE id = ?
                """,
                (image_id,),
            )

        db.mark_image_unreadable(image_id, "File not found")

        # Read the columns directly: get_image_by_id does not project the
        # pixel-derived caches, and asserting through it would pass vacuously.
        with db.get_db() as conn:
            row = conn.execute(
                """
                SELECT content_fingerprint, embedding, aesthetic_score,
                       ai_rating, ai_caption
                FROM images WHERE id = ?
                """,
                (image_id,),
            ).fetchone()
        assert row["content_fingerprint"] is None
        assert row["embedding"] is None
        assert row["aesthetic_score"] is None
        assert row["ai_rating"] is None
        assert row["ai_caption"] is None


class TestScanningDoesNotDestroyTags:
    """The second route to the same loss, through the scan/upsert path.

    ``add_image`` sets ``mark_unreadable`` from ``is_readable=False``, and
    ``_should_clear_derived_state`` short-circuits to True on that flag alone —
    so re-scanning a folder while a drive is disconnected wiped the tags of
    every row it could not read, exactly like listing the gallery did.
    """

    def test_rescanning_an_unreadable_file_keeps_its_tags(self, test_db):
        image_id = db.add_image(path=r"L:\lib\scanned.png", filename="scanned.png")
        db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])
        db.set_user_rating(image_id, 3)

        # What a scan does when it cannot read the file it already indexed.
        db.add_image(
            path=r"L:\lib\scanned.png",
            filename="scanned.png",
            is_readable=False,
            read_error="File not found",
        )

        assert _tag_names(image_id) == ["1girl"]
        row = db.get_image_by_id(image_id)
        assert row["is_readable"] == 0
        assert row["user_rating"] == 3

    def test_rescanning_a_file_whose_pixels_changed_still_drops_its_ai_tags(self, test_db):
        """The content-change path must keep clearing: those tags describe old pixels."""
        image_id = db.add_image(
            path=r"L:\lib\edited.png",
            filename="edited.png",
            content_fingerprint="fingerprint-before",
        )
        db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9, "source": "tagger"}])

        db.add_image(
            path=r"L:\lib\edited.png",
            filename="edited.png",
            content_fingerprint="fingerprint-after",
        )

        assert _tag_names(image_id) == []

    def test_a_hand_typed_tag_survives_even_when_the_pixels_changed(self, test_db):
        """Only the machine's guesses go stale; the user's own words do not.

        ``add_tags`` already refuses to let re-tagging overwrite ``source='manual'``
        rows, so a content-change rescan silently deleting them contradicted the
        rule the tagger obeys. It is reachable in normal use: censoring an image
        in place changes its pixels, and the next scan took the labels with it.
        """
        image_id = db.add_image(
            path=r"L:\lib\censored.png",
            filename="censored.png",
            content_fingerprint="fingerprint-before",
        )
        db.add_tags(
            image_id,
            [
                {"tag": "1girl", "confidence": 0.9, "source": "tagger"},
                {"tag": "my_oc_name", "confidence": 1.0, "source": "manual"},
            ],
        )

        db.add_image(
            path=r"L:\lib\censored.png",
            filename="censored.png",
            content_fingerprint="fingerprint-after",
        )

        assert _tag_names(image_id) == ["my_oc_name"]


class TestBrowsingTheGalleryDoesNotDestroyTags:
    def test_listing_a_gallery_with_a_missing_file_keeps_its_tags(self, test_db):
        """This is the path that made the loss reachable without any user action.

        ``_filter_and_mark_missing_images`` runs on an ordinary gallery listing,
        so an unplugged drive plus one page load was the whole reproduction.
        """
        image_id = db.add_image(
            path=r"Q:\unplugged-drive\photo.png", filename="photo.png"
        )
        db.add_tags(image_id, [{"tag": "masterpiece", "confidence": 0.95}])

        live, missing = ImageService()._filter_and_mark_missing_images(
            [{"id": image_id, "path": r"Q:\unplugged-drive\photo.png"}]
        )

        assert live == []
        assert missing == 1
        assert _tag_names(image_id) == ["masterpiece"], (
            "browsing the gallery must never delete tags"
        )

    def test_a_reconnect_can_hand_back_a_row_that_still_has_its_tags(self, test_db):
        """Find Moved Files restores the row; it must find the work still attached."""
        image_id = db.add_image(path=r"Q:\old\moved.png", filename="moved.png")
        db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])
        db.set_user_rating(image_id, 4)
        db.mark_image_unreadable(image_id, "File not found")

        db.reconnect_image_source_path(image_id, r"L:\new\moved.png")

        row = db.get_image_by_id(image_id)
        assert row["is_readable"] == 1
        assert row["user_rating"] == 4
        assert _tag_names(image_id) == ["1girl"]
