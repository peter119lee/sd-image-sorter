"""
Tests for image router endpoints.

Tests:
- GET /api/images - Image listing with filters
- GET /api/images/{image_id} - Single image retrieval
- GET /api/image-file/{image_id} - Image file serving
- GET /api/image-thumbnail/{image_id} - Thumbnail generation

Priority: HIGH
"""
import os
import re
import sys
import json
import base64
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image
from utils.pagination_cursor import decode_image_cursor

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestGetImages:
    """Tests for GET /api/images endpoint."""

    def test_get_images_returns_list(self, test_client, tmp_path):
        """Getting images should return a list."""
        from PIL import Image

        image_path = tmp_path / "router_list.png"
        Image.new("RGB", (32, 32), "white").save(image_path)
        test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            checkpoint="sd_xl_base_1.0.safetensors [abcd1234]",
            metadata_json="{}",
        )

        response = test_client.get("/api/images")

        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert "total" in data
        assert isinstance(data["images"], list)
        assert "checkpoint_normalized" in data["images"][0]

    def test_get_images_with_limit(self, test_client, test_db_with_images):
        """Limit parameter should limit results."""
        response = test_client.get("/api/images?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert len(data["images"]) <= 2

    def test_get_images_with_offset(self, test_client, test_db_with_images):
        """Offset parameter should skip images."""
        response1 = test_client.get("/api/images?limit=10")
        response2 = test_client.get("/api/images?limit=10&offset=2")

        data1 = response1.json()
        data2 = response2.json()

        # Total count should be same
        assert data1["total"] == data2["total"]

    def test_filter_by_generator(self, test_client, test_db_with_images):
        """Filtering by generator should work."""
        response = test_client.get("/api/images?generators=comfyui")

        assert response.status_code == 200
        data = response.json()

        for img in data["images"]:
            assert img["generator"] == "comfyui"

    def test_filter_by_multiple_generators(self, test_client, test_db_with_images):
        """Filtering by multiple generators should use OR logic."""
        response = test_client.get("/api/images?generators=comfyui,nai")

        assert response.status_code == 200
        data = response.json()

        generators = {img["generator"] for img in data["images"]}
        assert generators.issubset({"comfyui", "nai"})

    def test_filter_by_tags(self, test_client, test_db_with_images):
        """Filtering by tags should use AND logic."""
        response = test_client.get("/api/images?tags=landscape,outdoor")

        assert response.status_code == 200
        data = response.json()

        # All returned images should have both tags
        # (Note: this test depends on test data having images with both tags)

    def test_filter_by_ratings(self, test_client, test_db_with_images):
        """Filtering by ratings should work."""
        response = test_client.get("/api/images?ratings=explicit,sensitive")

        assert response.status_code == 200

    def test_filter_by_search_query(self, test_client, test_db_with_images):
        """Searching in prompts should work."""
        response = test_client.get("/api/images?search=landscape")

        assert response.status_code == 200
        data = response.json()

        # Results should contain search term in prompt (case insensitive)
        for img in data["images"]:
            if img.get("prompt"):
                assert "landscape" in img["prompt"].lower()

    def test_prompt_contains_mode_in_gallery_query_includes_parenthesized_variants(self, test_client, test_db, tmp_path):
        """Gallery prompt filters should pass contains mode through to the image query."""
        expected_ids = []
        for value in [
            "takamatsu_tomori",
            "takamatsu_tomori(bang dream!)",
            "takamatsu_tomori(bang dream!!!!!its mygo)",
        ]:
            image_path = tmp_path / f"router_gallery_prompt_contains_{len(expected_ids)}.png"
            Image.new("RGB", (32, 32), "white").save(image_path)
            expected_ids.append(
                test_client.test_db.add_image(
                    path=str(image_path),
                    filename=image_path.name,
                    prompt=f"{value}, 1girl",
                    metadata_json="{}",
                )
            )
        other_path = tmp_path / "router_gallery_prompt_contains_other.png"
        Image.new("RGB", (32, 32), "white").save(other_path)
        test_client.test_db.add_image(
            path=str(other_path),
            filename=other_path.name,
            prompt="shiina_taki, 1girl",
            metadata_json="{}",
        )

        response = test_client.get(
            "/api/images",
            params={
                "prompts": "takamatsu_tomori",
                "prompt_match_mode": "contains",
                "sort_by": "oldest",
                "limit": 10,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert [image["id"] for image in data["images"]] == expected_ids
        assert data["total"] == len(expected_ids)

    def test_filter_by_checkpoint_normalized_search_query(self, test_client, tmp_path):
        from PIL import Image

        image_path = tmp_path / "router_checkpoint_search.png"
        Image.new("RGB", (32, 32), "white").save(image_path)
        test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            checkpoint="RealisticVisionV51.safetensors [abc12345]",
            metadata_json="{}",
        )

        response = test_client.get("/api/images?search=realisticvisionv51")

        assert response.status_code == 200
        data = response.json()
        assert len(data["images"]) == 1
        assert data["images"][0]["checkpoint_normalized"] == "RealisticVisionV51"

    def test_filter_by_dimensions(self, test_client, test_db_with_images):
        """Filtering by dimensions should work."""
        response = test_client.get("/api/images?min_width=1000")

        assert response.status_code == 200
        data = response.json()

        for img in data["images"]:
            assert img["width"] >= 1000

    def test_filter_by_aspect_ratio(self, test_client, test_db_with_images):
        """Filtering by aspect ratio should work."""
        response = test_client.get("/api/images?aspect_ratio=landscape")

        assert response.status_code == 200
        data = response.json()

        for img in data["images"]:
            ratio = img["width"] / img["height"]
            assert ratio > 1.1

    def test_color_filters_apply_to_offset_pagination_and_total(self, test_client, test_db, tmp_path):
        """Color filters must survive the non-cursor pagination branch."""
        import database as db

        bright_path = tmp_path / "bright.png"
        dim_path = tmp_path / "dim.png"
        neutral_path = tmp_path / "neutral.png"
        for path, color in [(bright_path, "white"), (dim_path, "black"), (neutral_path, "gray")]:
            Image.new("RGB", (32, 32), color=color).save(path)

        bright_id = db.add_image(path=str(bright_path), filename=bright_path.name, metadata_json="{}")
        dim_id = db.add_image(path=str(dim_path), filename=dim_path.name, metadata_json="{}")
        neutral_id = db.add_image(path=str(neutral_path), filename=neutral_path.name, metadata_json="{}")
        db.update_image_colors(bright_id, {
            "avg_brightness": 245,
            "color_temperature": "warm",
            "color_saturation": 0.2,
            "brightness_skew": 0.7,
            "brightness_distribution": "right_heavy",
        })
        db.update_image_colors(dim_id, {
            "avg_brightness": 15,
            "color_temperature": "cool",
            "color_saturation": 0.1,
            "brightness_skew": -0.8,
            "brightness_distribution": "left_heavy",
        })
        db.update_image_colors(neutral_id, {
            "avg_brightness": 128,
            "color_temperature": "neutral",
            "color_saturation": 0.05,
            "brightness_skew": 0.0,
            "brightness_distribution": "balanced",
        })

        response = test_client.get(
            "/api/images",
            params={
                "offset": 0,
                "sort_by": "brightness",
                "brightness_min": 200,
                "color_temperature": "warm",
                "brightness_distribution": "right_heavy",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert [image["id"] for image in payload["images"]] == [bright_id]
        assert payload["total"] == 1

    def test_sort_by_options(self, test_client, test_db_with_images):
        """Various sort options should work."""
        sort_options = ["newest", "oldest", "name_asc", "name_desc", "file_size", "aesthetic", "aesthetic_asc"]

        for sort_by in sort_options:
            response = test_client.get(f"/api/images?sort_by={sort_by}")
            assert response.status_code == 200, f"Failed for sort_by={sort_by}"

    def test_invalid_sort_uses_default(self, test_client, test_db_with_images):
        """Invalid sort option should return 400 error."""
        response = test_client.get("/api/images?sort_by=invalid_sort")

        # Should return 400 for invalid sort option
        assert response.status_code == 400

    def test_combined_filters(self, test_client, test_db_with_images):
        """Multiple filters combined should work."""
        response = test_client.get(
            "/api/images?generators=comfyui&min_width=500&limit=10"
        )

        assert response.status_code == 200
        data = response.json()

        for img in data["images"]:
            assert img["generator"] == "comfyui"
            assert img["width"] >= 500

    def test_get_images_skips_missing_files_and_backfills_cursor_page(self, test_client, tmp_path):
        """Newest gallery pages should skip stale rows instead of returning an empty broken grid."""
        import database as db

        live_old = tmp_path / "live-old.png"
        live_new = tmp_path / "live-new.png"
        Image.new("RGB", (32, 32), color="blue").save(live_old)
        Image.new("RGB", (32, 32), color="green").save(live_new)

        live_old_id = db.add_image(path=str(live_old), filename=live_old.name, metadata_json="{}")
        live_new_id = db.add_image(path=str(live_new), filename=live_new.name, metadata_json="{}")
        missing_ids = [
            db.add_image(path=str(tmp_path / f"missing-{index}.png"), filename=f"missing-{index}.png", metadata_json="{}")
            for index in range(3)
        ]

        response = test_client.get("/api/images?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == [live_new_id, live_old_id]
        assert data["total"] == 2

        for image_id in missing_ids:
            row = db.get_image_by_id(image_id)
            assert row["is_readable"] == 0
            assert "file not found" in (row["read_error"] or "").lower()

    def test_get_images_does_not_mark_moved_row_missing_from_stale_snapshot(self, test_client, tmp_path, monkeypatch):
        """A gallery read racing with a move should not poison the new DB path."""
        import database as db
        from services import image_service

        old_path = tmp_path / "move-race-old.png"
        new_path = tmp_path / "move-race-new.png"
        Image.new("RGB", (32, 32), color="blue").save(new_path)
        image_id = db.add_image(
            path=str(old_path),
            filename=old_path.name,
            prompt="move race token",
            metadata_json="{}",
        )

        original_get_image_by_id = db.get_image_by_id

        def move_before_missing_mark(row_id):
            if int(row_id) == image_id:
                db.update_image_path(image_id, str(new_path))
            return original_get_image_by_id(row_id)

        monkeypatch.setattr(image_service.db, "get_image_by_id", move_before_missing_mark)

        response = test_client.get("/api/images?limit=10&search=move%20race%20token")

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == [image_id]
        assert data["images"][0]["path"] == str(new_path)

        row = original_get_image_by_id(image_id)
        assert row["is_readable"] == 1
        assert row["read_error"] is None

    def test_get_images_skips_missing_files_for_offset_sorts(self, test_client, tmp_path):
        """Offset pagination should also backfill around stale rows for non-cursor sorts."""
        import database as db

        live_a = tmp_path / "live-c.png"
        live_b = tmp_path / "live-d.png"
        Image.new("RGB", (32, 32), color="red").save(live_a)
        Image.new("RGB", (32, 32), color="yellow").save(live_b)

        db.add_image(path=str(tmp_path / "aaa-missing.png"), filename="aaa-missing.png", metadata_json="{}")
        db.add_image(path=str(tmp_path / "bbb-missing.png"), filename="bbb-missing.png", metadata_json="{}")
        live_a_id = db.add_image(path=str(live_a), filename=live_a.name, metadata_json="{}")
        live_b_id = db.add_image(path=str(live_b), filename=live_b.name, metadata_json="{}")

        response = test_client.get("/api/images?sort_by=name_asc&limit=2")

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == [live_a_id, live_b_id]
        assert data["total"] == 2

    def test_get_images_sanitizes_non_utf8_bytes_in_listing(self, test_client, test_db, tmp_path):
        """List payloads should not crash when historical rows contain raw bytes."""
        import database as db

        image_path = tmp_path / "listing-bytes.png"
        Image.new("RGB", (32, 32), color="purple").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE images SET filename = ?, generator = ? WHERE id = ?",
                (b"listing-\xff.png", b"comfy\xffui", image_id),
            )

        response = test_client.get("/api/images?limit=10")

        assert response.status_code == 200
        data = response.json()
        image = next(item for item in data["images"] if item["id"] == image_id)
        assert image["filename"] == "listing-\ufffd.png"
        assert image["generator"] == "comfy\ufffdui"

    def test_get_images_accepts_opaque_cursor_and_survives_deleted_anchor_row(self, test_client, tmp_path):
        """Router pagination should accept opaque next_cursor tokens and continue after deleted anchor rows."""
        import database as db

        image_ids = []
        for index in range(4):
            image_path = tmp_path / f"opaque-router-{index}.png"
            Image.new("RGB", (32, 32), color="white").save(image_path)
            image_ids.append(
                db.add_image(
                    path=str(image_path),
                    filename=image_path.name,
                    metadata_json="{}",
                    created_at=datetime(2024, 1, 1, 0, 0, index),
                )
            )

        expected_ids = list(reversed(image_ids))
        first_response = test_client.get("/api/images?sort_by=newest&limit=2")

        assert first_response.status_code == 200
        first_payload = first_response.json()
        assert [item["id"] for item in first_payload["images"]] == expected_ids[:2]
        assert first_payload["next_cursor"] != str(expected_ids[1])

        cursor = decode_image_cursor(first_payload["next_cursor"])
        assert cursor.image_id == expected_ids[1]
        assert cursor.is_opaque is True

        with db.get_db() as conn:
            conn.execute("DELETE FROM images WHERE id = ?", (cursor.image_id,))

        second_response = test_client.get(
            f"/api/images?sort_by=newest&limit=2&cursor={first_payload['next_cursor']}"
        )

        assert second_response.status_code == 200
        second_payload = second_response.json()
        assert [item["id"] for item in second_payload["images"]] == expected_ids[2:]
        assert second_payload["has_more"] is False


class TestSelectionIds:
    """Tests for POST /api/images/selection-ids endpoint."""

    def _seed_full_gallery_scope(self, db, tmp_path: Path):
        alpha = tmp_path / "selection-contract-alpha"
        beta = tmp_path / "selection-contract-beta"
        alpha.mkdir()
        beta.mkdir()
        collection = db.create_collection("Selection Contract")
        collection_id = int(collection["id"])

        def add_case(name, *, folder=alpha, generator="comfyui", prompt="target prompt", rating=5, color="warm", member=True):
            image_path = folder / f"{name}.png"
            image_id = db.add_image(
                path=str(image_path),
                filename=image_path.name,
                generator=generator,
                prompt=prompt,
                metadata_json="{}",
            )
            db.set_user_rating(image_id, rating)
            db.update_image_colors(image_id, {
                "avg_brightness": 128,
                "color_temperature": color,
                "brightness_distribution": "balanced",
            })
            if member:
                db.set_collection_membership(collection_id, image_id, True)
            return image_id

        keep_id = add_case("keep")
        add_case("low-rating", rating=2)
        add_case("excluded-prompt", prompt="target prompt, blocked-term")
        add_case("excluded-color", color="cool")
        add_case("wrong-collection", member=False)
        add_case("wrong-folder", folder=beta)
        add_case("no-metadata", generator="unknown", prompt=None)

        payload = {
            "sortBy": "name_asc",
            "minUserRating": 4,
            "excludePrompts": ["blocked-term"],
            "excludeColors": ["cool"],
            "collectionId": collection_id,
            "folder": str(alpha),
            "hasMetadata": True,
        }
        return keep_id, payload

    def test_selection_ids_returns_all_filtered_ids_in_sort_order(self, test_client, test_db_with_images):
        """Filtered selection should return the full matching ID set in current sort order."""
        expected_by_filename = {
            image["filename"]: image_id
            for image, image_id in zip(test_db_with_images["images"], test_db_with_images["image_ids"])
        }
        expected_ids = [
            expected_by_filename[filename]
            for filename in sorted(expected_by_filename.keys())
        ]

        response = test_client.post("/api/images/selection-ids", json={
            "sortBy": "name_asc",
        })

        assert response.status_code == 200
        assert response.json() == {
            "image_ids": expected_ids,
            "total": len(expected_ids),
        }

    def test_selection_ids_uses_exact_filter_contract(self, test_client, test_db_with_images):
        """Filtered selection should reuse the real DB filter contract, including exact LoRA matching."""
        comfyui_id = test_db_with_images["image_ids"][0]

        response = test_client.post("/api/images/selection-ids", json={
            "generators": ["comfyui", "nai"],
            "loras": ["add_detail"],
            "sortBy": "newest",
        })

        assert response.status_code == 200
        assert response.json() == {
            "image_ids": [comfyui_id],
            "total": 1,
        }

    def test_selection_ids_rejects_invalid_sort(self, test_client, test_db_with_images):
        """Filtered selection should reject invalid sort values instead of silently guessing."""
        response = test_client.post("/api/images/selection-ids", json={
            "sortBy": "not-a-real-sort",
        })

        assert response.status_code == 400
        assert "Invalid sort_by value" in response.text

    def test_selection_ids_treats_empty_aspect_ratio_as_no_filter(self, test_client, test_db_with_images):
        """Frontend empty aspect-ratio values should not make all-filtered selection fail."""
        response = test_client.post("/api/images/selection-ids", json={
            "aspectRatio": "",
            "sortBy": "name_asc",
        })

        assert response.status_code == 200
        assert response.json()["total"] == len(test_db_with_images["image_ids"])

    def test_selection_token_treats_empty_aspect_ratio_as_no_filter(self, test_client, test_db_with_images):
        """Chunked all-filtered selection should also accept frontend empty aspect-ratio values."""
        response = test_client.post("/api/images/selection-token", json={
            "aspectRatio": "",
            "sortBy": "name_asc",
            "chunkSize": 2,
        })

        assert response.status_code == 200
        assert response.json()["total_estimate"] == len(test_db_with_images["image_ids"])

    def test_selection_ids_post_filter_scans_sparse_matches_without_truncation(self, test_client, test_db_with_images):
        """Selection ID resolution should not truncate when SQL prefilter returns many false positives."""
        exact_ids = []
        for index in range(5):
            exact_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/router_selection_exact_{index}.png",
                    filename=f"router_selection_exact_{index}.png",
                    prompt="hero, studio shot",
                )
            )

        for index in range(45):
            test_client.test_db.add_image(
                path=f"/test/router_selection_false_positive_{index}.png",
                filename=f"router_selection_false_positive_{index}.png",
                prompt="superhero, studio shot",
            )

        expected_ids = list(reversed(exact_ids))
        response = test_client.post("/api/images/selection-ids", json={
            "prompts": ["hero"],
            "sortBy": "newest",
        })

        assert response.status_code == 200
        assert response.json() == {
            "image_ids": expected_ids,
            "total": len(expected_ids),
        }

    def test_selection_ids_prompt_contains_mode_includes_parenthesized_variants(self, test_client, test_db):
        """Filtered selection should preserve the user's prompt match mode."""
        expected_ids = []
        for value in [
            "takamatsu_tomori",
            "takamatsu_tomori(bang dream)",
            "takamatsu_tomori(bang dream!!!!!its mygo)",
        ]:
            expected_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/router_selection_prompt_contains_{len(expected_ids)}.png",
                    filename=f"router_selection_prompt_contains_{len(expected_ids)}.png",
                    prompt=f"{value}, 1girl",
                )
            )

        response = test_client.post("/api/images/selection-ids", json={
            "prompts": ["takamatsu_tomori"],
            "promptMatchMode": "contains",
            "sortBy": "oldest",
        })

        assert response.status_code == 200
        assert response.json() == {
            "image_ids": expected_ids,
            "total": len(expected_ids),
        }

    def test_selection_ids_preserves_tag_mode_or_and_excludes(self, test_client, test_db_with_images):
        """Legacy filtered selection must match the gallery's OR and exclude semantics."""
        landscape_id = test_db_with_images["image_ids"][0]
        portrait_id = test_db_with_images["image_ids"][2]

        response = test_client.post("/api/images/selection-ids", json={
            "tags": ["landscape", "portrait"],
            "tagMode": "or",
            "sortBy": "oldest",
        })

        assert response.status_code == 200
        assert response.json()["image_ids"] == [landscape_id, portrait_id]

        excluded = test_client.post("/api/images/selection-ids", json={
            "tags": ["landscape", "portrait"],
            "tagMode": "or",
            "excludeTags": ["portrait"],
            "sortBy": "oldest",
        })

        assert excluded.status_code == 200
        assert excluded.json() == {"image_ids": [landscape_id], "total": 1}

    def test_selection_ids_preserves_full_gallery_filter_contract(self, test_client, test_db, tmp_path):
        """Legacy all-filtered selection must not drop Gallery filters added after v3.2.2."""
        keep_id, payload = self._seed_full_gallery_scope(test_client.test_db, tmp_path)

        response = test_client.post("/api/images/selection-ids", json=payload)

        assert response.status_code == 200
        assert response.json() == {"image_ids": [keep_id], "total": 1}

    def test_selection_ids_rejects_oversized_legacy_response(self, test_client, monkeypatch):
        """The compatibility endpoint must not return unbounded giant ID arrays."""
        import services.image_service as image_service_module

        monkeypatch.setattr(
            image_service_module.db,
            "get_filtered_image_ids",
            lambda **_kwargs: list(range(1, image_service_module.SELECTION_IDS_MAX_RESPONSE + 2)),
        )

        response = test_client.post("/api/images/selection-ids", json={"sortBy": "oldest"})

        assert response.status_code == 413
        assert "selection-ids is limited" in response.text

    def test_selection_query_token_returns_stateless_chunk_contract(self, test_client, test_db_with_images):
        """Selection token should let clients page IDs without one giant response."""
        response = test_client.post("/api/images/selection-token", json={
            "sortBy": "name_asc",
            "chunkSize": 2,
        })

        assert response.status_code == 200
        payload = response.json()
        assert payload["selection_token"]
        assert payload["chunk_size"] == 2
        assert payload["total_estimate"] == len(test_db_with_images["image_ids"])
        assert payload["exact_total"] is True

        first = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": payload["selection_token"], "offset": 0, "limit": 2},
        )
        second = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": payload["selection_token"], "offset": 2, "limit": 2},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        expected_by_filename = {
            image["filename"]: image_id
            for image, image_id in zip(test_db_with_images["images"], test_db_with_images["image_ids"])
        }
        expected_ids = [
            expected_by_filename[filename]
            for filename in sorted(expected_by_filename.keys())
        ]
        assert first.json()["image_ids"] == expected_ids[:2]
        assert first.json()["next_offset"] == 2
        assert first.json()["has_more"] is True
        assert second.json()["image_ids"] == expected_ids[2:4]

    def test_selection_token_preserves_color_filter_contract(self, test_client, test_db, tmp_path):
        """Chunked filtered selection must not drop color filters from the token."""
        import database as db

        bright_path = tmp_path / "token-bright.png"
        dim_path = tmp_path / "token-dim.png"
        Image.new("RGB", (32, 32), color="white").save(bright_path)
        Image.new("RGB", (32, 32), color="black").save(dim_path)

        bright_id = db.add_image(path=str(bright_path), filename=bright_path.name, metadata_json="{}")
        dim_id = db.add_image(path=str(dim_path), filename=dim_path.name, metadata_json="{}")
        db.update_image_colors(bright_id, {
            "avg_brightness": 240,
            "color_temperature": "warm",
            "brightness_distribution": "right_heavy",
        })
        db.update_image_colors(dim_id, {
            "avg_brightness": 20,
            "color_temperature": "cool",
            "brightness_distribution": "left_heavy",
        })

        token_response = test_client.post("/api/images/selection-token", json={
            "sortBy": "brightness",
            "chunkSize": 10,
            "brightnessMin": 200,
            "colorTemperature": "warm",
            "brightnessDistribution": "right_heavy",
        })
        assert token_response.status_code == 200
        assert token_response.json()["total_estimate"] == 1

        chunk_response = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token_response.json()["selection_token"], "offset": 0, "limit": 10},
        )

        assert chunk_response.status_code == 200
        assert chunk_response.json()["image_ids"] == [bright_id]
        assert dim_id not in chunk_response.json()["image_ids"]

    def test_selection_token_preserves_tag_mode_or_and_excludes(self, test_client, test_db_with_images):
        """Chunked selection tokens must carry tag OR mode and exclude filters."""
        landscape_id = test_db_with_images["image_ids"][0]
        portrait_id = test_db_with_images["image_ids"][2]

        token_response = test_client.post("/api/images/selection-token", json={
            "tags": ["landscape", "portrait"],
            "tagMode": "or",
            "excludeTags": ["portrait"],
            "sortBy": "oldest",
            "chunkSize": 10,
        })
        assert token_response.status_code == 200
        assert token_response.json()["total_estimate"] == 1

        chunk_response = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token_response.json()["selection_token"], "offset": 0, "limit": 10},
        )

        assert chunk_response.status_code == 200
        assert chunk_response.json()["image_ids"] == [landscape_id]
        assert portrait_id not in chunk_response.json()["image_ids"]

    def test_tag_sidecar_selection_token_preserves_tag_mode_or_and_excludes(self, test_client, test_db_with_images):
        """Sidecar export helpers must decode the full selection-token contract."""
        from services.tag_export_service import count_selection_token_ids, iter_selection_token_id_chunks

        landscape_id = test_db_with_images["image_ids"][0]
        token_response = test_client.post("/api/images/selection-token", json={
            "tags": ["landscape", "portrait"],
            "tagMode": "or",
            "excludeTags": ["portrait"],
            "sortBy": "oldest",
            "chunkSize": 10,
        })
        assert token_response.status_code == 200
        token = token_response.json()["selection_token"]

        assert count_selection_token_ids(token) == 1
        assert list(iter_selection_token_id_chunks(token, chunk_size=10)) == [[landscape_id]]

    def test_selection_token_and_sidecar_preserve_full_gallery_filter_contract(self, test_client, test_db, tmp_path):
        """Token chunks and sidecar helpers must keep the same full Gallery scope."""
        from services.tag_export_service import count_selection_token_ids, iter_selection_token_id_chunks

        keep_id, payload = self._seed_full_gallery_scope(test_client.test_db, tmp_path)
        token_response = test_client.post("/api/images/selection-token", json={**payload, "chunkSize": 10})
        assert token_response.status_code == 200
        assert token_response.json()["total_estimate"] == 1
        token = token_response.json()["selection_token"]

        chunk_response = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token, "offset": 0, "limit": 10},
        )

        assert chunk_response.status_code == 200
        assert chunk_response.json()["image_ids"] == [keep_id]
        assert count_selection_token_ids(token) == 1
        assert list(iter_selection_token_id_chunks(token, chunk_size=10)) == [[keep_id]]

    def test_selection_token_can_exclude_small_explicit_selection(self, test_client, test_db_with_images):
        """Token mode should preserve filtered-invert semantics without materializing every ID."""
        excluded_id = test_db_with_images["image_ids"][0]

        response = test_client.post("/api/images/selection-token", json={
            "sortBy": "name_asc",
            "chunkSize": 10,
            "excludedImageIds": [excluded_id],
        })

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_estimate"] == len(test_db_with_images["image_ids"]) - 1

        chunk = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": payload["selection_token"], "offset": 0, "limit": 10},
        )

        assert chunk.status_code == 200
        ids = chunk.json()["image_ids"]
        assert excluded_id not in ids
        assert len(ids) == len(test_db_with_images["image_ids"]) - 1

    def test_export_selection_data_token_respects_excluded_ids(self, test_client, test_db_with_images):
        """Token export preview should use the token scope, including excluded IDs."""
        excluded_id = test_db_with_images["image_ids"][0]
        token_response = test_client.post("/api/images/selection-token", json={
            "sortBy": "name_asc",
            "excludedImageIds": [excluded_id],
        })
        assert token_response.status_code == 200

        response = test_client.post("/api/images/export-data", json={
            "selection_token": token_response.json()["selection_token"],
            "offset": 0,
            "limit": 10,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == len(test_db_with_images["image_ids"]) - 1
        assert excluded_id not in [image["id"] for image in data["images"]]

    def test_selection_query_token_marks_prompt_total_as_estimate(self, test_client, test_db_with_images):
        """Prompt terms can still require post-filtering, so token totals must be honest."""
        response = test_client.post("/api/images/selection-token", json={
            "prompts": ["hero"],
            "sortBy": "newest",
        })

        assert response.status_code == 200
        payload = response.json()
        assert payload["selection_token"]
        assert payload["exact_total"] is False
        assert isinstance(payload["total_estimate"], int)

    def test_selection_chunk_post_filter_offset_skips_exact_matches_only(self, test_client, test_db_with_images):
        """Chunked selection must not treat SQL false positives as offset positions."""
        exact_ids = []
        for index in range(5):
            exact_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/router_selection_chunk_exact_{index}.png",
                    filename=f"router_selection_chunk_exact_{index}.png",
                    prompt="hero, studio shot",
                )
            )

        for index in range(45):
            test_client.test_db.add_image(
                path=f"/test/router_selection_chunk_false_positive_{index}.png",
                filename=f"router_selection_chunk_false_positive_{index}.png",
                prompt="superhero, studio shot",
            )

        token_response = test_client.post("/api/images/selection-token", json={
            "prompts": ["hero"],
            "sortBy": "newest",
            "chunkSize": 2,
        })
        assert token_response.status_code == 200
        token = token_response.json()["selection_token"]

        chunk_response = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token, "offset": 2, "limit": 2},
        )

        assert chunk_response.status_code == 200
        assert chunk_response.json()["image_ids"] == list(reversed(exact_ids))[2:4]

    def test_selection_token_prompt_contains_mode_pages_parenthesized_variants(self, test_client, test_db):
        """Selection-token chunks should carry contains-mode prompt semantics."""
        expected_ids = []
        for value in [
            "takamatsu_tomori",
            "takamatsu_tomori(bang dream!)",
            "takamatsu_tomori(bang dream!!!!!its mygo)",
        ]:
            expected_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/router_selection_token_contains_{len(expected_ids)}.png",
                    filename=f"router_selection_token_contains_{len(expected_ids)}.png",
                    prompt=f"{value}, 1girl",
                )
            )

        token_response = test_client.post("/api/images/selection-token", json={
            "prompts": ["takamatsu_tomori"],
            "promptMatchMode": "contains",
            "sortBy": "oldest",
            "chunkSize": 2,
        })
        assert token_response.status_code == 200
        payload = token_response.json()
        assert payload["exact_total"] is True

        chunk_response = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": payload["selection_token"], "offset": 1, "limit": 2},
        )

        assert chunk_response.status_code == 200
        assert chunk_response.json()["image_ids"] == expected_ids[1:]

    def test_selection_token_rejects_random_sort(self, test_client):
        """Random ordering cannot be split into stateless offset chunks without duplicates/gaps."""
        response = test_client.post("/api/images/selection-token", json={
            "sortBy": "random",
            "chunkSize": 2,
        })

        assert response.status_code == 400
        assert "random sort cannot use the chunked selection token protocol" in response.text

    def test_selection_chunk_rejects_tampered_filter_types(self, test_client):
        """Decoded token payloads should fail as 400 instead of leaking TypeError as 500."""
        token_payload = {
            "v": 1,
            "filters": {
                "sortBy": "newest",
                "minWidth": "not-an-int",
                "maxWidth": 100,
            },
        }
        raw = json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        selection_token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        response = test_client.get("/api/images/selection-chunk", params={
            "selection_token": selection_token,
            "offset": 0,
            "limit": 100,
        })

        assert response.status_code == 400
        assert "Invalid selection token" in response.text

    def test_selection_chunk_rejects_invalid_token(self, test_client):
        """Chunk endpoint should not silently reinterpret malformed selection tokens."""
        response = test_client.get("/api/images/selection-chunk", params={
            "selection_token": "not-a-token",
            "offset": 0,
            "limit": 100,
        })

        assert response.status_code == 400
        assert "Invalid selection token" in response.text


class TestGetSingleImage:
    """Tests for GET /api/images/{image_id} endpoint."""

    def test_get_existing_image(self, test_client, test_db_with_images):
        """Getting existing image should return data."""
        image_id = test_db_with_images["image_ids"][0]

        response = test_client.get(f"/api/images/{image_id}")

        assert response.status_code == 200
        data = response.json()
        assert "image" in data
        assert "tags" in data
        assert data["image"]["id"] == image_id
        assert "checkpoint_normalized" in data["image"]

    def test_get_nonexistent_image(self, test_client):
        """Getting nonexistent image should return 404."""
        response = test_client.get("/api/images/999999")

        assert response.status_code == 404

    def test_get_image_includes_tags(self, test_client, test_db_with_images):
        """Image response should include tags."""
        # First image has tags
        image_id = test_db_with_images["image_ids"][0]

        response = test_client.get(f"/api/images/{image_id}")

        assert response.status_code == 200
        data = response.json()
        assert len(data["tags"]) > 0

    def test_get_existing_image_ignores_embedding_blob(self, test_client, test_db):
        """Image detail payload should stay JSON-safe even when embeddings exist in the database."""
        import database as db

        image_id = db.add_image(
            path="/test/embedded.png",
            filename="embedded.png",
            metadata_json="{}",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE images SET embedding = ? WHERE id = ?", (b"\xf2\x00\x01", image_id))

        response = test_client.get(f"/api/images/{image_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["image"]["id"] == image_id
        assert "embedding" not in data["image"]

    def test_get_existing_image_sanitizes_non_utf8_bytes_fields(self, test_client, test_db):
        """Image detail payload should replace undecodable bytes instead of crashing."""
        import database as db

        image_id = db.add_image(
            path="/test/bytes-detail.png",
            filename="bytes-detail.png",
            metadata_json="{}",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE images SET prompt = ?, metadata_json = ? WHERE id = ?",
                (b"city\xffnight", b'{"raw":"\xff"}', image_id),
            )

        response = test_client.get(f"/api/images/{image_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["image"]["prompt"] == "city\ufffdnight"
        assert data["image"]["metadata_json"] == '{"raw":"\ufffd"}'


class TestExportSelectionData:
    """Tests for POST /api/images/export-data endpoint."""

    def test_export_selection_data_returns_prompts_and_tags(self, test_client, test_db_with_images):
        """Batch export payload should include prompt and tag data in request order."""
        image_ids = test_db_with_images["image_ids"][:2]

        response = test_client.post("/api/images/export-data", json={"image_ids": image_ids})

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == image_ids
        assert data["images"][0]["filename"]
        assert "prompt" in data["images"][0]
        assert isinstance(data["images"][0]["tags"], list)
        assert data["missing_ids"] == []

    def test_export_selection_data_carries_the_sidecar_caption(self, test_client, test_db):
        """A caption-only row must not export as an image with no text.

        Migration 042 moved ``.txt`` sidecar text out of ``prompt`` into
        ``sidecar_caption``, and ``services/tag_export/captions.py`` promotes a
        prose sidecar when it composes the natural-language caption. The export
        payload omitted the column, so a client composing the same caption from
        this page saw an empty prompt and an empty ai_caption for a row that
        does carry text.
        """
        import database as db

        image_id = int(db.add_image(
            path="/test/export-sidecar.png",
            filename="export-sidecar.png",
            generator="others",
            prompt=None,
            metadata_json="{}",
        ))
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET sidecar_caption = ?, sidecar_caption_format = NULL WHERE id = ?",
                ("A woman with silver hair stands in a sunlit doorway.", image_id),
            )

        response = test_client.post("/api/images/export-data", json={"image_ids": [image_id]})

        assert response.status_code == 200, response.text
        image = response.json()["images"][0]
        assert image["prompt"] == ""
        assert image["ai_caption"] == ""
        assert image["sidecar_caption"] == (
            "A woman with silver hair stands in a sunlit doorway."
        )

    def test_export_selection_data_reports_missing_ids(self, test_client, test_db_with_images):
        """Missing images should not fail the whole export payload."""
        existing_id = test_db_with_images["image_ids"][0]

        response = test_client.post(
            "/api/images/export-data",
            json={"image_ids": [existing_id, 999999]},
        )

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == [existing_id]
        assert data["missing_ids"] == [999999]

    def test_export_selection_data_sanitizes_non_utf8_bytes(self, test_client, test_db):
        """Export payloads should replace undecodable bytes instead of failing validation."""
        import database as db

        image_id = db.add_image(
            path="/test/export-bytes.png",
            filename="export-bytes.png",
            metadata_json="{}",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE images SET filename = ?, prompt = ? WHERE id = ?",
                (b"export-\xff.png", b"prompt\xffbytes", image_id),
            )

        response = test_client.post("/api/images/export-data", json={"image_ids": [image_id]})

        assert response.status_code == 200
        data = response.json()
        assert data["missing_ids"] == []
        assert data["images"][0]["filename"] == "export-\ufffd.png"
        assert data["images"][0]["prompt"] == "prompt\ufffdbytes"

    def test_export_selection_data_rejects_empty_ids(self, test_client):
        """Validation should reject empty export selections."""
        response = test_client.post("/api/images/export-data", json={"image_ids": []})

        assert response.status_code == 400

    def test_export_selection_data_accepts_selection_token_page(self, test_client, test_db_with_images):
        """Large export previews should page by selection token instead of requiring giant ID payloads."""
        token_response = test_client.post("/api/images/selection-token", json={
            "sortBy": "name_asc",
            "chunkSize": 2,
        })
        assert token_response.status_code == 200

        response = test_client.post("/api/images/export-data", json={
            "selection_token": token_response.json()["selection_token"],
            "offset": 0,
            "limit": 2,
        })

        assert response.status_code == 200
        data = response.json()
        expected_by_filename = {
            image["filename"]: image_id
            for image, image_id in zip(test_db_with_images["images"], test_db_with_images["image_ids"])
        }
        expected_ids = [
            expected_by_filename[filename]
            for filename in sorted(expected_by_filename.keys())
        ]
        assert [item["id"] for item in data["images"]] == expected_ids[:2]
        assert data["missing_ids"] == []
        assert data["count"] == 2
        assert data["total"] == len(test_db_with_images["image_ids"])
        assert data["offset"] == 0
        assert data["limit"] == 2
        assert data["next_offset"] == 2
        assert data["has_more"] is True
        assert data["source"] == "selection_token"
        assert data["exact_total"] is True

    def test_export_selection_data_token_page_skips_prompt_false_positives(self, test_client, test_db):
        """Token export paging must share selection post-filter offset semantics."""
        exact_ids = []
        for index in range(4):
            exact_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/export_token_exact_{index}.png",
                    filename=f"export_token_exact_{index}.png",
                    prompt="hero, studio shot",
                )
            )
        for index in range(20):
            test_client.test_db.add_image(
                path=f"/test/export_token_false_positive_{index}.png",
                filename=f"export_token_false_positive_{index}.png",
                prompt="superhero, studio shot",
            )

        token_response = test_client.post("/api/images/selection-token", json={
            "prompts": ["hero"],
            "sortBy": "newest",
            "chunkSize": 2,
        })
        assert token_response.status_code == 200

        response = test_client.post("/api/images/export-data", json={
            "selection_token": token_response.json()["selection_token"],
            "offset": 2,
            "limit": 2,
        })

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == list(reversed(exact_ids))[2:4]
        assert data["source"] == "selection_token"
        assert data["exact_total"] is False

    def test_export_selection_data_token_preserves_tag_mode_or_and_excludes(self, test_client, test_db_with_images):
        """Export previews must use the same token contract as gallery selection."""
        landscape_id = test_db_with_images["image_ids"][0]

        token_response = test_client.post("/api/images/selection-token", json={
            "tags": ["landscape", "portrait"],
            "tagMode": "or",
            "excludeTags": ["portrait"],
            "sortBy": "oldest",
            "chunkSize": 10,
        })
        assert token_response.status_code == 200

        response = test_client.post("/api/images/export-data", json={
            "selection_token": token_response.json()["selection_token"],
            "offset": 0,
            "limit": 10,
        })

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data["images"]] == [landscape_id]
        assert data["total"] == 1

    def test_export_selection_data_rejects_tampered_selection_token(self, test_client):
        """Export-data token mode should fail closed like selection chunks."""
        raw = json.dumps({"v": 1, "filters": {"tags": "not-a-list"}}).encode("utf-8")
        selection_token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        response = test_client.post("/api/images/export-data", json={
            "selection_token": selection_token,
            "offset": 0,
            "limit": 2,
        })

        assert response.status_code == 400

    def test_export_selection_data_rejects_oversized_token_limit(self, test_client):
        """Export-data token pages must keep the same cap as selection chunks."""
        response = test_client.post("/api/images/export-data", json={
            "selection_token": "not-a-token",
            "offset": 0,
            "limit": 10001,
        })

        assert response.status_code == 400


class TestImageFileServing:
    """Tests for GET /api/image-file/{image_id} endpoint."""

    def test_get_image_file_not_found_in_db(self, test_client):
        """Requesting file for nonexistent DB entry should return 404."""
        response = test_client.get("/api/image-file/999999")

        assert response.status_code == 404

    def test_get_image_file_missing_on_disk(self, test_client, test_db):
        """Requesting file that doesn't exist on disk should return 404."""
        import database as db

        # Add image with non-existent path
        image_id = db.add_image(
            path="/nonexistent/path/image.png",
            filename="image.png",
        )

        response = test_client.get(f"/api/image-file/{image_id}")

        assert response.status_code == 404


class TestThumbnailGeneration:
    """Tests for GET /api/image-thumbnail/{image_id} endpoint."""

    def test_thumbnail_not_found_in_db(self, test_client):
        """Requesting thumbnail for nonexistent DB entry should return 404."""
        response = test_client.get("/api/image-thumbnail/999999")

        assert response.status_code == 404

    def test_thumbnail_missing_on_disk(self, test_client, test_db):
        """Requesting thumbnail for file not on disk should return 404."""
        import database as db

        image_id = db.add_image(
            path="/nonexistent/path/thumb.png",
            filename="thumb.png",
        )

        response = test_client.get(f"/api/image-thumbnail/{image_id}")

        assert response.status_code == 404

    @pytest.mark.skipif(os.name == "nt", reason="WSL Windows-path remap is only relevant on POSIX hosts")
    def test_thumbnail_windows_drive_path_resolves_under_wsl_mount(self, test_client, test_db):
        """Windows drive paths stored in SQLite should resolve to /mnt/<drive>/ files under WSL."""
        import database as db

        repo_root = Path(__file__).resolve().parents[3]
        if len(repo_root.parts) < 3 or repo_root.parts[1] != "mnt":
            pytest.skip("Repository is not mounted from /mnt/<drive> in this environment")

        drive = repo_root.parts[2]
        mount_root = Path("/mnt") / drive
        temp_dir = repo_root / ".tmp" / "pytest-wsl-paths"
        temp_dir.mkdir(parents=True, exist_ok=True)
        image_path = temp_dir / "windows-drive-thumb.png"

        try:
            Image.new("RGB", (64, 64), color="purple").save(image_path)
            rest_parts = image_path.relative_to(mount_root).parts
            windows_path = f"{drive.upper()}:\\{'\\'.join(rest_parts)}"

            image_id = db.add_image(
                path=windows_path,
                filename=image_path.name,
            )

            response = test_client.get(f"/api/image-thumbnail/{image_id}")

            assert response.status_code == 200
            assert response.headers.get("content-type") in ["image/png", "image/jpeg", "image/webp"]
        finally:
            image_path.unlink(missing_ok=True)


class TestAestheticEndpoints:
    """Tests for aesthetic scoring endpoints wired into the image router stack."""

    def test_score_all_returns_total_for_unscored_images(self, test_client, test_db):
        """Starting background scoring should report how many images need scores."""
        import database as db

        first_id = db.add_image(path="/tmp/aesthetic-1.png", filename="aesthetic-1.png", metadata_json="{}")
        second_id = db.add_image(path="/tmp/aesthetic-2.png", filename="aesthetic-2.png", metadata_json="{}")

        with db.get_db() as conn:
            conn.execute("UPDATE images SET aesthetic_score = ? WHERE id = ?", (6.5, first_id))

        with patch('aesthetic.is_available', return_value=True):
            response = test_client.post("/api/aesthetic/score-all")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total"] == 1

    def test_thumbnail_size_parameter(self, test_client, test_db_with_images, tmp_path):
        """Thumbnail size parameter should affect output."""
        import database as db
        from PIL import Image

        # Create a real image file
        img_path = tmp_path / "thumb_test.png"
        img = Image.new("RGB", (1024, 1024), color="blue")
        img.save(img_path)

        image_id = db.add_image(
            path=str(img_path),
            filename="thumb_test.png",
        )

        response = test_client.get(f"/api/image-thumbnail/{image_id}?size=128")

        # Should succeed
        assert response.status_code == 200
        assert response.headers.get("content-type") in ["image/png", "image/jpeg", "image/webp"]

    def test_thumbnail_invalid_size(self, test_client, test_db, tmp_path):
        """Invalid thumbnail size should be rejected."""
        import database as db
        from PIL import Image

        img_path = tmp_path / "size_test.png"
        img = Image.new("RGB", (512, 512), color="green")
        img.save(img_path)

        image_id = db.add_image(
            path=str(img_path),
            filename="size_test.png",
        )

        # Size must be >= 1 (ge=1 constraint)
        response = test_client.get(f"/api/image-thumbnail/{image_id}?size=0")

        # Should reject with validation error (422) or bad request (400)
        assert response.status_code in [400, 422]

    def test_thumbnail_caching_headers(self, test_client, test_db, tmp_path):
        """Thumbnail response should have caching headers."""
        import database as db
        from PIL import Image

        img_path = tmp_path / "cache_test.png"
        img = Image.new("RGB", (512, 512), color="red")
        img.save(img_path)

        image_id = db.add_image(
            path=str(img_path),
            filename="cache_test.png",
        )

        response = test_client.get(f"/api/image-thumbnail/{image_id}")

        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "Last-Modified" in response.headers

    def test_thumbnail_unreadable_file_returns_placeholder(self, test_client, test_db, tmp_path):
        """Unreadable files should render a placeholder thumbnail instead of 500."""
        import database as db

        broken_path = tmp_path / "broken.png"
        broken_path.write_bytes(b"not-a-real-png")

        image_id = db.add_image(
            path=str(broken_path),
            filename="broken.png",
        )

        response = test_client.get(f"/api/image-thumbnail/{image_id}")

        assert response.status_code == 200
        assert response.headers.get("content-type") == "image/webp"
        assert response.headers.get("X-Thumbnail-Placeholder") == "UNREADABLE"


class TestRemoveSelectedImages:
    """Tests for POST /api/images/remove-selected endpoint."""

    def test_remove_selected_start_job_removes_rows_keeps_files_and_completes(self, test_client, test_db, tmp_path):
        """v3.3.2 Phase-1: the background remove job drops DB rows (files stay on
        disk) and reports a terminal 'done' progress payload mirroring the
        synchronous endpoint's removed/missing_ids shape. TestClient runs the
        BackgroundTask synchronously after the response."""
        import database as db
        from PIL import Image

        image_path = tmp_path / "remove-job.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        start = test_client.post(
            "/api/images/remove-selected/start",
            json={"image_ids": [image_id]},
        )
        assert start.status_code == 200
        assert start.json().get("status") == "started"

        progress = test_client.get("/api/images/remove-selected/progress").json()
        assert progress["status"] == "done"
        assert progress["total"] == 1
        assert progress["removed"] == 1
        assert progress["missing_ids"] == []

        assert image_path.exists()  # remove keeps the file on disk
        assert db.get_image_by_id(image_id) is None

    def test_remove_selected_images_removes_database_rows_but_keeps_files(self, test_client, test_db, tmp_path):
        import database as db
        from PIL import Image

        image_path = tmp_path / "remove-from-gallery.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )
        db.add_tags(image_id, [
            {"tag": "kept_file", "confidence": 0.95},
        ])

        response = test_client.post(
            "/api/images/remove-selected",
            json={"image_ids": [image_id, 999999]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["removed"] == 1
        assert payload["missing_ids"] == [999999]
        assert payload["permanent_delete"] is False
        assert image_path.exists()
        assert db.get_image_by_id(image_id) is None
        assert db.get_image_tags(image_id) == []


class TestDeleteSelectedImages:
    """Tests for POST /api/images/delete-selected endpoint."""

    def test_delete_selected_images_requires_explicit_confirmation(self, test_client, test_db, tmp_path):
        import database as db
        from PIL import Image

        image_path = tmp_path / "delete-confirm.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": False},
        )

        assert response.status_code == 400
        error_text = response.json().get("detail") or response.json().get("error") or ""
        assert "explicit confirmation" in error_text
        assert image_path.exists()
        assert db.get_image_by_id(image_id) is not None

    def test_delete_selected_images_moves_files_to_trash_and_removes_database_rows(self, test_client, test_db, tmp_path, monkeypatch):
        import database as db
        from PIL import Image
        from services import image_service

        trashed_paths = []

        def fake_move_file_to_trash(path):
            trashed_paths.append(Path(path))
            Path(path).unlink()

        monkeypatch.setattr(image_service, "move_file_to_trash", fake_move_file_to_trash)

        image_path = tmp_path / "delete-success.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": True},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] == 1
        assert payload["failed"] == []
        assert payload["permanent_delete"] is False
        assert payload["trash_used"] is True
        assert trashed_paths == [image_path]
        assert not image_path.exists()
        assert db.get_image_by_id(image_id) is None

    def test_delete_selected_start_job_trashes_files_and_completes(self, test_client, test_db, tmp_path, monkeypatch):
        """v3.3.2 Phase-1: the background delete job trashes files, removes DB
        rows, and reports a terminal 'done' progress payload mirroring the
        synchronous endpoint's deleted/failed shape. TestClient runs the
        BackgroundTask synchronously after the response, so the job is terminal
        by the time we poll /progress."""
        import database as db
        from PIL import Image
        from services import image_service

        trashed_paths = []

        def fake_move_file_to_trash(path):
            trashed_paths.append(Path(path))
            Path(path).unlink()

        monkeypatch.setattr(image_service, "move_file_to_trash", fake_move_file_to_trash)

        image_path = tmp_path / "delete-job.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        start = test_client.post(
            "/api/images/delete-selected/start",
            json={"image_ids": [image_id], "confirm_delete_files": True},
        )
        assert start.status_code == 200
        assert start.json().get("status") == "started"

        progress = test_client.get("/api/images/delete-selected/progress").json()
        assert progress["status"] == "done"
        assert progress["total"] == 1
        assert progress["deleted"] == 1
        assert progress["failed"] == []

        assert trashed_paths == [image_path]
        assert not image_path.exists()
        assert db.get_image_by_id(image_id) is None

    def test_delete_selected_start_job_requires_explicit_confirmation(self, test_client, test_db, tmp_path):
        """v3.3.2 Phase-1: the background delete job refuses to start without
        explicit file-deletion confirmation, just like the sync endpoint."""
        import database as db
        from PIL import Image

        image_path = tmp_path / "delete-job-confirm.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/images/delete-selected/start",
            json={"image_ids": [image_id], "confirm_delete_files": False},
        )

        assert response.status_code == 400
        assert image_path.exists()
        assert db.get_image_by_id(image_id) is not None

    def test_delete_selected_images_reports_partial_failures_without_deleting_db_rows(self, test_client, test_db, tmp_path, monkeypatch):
        import database as db
        from PIL import Image
        from services import image_service

        def fake_move_file_to_trash(path):
            Path(path).unlink()

        monkeypatch.setattr(image_service, "move_file_to_trash", fake_move_file_to_trash)

        existing_path = tmp_path / "delete-partial-existing.png"
        missing_path = tmp_path / "delete-partial-missing.png"
        Image.new("RGB", (8, 8), color="white").save(existing_path)

        existing_id = db.add_image(
            path=str(existing_path),
            filename=existing_path.name,
            metadata_json="{}",
        )
        missing_id = db.add_image(
            path=str(missing_path),
            filename=missing_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [existing_id, missing_id], "confirm_delete_files": True},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] == 1
        assert payload["permanent_delete"] is False
        assert payload["trash_used"] is True
        assert len(payload["failed"]) == 1
        assert payload["failed"][0]["image_id"] == missing_id
        assert payload["failed"][0]["filename"] == missing_path.name
        assert "not found on disk" in payload["failed"][0]["error"].lower()
        assert not existing_path.exists()
        assert db.get_image_by_id(existing_id) is None
        assert db.get_image_by_id(missing_id) is not None

    def test_delete_selected_images_does_not_permanently_delete_when_trash_fails(self, test_client, test_db, tmp_path, monkeypatch):
        import database as db
        from PIL import Image
        from services import image_service

        image_path = tmp_path / "delete-trash-fails.png"
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        def fail_move_file_to_trash(path):
            raise RuntimeError("trash unavailable")

        monkeypatch.setattr(image_service, "move_file_to_trash", fail_move_file_to_trash)

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": True},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] == 0
        assert payload["permanent_delete"] is False
        assert payload["trash_used"] is True
        assert len(payload["failed"]) == 1
        assert "trash unavailable" in payload["failed"][0]["error"]
        assert image_path.exists()
        assert db.get_image_by_id(image_id) is not None


class TestReparseImage:
    """Tests for POST /api/images/{image_id}/reparse endpoint."""

    def test_reparse_nonexistent_image(self, test_client):
        """Reparsing nonexistent image should return 404."""
        response = test_client.post("/api/images/999999/reparse")

        assert response.status_code == 404

    def test_reparse_missing_file(self, test_client, test_db):
        """Reparsing image with missing file should return 404."""
        import database as db

        image_id = db.add_image(
            path="/nonexistent/reparse.png",
            filename="reparse.png",
        )

        response = test_client.post(f"/api/images/{image_id}/reparse")

        # File doesn't exist, should fail with 404
        assert response.status_code == 404


class TestExportSelectionDataAdvanced:
    """Tests for selected-image prompt/tag export data (advanced scenarios)."""

    def test_export_selection_data_token_respects_prompt_contains_mode(self, test_client, test_db):
        """Token export preview should not fall back to exact prompt matching."""
        expected_ids = []
        for value in [
            "takamatsu_tomori",
            "takamatsu_tomori(bang dream!)",
            "takamatsu_tomori(bang dream!!!!!its mygo)",
        ]:
            expected_ids.append(
                test_client.test_db.add_image(
                    path=f"/test/export_token_contains_{len(expected_ids)}.png",
                    filename=f"export_token_contains_{len(expected_ids)}.png",
                    prompt=f"{value}, 1girl",
                )
            )

        token_response = test_client.post("/api/images/selection-token", json={
            "prompts": ["takamatsu_tomori"],
            "promptMatchMode": "contains",
            "sortBy": "oldest",
        })
        assert token_response.status_code == 200

        response = test_client.post("/api/images/export-data", json={
            "selection_token": token_response.json()["selection_token"],
            "offset": 1,
            "limit": 2,
        })

        assert response.status_code == 200
        data = response.json()
        assert [image["id"] for image in data["images"]] == expected_ids[1:]
        assert data["exact_total"] is True

    def test_export_data_includes_sd_negative_params_and_caption(self, test_client, tmp_path: Path):
        """Export previews should expose enough SD data for Pro prompt/caption workflows."""
        import database as db

        image_path = tmp_path / "pro_export.png"
        Image.new("RGB", (32, 32), "white").save(image_path)
        metadata_json = json.dumps({
            "_parsed": {
                "generation_params": {
                    "steps": 28,
                    "sampler": "DPM++ 2M",
                    "cfg_scale": 7.5,
                    "seed": 12345,
                    "size": "832x1216",
                    "model": "ponyDiffusionV6XL.safetensors",
                }
            }
        })
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            generator="webui",
            prompt="masterpiece, 1girl",
            negative_prompt="lowres, bad anatomy",
            checkpoint="ponyDiffusionV6XL.safetensors",
            width=832,
            height=1216,
            metadata_json=metadata_json,
        )
        with db.get_db() as conn:
            conn.execute("UPDATE images SET ai_caption = ? WHERE id = ?", ("anime girl standing", image_id))
        db.add_tags(image_id, [{"tag": "solo", "confidence": 0.9}])

        response = test_client.post("/api/images/export-data", json={"image_ids": [image_id]})

        assert response.status_code == 200
        data = response.json()
        image = data["images"][0]
        assert image["negative_prompt"] == "lowres, bad anatomy"
        assert image["generation_params"]["steps"] == 28
        assert image["generation_params"]["sampler"] == "DPM++ 2M"
        assert image["generation_params"]["model"] == "ponyDiffusionV6XL.safetensors"
        assert image["ai_caption"] == "anime girl standing"
        assert image["tags"] == ["solo"]


class TestUtilityImageEndpoints:
    """Tests for upload parsing and file explorer helpers."""

    def test_parse_uploaded_image_returns_metadata(self, test_client, mock_comfyui_image):
        with open(mock_comfyui_image, "rb") as handle:
            response = test_client.post(
                "/api/parse-image",
                files={"file": ("comfyui_image.png", handle, "image/png")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["generator"] == "comfyui"
        assert data["checkpoint"]
        assert isinstance(data["loras"], list)
        assert data["width"] == 1024
        assert data["height"] == 768

    def test_parse_uploaded_image_returns_temp_source_path_for_followup_save(self, test_client, mock_comfyui_image):
        with open(mock_comfyui_image, "rb") as handle:
            response = test_client.post(
                "/api/parse-image",
                files={"file": ("comfyui_image.png", handle, "image/png")},
            )

        assert response.status_code == 200
        data = response.json()
        temp_source_path = data.get("source_temp_path")
        assert temp_source_path
        assert Path(temp_source_path).exists()
        assert temp_source_path.lower().endswith(".png")

    def test_parse_uploaded_image_rejects_truncated_png(self, test_client, tmp_path):
        truncated_path = tmp_path / "truncated.png"
        Image.new("RGB", (64, 64), color="white").save(truncated_path)
        payload = truncated_path.read_bytes()
        truncated_path.write_bytes(payload[: max(1, len(payload) // 2)])

        with open(truncated_path, "rb") as handle:
            response = test_client.post(
                "/api/parse-image",
                files={"file": ("truncated.png", handle, "image/png")},
            )

        assert response.status_code == 422
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "parse" in detail.lower() or "image" in detail.lower()
        assert re.search(r"[A-Za-z]:\\", detail) is None, detail

    def test_parse_uploaded_image_rejects_oversized_upload(self, test_client, monkeypatch):
        from routers import images as images_router

        monkeypatch.setattr(images_router, "PARSE_IMAGE_UPLOAD_MAX_BYTES", 1024)

        response = test_client.post(
            "/api/parse-image",
            files={"file": ("too-large.png", b"x" * 1025, "image/png")},
        )

        assert response.status_code == 413
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "too large" in detail.lower()


class TestImageMetadataEditor:
    """Tests for POST /api/image-metadata/save-edited endpoint."""

    def test_reader_metadata_edit_saves_new_png_without_overwriting(self, test_client, tmp_path):
        from metadata_parser import parse_image

        source = tmp_path / "source.png"
        output = tmp_path / "source.metadata-edited.png"
        Image.new("RGBA", (16, 16), (255, 255, 255, 180)).save(source)

        response = test_client.post("/api/image-metadata/save-edited", json={
            "source_path": str(source),
            "output_path": str(output),
            "format": "png",
            "metadata": {
                "prompt": "cat",
                "negative_prompt": "bad anatomy",
                "seed": "123",
                "sampler": "Euler a",
                "steps": 28,
                "cfg_scale": 7.0,
                "model": "fooModel.safetensors",
                "size": "16x16",
                "loras": "detail_tweaker, add_detail",
            },
            "allow_overwrite": False,
        })

        assert response.status_code == 200
        payload = response.json()
        assert output.exists()
        assert payload["output_path"] == str(output.resolve())
        assert payload["format"] == "png"
        assert payload["warnings"] == []

        with Image.open(output) as saved:
            parameters = saved.info.get("parameters") or ""
            assert "cat" in parameters
            assert "Negative prompt: bad anatomy" in parameters
            assert "Steps: 28" in parameters

        reparsed = parse_image(str(output))
        assert reparsed["prompt"] == "cat"
        assert reparsed["negative_prompt"] == "bad anatomy"
        assert reparsed["checkpoint"] == "fooModel.safetensors"
        assert reparsed["loras"] == ["detail_tweaker", "add_detail"]

    def test_reader_metadata_edit_refuses_existing_output_without_confirmation(self, test_client, tmp_path):
        source = tmp_path / "source.png"
        output = tmp_path / "existing.png"
        Image.new("RGB", (16, 16), "white").save(source)
        Image.new("RGB", (16, 16), "black").save(output)

        response = test_client.post("/api/image-metadata/save-edited", json={
            "source_path": str(source),
            "output_path": str(output),
            "format": "png",
            "metadata": {"prompt": "cat"},
            "allow_overwrite": False,
        })

        assert response.status_code == 409
        detail = response.json().get("detail") or response.json().get("error") or response.json().get("message") or ""
        assert "already exists" in detail.lower()

    def test_reader_metadata_edit_refuses_same_path_without_confirmation(self, test_client, tmp_path):
        source = tmp_path / "same-path-source.png"
        Image.new("RGB", (16, 16), "white").save(source)

        response = test_client.post("/api/image-metadata/save-edited", json={
            "source_path": str(source),
            "output_path": str(source),
            "format": "png",
            "metadata": {"prompt": "cat"},
            "allow_overwrite": False,
        })

        assert response.status_code == 409
        detail = response.json().get("detail") or response.json().get("error") or response.json().get("message") or ""
        assert "same as the source" in detail.lower() or "overwrite" in detail.lower()

    def test_reader_metadata_edit_same_path_overwrite_refreshes_index_without_clearing_derived_state(
        self,
        test_client,
        test_db,
        tmp_path,
    ):
        import database as db

        source = tmp_path / "same-path-refresh.png"
        Image.new("RGBA", (16, 16), (255, 255, 255, 180)).save(source)

        original_created_at = datetime(2024, 1, 2, 3, 4, 5)
        image_id = db.add_image(
            path=str(source),
            filename=source.name,
            generator="unknown",
            prompt="before overwrite",
            metadata_json="{}",
            width=16,
            height=16,
            file_size=source.stat().st_size,
            created_at=original_created_at,
        )
        db.add_tags(image_id, [{"tag": "kept_tag", "confidence": 0.88}])
        with db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET tagged_at = ?, ai_caption = ?, aesthetic_score = ?, embedding = ?
                WHERE id = ?
                """,
                ("2024-02-03 04:05:06", "keep caption", 6.5, b"\xaa\xbb\xcc", image_id),
            )

        response = test_client.post("/api/image-metadata/save-edited", json={
            "source_path": str(source),
            "output_path": str(source),
            "format": "png",
            "metadata": {
                "prompt": "cat",
                "negative_prompt": "bad anatomy",
                "steps": 28,
                "cfg_scale": 7.0,
                "model": "fooModel.safetensors",
                "size": "16x16",
                "loras": "detail_tweaker, add_detail",
            },
            "allow_overwrite": True,
        })

        assert response.status_code == 200
        image = db.get_image_by_id(image_id)
        assert image["prompt"] == "cat"
        assert image["negative_prompt"] == "bad anatomy"
        assert image["checkpoint"] == "fooModel.safetensors"
        assert image["checkpoint_normalized"] == "fooModel"
        assert str(image["library_order_time"]) == original_created_at.strftime("%Y-%m-%d %H:%M:%S")
        assert str(image["created_at"]) == original_created_at.strftime("%Y-%m-%d %H:%M:%S")
        assert {tag["tag"] for tag in db.get_image_tags(image_id)} == {"kept_tag"}

        with db.get_db() as conn:
            row = conn.execute(
                """
                SELECT tagged_at, ai_caption, aesthetic_score, embedding, source_mtime_ns, source_size
                FROM images
                WHERE id = ?
                """,
                (image_id,),
            ).fetchone()

        assert row["tagged_at"] == "2024-02-03 04:05:06"
        assert row["ai_caption"] == "keep caption"
        assert row["aesthetic_score"] == pytest.approx(6.5)
        assert row["embedding"] == b"\xaa\xbb\xcc"
        assert row["source_mtime_ns"] == source.stat().st_mtime_ns
        assert row["source_size"] == source.stat().st_size

    def test_open_folder_selects_existing_image(self, test_client, tmp_path, monkeypatch):
        import database as db
        from routers import images as images_router

        image_path = tmp_path / "open-folder-test.png"
        Image.new("RGB", (32, 32), color="purple").save(image_path)
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        calls = []
        monkeypatch.setattr(images_router.sys, "platform", "win32")
        monkeypatch.setattr(images_router.subprocess, "Popen", lambda args: calls.append(args) or MagicMock())

        response = test_client.post("/api/open-folder", json={"image_id": image_id})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert calls
        assert calls[0][0] == "explorer"

    def test_open_folder_requires_image_id(self, test_client):
        response = test_client.post("/api/open-folder", json={})

        assert response.status_code == 400


class TestFolderFilter:
    """v3.3.2 Library Navigation: GET /api/images?folder= scopes the gallery to a folder subtree.

    Files must exist on disk because ImageService._filter_and_mark_missing_images
    drops rows whose paths no longer resolve, so each test builds a real nested
    folder tree under tmp_path.
    """

    def _seed_nested_library(self, tmp_path):
        from PIL import Image
        import database as db

        layout = {
            "alpha_root": tmp_path / "alpha" / "a1.png",
            "alpha_sub": tmp_path / "alpha" / "sub" / "a2.png",
            "beta_root": tmp_path / "beta" / "b1.png",
        }
        ids = {}
        for key, path in layout.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), "white").save(path)
            ids[key] = db.add_image(path=str(path), filename=path.name, metadata_json="{}")
        return ids, layout

    def test_folder_filter_returns_recursive_subtree(self, test_client, tmp_path):
        _ids, layout = self._seed_nested_library(tmp_path)

        response = test_client.get("/api/images", params={"folder": str(tmp_path / "alpha")})

        assert response.status_code == 200
        data = response.json()
        returned = {img["path"] for img in data["images"]}
        assert returned == {str(layout["alpha_root"]), str(layout["alpha_sub"])}
        assert data["total"] == 2

    def test_folder_filter_scopes_to_sibling(self, test_client, tmp_path):
        _ids, layout = self._seed_nested_library(tmp_path)

        response = test_client.get("/api/images", params={"folder": str(tmp_path / "beta")})

        assert response.status_code == 200
        data = response.json()
        returned = {img["path"] for img in data["images"]}
        assert returned == {str(layout["beta_root"])}
        assert data["total"] == 1

    def test_no_folder_returns_all(self, test_client, tmp_path):
        self._seed_nested_library(tmp_path)

        response = test_client.get("/api/images")

        assert response.status_code == 200
        assert response.json()["total"] == 3

    def test_folder_filter_composes_with_generator(self, test_client, tmp_path):
        from PIL import Image
        import database as db

        alpha = tmp_path / "alpha"
        alpha.mkdir(parents=True, exist_ok=True)
        comfy_path = alpha / "comfy.png"
        nai_path = alpha / "nai.png"
        Image.new("RGB", (16, 16), "white").save(comfy_path)
        Image.new("RGB", (16, 16), "white").save(nai_path)
        db.add_image(path=str(comfy_path), filename="comfy.png", generator="comfyui", metadata_json="{}")
        db.add_image(path=str(nai_path), filename="nai.png", generator="nai", metadata_json="{}")

        response = test_client.get(
            "/api/images", params={"folder": str(alpha), "generators": "comfyui"}
        )

        assert response.status_code == 200
        data = response.json()
        assert {img["path"] for img in data["images"]} == {str(comfy_path)}
        assert data["total"] == 1


class TestLibraryFolders:
    """v3.3.2 Library Navigation: GET /api/folders lists distinct image directories for the tree."""

    def test_lists_distinct_image_directories(self, test_client, tmp_path):
        from PIL import Image
        import database as db

        paths = [
            tmp_path / "alpha" / "a1.png",
            tmp_path / "alpha" / "sub" / "a2.png",
            tmp_path / "beta" / "b1.png",
        ]
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), "white").save(p)
            db.add_image(path=str(p), filename=p.name, metadata_json="{}")

        response = test_client.get("/api/folders")

        assert response.status_code == 200
        folders = set(response.json()["folders"])

        def norm(path):
            return str(path).replace("\\", "/")

        assert folders == {
            norm(tmp_path / "alpha"),
            norm(tmp_path / "alpha" / "sub"),
            norm(tmp_path / "beta"),
        }

    def test_folders_empty_when_no_images(self, test_client, test_db):
        response = test_client.get("/api/folders")
        assert response.status_code == 200
        assert response.json()["folders"] == []


class TestLibraryRootsEndpoint:
    """v3.3.2 Library Navigation: GET /api/library-roots lists registered roots with counts."""

    def test_lists_roots_with_recursive_image_counts(self, test_client, tmp_path):
        from PIL import Image
        import database as db

        root = tmp_path / "lib"
        sub = root / "sub"
        sub.mkdir(parents=True, exist_ok=True)
        for p in [root / "a.png", sub / "b.png"]:
            Image.new("RGB", (16, 16), "white").save(p)
            db.add_image(path=str(p), filename=p.name, metadata_json="{}")
        db.add_library_root(str(root), label="Lib")

        resp = test_client.get("/api/library-roots")
        assert resp.status_code == 200
        roots = resp.json()["roots"]
        assert len(roots) == 1
        assert roots[0]["label"] == "Lib"
        assert roots[0]["image_count"] == 2  # recursive: a.png + sub/b.png

    def test_empty_when_no_roots(self, test_client, test_db):
        resp = test_client.get("/api/library-roots")
        assert resp.status_code == 200
        assert resp.json()["roots"] == []

    def test_exists_uses_runtime_path_without_rewriting_response(
        self,
        test_client,
        test_db,
        monkeypatch,
    ):
        import os
        import database as db

        if os.name == "nt":
            registered = db.add_library_root("/mnt/l/Library/Images")
            runtime_path = r"L:\Library\Images"
        else:
            registered = db.add_library_root(r"L:\Library\Images")
            runtime_path = "/mnt/l/Library/Images"
        assert registered is not None
        checked_paths = []

        def fake_isdir(path):
            checked_paths.append(path)
            return path == runtime_path

        monkeypatch.setattr("services.image.gallery.os.path.isdir", fake_isdir)

        response = test_client.get("/api/library-roots")

        assert response.status_code == 200
        root = response.json()["roots"][0]
        assert root["path"] == registered["path"]
        assert root["exists"] is True
        assert checked_paths == [runtime_path]

    def test_count_database_failure_is_not_reported_as_zero(
        self,
        test_client,
        test_db,
    ):
        import database as db

        root = db.add_library_root("/library/failing-root", label="Failing")
        assert root is not None
        with db.get_db() as conn:
            conn.execute("ALTER TABLE images RENAME TO images_count_failure")

        try:
            response = test_client.get("/api/library-roots")
        finally:
            with db.get_db() as conn:
                conn.execute("ALTER TABLE images_count_failure RENAME TO images")

        assert response.status_code == 500
        payload = response.json()
        assert payload["code"] == "library_root_count_failed"
        assert payload["root_path"] == "/library/failing-root"
        assert payload["status_code"] == 500
        assert payload["type"] == "HTTPException"
        assert payload["error"] == payload["message"]
        assert "OperationalError: no such table: images" in payload["database_error"]
        assert "/library/failing-root" in payload["message"]
        assert "Restart SD Image Sorter" in payload["message"]
        assert "Support Diagnostics" in payload["message"]
        assert "compatible database backup" in payload["message"]
        assert "roots" not in payload


class TestSelectionFolderScope:
    """v3.3.2 Library Navigation: selection tokens (select-all) must respect the folder scope."""

    def test_selection_token_respects_folder(self, test_client, tmp_path):
        from PIL import Image
        import database as db

        layout = {
            "a1": tmp_path / "alpha" / "a1.png",
            "a2": tmp_path / "alpha" / "sub" / "a2.png",
            "b1": tmp_path / "beta" / "b1.png",
        }
        ids = {}
        for key, p in layout.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), "white").save(p)
            ids[key] = db.add_image(path=str(p), filename=p.name, metadata_json="{}")

        token_resp = test_client.post(
            "/api/images/selection-token", json={"folder": str(tmp_path / "alpha")}
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        assert token_data["total_estimate"] == 2

        chunk_resp = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token_data["selection_token"], "limit": 100},
        )
        assert chunk_resp.status_code == 200
        assert set(chunk_resp.json()["image_ids"]) == {ids["a1"], ids["a2"]}


class TestEdgeCases:
    """Edge case tests for image endpoints."""

    def test_empty_database(self, test_client, test_db):
        """Empty database should return empty list."""
        response = test_client.get("/api/images")

        assert response.status_code == 200
        data = response.json()
        assert data["images"] == []
        assert data["total"] == 0

    def test_limit_zero_returns_all(self, test_client, test_db_with_images):
        """Limit 0 should be rejected with validation error."""
        response = test_client.get("/api/images?limit=0")

        # Should fail validation since limit must be >= 1 (ge=1 constraint)
        # Returns 400 for business logic validation
        assert response.status_code in [400, 422]

    def test_negative_offset(self, test_client, test_db_with_images):
        """Negative offset should now be rejected (v3.2.2: API contract tightened)."""
        response = test_client.get("/api/images?offset=-1")

        # The API now enforces offset >= 0 (was silently treated as 0 before).
        # Either FastAPI 422 or the project's translated 400 is acceptable.
        assert response.status_code in [400, 422]
        body = response.json()
        # Confirm the error names the offset field for clear UX.
        if isinstance(body, dict):
            payload = body.get("details") or body.get("detail") or body
            assert "offset" in str(payload).lower()

    def test_special_characters_in_search(self, test_client, test_db_with_images):
        """Special characters in search should be handled."""
        # Characters that could cause SQL issues
        special_chars = ["'", '"', ";", "%", "_"]

        for char in special_chars:
            response = test_client.get(f"/api/images?search={char}")
            # Should not cause server error
            assert response.status_code in [200, 422]

    def test_unicode_in_search(self, test_client, test_db_with_images):
        """Unicode characters in search should work."""
        response = test_client.get("/api/images?search=anime")

        assert response.status_code == 200

    def test_very_long_search_query(self, test_client, test_db_with_images):
        """Very long search query should be handled."""
        long_query = "a" * 1000
        response = test_client.get(f"/api/images?search={long_query}")

        # Should not crash
        assert response.status_code in [200, 422]


class TestSecurityHeaders:
    """Security-related tests for image endpoints."""

    def test_no_sql_injection_via_path(self, test_client, test_db):
        """SQL injection via path parameter should be blocked."""
        # Try SQL injection in image_id
        response = test_client.get("/api/images/1; DROP TABLE images; --")

        # Should return 400 (bad request) or 422 (validation error - not a valid integer)
        assert response.status_code in [400, 422]

    def test_no_path_traversal_in_image_id(self, test_client):
        """Path traversal via image_id should be blocked."""
        response = test_client.get("/api/images/../../../etc/passwd")

        # Should return validation error (not a valid integer path parameter)
        # FastAPI returns 404 for path that doesn't match route pattern
        assert response.status_code in [404, 422]

    def test_content_type_on_file_response(self, test_client, test_db, tmp_path):
        """Image file responses should have correct content type."""
        import database as db
        from PIL import Image

        # Create PNG
        img_path = tmp_path / "content_type.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        image_id = db.add_image(path=str(img_path), filename="content_type.png")

        response = test_client.get(f"/api/image-file/{image_id}")

        assert response.status_code == 200
        # Should have an image content type
        content_type = response.headers.get("content-type", "")
        assert "image/" in content_type or content_type == "application/octet-stream"


class TestBulkJobEndpoints:
    """Debt-22: durable-id background bulk jobs (delete / remove / export)."""

    def _add_image(self, tmp_path, name):
        import database as db
        from PIL import Image

        image_path = tmp_path / name
        Image.new("RGB", (8, 8), color="white").save(image_path)
        image_id = db.add_image(path=str(image_path), filename=image_path.name, metadata_json="{}")
        return image_id, image_path

    def test_delete_selected_background_returns_job_envelope_and_completes(
        self, test_client, test_db, tmp_path, monkeypatch
    ):
        import database as db
        from services import image_service

        trashed = []

        def fake_move_file_to_trash(path):
            trashed.append(Path(path))
            Path(path).unlink()

        monkeypatch.setattr(image_service, "move_file_to_trash", fake_move_file_to_trash)
        image_id, image_path = self._add_image(tmp_path, "bg-delete.png")

        start = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": True, "background": True},
        )
        assert start.status_code == 200
        envelope = start.json()
        assert envelope["kind"] == "delete_files"
        assert envelope["operation"] == "delete"
        job_id = envelope["id"]
        assert envelope["job_id"] == job_id

        # TestClient runs the BackgroundTask synchronously, so the job is
        # terminal by the time we poll the durable status endpoint.
        job = test_client.get(f"/api/bulk-jobs/{job_id}").json()
        assert job["status"] == "done"
        assert job["total"] == 1
        assert job["processed"] == 1
        assert job["result"] == {"deleted": 1}
        assert trashed == [image_path]
        assert db.get_image_by_id(image_id) is None

    def test_remove_selected_background_returns_job_envelope_and_keeps_files(
        self, test_client, test_db, tmp_path
    ):
        import database as db

        image_id, image_path = self._add_image(tmp_path, "bg-remove.png")

        start = test_client.post(
            "/api/images/remove-selected",
            json={"image_ids": [image_id], "background": True},
        )
        assert start.status_code == 200
        envelope = start.json()
        assert envelope["kind"] == "remove_from_gallery"
        job_id = envelope["id"]

        job = test_client.get(f"/api/bulk-jobs/{job_id}").json()
        assert job["status"] == "done"
        assert job["result"]["removed"] == 1
        assert image_path.exists()  # remove keeps files
        assert db.get_image_by_id(image_id) is None

    def test_delete_selected_background_still_requires_confirmation(self, test_client, test_db, tmp_path):
        import database as db

        image_id, image_path = self._add_image(tmp_path, "bg-delete-confirm.png")

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": False, "background": True},
        )
        assert response.status_code == 400
        assert image_path.exists()
        assert db.get_image_by_id(image_id) is not None

    def test_sync_delete_path_unchanged_without_background(self, test_client, test_db, tmp_path, monkeypatch):
        """Small selections (no background flag) keep the synchronous response
        shape with no job envelope."""
        import database as db
        from services import image_service

        monkeypatch.setattr(image_service, "move_file_to_trash", lambda p: Path(p).unlink())
        image_id, _ = self._add_image(tmp_path, "sync-delete.png")

        response = test_client.post(
            "/api/images/delete-selected",
            json={"image_ids": [image_id], "confirm_delete_files": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] == 1
        assert payload["trash_used"] is True
        assert "job_id" not in payload  # sync path, not a background job envelope
        assert db.get_image_by_id(image_id) is None

    def test_export_batch_background_writes_sidecars_and_completes(
        self, test_client, test_db_with_images, tmp_path
    ):
        image_ids = test_db_with_images["image_ids"]
        output_folder = tmp_path / "sidecars"

        start = test_client.post(
            "/api/tags/export-batch",
            json={
                "image_ids": image_ids[:2],
                "output_folder": str(output_folder),
                "output_mode": "folder",
                "content_mode": "tags",
                "background": True,
            },
        )
        assert start.status_code == 200
        envelope = start.json()
        assert envelope["kind"] == "export_sidecars"
        job_id = envelope["id"]

        job = test_client.get(f"/api/bulk-jobs/{job_id}").json()
        assert job["status"] == "done"
        assert job["result"]["exported"] >= 1
        written = list(output_folder.glob("*.txt"))
        assert len(written) >= 1

    def test_bulk_jobs_list_contains_started_job(self, test_client, test_db, tmp_path):
        image_id, _ = self._add_image(tmp_path, "bg-list.png")
        start = test_client.post(
            "/api/images/remove-selected",
            json={"image_ids": [image_id], "background": True},
        )
        job_id = start.json()["id"]

        listing = test_client.get("/api/bulk-jobs").json()
        assert any(job["id"] == job_id for job in listing["jobs"])

    def test_get_unknown_bulk_job_returns_404(self, test_client, test_db):
        response = test_client.get("/api/bulk-jobs/does-not-exist")
        assert response.status_code == 404

    def test_cancel_unknown_bulk_job_returns_404(self, test_client, test_db):
        response = test_client.post("/api/bulk-jobs/does-not-exist/cancel")
        assert response.status_code == 404


def _set_image_columns(db, image_id, **cols):
    """Directly set columns (ai_caption/nl_caption/aesthetic_score/color_saturation)
    that add_image does not expose, mirroring the direct-SQL pattern in test_database.py."""
    if not cols:
        return
    assignments = ", ".join(f"{key} = ?" for key in cols)
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE images SET {assignments} WHERE id = ?",
            (*cols.values(), image_id),
        )


def _seed_meta(seed=None, noise_seed=None, extra=None):
    """Build a realistic compact metadata_json blob with the parsed seed path."""
    gen_params = {}
    if seed is not None:
        gen_params["seed"] = seed
    if noise_seed is not None:
        gen_params["noise_seed"] = noise_seed
    if extra:
        gen_params.update(extra)
    return json.dumps({
        "_compact": {"version": 1},
        "_parsed": {"version": 2, "generation_params": gen_params},
    })


class TestAuroraPhase3Filters:
    """Aurora Phase 3 gallery filters: no_caption, aesthetic_unscored,
    min/max_saturation, seed, plus the /api/images/count preview endpoint and
    selection-token composition."""

    def _add(self, db, filename, **kwargs):
        return db.add_image(
            path=f"/lib/{filename}",
            filename=filename,
            generator="comfyui",
            metadata_json=kwargs.pop("metadata_json", "{}"),
            **kwargs,
        )

    # ---- no_caption ---------------------------------------------------
    def test_no_caption_filters_to_uncaptioned(self, test_db):
        ai_only = self._add(test_db, "ai.png")
        _set_image_columns(test_db, ai_only, ai_caption="a cat sitting")
        nl_only = self._add(test_db, "nl.png")
        _set_image_columns(test_db, nl_only, nl_caption="natural language caption")
        self._add(test_db, "blank.png")  # both NULL -> uncaptioned

        assert test_db.get_filtered_image_count(no_caption=True) == 1
        page = test_db.get_images_paginated(no_caption=True, limit=50)
        assert [i["filename"] for i in page["images"]] == ["blank.png"]
        assert page["total"] == 1

    def test_no_caption_treats_empty_string_as_uncaptioned(self, test_db):
        blank = self._add(test_db, "empty.png")
        _set_image_columns(test_db, blank, ai_caption="", nl_caption="")
        assert test_db.get_filtered_image_count(no_caption=True) == 1

    def test_no_caption_none_is_noop(self, test_db):
        self._add(test_db, "x.png")
        captioned = self._add(test_db, "y.png")
        _set_image_columns(test_db, captioned, ai_caption="hi")
        assert test_db.get_filtered_image_count(no_caption=None) == 2

    # ---- aesthetic_unscored ------------------------------------------
    def test_aesthetic_unscored_matches_null_only(self, test_db):
        scored = self._add(test_db, "scored.png")
        _set_image_columns(test_db, scored, aesthetic_score=7.5)
        self._add(test_db, "unscored.png")  # NULL

        assert test_db.get_filtered_image_count(aesthetic_unscored=True) == 1
        page = test_db.get_images_paginated(aesthetic_unscored=True, limit=50)
        assert [i["filename"] for i in page["images"]] == ["unscored.png"]

    def test_aesthetic_unscored_takes_precedence_over_range(self, test_db):
        scored = self._add(test_db, "s.png")
        _set_image_columns(test_db, scored, aesthetic_score=8.0)
        self._add(test_db, "u.png")  # NULL score

        # A min/max range alone would drop the unscored row; unscored=True wins
        # and ignores the range entirely.
        assert test_db.get_filtered_image_count(
            aesthetic_unscored=True, min_aesthetic=5.0, max_aesthetic=9.0
        ) == 1
        page = test_db.get_images_paginated(
            aesthetic_unscored=True, min_aesthetic=5.0, max_aesthetic=9.0, limit=50
        )
        assert [i["filename"] for i in page["images"]] == ["u.png"]

    def test_aesthetic_range_still_works_without_unscored(self, test_db):
        low = self._add(test_db, "low.png"); _set_image_columns(test_db, low, aesthetic_score=2.0)
        high = self._add(test_db, "high.png"); _set_image_columns(test_db, high, aesthetic_score=8.0)
        self._add(test_db, "none.png")  # NULL excluded by a numeric bound
        assert test_db.get_filtered_image_count(min_aesthetic=5.0) == 1

    # ---- saturation ---------------------------------------------------
    def test_saturation_range(self, test_db):
        lo = self._add(test_db, "lo.png"); _set_image_columns(test_db, lo, color_saturation=10.0)
        mid = self._add(test_db, "mid.png"); _set_image_columns(test_db, mid, color_saturation=50.0)
        hi = self._add(test_db, "hi.png"); _set_image_columns(test_db, hi, color_saturation=200.0)
        self._add(test_db, "null.png")  # NULL saturation

        assert test_db.get_filtered_image_count(min_saturation=40, max_saturation=100) == 1
        page = test_db.get_images_paginated(min_saturation=40, max_saturation=100, limit=50)
        assert [i["filename"] for i in page["images"]] == ["mid.png"]

    def test_saturation_excludes_unanalyzed_rows(self, test_db):
        self._add(test_db, "n1.png")  # NULL
        analyzed = self._add(test_db, "a1.png")
        _set_image_columns(test_db, analyzed, color_saturation=5.0)
        # min_saturation=0 still requires a non-null value, so the NULL row is out.
        assert test_db.get_filtered_image_count(min_saturation=0) == 1

    # ---- seed ---------------------------------------------------------
    def test_seed_matches_webui_and_nai_shapes(self, test_db):
        webui = self._add(test_db, "webui.png",
                          metadata_json=_seed_meta(seed=987654321, extra={"steps": 30}))
        self._add(test_db, "nai.png", metadata_json=_seed_meta(seed=111222333))

        assert test_db.get_filtered_image_count(seed=987654321) == 1
        page = test_db.get_images_paginated(seed=987654321, limit=50)
        assert [i["filename"] for i in page["images"]] == ["webui.png"]
        assert test_db.get_filtered_image_count(seed=111222333) == 1

    def test_seed_matches_comfyui_noise_seed(self, test_db):
        self._add(test_db, "comfy.png", metadata_json=_seed_meta(noise_seed=424242))
        assert test_db.get_filtered_image_count(seed=424242) == 1
        page = test_db.get_images_paginated(seed=424242, limit=50)
        assert [i["filename"] for i in page["images"]] == ["comfy.png"]

    def test_seed_does_not_crash_on_invalid_or_empty_metadata(self, test_db):
        self._add(test_db, "bad.png", metadata_json="not json at all")
        self._add(test_db, "blank.png", metadata_json="")
        self._add(test_db, "good.png", metadata_json=_seed_meta(seed=555))
        # Must not raise on malformed rows, and only the good row matches.
        assert test_db.get_filtered_image_count(seed=555) == 1
        page = test_db.get_images_paginated(seed=555, limit=50)
        assert [i["filename"] for i in page["images"]] == ["good.png"]

    def test_seed_no_match_returns_zero(self, test_db):
        self._add(test_db, "s.png", metadata_json=_seed_meta(seed=1))
        assert test_db.get_filtered_image_count(seed=999999) == 0

    # ---- GET /api/images endpoint acceptance --------------------------
    @pytest.mark.parametrize("query", [
        "no_caption=true",
        "aesthetic_unscored=true",
        "min_saturation=10&max_saturation=200",
        "seed=12345",
    ])
    def test_get_images_endpoint_accepts_new_filters(self, test_client, query):
        resp = test_client.get(f"/api/images?{query}&limit=5")
        assert resp.status_code == 200, resp.text

    def test_get_images_endpoint_seed_filters_result(self, test_client, tmp_path):
        # The gallery listing drops rows whose files are missing on disk, so
        # these must be real files (unlike the db-layer / count / selection paths).
        db = test_client.test_db
        keep_path = tmp_path / "keep.png"
        drop_path = tmp_path / "drop.png"
        Image.new("RGB", (16, 16), "white").save(keep_path)
        Image.new("RGB", (16, 16), "white").save(drop_path)
        keep = db.add_image(path=str(keep_path), filename="keep.png",
                            metadata_json=_seed_meta(seed=314159))
        db.add_image(path=str(drop_path), filename="drop.png",
                     metadata_json=_seed_meta(seed=271828))
        resp = test_client.get("/api/images?seed=314159&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert [i["id"] for i in data["images"]] == [keep]

    # ---- /api/images/count -------------------------------------------
    def test_count_endpoint_exact_totals_with_filters(self, test_client):
        db = test_client.test_db
        db.add_image(path="/lib/c1.png", filename="c1.png", metadata_json="{}")
        db.add_image(path="/lib/c2.png", filename="c2.png", metadata_json="{}")
        captioned = db.add_image(path="/lib/c3.png", filename="c3.png", metadata_json="{}")
        _set_image_columns(db, captioned, ai_caption="captioned")

        assert test_client.get("/api/images/count").json()["total"] == 3
        assert test_client.get("/api/images/count?no_caption=true").json()["total"] == 2

    def test_count_endpoint_always_counts_no_sentinel(self, test_client):
        db = test_client.test_db
        for i in range(3):
            db.add_image(path=f"/lib/s{i}.png", filename=f"s{i}.png", metadata_json="{}")
        total = test_client.get("/api/images/count").json()["total"]
        assert total == 3  # never the -1 skip sentinel

    def test_count_route_not_shadowed_by_image_id(self, test_client):
        # /api/images/count must resolve to the count endpoint, not be captured
        # by GET /api/images/{image_id} (which would 422 on the non-int "count").
        resp = test_client.get("/api/images/count")
        assert resp.status_code == 200
        assert "total" in resp.json()

    def test_count_seed_filter_matches_images_endpoint(self, test_client):
        db = test_client.test_db
        db.add_image(path="/lib/x.png", filename="x.png", metadata_json=_seed_meta(seed=42))
        db.add_image(path="/lib/y.png", filename="y.png", metadata_json=_seed_meta(seed=7))
        assert test_client.get("/api/images/count?seed=42").json()["total"] == 1

    # ---- selection-token composition ----------------------------------
    def test_new_filters_compose_with_selection_token(self, test_client):
        db = test_client.test_db
        seeded = db.add_image(path="/lib/seeded.png", filename="seeded.png",
                              metadata_json=_seed_meta(seed=314159))
        db.add_image(path="/lib/other.png", filename="other.png",
                     metadata_json=_seed_meta(seed=271828))

        token_resp = test_client.post("/api/images/selection-token", json={"seed": 314159})
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        assert token_data["total_estimate"] == 1

        chunk = test_client.get(
            f"/api/images/selection-chunk?selection_token={token_data['selection_token']}"
        ).json()
        assert chunk["image_ids"] == [seeded]

    def test_new_filters_compose_with_selection_ids(self, test_client):
        db = test_client.test_db
        uncaptioned = db.add_image(path="/lib/u.png", filename="u.png", metadata_json="{}")
        captioned = db.add_image(path="/lib/c.png", filename="c.png", metadata_json="{}")
        _set_image_columns(db, captioned, nl_caption="has caption")

        resp = test_client.post("/api/images/selection-ids", json={"noCaption": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["image_ids"] == [uncaptioned]


class TestPipelineQueueEndpoint:
    """GET /api/tags/pipeline-queue read-only snapshot."""

    def test_returns_snapshot_shape(self, test_client):
        resp = test_client.get("/api/tags/pipeline-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) >= {"total_queued", "queued", "last_start_error"}
        assert isinstance(data["total_queued"], int)
        assert isinstance(data["queued"], list)

    def test_idle_queue_is_empty(self, test_client):
        data = test_client.get("/api/tags/pipeline-queue").json()
        assert data["total_queued"] == 0
        assert data["queued"] == []


def test_patch_image_caption_explicit_clear_semantics(test_client, tmp_path):
    """FE-3: caption PATCH writes only the fields present in the body."""
    import database as db

    image_path = tmp_path / "caption-edit.png"
    image_path.write_bytes(b"not a real image")
    image_id = db.add_image(path=str(image_path), filename=image_path.name)

    response = test_client.patch(f"/api/images/{image_id}/caption", json={
        "ai_caption": "1girl, smiling, park",
        "nl_caption": "A girl smiling in a park.",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_caption"] == "1girl, smiling, park"
    assert payload["nl_caption"] == "A girl smiling in a park."

    # Clearing NL alone must not touch ai_caption.
    response = test_client.patch(f"/api/images/{image_id}/caption", json={
        "nl_caption": "",
    })
    assert response.status_code == 200
    image = db.get_image_by_id(image_id)
    assert image["ai_caption"] == "1girl, smiling, park"
    assert image["nl_caption"] == ""

    # Empty body is a client error, not a silent no-op.
    response = test_client.patch(f"/api/images/{image_id}/caption", json={})
    assert response.status_code == 400

    # Unknown image id -> 404.
    response = test_client.patch("/api/images/999999/caption", json={
        "ai_caption": "x",
    })
    assert response.status_code == 404
