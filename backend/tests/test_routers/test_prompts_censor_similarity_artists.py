"""
Minimal route coverage for prompts, censor, similarity, and artists endpoints.

Focuses on high-value validation behavior and a few lightweight success-path
assertions that do not require heavy model execution.
"""
import base64
import io
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from fastapi import BackgroundTasks, HTTPException
from PIL import Image, ImageDraw

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestPromptsRouter:
    def test_list_categories_returns_builtin_fallback_when_library_is_empty(self, test_client):
        response = test_client.get("/api/prompts/categories")

        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        assert data["categories"]["outfit"]
        assert data["categories"]["pose"]
        assert data["categories"]["style"]

    def test_list_categories_orders_tags_by_frequency(self, test_client, monkeypatch):
        from routers import prompts as prompts_router

        class FakeGenerator:
            def get_tag_pool(self):
                return {
                    "style": [
                        {"tag": "low", "count": 1},
                        {"tag": "high", "count": 3},
                    ],
                    "pose": [
                        {"tag": "sit", "count": 2},
                        {"tag": "stand", "count": 5},
                    ],
                }

        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: FakeGenerator())

        response = test_client.get("/api/prompts/categories")

        assert response.status_code == 200
        data = response.json()
        assert data["categories"]["style"] == ["high", "low"]
        assert data["categories"]["pose"] == ["stand", "sit"]

    def test_get_missing_category_returns_404(self, test_client, monkeypatch):
        from routers import prompts as prompts_router

        class FakeGenerator:
            def get_tag_pool(self):
                return {"style": [{"tag": "high", "count": 3}]}

        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: FakeGenerator())

        response = test_client.get("/api/prompts/category/missing")

        assert response.status_code == 404

    def test_categorize_respects_user_recategorized_tags(self, test_client):
        recategorized = test_client.post(
            "/api/prompts/recategorize?tag=school_uniform&category=style",
            json={},
        )
        assert recategorized.status_code == 200

        response = test_client.post(
            "/api/prompts/categorize",
            json=["school uniform", "standing"],
        )

        assert response.status_code == 200
        results = {item["tag"]: item["category"] for item in response.json()["results"]}
        assert results["school uniform"] == "style"
        assert results["standing"] == "pose"

    def test_generate_prompt_uses_generator_output(self, test_client, monkeypatch):
        from routers import prompts as prompts_router

        class FakeGenerator:
            def generate(self, config):
                assert config["categories"]["style"]["tags"] == ["cinematic_lighting"]
                assert config["categories"]["pose"]["tags"] == ["standing"]
                assert config["tag_sets"] == ["Outfit Combo"]
                assert config["quality_preset"] == "none"
                assert config["count_tag"] == ""
                assert config["include_negative"] is False
                return {
                    "prompt": "1girl, masterpiece",
                    "negative_prompt": "lowres",
                    "seed": config["seed"],
                    "config": config,
                }

        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: FakeGenerator())

        response = test_client.post(
            "/api/prompts/generate",
            json={
                "seed": 12345,
                "include_negative": False,
                "quality_preset": "none",
                "count_tag": "",
                "tag_sets": ["Outfit Combo"],
                "categories": {
                    "style": {"tags": ["cinematic_lighting"], "weight": 1.0, "locked": True},
                    "pose": {"tags": ["standing"], "weight": 0.5, "locked": False},
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["prompt"] == "1girl, masterpiece"
        assert data["negative_prompt"] == "lowres"
        assert data["seed"] == 12345
        assert data["config"]["categories"]["style"]["locked"] is True

    def test_generate_prompt_honors_count_parameter(self, test_client, monkeypatch):
        """count=N must return N generated prompts, not silently one."""
        from routers import prompts as prompts_router

        class FakeGenerator:
            def __init__(self):
                self.calls = []

            def generate(self, config):
                self.calls.append(dict(config))
                return {
                    "positive_prompt": f"prompt-{len(self.calls)}",
                    "negative_prompt": "lowres",
                    "tags_used": [],
                    "exclusions_applied": [],
                    "warnings": [],
                }

        fake = FakeGenerator()
        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: fake)

        response = test_client.post(
            "/api/prompts/generate",
            json={"seed": 100, "count": 3, "count_tag": "", "quality_preset": "none"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert [item["positive_prompt"] for item in data["prompts"]] == [
            "prompt-1",
            "prompt-2",
            "prompt-3",
        ]
        # A fixed seed must still produce *distinct* prompts per slot, varied
        # deterministically so the same request reproduces the same batch.
        assert [call.get("seed") for call in fake.calls] == [100, 101, 102]
        # Single-prompt top-level contract stays intact for existing callers
        # (prompt-lab.js reads result.positive_prompt || result.prompt).
        assert data["positive_prompt"] == "prompt-1"
        assert data["prompt"] == "prompt-1"

    def test_generate_prompt_count_one_keeps_single_prompt_contract(self, test_client, monkeypatch):
        """Default count=1 performs exactly one generation and echoes it."""
        from routers import prompts as prompts_router

        class FakeGenerator:
            def __init__(self):
                self.calls = 0

            def generate(self, config):
                self.calls += 1
                return {
                    "positive_prompt": "solo prompt",
                    "negative_prompt": "",
                    "tags_used": [],
                    "exclusions_applied": [],
                    "warnings": [],
                }

        fake = FakeGenerator()
        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: fake)

        response = test_client.post(
            "/api/prompts/generate",
            json={"count_tag": "", "quality_preset": "none"},
        )

        assert response.status_code == 200
        data = response.json()
        assert fake.calls == 1
        assert data["count"] == 1
        assert len(data["prompts"]) == 1
        assert data["positive_prompt"] == "solo prompt"

    def test_validate_prompt_uses_generator_output(self, test_client, monkeypatch):
        from routers import prompts as prompts_router

        class FakeGenerator:
            def validate_prompt(self, tags):
                return {"valid": True, "violations": [], "suggestions": []}

        monkeypatch.setattr(prompts_router, "get_generator", lambda _db: FakeGenerator())

        response = test_client.post("/api/prompts/validate", json={"tags": ["1girl", "solo"]})

        assert response.status_code == 200
        data = response.json()
        assert data == {"valid": True, "violations": [], "suggestions": []}

    def test_prompt_stats_recipe_show_more_is_not_capped_by_checkpoint_limit(self, test_client, test_db):
        checkpoints = ["pl_recipe_alpha", "pl_recipe_beta", "pl_recipe_gamma"]
        for index, checkpoint in enumerate(checkpoints):
            image_id = test_db.add_image(
                path=f"/tmp/{checkpoint}.png",
                filename=f"{checkpoint}.png",
                metadata_json="{}",
                checkpoint=checkpoint,
                prompt=f"{checkpoint}_token, cinematic_lighting",
            )
            with test_db.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
                    (image_id, f"{checkpoint}_tag", 0.95 - index * 0.01),
                )

        response = test_client.get("/api/prompts/stats?checkpoint_limit=1&leader_limit=1&recipe_limit=2&tag_limit=2")

        assert response.status_code == 200
        data = response.json()
        assert len(data["checkpoint_recipes"]) == 2
        assert data["checkpoint_recipes_total"] == 3
        assert data["checkpoint_recipes_has_more"] is True
        assert data["top_checkpoints_total"] == 3
        assert data["top_checkpoints_has_more"] is True

    def test_prompt_stats_group_checkpoint_variants_by_normalized_name(self, test_client, test_db):
        for index in range(3):
            image_id = test_db.add_image(
                path=f"/tmp/rv51_variant_{index}.png",
                filename=f"rv51_variant_{index}.png",
                metadata_json="{}",
                checkpoint="RealisticVisionV51.safetensors [abc12345]" if index % 2 == 0 else "RealisticVisionV51.safetensors",
                prompt="cinematic portrait, studio lighting",
            )
            with test_db.get_db() as conn:
                conn.execute(
                    "UPDATE images SET aesthetic_score = ? WHERE id = ?",
                    (7.5 + index * 0.1, image_id),
                )
                conn.execute(
                    "INSERT INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
                    (image_id, "studio_lighting", 0.9),
                )

        response = test_client.get("/api/prompts/stats?checkpoint_limit=10&leader_limit=10&recipe_limit=10")

        assert response.status_code == 200
        data = response.json()
        assert any(item["name"] == "RealisticVisionV51" and item["count"] == 3 for item in data["top_checkpoints"])
        assert any(item["name"] == "RealisticVisionV51" and item["count"] == 3 for item in data["checkpoint_score_leaders"])
        assert any(recipe["name"] == "RealisticVisionV51" and "studio_lighting" in recipe["tags"] for recipe in data["checkpoint_recipes"])

    def test_top_scored_examples_skip_images_whose_file_is_gone(self, test_client, test_db):
        """Every example card renders a thumbnail and three actions keyed on the
        id, so an unreadable row hands the user a broken image and three
        buttons that cannot work."""
        gone = test_db.add_image(path="/tmp/pl-gone.png", filename="pl-gone.png", metadata_json="{}")
        kept = test_db.add_image(path="/tmp/pl-kept.png", filename="pl-kept.png", metadata_json="{}")
        with test_db.get_db() as conn:
            conn.execute("UPDATE images SET aesthetic_score = 9.5 WHERE id = ?", (gone,))
            conn.execute("UPDATE images SET aesthetic_score = 8.0 WHERE id = ?", (kept,))
            conn.execute(
                "UPDATE images SET is_readable = 0, read_error = 'missing' WHERE id = ?",
                (gone,),
            )

        response = test_client.get("/api/prompts/stats")

        assert response.status_code == 200
        data = response.json()
        assert [entry["id"] for entry in data["top_scored_images"]] == [kept]
        # The show-more affordance must not promise rows that can never render.
        assert data["top_scored_images_total"] == 1
        assert data["top_scored_images_has_more"] is False
        # The headline library stat still counts every scored row on record.
        assert data["scored_images"] == 2


class TestCensorRouterValidation:
    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ({"image_id": 1, "model_type": "invalid"}, 400),
            ({"image_id": 0, "model_type": "legacy"}, 400),
            ({"image_id": 1, "model_type": "legacy", "confidence_threshold": 1.1}, 400),
        ],
    )
    def test_detect_validation_rejects_invalid_payloads(self, test_client, payload, expected_status):
        response = test_client.post("/api/censor/detect", json=payload)

        assert response.status_code == expected_status

    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ({"image_id": 1, "regions": [[0, 0, 10, 10]], "style": "invalid"}, 400),
            ({"image_id": 1, "regions": [], "style": "mosaic"}, 400),
            ({"image_id": 1, "regions": [[0, 0, 10, 10]], "style": "mosaic", "block_size": 0}, 400),
        ],
    )
    def test_preview_validation_rejects_invalid_payloads(self, test_client, payload, expected_status):
        response = test_client.post("/api/censor/preview", json=payload)

        assert response.status_code == expected_status

    @pytest.mark.parametrize("style", ["black_bar", "white_bar", "solid"])
    def test_legacy_preview_accepts_current_bar_styles(self, test_client, test_db, tmp_path, style):
        image_path = tmp_path / f"censor-{style}.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/preview",
            json={
                "image_id": image_id,
                "regions": [[0, 0, 16, 16]],
                "style": style,
            },
        )

        assert response.status_code == 200
        assert response.json()["preview"].startswith("data:image/jpeg;base64,")

    def test_save_data_validation_rejects_invalid_metadata(self, test_client):
        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": "data:image/png;base64,AAAA",
                "filename": "image.png",
                "output_folder": "/tmp",
                "metadata_option": "invalid",
                "output_format": "gif",
            },
        )

        assert response.status_code == 400

    def test_save_data_accepts_minimal_metadata_with_jpg_output(self, test_client, tmp_path):
        from PIL import Image, PngImagePlugin

        source_path = tmp_path / "source.png"
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("prompt", "manual metadata test")
        Image.new("RGB", (32, 32), color="blue").save(source_path, pnginfo=pnginfo, dpi=(300, 300))

        image_id = test_client.test_db.add_image(
            path=str(source_path),
            filename="source.png",
            metadata_json="{}",
        )

        output_folder = tmp_path / "out"
        payload_image = base64.b64encode(source_path.read_bytes()).decode("ascii")

        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": f"data:image/png;base64,{payload_image}",
                "filename": "saved-image.png",
                "output_folder": str(output_folder),
                "metadata_option": "minimal",
                "output_format": "jpg",
                "original_image_id": image_id,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"].endswith(".jpg")
        assert (output_folder / data["filename"]).exists()

    def test_save_data_overwrite_refreshes_indexed_state(self, test_client, tmp_path):
        from image_fingerprint import compute_image_content_fingerprint

        source_path = tmp_path / "save-data-overwrite.png"
        Image.new("RGB", (32, 32), color="white").save(source_path)

        image_id = test_client.test_db.add_image(
            path=str(source_path),
            filename="save-data-overwrite.png",
            metadata_json="{}",
        )

        original_fingerprint = compute_image_content_fingerprint(str(source_path))
        test_client.test_db.add_tags(
            image_id,
            [{"tag": "stale_tag", "confidence": 0.9}],
            content_fingerprint=original_fingerprint,
        )
        with test_client.test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
                WHERE id = ?
                """,
                ("stale caption", 0.88, b"embedding", original_fingerprint, image_id),
            )
            conn.commit()

        edited_path = tmp_path / "save-data-updated.png"
        Image.new("RGB", (32, 32), color="black").save(edited_path)
        payload_image = base64.b64encode(edited_path.read_bytes()).decode("ascii")

        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": f"data:image/png;base64,{payload_image}",
                "filename": source_path.name,
                "output_folder": str(tmp_path),
                "metadata_option": "strip",
                "output_format": "png",
                "original_image_id": image_id,
                "allow_overwrite": True,
            },
        )

        assert response.status_code == 200
        assert Path(response.json()["output_path"]).resolve() == source_path.resolve()

        refreshed_fingerprint = compute_image_content_fingerprint(str(source_path))
        with test_client.test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ai_caption, aesthetic_score, embedding, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            )
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM tags WHERE image_id = ?", (image_id,))
            tag_count = cursor.fetchone()[0]

        assert tag_count == 0
        assert row["ai_caption"] is None
        assert row["aesthetic_score"] is None
        assert row["embedding"] is None
        assert row["content_fingerprint"] == refreshed_fingerprint
        assert refreshed_fingerprint != original_fingerprint
        response_payload = response.json()
        assert response_payload["overwrote_existing"] is True
        assert response_payload["overwrote_indexed_path"] is True
        assert response_payload["reconciled_image_id"] == image_id

    def test_save_data_requires_explicit_overwrite_for_existing_output(self, test_client, tmp_path):
        source_path = tmp_path / "save-data-conflict.png"
        Image.new("RGB", (32, 32), color="white").save(source_path)
        image_id = test_client.test_db.add_image(
            path=str(source_path),
            filename=source_path.name,
            metadata_json="{}",
        )
        payload_image = base64.b64encode(source_path.read_bytes()).decode("ascii")

        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": f"data:image/png;base64,{payload_image}",
                "filename": source_path.name,
                "output_folder": str(tmp_path),
                "metadata_option": "strip",
                "output_format": "png",
                "original_image_id": image_id,
            },
        )

        assert response.status_code == 409
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "Confirm overwrite" in detail

    def test_save_data_rejects_oversized_decoded_payload(self, test_client, monkeypatch):
        from services import censor_service as censor_service_module

        monkeypatch.setattr(censor_service_module, "MAX_SAVE_DATA_BYTES", 32)
        payload_image = base64.b64encode(b"x" * 33).decode("ascii")

        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": f"data:image/png;base64,{payload_image}",
                "filename": "saved-image.png",
                "output_folder": tempfile.gettempdir(),
                "metadata_option": "strip",
                "output_format": "png",
                "allow_overwrite": True,
            },
        )

        assert response.status_code == 413
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "too large" in detail.lower()

    def test_save_data_rejects_oversized_pixel_dimensions(self, test_client, tmp_path, monkeypatch):
        from services import censor_service as censor_service_module

        monkeypatch.setattr(censor_service_module, "MAX_SAVE_DATA_PIXELS", 1000)
        image_path = tmp_path / "large-ish.png"
        Image.new("RGB", (40, 40), color="green").save(image_path)
        payload_image = base64.b64encode(image_path.read_bytes()).decode("ascii")

        response = test_client.post(
            "/api/censor/save-data",
            json={
                "image_data": f"data:image/png;base64,{payload_image}",
                "filename": "saved-image.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 413
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "too large" in detail.lower()

    def test_save_operations_applies_pen_stroke(self, test_client, tmp_path):
        image_path = tmp_path / "operations-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="operations-source.png",
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "stroke",
                        "tool": "pen",
                        "brush_size": 12,
                        "pen_color": "#00ff00",
                        "pen_opacity": 1,
                        "points": [{"x": 16, "y": 16}],
                    }
                ],
                "filename": "operations-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 200
        output_path = Path(response.json()["output_path"])
        assert output_path.exists()

        with Image.open(output_path) as result:
            pixel = result.convert("RGBA").getpixel((16, 16))
            assert pixel[1] > 200
            assert pixel[0] < 80

    def test_save_operations_mosaic_stroke_spans_multiple_blocks(self, test_client, tmp_path):
        image_path = tmp_path / "mosaic-stroke-source.png"
        source = Image.new("RGB", (64, 64), color="black")
        for x in range(source.width):
            stripe = 255 if x % 2 else 0
            for y in range(source.height):
                source.putpixel((x, y), (stripe, stripe, stripe))
        source.save(image_path)

        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="mosaic-stroke-source.png",
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "stroke",
                        "tool": "brush",
                        "style": "mosaic",
                        "brush_size": 16,
                        "block_size": 8,
                        "points": [
                            {"x": 4, "y": 32},
                            {"x": 60, "y": 32},
                        ],
                    }
                ],
                "filename": "mosaic-stroke-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 200
        output_path = Path(response.json()["output_path"])
        assert output_path.exists()

        with Image.open(output_path) as result:
            result_rgb = result.convert("RGB")
            touched_samples = [result_rgb.getpixel((x, 32))[0] for x in (8, 16, 24, 32, 40, 48, 56)]
            untouched_sample = result_rgb.getpixel((8, 4))[0]

        assert untouched_sample in {0, 255}
        assert all(30 < value < 225 for value in touched_samples)
        assert len(set(touched_samples)) >= 2

    def test_save_operations_applies_cached_mask_ref(self, test_client, tmp_path, monkeypatch):
        from services import censor_service as censor_service_module
        from services.censor_service import CensorService

        monkeypatch.setattr(censor_service_module, "MASK_INLINE_DATA_PIXEL_THRESHOLD", 1)
        monkeypatch.setattr(CensorService, "_mask_cache_dir", tmp_path / "mask-cache")
        with CensorService._mask_cache_lock:
            CensorService._mask_cache_index = {}

        image_path = tmp_path / "mask-ref-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="mask-ref-source.png",
            metadata_json="{}",
        )

        mask_image = Image.new("L", (32, 32), 0)
        ImageDraw.Draw(mask_image).rectangle([8, 8, 24, 24], fill=255)
        mask_payload = CensorService._build_mask_payload(mask_image)

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "mask_effect",
                        "style": "black_bar",
                        "mask_ref": mask_payload["mask_ref"],
                        "mask_bounds": mask_payload["mask_bounds"],
                    }
                ],
                "filename": "mask-ref-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 200
        output_path = Path(response.json()["output_path"])
        assert output_path.exists()

        with Image.open(output_path) as result:
            rgba = result.convert("RGBA")
            assert rgba.getpixel((16, 16))[:3] == (0, 0, 0)
            assert rgba.getpixel((2, 2))[:3] == (255, 255, 255)

    def test_save_operations_applies_bounded_inline_mask_crop(self, test_client, tmp_path):
        from services.censor_service import CensorService

        image_path = tmp_path / "inline-mask-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )
        mask_image = Image.new("L", (32, 32), 0)
        ImageDraw.Draw(mask_image).rectangle([8, 9, 13, 15], fill=255)
        payload = CensorService._build_mask_payload(mask_image)

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "mask_effect",
                        "style": "black_bar",
                        "mask_data": payload["mask"],
                        "mask_bounds": payload["mask_bounds"],
                        "mask_image_width": payload["image_width"],
                        "mask_image_height": payload["image_height"],
                    }
                ],
                "filename": "inline-mask-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 200
        with Image.open(Path(response.json()["output_path"])) as result:
            rgba = result.convert("RGBA")
            assert rgba.getpixel((10, 12))[:3] == (0, 0, 0)
            assert rgba.getpixel((2, 2))[:3] == (255, 255, 255)

    def test_save_operations_rejects_mismatched_bounded_inline_mask(self, test_client, tmp_path):
        image_path = tmp_path / "mismatched-inline-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )
        rgba_mask = Image.new("RGBA", (5, 5), (255, 255, 255, 255))
        buffer = io.BytesIO()
        rgba_mask.save(buffer, format="PNG")
        mask_data = f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
        output_folder = tmp_path / "out"

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "mask_effect",
                        "style": "black_bar",
                        "mask_data": mask_data,
                        "mask_bounds": [8, 8, 16, 16],
                        "mask_image_width": 32,
                        "mask_image_height": 32,
                    }
                ],
                "filename": "must-not-exist.png",
                "output_folder": str(output_folder),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 400
        assert "inline mask" in response.text.lower()
        remaining_outputs = list(output_folder.glob("*")) if output_folder.exists() else []
        assert remaining_outputs == []

    def test_save_operations_applies_box_geometry_effect(self, test_client, tmp_path):
        image_path = tmp_path / "geometry-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="geometry-source.png",
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "geometry_effect",
                        "style": "black_bar",
                        "block_size": 8,
                        "blur_radius": 4,
                        "regions": [{"box": [4, 4, 20, 20]}],
                    }
                ],
                "filename": "geometry-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 200
        output_path = Path(response.json()["output_path"])
        assert output_path.exists()

        with Image.open(output_path) as result:
            pixel = result.convert("RGBA").getpixel((10, 10))
            assert pixel[:3] == (0, 0, 0)

    @pytest.mark.parametrize(
        ("coordinate", "expected_detail"),
        [
            (1e9, "outside the supported range"),
            (10**400, "expected a finite number"),
        ],
    )
    def test_save_operations_rejects_invalid_geometry_coordinate(
        self,
        test_client,
        tmp_path,
        coordinate,
        expected_detail,
    ):
        image_path = tmp_path / "invalid-geometry-source.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "geometry_effect",
                        "style": "mosaic",
                        "regions": [
                            {"polygon": [[coordinate, 6.5], [8.5, 0.1], [5e8, 12]]},
                        ],
                    }
                ],
                "filename": "invalid-geometry-output.png",
                "output_folder": str(tmp_path / "out"),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 400
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert expected_detail in detail
        assert "regions[0].polygon[0].x" in detail

    def test_save_operations_overwrite_refreshes_indexed_state(self, test_client, tmp_path):
        from image_fingerprint import compute_image_content_fingerprint

        image_path = tmp_path / "save-operations-overwrite.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="save-operations-overwrite.png",
            metadata_json="{}",
        )

        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        test_client.test_db.add_tags(
            image_id,
            [{"tag": "stale_tag", "confidence": 0.9}],
            content_fingerprint=original_fingerprint,
        )
        with test_client.test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
                WHERE id = ?
                """,
                ("stale caption", 0.91, b"embedding", original_fingerprint, image_id),
            )
            conn.commit()

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [
                    {
                        "kind": "geometry_effect",
                        "style": "black_bar",
                        "block_size": 8,
                        "blur_radius": 4,
                        "regions": [{"box": [4, 4, 20, 20]}],
                    }
                ],
                "filename": image_path.name,
                "output_folder": str(tmp_path),
                "metadata_option": "strip",
                "output_format": "png",
                "allow_overwrite": True,
            },
        )

        assert response.status_code == 200
        assert Path(response.json()["output_path"]).resolve() == image_path.resolve()

        refreshed_fingerprint = compute_image_content_fingerprint(str(image_path))
        with test_client.test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ai_caption, aesthetic_score, embedding, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            )
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM tags WHERE image_id = ?", (image_id,))
            tag_count = cursor.fetchone()[0]

        assert tag_count == 0
        assert row["ai_caption"] is None
        assert row["aesthetic_score"] is None
        assert row["embedding"] is None
        assert row["content_fingerprint"] == refreshed_fingerprint
        assert refreshed_fingerprint != original_fingerprint
        response_payload = response.json()
        assert response_payload["overwrote_existing"] is True
        assert response_payload["overwrote_indexed_path"] is True
        assert response_payload["reconciled_image_id"] == image_id

    def test_save_operations_requires_explicit_overwrite_for_existing_output(self, test_client, tmp_path):
        image_path = tmp_path / "save-operations-conflict.png"
        Image.new("RGB", (32, 32), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/save-operations",
            json={
                "original_image_id": image_id,
                "operations": [],
                "filename": image_path.name,
                "output_folder": str(tmp_path),
                "metadata_option": "strip",
                "output_format": "png",
            },
        )

        assert response.status_code == 409
        data = response.json()
        detail = data.get("detail") or data.get("error") or data.get("message") or ""
        assert "Confirm overwrite" in detail

    def test_concurrent_legacy_cold_start_loads_once_and_both_calls_succeed(
        self,
        test_db,
        monkeypatch,
        tmp_path,
    ):
        import censor as censor_module
        from services import censor_service as censor_service_module
        from services.censor_service import CensorDetectRequest, CensorService

        image_path = tmp_path / "concurrent-censor.png"
        Image.new("RGB", (32, 32), color="red").save(image_path)
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )
        model_path = str(tmp_path / "privacy.onnx")
        call_barrier = threading.Barrier(2)
        construction_barrier = threading.Barrier(2)
        counts = {"constructed": 0, "loaded": 0}
        counts_lock = threading.Lock()

        class ConcurrentDetector:
            def __init__(self, selected_model_path):
                self.model_path = selected_model_path
                self.session = None
                with counts_lock:
                    counts["constructed"] += 1
                try:
                    construction_barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    assert counts["constructed"] == 1

            def load(self):
                with counts_lock:
                    counts["loaded"] += 1
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                if self.session is None:
                    raise RuntimeError("Model not loaded")
                return []

        monkeypatch.setattr(censor_module, "_detector", None)
        monkeypatch.setattr(censor_module, "CensorDetector", ConcurrentDetector)
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: model_path,
        )
        service = CensorService()
        request = CensorDetectRequest(image_id=image_id, model_type="legacy")

        def detect_after_both_threads_start():
            call_barrier.wait(timeout=1)
            return service.detect(request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(detect_after_both_threads_start) for _ in range(2)]
            results = [future.result(timeout=3) for future in futures]

        assert [result["status"] for result in results] == ["ok", "ok"]
        assert counts == {"constructed": 1, "loaded": 1}

    def test_failed_legacy_switch_preserves_previous_detector_and_next_call_retries(
        self,
        test_db,
        monkeypatch,
        tmp_path,
    ):
        import censor as censor_module
        from services import censor_service as censor_service_module
        from services.censor_service import CensorDetectRequest, CensorService

        image_path = tmp_path / "retry-censor.png"
        Image.new("RGB", (32, 32), color="green").save(image_path)
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )
        model_path = str(tmp_path / "retry.onnx")
        counts = {"constructed": 0, "loaded": 0}

        class PreviousDetector:
            def __init__(self):
                self.model_path = str(tmp_path / "previous.onnx")
                self.session = object()

        class RetryDetector:
            def __init__(self, selected_model_path):
                counts["constructed"] += 1
                self.model_path = selected_model_path
                self.session = None

            def load(self):
                counts["loaded"] += 1
                if counts["loaded"] == 1:
                    raise RuntimeError("Failed to load legacy test model")
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                if self.session is None:
                    raise RuntimeError("Model not loaded")
                return []

        previous_detector = PreviousDetector()
        monkeypatch.setattr(censor_module, "_detector", previous_detector)
        monkeypatch.setattr(censor_module, "CensorDetector", RetryDetector)
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: model_path,
        )
        service = CensorService()
        request = CensorDetectRequest(image_id=image_id, model_type="legacy")

        with pytest.raises(HTTPException, match="Failed to load legacy test model"):
            service.detect(request)

        assert service._detector is None
        assert censor_module._detector is previous_detector

        result = service.detect(request)

        assert result["status"] == "ok"
        assert counts == {"constructed": 2, "loaded": 2}
        assert censor_module._detector is not previous_detector
        assert censor_module._detector.model_path == model_path

    def test_concurrent_detector_switch_returns_each_requested_instance(
        self,
        monkeypatch,
        tmp_path,
    ):
        import censor as censor_module

        model_a = str(tmp_path / "return-window-a.onnx")
        model_b = str(tmp_path / "return-window-b.onnx")

        class ConfiguredDetector:
            def __init__(self, selected_model_path):
                self.model_path = selected_model_path
                self.session = None

            def load(self):
                self.session = object()

        class PublishWindowLock:
            def __init__(self):
                self._lock = threading.Lock()
                self._state_lock = threading.Lock()
                self._exit_count = 0
                self.first_publish_exited = threading.Event()
                self.second_publish_exited = threading.Event()

            def __enter__(self):
                if not self._lock.acquire(timeout=3):
                    raise RuntimeError("Timed out acquiring detector test lock")
                return self

            def __exit__(self, exc_type, exc, traceback):
                self._lock.release()
                with self._state_lock:
                    self._exit_count += 1
                    exit_count = self._exit_count
                if exit_count == 1:
                    self.first_publish_exited.set()
                    if not self.second_publish_exited.wait(timeout=3):
                        raise RuntimeError("Timed out waiting for second detector publish")
                elif exit_count == 2:
                    self.second_publish_exited.set()
                return False

        publish_lock = PublishWindowLock()
        monkeypatch.setattr(censor_module, "_detector", None)
        monkeypatch.setattr(censor_module, "_detector_lock", publish_lock)
        monkeypatch.setattr(censor_module, "CensorDetector", ConfiguredDetector)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(censor_module.get_detector, model_a)
            assert publish_lock.first_publish_exited.wait(timeout=1)
            second_future = executor.submit(censor_module.get_detector, model_b)
            first_detector = first_future.result(timeout=3)
            second_detector = second_future.result(timeout=3)

        assert first_detector.model_path == model_a
        assert second_detector.model_path == model_b
        assert first_detector is not second_detector

    def test_concurrent_legacy_configuration_changes_use_the_requested_detector(
        self,
        test_db,
        monkeypatch,
        tmp_path,
    ):
        import censor as censor_module
        from services.censor_service import CensorDetectRequest, CensorService

        image_paths = []
        image_ids = []
        for name, color in (("config-a.png", "blue"), ("config-b.png", "yellow")):
            image_path = tmp_path / name
            Image.new("RGB", (32, 32), color=color).save(image_path)
            image_paths.append(str(image_path))
            image_ids.append(
                test_db.add_image(
                    path=str(image_path),
                    filename=name,
                    metadata_json="{}",
                )
            )

        model_a = str(tmp_path / "model-a.onnx")
        model_b = str(tmp_path / "model-b.onnx")
        first_load_started = threading.Event()
        release_first_load = threading.Event()
        second_load_called = threading.Event()
        release_second_load = threading.Event()
        load_lock = threading.Lock()

        class ConfiguredDetector:
            def __init__(self, selected_model_path):
                self.model_path = selected_model_path
                self.session = None

            def load(self):
                if self.model_path == model_b:
                    second_load_called.set()
                with load_lock:
                    if self.model_path == model_a:
                        first_load_started.set()
                        if not release_first_load.wait(timeout=3):
                            raise RuntimeError("Timed out releasing model A load")
                    elif not release_second_load.wait(timeout=3):
                        raise RuntimeError("Timed out releasing model B load")
                    self.session = object()

            def detect(self, image_path, _threshold, **_kwargs):
                if self.session is None:
                    raise RuntimeError("Model not loaded")
                return [
                    {
                        "class": "breasts",
                        "confidence": 0.9,
                        "box": [0, 0, 8, 8],
                        "model_path": self.model_path,
                        "image_path": image_path,
                    }
                ]

        monkeypatch.setattr(censor_module, "_detector", None)
        monkeypatch.setattr(censor_module, "CensorDetector", ConfiguredDetector)
        service = CensorService()
        monkeypatch.setattr(
            service,
            "_resolve_legacy_model_path",
            lambda requested_path, *, allowed_base: requested_path,
        )
        requests = (
            CensorDetectRequest(
                image_id=image_ids[0],
                model_type="legacy",
                model_path=model_a,
            ),
            CensorDetectRequest(
                image_id=image_ids[1],
                model_type="legacy",
                model_path=model_b,
            ),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(service.detect, requests[0])
            assert first_load_started.wait(timeout=1)
            second_future = executor.submit(service.detect, requests[1])
            second_started_before_first_finished = second_load_called.wait(timeout=0.25)
            release_first_load.set()
            try:
                first_result = first_future.result(timeout=3)
            except Exception as exc:
                first_result = exc
            finally:
                release_second_load.set()
            second_result = second_future.result(timeout=3)

        assert not isinstance(first_result, Exception)
        assert second_started_before_first_finished is False
        assert first_result["detections"][0]["model_path"] == model_a
        assert first_result["detections"][0]["image_path"] == image_paths[0]
        assert second_result["detections"][0]["model_path"] == model_b
        assert second_result["detections"][0]["image_path"] == image_paths[1]

    def test_detect_legacy_uses_local_default_model_when_path_is_blank(self, test_client, monkeypatch, tmp_path):
        from PIL import Image
        import censor as censor_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "censor-test.png"
        Image.new("RGB", (64, 64), color="red").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="censor-test.png",
            metadata_json="{}",
        )

        captured = {}

        class FakeDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()
                captured["model_path"] = self.model_path

            def detect(self, _image_path, _threshold, **_kwargs):
                return [{"class": "breasts", "confidence": 0.9, "box": [0, 0, 16, 16]}]

        monkeypatch.setattr(censor_service_module, "get_default_legacy_model_path", lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"))
        monkeypatch.setattr(censor_module, "CensorDetector", FakeDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "legacy", "confidence_threshold": 0.5},
        )

        assert response.status_code == 200
        assert captured["model_path"].endswith("wenaka_yolov8s-seg.onnx")
        assert response.json()["detections"][0]["class"] == "breasts"

    def test_detect_resolves_backend_relative_source_paths(self, test_client, monkeypatch, tmp_path):
        from PIL import Image
        import censor as censor_module
        from services import censor_service as censor_service_module

        backend_root = Path(__file__).resolve().parents[2]
        captured = {}

        class FakeDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, image_path, _threshold, **_kwargs):
                captured["image_path"] = image_path
                return [{"class": "breasts", "confidence": 0.9, "box": [0, 0, 16, 16]}]

        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", FakeDetector)

        with tempfile.TemporaryDirectory(dir=backend_root) as relative_dir:
            image_path = Path(relative_dir) / "relative-censor.png"
            Image.new("RGB", (64, 64), color="green").save(image_path)

            image_id = test_client.test_db.add_image(
                path=str(image_path.relative_to(backend_root)),
                filename="relative-censor.png",
                metadata_json="{}",
            )

            response = test_client.post(
                "/api/censor/detect",
                json={"image_id": image_id, "model_type": "legacy", "confidence_threshold": 0.5},
            )

        assert response.status_code == 200
        assert captured["image_path"] == str(image_path.resolve())
        assert response.json()["detections"][0]["class"] == "breasts"

    def test_detect_reports_missing_source_path_with_actionable_detail(self, test_client, tmp_path):
        missing_path = tmp_path / "missing-source.png"
        image_id = test_client.test_db.add_image(
            path=str(missing_path),
            filename="missing-source.png",
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "nudenet", "confidence_threshold": 0.5},
        )

        assert response.status_code == 404
        error_text = response.json()["error"]
        assert "source file is missing on disk" in error_text
        assert "Auto Censor needs the original file" in error_text
        assert str(missing_path) in error_text
        assert "Reconnect it and rescan that folder" in error_text

    def test_combined_mask_uses_transparent_alpha_png(self):
        from io import BytesIO
        from PIL import Image
        from services.censor_service import CensorService
        import base64

        data_url = CensorService._build_combined_mask_data_url(
            (32, 32),
            [{"class": "breasts", "box": [4, 4, 20, 20], "polygon": [[4, 4], [20, 4], [20, 20], [4, 20]]}],
        )

        assert data_url and data_url.startswith("data:image/png;base64,")
        encoded = data_url.split(",", 1)[1]
        image = Image.open(BytesIO(base64.b64decode(encoded))).convert("RGBA")
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((10, 10))[3] == 255

    def test_combined_mask_can_include_box_only_regions(self):
        from io import BytesIO
        from PIL import Image
        from services.censor_service import CensorService
        import base64

        data_url = CensorService._build_combined_mask_data_url(
            (32, 32),
            [{"class": "anus", "box": [4, 4, 20, 20]}],
            include_boxes=True,
        )

        assert data_url and data_url.startswith("data:image/png;base64,")
        encoded = data_url.split(",", 1)[1]
        image = Image.open(BytesIO(base64.b64decode(encoded))).convert("RGBA")
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((10, 10))[3] == 255

    def test_filter_detections_matches_buttocks_family_aliases(self):
        from services.censor_service import CensorService

        detections = [
            {"class": "buttocks_exposed", "box": [0, 0, 10, 10]},
            {"class": "female_breast_exposed", "box": [0, 0, 10, 10]},
            {"class": "face", "box": [0, 0, 10, 10]},
        ]

        filtered = CensorService._filter_detections_by_targets(detections, ["buttocks", "breasts"])

        classes = [item["class"] for item in filtered]
        assert "buttocks_exposed" in classes
        assert "female_breast_exposed" in classes
        assert "face" not in classes

    def test_detect_filters_target_classes_and_returns_combined_mask(self, test_client, monkeypatch, tmp_path):
        from PIL import Image
        import censor as censor_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "censor-mask-test.png"
        Image.new("RGB", (80, 80), color="blue").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="censor-mask-test.png",
            metadata_json="{}",
        )

        class FakeDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                return [
                    {
                        "class": "breasts",
                        "confidence": 0.92,
                        "box": [0, 0, 24, 24],
                        "polygon": [[0, 0], [24, 0], [24, 24], [0, 24]],
                    },
                    {
                        "class": "anus",
                        "confidence": 0.88,
                        "box": [40, 40, 60, 60],
                    },
                ]

        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", FakeDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={
                "image_id": image_id,
                "model_type": "legacy",
                "confidence_threshold": 0.5,
                "target_classes": ["anus"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert [d["class"] for d in data["detections"]] == ["anus"]
        assert data["geometry_mode"] == "box"
        assert data["combined_mask"].startswith("data:image/png;base64,")
        assert data["combined_mask_ref"] is None
        assert data["combined_mask_bounds"] == [40, 40, 61, 61]
        assert data["image_width"] == 80
        assert data["image_height"] == 80

    def test_detect_both_fails_when_neither_backend_runs(self, test_client, monkeypatch, tmp_path):
        import censor as censor_module
        import nudenet_detector as nudenet_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "combined-total-failure.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        class FailedNudeNet:
            def detect(self, _image_path, *, conf_threshold, exposed_only, **_kwargs):
                raise RuntimeError(
                    f"nudenet runtime unavailable at threshold={conf_threshold}, exposed_only={exposed_only}"
                )

        class FailedLegacyDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                raise RuntimeError("legacy inference session failed")

        monkeypatch.setattr(nudenet_module, "get_nudenet_detector", lambda: FailedNudeNet())
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", FailedLegacyDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "both", "confidence_threshold": 0.42},
        )

        assert response.status_code == 500, response.text
        error_text = response.json()["error"]
        assert "both detection engines failed" in error_text.lower()
        assert "NudeNet" in error_text
        assert "nudenet runtime unavailable" in error_text
        assert "Legacy YOLO" in error_text
        assert "legacy inference session failed" in error_text

    def test_detect_both_returns_partial_results_with_warning(self, test_client, monkeypatch, tmp_path):
        import censor as censor_module
        import nudenet_detector as nudenet_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "combined-partial-success.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        class FailedNudeNet:
            def detect(self, _image_path, *, conf_threshold, exposed_only, **_kwargs):
                raise RuntimeError(
                    f"nudenet dependency failed at threshold={conf_threshold}, exposed_only={exposed_only}"
                )

        class WorkingLegacyDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                return [{"class": "breasts", "confidence": 0.91, "box": [4, 4, 20, 20]}]

        monkeypatch.setattr(nudenet_module, "get_nudenet_detector", lambda: FailedNudeNet())
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", WorkingLegacyDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "both", "confidence_threshold": 0.42},
        )

        assert response.status_code == 200, response.text
        result = response.json()
        assert result["detections"] == [
            {
                "class": "breasts",
                "confidence": 0.91,
                "box": [4, 4, 20, 20],
                "source": "legacy",
            }
        ]
        assert len(result["warnings"]) == 1
        assert "NudeNet" in result["warnings"][0]
        assert "nudenet dependency failed" in result["warnings"][0]
        assert "Legacy YOLO" in result["warnings"][0]

    def test_detect_both_clean_empty_result_has_no_warnings(self, test_client, monkeypatch, tmp_path):
        import censor as censor_module
        import nudenet_detector as nudenet_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "combined-clean-empty.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        class EmptyNudeNet:
            def detect(self, _image_path, *, conf_threshold, exposed_only, **_kwargs):
                assert conf_threshold == 0.42
                assert exposed_only is True
                return []

        class EmptyLegacyDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, threshold, **_kwargs):
                assert threshold == 0.42
                return []

        monkeypatch.setattr(nudenet_module, "get_nudenet_detector", lambda: EmptyNudeNet())
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", EmptyLegacyDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "both", "confidence_threshold": 0.42},
        )

        assert response.status_code == 200, response.text
        result = response.json()
        assert result["detections"] == []
        assert result["warnings"] == []

    def test_detect_both_keeps_empty_partial_result_with_warning(self, test_client, monkeypatch, tmp_path):
        import censor as censor_module
        import nudenet_detector as nudenet_module
        from services import censor_service as censor_service_module

        image_path = tmp_path / "combined-empty-partial.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
        )

        class EmptyNudeNet:
            def detect(self, _image_path, *, conf_threshold, exposed_only, **_kwargs):
                assert conf_threshold == 0.42
                assert exposed_only is True
                return []

        class FailedLegacyDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                raise RuntimeError("legacy model could not run")

        monkeypatch.setattr(nudenet_module, "get_nudenet_detector", lambda: EmptyNudeNet())
        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", FailedLegacyDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={"image_id": image_id, "model_type": "both", "confidence_threshold": 0.42},
        )

        assert response.status_code == 200, response.text
        result = response.json()
        assert result["detections"] == []
        assert len(result["warnings"]) == 1
        assert "Legacy YOLO" in result["warnings"][0]
        assert "legacy model could not run" in result["warnings"][0]
        assert "NudeNet" in result["warnings"][0]

    def test_detect_returns_cached_mask_ref_for_large_combined_masks(self, test_client, monkeypatch, tmp_path):
        import censor as censor_module
        from services import censor_service as censor_service_module
        from services.censor_service import CensorService

        monkeypatch.setattr(censor_service_module, "MASK_INLINE_DATA_PIXEL_THRESHOLD", 1)
        monkeypatch.setattr(CensorService, "_mask_cache_dir", tmp_path / "mask-cache")
        with CensorService._mask_cache_lock:
            CensorService._mask_cache_index = {}

        image_path = tmp_path / "detect-large-mask.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="detect-large-mask.png",
            metadata_json="{}",
        )

        class FakeDetector:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = None

            def load(self):
                self.session = object()

            def detect(self, _image_path, _threshold, **_kwargs):
                return [
                    {
                        "class": "breasts",
                        "confidence": 0.96,
                        "box": [12, 10, 40, 44],
                        "polygon": [[12, 10], [40, 10], [40, 44], [12, 44]],
                    },
                ]

        monkeypatch.setattr(
            censor_service_module,
            "get_default_legacy_model_path",
            lambda: str(tmp_path / "wenaka_yolov8s-seg.onnx"),
        )
        monkeypatch.setattr(censor_module, "CensorDetector", FakeDetector)

        response = test_client.post(
            "/api/censor/detect",
            json={
                "image_id": image_id,
                "model_type": "legacy",
                "confidence_threshold": 0.5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["combined_mask"] is None
        assert data["combined_mask_ref"]
        assert data["combined_mask_bounds"] == [12, 10, 41, 45]
        assert data["image_width"] == 64
        assert data["image_height"] == 64

        preview_response = test_client.get(
            f"/api/censor/mask-cache/{data['combined_mask_ref']}?width=14&height=18"
        )
        assert preview_response.status_code == 200
        assert preview_response.headers["content-type"].startswith("image/png")
        preview_image = Image.open(io.BytesIO(preview_response.content)).convert("RGBA")
        assert preview_image.size == (14, 18)
        assert preview_image.getpixel((7, 9))[3] > 0

    def test_segment_text_keeps_inline_mask_for_small_images(self, test_client, monkeypatch, tmp_path):
        from services import censor_service as censor_service_module

        image_path = tmp_path / "segment-small.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="segment-small.png",
            metadata_json="{}",
        )

        class FakeRefiner:
            def segment_by_text(self, image, text_prompt, presence_threshold=None):
                assert image.size == (64, 64)
                assert text_prompt == "face"
                assert presence_threshold is None
                mask = np.zeros((64, 64), dtype=np.uint8)
                mask[12:48, 20:44] = 1
                return mask

        monkeypatch.setattr(
            censor_service_module,
            "get_model_health",
            lambda: {"censor": {"sam3": {"available": True, "message": "ready"}}},
        )
        monkeypatch.setitem(
            sys.modules,
            "sam3_refiner",
            type("FakeSam3Module", (), {"get_sam3_refiner": staticmethod(lambda: FakeRefiner())})(),
        )

        response = test_client.post(
            "/api/censor/segment-text",
            json={"image_id": image_id, "text_prompt": "face"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mask"].startswith("data:image/png;base64,")
        assert data["mask_ref"] is None
        assert data["mask_bounds"] == [20, 12, 44, 48]
        assert data["image_width"] == 64
        assert data["image_height"] == 64

    def test_segment_text_returns_cached_mask_ref_for_large_masks(self, test_client, monkeypatch, tmp_path):
        from services import censor_service as censor_service_module
        from services.censor_service import CensorService

        monkeypatch.setattr(censor_service_module, "MASK_INLINE_DATA_PIXEL_THRESHOLD", 1)
        monkeypatch.setattr(CensorService, "_mask_cache_dir", tmp_path / "mask-cache")
        with CensorService._mask_cache_lock:
            CensorService._mask_cache_index = {}

        image_path = tmp_path / "segment-large.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="segment-large.png",
            metadata_json="{}",
        )

        class FakeRefiner:
            def segment_by_text(self, image, text_prompt, presence_threshold=None):
                assert image.size == (64, 64)
                assert text_prompt == "face"
                assert presence_threshold is None
                mask = np.zeros((64, 64), dtype=np.uint8)
                mask[12:48, 20:44] = 1
                return mask

        monkeypatch.setattr(
            censor_service_module,
            "get_model_health",
            lambda: {"censor": {"sam3": {"available": True, "message": "ready"}}},
        )
        monkeypatch.setitem(
            sys.modules,
            "sam3_refiner",
            type("FakeSam3Module", (), {"get_sam3_refiner": staticmethod(lambda: FakeRefiner())})(),
        )

        response = test_client.post(
            "/api/censor/segment-text",
            json={"image_id": image_id, "text_prompt": "face"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mask"] is None
        assert data["mask_ref"]
        assert data["mask_bounds"] == [20, 12, 44, 48]
        assert data["image_width"] == 64
        assert data["image_height"] == 64

        preview_response = test_client.get(f"/api/censor/mask-cache/{data['mask_ref']}?width=12&height=18")
        assert preview_response.status_code == 200
        assert preview_response.headers["content-type"].startswith("image/png")
        preview_image = Image.open(io.BytesIO(preview_response.content)).convert("RGBA")
        assert preview_image.size == (12, 18)
        assert preview_image.getpixel((6, 9))[3] > 0

    def test_batch_refine_mask_threads_slider_confidence_to_refiner(self, test_client, monkeypatch, tmp_path):
        """The sam3_confidence the frontend sends on every batch refine must
        reach SAM3's refine_box as confidence_threshold (it used to be a dead
        knob that batch_refine_mask never read)."""
        from services import censor_service as censor_service_module

        image_path = tmp_path / "refine-batch.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="refine-batch.png",
            metadata_json="{}",
        )

        captured = []

        class FakeRefiner:
            def refine_box(self, image, box, text_prompt=None, confidence_threshold=None):
                captured.append({
                    "box": box,
                    "text_prompt": text_prompt,
                    "confidence_threshold": confidence_threshold,
                })
                mask = np.zeros((64, 64), dtype=np.uint8)
                mask[12:48, 20:44] = 1
                return mask

        monkeypatch.setattr(
            censor_service_module,
            "get_model_health",
            lambda: {"censor": {"sam3": {"available": True, "message": "ready"}}},
        )
        monkeypatch.setitem(
            sys.modules,
            "sam3_refiner",
            type("FakeSam3Module", (), {"get_sam3_refiner": staticmethod(lambda: FakeRefiner())})(),
        )

        response = test_client.post(
            "/api/censor/batch-refine-mask",
            json={
                "items": [
                    {"image_id": image_id, "box": [1, 2, 30, 40]},
                    {"image_id": image_id, "box": [2, 3, 31, 41], "sam3_confidence": 0.2},
                ],
                "sam3_confidence": 0.85,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["refined"] == 2
        assert data["fallback"] == 0
        # Batch-level slider value is the default for every item...
        assert captured[0]["confidence_threshold"] == pytest.approx(0.85)
        # ...but an explicit per-item value wins.
        assert captured[1]["confidence_threshold"] == pytest.approx(0.2)
        # Response shape is unchanged by the new gating.
        first = data["results"][0]
        assert first["status"] == "ok"
        assert first["mask"].startswith("data:image/png;base64,")
        assert first["mask_bounds"] == [20, 12, 44, 48]

    def test_batch_refine_mask_defaults_confidence_to_half_when_omitted(self, test_client, monkeypatch, tmp_path):
        from services import censor_service as censor_service_module

        image_path = tmp_path / "refine-default.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="refine-default.png",
            metadata_json="{}",
        )

        captured = {}

        class FakeRefiner:
            def refine_box(self, image, box, text_prompt=None, confidence_threshold=None):
                captured["confidence_threshold"] = confidence_threshold
                return None  # exercises the fallback branch too

        monkeypatch.setattr(
            censor_service_module,
            "get_model_health",
            lambda: {"censor": {"sam3": {"available": True, "message": "ready"}}},
        )
        monkeypatch.setitem(
            sys.modules,
            "sam3_refiner",
            type("FakeSam3Module", (), {"get_sam3_refiner": staticmethod(lambda: FakeRefiner())})(),
        )

        response = test_client.post(
            "/api/censor/batch-refine-mask",
            json={"items": [{"image_id": image_id, "box": [1, 2, 30, 40]}]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["refined"] == 0
        assert data["fallback"] == 1
        assert captured["confidence_threshold"] == pytest.approx(0.5)

    def test_refine_mask_single_endpoint_passes_optional_confidence(self, test_client, monkeypatch, tmp_path):
        """Single refine: omitted -> None (refiner defaults, backward
        compatible); provided -> threaded through."""
        from services import censor_service as censor_service_module

        image_path = tmp_path / "refine-single.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="refine-single.png",
            metadata_json="{}",
        )

        captured = []

        class FakeRefiner:
            def refine_box(self, image, box, text_prompt=None, confidence_threshold=None):
                captured.append(confidence_threshold)
                mask = np.zeros((64, 64), dtype=np.uint8)
                mask[12:48, 20:44] = 1
                return mask

        monkeypatch.setattr(
            censor_service_module,
            "get_model_health",
            lambda: {"censor": {"sam3": {"available": True, "message": "ready"}}},
        )
        monkeypatch.setitem(
            sys.modules,
            "sam3_refiner",
            type("FakeSam3Module", (), {"get_sam3_refiner": staticmethod(lambda: FakeRefiner())})(),
        )

        legacy = test_client.post(
            "/api/censor/refine-mask",
            json={"image_id": image_id, "box": [1, 2, 30, 40]},
        )
        assert legacy.status_code == 200
        assert legacy.json()["status"] == "ok"
        assert captured[-1] is None

        gated = test_client.post(
            "/api/censor/refine-mask",
            json={"image_id": image_id, "box": [1, 2, 30, 40], "sam3_confidence": 0.7},
        )
        assert gated.status_code == 200
        assert gated.json()["status"] == "ok"
        assert captured[-1] == pytest.approx(0.7)

    @pytest.mark.parametrize(
        "endpoint,payload",
        [
            ("/api/censor/refine-mask", {"image_id": 1, "box": [1, 2, 3, 4], "sam3_confidence": 1.5}),
            ("/api/censor/refine-mask", {"image_id": 1, "box": [1, 2, 3, 4], "sam3_confidence": -0.1}),
            ("/api/censor/batch-refine-mask", {"items": [{"image_id": 1, "box": [1, 2, 3, 4]}], "sam3_confidence": 1.5}),
            ("/api/censor/batch-refine-mask", {"items": [{"image_id": 1, "box": [1, 2, 3, 4], "sam3_confidence": -0.1}]}),
        ],
    )
    def test_refine_endpoints_reject_out_of_range_confidence(self, test_client, endpoint, payload):
        # main.py remaps FastAPI validation errors (422) to 400 app-wide.
        response = test_client.post(endpoint, json=payload)
        assert response.status_code == 400

    def test_censor_models_returns_recommended_backend(self, test_client):
        response = test_client.get("/api/censor/models")

        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert "recommended_backend" in data

    def test_censor_models_nudenet_exposes_model_downloaded(self, test_client):
        # The frontend warns before a cold NudeNet run (first detect downloads
        # the ONNX weights, ~2 min). That warning keys off model_downloaded, so
        # the field must always be present and boolean on the nudenet entry.
        response = test_client.get("/api/censor/models")

        assert response.status_code == 200
        nudenet = next(
            (model for model in response.json()["models"] if model.get("id") == "nudenet"),
            None,
        )
        assert nudenet is not None
        assert "model_downloaded" in nudenet
        assert isinstance(nudenet["model_downloaded"], bool)


class TestSimilarityRouterValidation:
    def test_embed_images_uses_background_tasks_instead_of_daemon_thread(self, monkeypatch):
        from services.similarity_service import SimilarityService

        class FakeIndex:
            def get_progress(self):
                return {"running": False}

            def embed_batch(self, image_ids):
                return image_ids

        fake_index = FakeIndex()
        monkeypatch.setattr(
            "services.similarity_service.get_similarity_index",
            lambda _db: fake_index,
        )
        monkeypatch.setattr(
            "services.similarity_service.ensure_clip_model_ready",
            lambda: "fastembed:in-memory",
        )

        service = SimilarityService()
        background_tasks = BackgroundTasks()
        result = service.embed_images(background_tasks, [1, 2, 3])

        assert result["status"] == "started"
        assert len(background_tasks.tasks) == 1
        assert background_tasks.tasks[0].func == fake_index.embed_batch
        assert background_tasks.tasks[0].args == ([1, 2, 3],)

    def test_search_similar_returns_pagination_metadata(self, test_client):
        from routers import similarity as similarity_router

        class FakeService:
            def search_similar(self, image_id, limit, threshold, offset, collection_id=None):
                assert image_id == 77
                assert limit == 2
                assert offset == 2
                assert threshold == 0.6
                assert collection_id is None
                return {
                    "query_image_id": image_id,
                    "results": [{"id": 101, "filename": "match.png", "similarity": 0.91}],
                    "count": 1,
                    "total": 5,
                    "has_more": True,
                    "offset": offset,
                    "limit": limit,
                }

        similarity_router.set_similarity_service(FakeService())
        try:
            response = test_client.get("/api/similarity/search/77?limit=2&offset=2&threshold=0.6")
        finally:
            similarity_router.set_similarity_service(None)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["has_more"] is True
        assert data["offset"] == 2
        assert data["limit"] == 2

    def test_duplicates_return_pagination_metadata(self, test_client):
        from routers import similarity as similarity_router

        class FakeService:
            def find_duplicates(self, threshold, limit, offset):
                assert threshold == 0.97
                assert limit == 3
                assert offset == 3
                return {
                    "duplicates": [{"image_a": {"id": 1}, "image_b": {"id": 2}, "similarity": 0.99}],
                    "count": 1,
                    "total": 7,
                    "has_more": True,
                    "offset": offset,
                    "limit": limit,
                    "threshold": threshold,
                }

        similarity_router.set_similarity_service(FakeService())
        try:
            response = test_client.get("/api/similarity/duplicates?threshold=0.97&limit=3&offset=3")
        finally:
            similarity_router.set_similarity_service(None)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 7
        assert data["has_more"] is True
        assert data["offset"] == 3
        assert data["limit"] == 3

    def test_search_similar_rejects_invalid_limit(self, test_client):
        response = test_client.get("/api/similarity/search/1?limit=0")

        assert response.status_code in [400, 422]

    def test_search_similar_rejects_invalid_threshold(self, test_client):
        response = test_client.get("/api/similarity/search/1?threshold=1.1")

        assert response.status_code in [400, 422]

    def test_duplicates_rejects_invalid_threshold(self, test_client):
        response = test_client.get("/api/similarity/duplicates?threshold=0.4")

        assert response.status_code in [400, 422]

    def test_search_similar_returns_404_for_missing_image(self, test_client):
        response = test_client.get("/api/similarity/search/999999")

        assert response.status_code == 404
        assert response.json()["error"] == "Image 999999 was not found."

    def test_search_similar_requires_embedding_for_existing_image(self, test_client):
        image_id = test_client.test_db.add_image(
            path="/tmp/no_embedding.png",
            filename="no_embedding.png",
            metadata_json="{}",
        )

        response = test_client.get(f"/api/similarity/search/{image_id}")

        assert response.status_code == 409
        assert "has no embedding yet" in response.json()["error"]

    def test_search_upload_rejects_invalid_image_content(self, test_client):
        response = test_client.post(
            "/api/similarity/search-upload",
            files={"file": ("not-image.txt", b"definitely not an image", "text/plain")},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid image file. Upload a readable PNG, JPG, or WebP image."

    def test_duplicates_report_insufficient_embeddings_instead_of_fake_empty(self, test_client):
        response = test_client.get("/api/similarity/duplicates?threshold=0.95")

        assert response.status_code == 200
        data = response.json()
        assert data["duplicates"] == []
        assert data["count"] == 0
        assert data["reason"] == "insufficient_embeddings"
        assert data["embedded_count"] == 0
        assert data["minimum_required"] == 2

    def test_duplicates_refuse_sync_search_above_embedding_limit(self, test_client, monkeypatch):
        import similarity as similarity_module

        monkeypatch.setattr(similarity_module, "DUPLICATE_SYNC_MAX_EMBEDDINGS", 2)
        monkeypatch.setattr(
            similarity_module,
            "_index",
            similarity_module.SimilarityIndex(test_client.test_db),
        )
        embedding = similarity_module.embedding_to_bytes(np.array([1, 0, 0, 0], dtype=np.float32))

        for index in range(3):
            image_id = test_client.test_db.add_image(
                path=f"/tmp/duplicate-limit-{index}.png",
                filename=f"duplicate-limit-{index}.png",
                metadata_json="{}",
            )
            with test_client.test_db.get_db() as conn:
                conn.execute("UPDATE images SET embedding = ? WHERE id = ?", (embedding, image_id))

        response = test_client.get("/api/similarity/duplicates?threshold=0.95")

        assert response.status_code == 200
        data = response.json()
        assert data["duplicates"] == []
        assert data["count"] == 0
        assert data["reason"] == "too_many_embeddings"
        assert data["embedded_count"] == 3
        assert data["max_embeddings"] == 2

    def test_model_status_reports_clip_readiness_payload(self, test_client):
        response = test_client.get("/api/similarity/model-status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "model_name" in data
        assert "available" in data
        # The Similar view localizes the tech detail via message_key.
        assert data["message_key"] in {
            "models.clip.loaded",
            "models.clip.ready",
            "models.clip.missingRuntime",
            "models.clip.missingModel",
        }

    def test_embed_progress_reports_skipped_unreadable_and_failed(self, test_db, tmp_path, monkeypatch):
        import similarity as similarity_module
        from PIL import Image

        good_path = tmp_path / "good.png"
        Image.new("RGB", (64, 64), color="white").save(good_path)

        unreadable_path = tmp_path / "unreadable.png"
        unreadable_path.write_bytes(good_path.read_bytes()[:-24])

        fail_path = tmp_path / "embed_fail.png"
        Image.new("RGB", (64, 64), color="blue").save(fail_path)

        missing_path = tmp_path / "missing.png"

        good_id = test_db.add_image(path=str(good_path), filename=good_path.name, metadata_json="{}")
        unreadable_id = test_db.add_image(path=str(unreadable_path), filename=unreadable_path.name, metadata_json="{}")
        fail_id = test_db.add_image(path=str(fail_path), filename=fail_path.name, metadata_json="{}")
        missing_id = test_db.add_image(path=str(missing_path), filename=missing_path.name, metadata_json="{}")

        monkeypatch.setattr(similarity_module, "_get_embed_model", lambda: object())

        def fake_embed(path, model=None):
            if path.endswith("embed_fail.png"):
                return None
            return np.ones(4, dtype=np.float32)

        monkeypatch.setattr(similarity_module, "embed_image_file", fake_embed)

        index = similarity_module.SimilarityIndex(test_db)
        result = index.embed_batch([good_id, unreadable_id, fail_id, missing_id])
        progress = index.get_progress()

        assert result["embedded"] == 1
        assert result["skipped"] == 1
        assert result["unreadable"] == 1
        assert result["failed"] == 1
        assert progress["embedded"] == 1
        assert progress["skipped"] == 1
        assert progress["unreadable"] == 1
        assert progress["failed"] == 1
        assert {issue["filename"] for issue in progress["recent_issues"]} == {
            "unreadable.png",
            "embed_fail.png",
            "missing.png",
        }

    def test_embed_batch_resolves_windows_indexed_path_before_embedding(self, test_db, tmp_path, monkeypatch):
        import similarity as similarity_module
        from PIL import Image

        resolved_path = tmp_path / "resolved-similarity.png"
        Image.new("RGB", (64, 64), color="white").save(resolved_path)

        indexed_windows_path = r"L:\datasets\resolved-similarity.png"
        image_id = test_db.add_image(
            path=indexed_windows_path,
            filename="resolved-similarity.png",
            metadata_json="{}",
        )

        monkeypatch.setattr(similarity_module, "_get_embed_model", lambda: object())
        monkeypatch.setattr(
            similarity_module,
            "resolve_existing_indexed_image_path",
            lambda primary_path, *, backend_file: str(resolved_path) if primary_path == indexed_windows_path else None,
        )
        monkeypatch.setattr(similarity_module, "verify_image_readable", lambda _path: (True, None))
        monkeypatch.setattr(similarity_module, "compute_image_content_fingerprint", lambda _path: "fp")

        captured = {}

        def fake_embed(path, model=None):
            captured["path"] = path
            return np.ones(4, dtype=np.float32)

        monkeypatch.setattr(similarity_module, "embed_image_file", fake_embed)

        result = similarity_module.SimilarityIndex(test_db).embed_batch([image_id])

        assert result["embedded"] == 1
        assert result["errors"] == 0
        assert captured["path"] == str(resolved_path)

    def test_embed_batch_rejects_pixels_changed_during_model_inference(self, test_db, tmp_path, monkeypatch):
        import similarity as similarity_module
        from image_fingerprint import compute_image_content_fingerprint
        from PIL import Image

        image_path = tmp_path / "changed-during-embedding.png"
        with Image.new("RGB", (64, 64), color="white") as original:
            original.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        monkeypatch.setattr(similarity_module, "_get_embed_model", lambda: object())

        def replace_source_after_embedding(_path, model=None):
            with Image.new("RGB", (64, 64), color="black") as replacement:
                replacement.save(image_path)
            return np.ones(4, dtype=np.float32)

        monkeypatch.setattr(similarity_module, "embed_image_file", replace_source_after_embedding)

        result = similarity_module.SimilarityIndex(test_db).embed_batch([image_id])

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT embedding, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert result["embedded"] == 0
        assert result["errors"] == 1
        assert result["skipped"] == 1
        assert result["failed"] == 0
        assert result["recent_issues"] == [
            {
                "kind": "skipped",
                "image_id": image_id,
                "filename": image_path.name,
                "path": str(image_path),
                "reason": "Image pixels changed while its embedding was being computed; rescan and retry",
            }
        ]
        assert row["embedding"] is None
        assert row["content_fingerprint"] == original_fingerprint

    def test_embed_batch_counts_only_embeddings_published_for_the_current_fingerprint(
        self,
        test_db,
        tmp_path,
        monkeypatch,
    ):
        import similarity as similarity_module
        from PIL import Image

        image_path = tmp_path / "changed-before-embedding-save.png"
        with Image.new("RGB", (64, 64), color="white") as image:
            image.save(image_path)
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint="fingerprint-1",
        )

        monkeypatch.setattr(similarity_module, "_get_embed_model", lambda: object())
        monkeypatch.setattr(
            similarity_module,
            "compute_image_content_fingerprint",
            lambda _path: "fingerprint-1",
        )

        def change_database_fingerprint_before_save(_path, model=None):
            with test_db.get_db() as conn:
                conn.execute(
                    "UPDATE images SET content_fingerprint = ? WHERE id = ?",
                    ("fingerprint-2", image_id),
                )
            return np.ones(4, dtype=np.float32)

        monkeypatch.setattr(
            similarity_module,
            "embed_image_file",
            change_database_fingerprint_before_save,
        )
        index = similarity_module.SimilarityIndex(test_db)
        invalidation_calls = []
        monkeypatch.setattr(index, "invalidate_vector_cache", lambda: invalidation_calls.append(True))

        result = index.embed_batch([image_id])

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT embedding, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert result["embedded"] == 0
        assert result["errors"] == 1
        assert result["skipped"] == 1
        assert result["recent_issues"][0]["reason"] == (
            "Image changed before its embedding could be saved; rescan and retry"
        )
        assert invalidation_calls == []
        assert row["embedding"] is None
        assert row["content_fingerprint"] == "fingerprint-2"

    def test_prepare_censor_legacy_returns_structured_conflict_for_civitai_login_wall(self, test_client, monkeypatch):
        import time
        from routers import models as models_router
        from services import model_service

        def raise_auth_wall(self):
            raise model_service.ExternalAuthRequiredError(
                model_service.build_civitai_auth_error(Path("/tmp/privacy-yolo"))
            )

        monkeypatch.setattr(model_service, "ensure_group", lambda _group: model_service.DependencyInstallResult(installed_packages=()))
        monkeypatch.setattr(model_service.ModelService, "download_privacy_yolo_bundle", raise_auth_wall)
        models_router._prepare_result.update(active=False, model_id="", status="", message="", error="")

        response = test_client.post("/api/models/prepare", json={"model_id": "censor-legacy"})
        assert response.status_code == 200
        assert response.json()["status"] == "downloading"

        for _ in range(50):
            time.sleep(0.05)
            prog = test_client.get("/api/models/download-progress").json()
            pr = prog.get("prepare_result", {})
            if not pr.get("active") and pr.get("status") == "error":
                break
        assert pr["status"] == "error"
        assert "Civitai" in pr["message"]

    def test_prepare_optional_dependency_system_python_error_returns_guidance(self, test_client, monkeypatch):
        import time
        from routers import models as models_router
        from services import model_service
        from optional_dependencies import UnsafeDependencyInstallError

        def raise_unsafe_install(self, model_id, source=None, variant=None):
            raise UnsafeDependencyInstallError(
                "Refusing to install optional AI Python packages into the system Python environment. "
                "Start SD Image Sorter with run.bat, run-portable.bat, or run.sh so the app-owned Python runtime is used. "
                "Packages not installed: torch>=2.0.0"
            )

        monkeypatch.setattr(model_service.ModelService, "prepare_model", raise_unsafe_install)
        models_router._prepare_result.update(active=False, model_id="", status="", message="", error="")

        response = test_client.post("/api/models/prepare", json={"model_id": "artist"})
        assert response.status_code == 200

        for _ in range(50):
            time.sleep(0.05)
            prog = test_client.get("/api/models/download-progress").json()
            pr = prog.get("prepare_result", {})
            if not pr.get("active") and pr.get("status") == "error":
                break

        assert pr["status"] == "error"
        assert pr["error_type"] == "UnsafeSystemPythonInstall"
        assert "system Python environment" in pr["message"]
        assert any("run-portable.bat" in step for step in pr["manual_steps"])

    def test_prepare_unsupported_platform_returns_persistent_guidance(self, test_client, monkeypatch):
        import time
        from routers import models as models_router
        from services import model_service
        from optional_dependencies import UnsupportedOptionalDependencyError

        def raise_unsupported_platform(self, model_id, source, variant):
            raise UnsupportedOptionalDependencyError(
                "SAM3 is CUDA-only in the current verified product runtime and is unavailable on macOS."
            )

        monkeypatch.setattr(model_service.ModelService, "prepare_model", raise_unsupported_platform)
        models_router._prepare_result.update(active=False, model_id="", status="", message="", error="")

        response = test_client.post("/api/models/prepare", json={"model_id": "sam3"})
        assert response.status_code == 200

        for _ in range(50):
            time.sleep(0.05)
            prog = test_client.get("/api/models/download-progress").json()
            pr = prog.get("prepare_result", {})
            if not pr.get("active") and pr.get("status") == "error":
                break

        assert pr["status"] == "error"
        assert pr["error_type"] == "UnsupportedPlatformRuntime"
        assert "unavailable on macOS" in pr["message"]
        assert pr["provider"] == "Torch / CUDA runtime"
        assert any("core Gallery" in step for step in pr["manual_steps"])
        assert any("NVIDIA CUDA" in step for step in pr["manual_steps"])

    def test_prepare_censor_legacy_bad_archive_returns_structured_download_failure(self, test_client, monkeypatch):
        import time
        from routers import models as models_router
        from services import model_service

        def raise_prepare_failure(self):
            raise model_service.ModelPreparationFailedError(
                model_service.build_privacy_yolo_prepare_error(
                    Path("/tmp/privacy-yolo"),
                    "Downloaded file was not a valid zip archive.",
                )
            )

        monkeypatch.setattr(model_service, "ensure_group", lambda _group: model_service.DependencyInstallResult(installed_packages=()))
        monkeypatch.setattr(model_service.ModelService, "download_privacy_yolo_bundle", raise_prepare_failure)
        models_router._prepare_result.update(active=False, model_id="", status="", message="", error="")

        response = test_client.post("/api/models/prepare", json={"model_id": "censor-legacy"})
        assert response.status_code == 200
        assert response.json()["status"] == "downloading"

        for _ in range(50):
            time.sleep(0.05)
            prog = test_client.get("/api/models/download-progress").json()
            pr = prog.get("prepare_result", {})
            if not pr.get("active") and pr.get("status") == "error":
                break
        assert pr["status"] == "error"
        assert "Privacy YOLO" in pr["message"] or "preparation failed" in pr["message"].lower()

    def test_similarity_search_upload_and_duplicates_ignore_unreadable_embedded_rows(self, test_db, tmp_path, monkeypatch):
        import similarity as similarity_module
        from PIL import Image

        query_path = tmp_path / "query.png"
        readable_match_path = tmp_path / "readable-match.png"
        unreadable_path = tmp_path / "historical-bad.png"

        Image.new("RGB", (64, 64), color="white").save(query_path)
        Image.new("RGB", (64, 64), color="gray").save(readable_match_path)
        Image.new("RGB", (64, 64), color="black").save(unreadable_path)

        query_id = test_db.add_image(path=str(query_path), filename=query_path.name, metadata_json="{}")
        readable_match_id = test_db.add_image(path=str(readable_match_path), filename=readable_match_path.name, metadata_json="{}")
        unreadable_id = test_db.add_image(
            path=str(unreadable_path),
            filename=unreadable_path.name,
            metadata_json="{}",
            is_readable=False,
            read_error="Truncated File Read",
        )

        embedding = similarity_module.embedding_to_bytes(np.array([1, 0, 0, 0], dtype=np.float32))
        with test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE images SET embedding = ? WHERE id = ?", (embedding, query_id))
            cursor.execute("UPDATE images SET embedding = ? WHERE id = ?", (embedding, readable_match_id))
            cursor.execute("UPDATE images SET embedding = ? WHERE id = ?", (embedding, unreadable_id))

        monkeypatch.setattr(
            similarity_module,
            "embed_image_pil",
            lambda _image: np.array([1, 0, 0, 0], dtype=np.float32),
        )

        upload_buf = io.BytesIO()
        Image.new("RGB", (64, 64), color="white").save(upload_buf, format="PNG")

        index = similarity_module.SimilarityIndex(test_db)

        search_result = index.search_by_id(query_id, limit=10, threshold=0.1)
        assert [item["id"] for item in search_result["results"]] == [readable_match_id]

        upload_result = index.search_by_upload(upload_buf.getvalue(), limit=10, threshold=0.1)
        upload_ids = [item["id"] for item in upload_result["results"]]
        assert unreadable_id not in upload_ids
        assert {query_id, readable_match_id}.issubset(set(upload_ids))

        duplicates_result = index.find_duplicates(threshold=0.99, limit=10, offset=0)
        duplicate_pairs = {
            tuple(sorted((pair["image_a"]["id"], pair["image_b"]["id"])))
            for pair in duplicates_result["duplicates"]
        }
        assert duplicate_pairs == {tuple(sorted((query_id, readable_match_id)))}


class TestArtistsRouterValidation:
    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ({"image_id": 0}, 400),
            ({"image_id": 1, "threshold": 1.1}, 400),
            ({"image_id": 1, "top_k": 0}, 400),
        ],
    )
    def test_identify_rejects_invalid_payloads(self, test_client, payload, expected_status):
        response = test_client.post("/api/artists/identify", json=payload)

        assert response.status_code == expected_status

    def test_identify_batch_rejects_empty_image_ids(self, test_client):
        response = test_client.post("/api/artists/identify-batch", json={"image_ids": []})

        assert response.status_code in [400, 422]

    def test_identify_batch_rejects_unbounded_image_id_lists(self, test_client):
        # Sending one more than ARTIST_BATCH_IMAGE_LIMIT must trip the
        # Pydantic max_length validator. The cap exists so a misbehaving
        # frontend cannot OOM the worker by spamming arbitrary id payloads.
        # Importing the constant keeps this test self-correcting if the
        # ceiling is tuned again.
        from routers.artists import ARTIST_BATCH_IMAGE_LIMIT

        response = test_client.post(
            "/api/artists/identify-batch",
            json={"image_ids": list(range(1, ARTIST_BATCH_IMAGE_LIMIT + 2))},
        )

        assert response.status_code == 400

    def test_identify_batch_rejects_local_model_without_path(self, test_client):
        response = test_client.post(
            "/api/artists/identify-batch",
            json={"image_ids": [1], "model_source": "local"},
        )

        assert response.status_code == 400

    def test_identify_batch_rejects_missing_local_model_file(self, test_client):
        response = test_client.post(
            "/api/artists/identify-batch",
            json={"image_ids": [1], "model_source": "local", "model_path": "C:/missing/artist.onnx"},
        )

        assert response.status_code == 400

    def test_identify_returns_503_when_model_is_unavailable(self, test_client, monkeypatch, tmp_path):
        from routers import artists as artists_router
        from PIL import Image

        image_path = tmp_path / "artist_test.png"
        Image.new("RGB", (64, 64), color="purple").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="artist_test.png",
            metadata_json="{}",
        )

        class FakeIdentifier:
            def identify(self, _image_path, top_k=5):
                return {"error": "Artist model unavailable. Install the required dependencies and restart the app."}

            def identify_with_threshold(self, _image_path, top_k, _threshold, **_kwargs):
                return self.identify(_image_path, top_k=top_k)

        monkeypatch.setattr(artists_router, "get_artist_identifier", lambda **kwargs: FakeIdentifier())

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 503
        assert "Artist model unavailable" in response.json()["error"]

    def test_identify_uses_low_default_threshold(self, test_client, monkeypatch, tmp_path):
        from artist_identifier import ARTIST_THRESHOLD_DEFAULT
        from routers import artists as artists_router
        from PIL import Image

        image_path = tmp_path / "artist_threshold.png"
        Image.new("RGB", (64, 64), color="orange").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="artist_threshold.png",
            metadata_json="{}",
        )

        captured = {}

        class FakeIdentifier:
            def identify(self, _image_path, top_k=5):
                return {
                    "artist": "undefined",
                    "confidence": 0.02,
                    "top_predictions": [{"artist": "artist_a", "confidence": 0.02}],
                    "model_loaded": True,
                }

            def identify_with_threshold(self, _image_path, top_k, _threshold, **_kwargs):
                return self.identify(_image_path, top_k=top_k)

        def fake_get_identifier(**kwargs):
            captured.update(kwargs)
            return FakeIdentifier()

        monkeypatch.setattr(artists_router, "get_artist_identifier", fake_get_identifier)

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 200
        assert captured["threshold"] == ARTIST_THRESHOLD_DEFAULT

    def test_identify_route_dispatches_model_work_to_threadpool(self):
        import asyncio
        import threading
        from routers import artists as artists_router

        event_loop_thread_id = threading.get_ident()
        captured = {}

        class FakeService:
            def identify_image(self, **kwargs):
                captured["inline_thread_id"] = threading.get_ident()
                captured["kwargs"] = kwargs
                return {
                    "image_id": kwargs["image_id"],
                    "artist": "fixture_artist",
                    "confidence": 0.91,
                    "top_predictions": [{"artist": "fixture_artist", "confidence": 0.91}],
                    "model_loaded": True,
                    "experimental": True,
                }

        async def fake_run_in_threadpool(func, **kwargs):
            captured["dispatched_func"] = func
            captured["dispatched_kwargs"] = kwargs
            return func(**kwargs)

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(artists_router, "run_in_threadpool", fake_run_in_threadpool)
            response = asyncio.run(
                artists_router.identify_artist(
                    artists_router.IdentifyRequest(image_id=123, threshold=0.12, top_k=3),
                    service=FakeService(),
                )
            )
        finally:
            monkeypatch.undo()

        assert response.image_id == 123
        assert captured["dispatched_func"].__name__ == "identify_image"
        assert captured["dispatched_kwargs"] == {
            "image_id": 123,
            "threshold": 0.12,
            "top_k": 3,
            "model_source": "huggingface",
            "model_path": None,
            "use_gpu": None,
        }
        assert captured["inline_thread_id"] == event_loop_thread_id

    def test_e2e_fake_artist_identifier_writes_prediction_without_real_runtime(self, test_client, monkeypatch, tmp_path):
        from routers import artists as artists_router
        from PIL import Image

        image_path = tmp_path / "artist_e2e_fixture.png"
        Image.new("RGB", (64, 64), color="cyan").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename="artist_e2e_fixture.png",
            metadata_json="{}",
        )

        monkeypatch.setenv("SD_IMAGE_SORTER_E2E_FAKE_ARTIST", "1")
        artists_router.set_artist_service(None)

        try:
            response = test_client.post(
                "/api/artists/identify",
                json={"image_id": image_id, "threshold": 0.0, "top_k": 2},
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["artist"] == "fixture_artist"
            assert payload["model_loaded"] is True

            with test_client.test_db.get_db() as conn:
                row = conn.execute(
                    """
                    SELECT ap.artist, ap.confidence, ap.top_predictions, i.content_fingerprint
                    FROM artist_predictions ap
                    JOIN images i ON i.id = ap.image_id
                    WHERE ap.image_id = ?
                    """,
                    (image_id,),
                ).fetchone()

            assert row is not None
            assert row["artist"] == "fixture_artist"
            assert row["confidence"] == pytest.approx(0.97)
            assert "fixture_artist" in row["top_predictions"]
            assert str(row["content_fingerprint"] or "").strip()
        finally:
            artists_router.set_artist_service(None)

    def test_identify_rejects_pixels_changed_during_model_inference(self, test_client, monkeypatch, tmp_path):
        from image_fingerprint import compute_image_content_fingerprint
        from routers import artists as artists_router

        image_path = tmp_path / "artist-changed-during-inference.png"
        with Image.new("RGB", (64, 64), color="white") as original:
            original.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        class ReplacingIdentifier:
            def identify_with_threshold(self, _image_path, _top_k, _threshold, **_kwargs):
                with Image.new("RGB", (64, 64), color="black") as replacement:
                    replacement.save(image_path)
                return {
                    "artist": "stale_artist",
                    "confidence": 0.92,
                    "top_predictions": [{"artist": "stale_artist", "confidence": 0.92}],
                    "model_loaded": True,
                }

        monkeypatch.setattr(artists_router, "get_artist_identifier", lambda **_kwargs: ReplacingIdentifier())

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "Image pixels changed while Artist Identification was running; rescan and retry"
        )
        with test_client.test_db.get_db() as conn:
            prediction = conn.execute(
                "SELECT artist FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            ).fetchone()
            stored_fingerprint = conn.execute(
                "SELECT content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()[0]
        assert prediction is None
        assert stored_fingerprint == original_fingerprint

    def test_identify_rejects_known_stale_index_before_model_inference(self, test_client, monkeypatch, tmp_path):
        from routers import artists as artists_router

        image_path = tmp_path / "artist-stale-index.png"
        with Image.new("RGB", (64, 64), color="green") as image:
            image.save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint="outdated-fingerprint",
        )
        identify_calls = []

        class UnexpectedIdentifier:
            def identify_with_threshold(self, *_args, **_kwargs):
                identify_calls.append(True)
                raise AssertionError("Artist inference must not run for a stale library row")

        monkeypatch.setattr(artists_router, "get_artist_identifier", lambda **_kwargs: UnexpectedIdentifier())

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "Image pixels changed since the library index was updated; rescan and retry"
        )
        assert identify_calls == []

    def test_identify_preserves_newer_prediction_when_database_fingerprint_changes_before_save(
        self,
        test_client,
        monkeypatch,
        tmp_path,
    ):
        from image_fingerprint import compute_image_content_fingerprint
        from routers import artists as artists_router

        image_path = tmp_path / "artist-database-changed-before-save.png"
        with Image.new("RGB", (64, 64), color="purple") as image:
            image.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_client.test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        class RacingIdentifier:
            def identify_with_threshold(self, _image_path, _top_k, _threshold, **_kwargs):
                with test_client.test_db.get_db() as conn:
                    conn.execute(
                        "UPDATE images SET content_fingerprint = ? WHERE id = ?",
                        ("newer-fingerprint", image_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO artist_predictions
                            (image_id, artist, confidence, top_predictions)
                        VALUES (?, ?, ?, ?)
                        """,
                        (image_id, "newer_artist", 0.99, "[{'artist': 'newer_artist'}]"),
                    )
                return {
                    "artist": "stale_artist",
                    "confidence": 0.45,
                    "top_predictions": [{"artist": "stale_artist", "confidence": 0.45}],
                    "model_loaded": True,
                }

        monkeypatch.setattr(artists_router, "get_artist_identifier", lambda **_kwargs: RacingIdentifier())

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "Image changed before its Artist prediction could be saved; rescan and retry"
        )
        with test_client.test_db.get_db() as conn:
            row = conn.execute(
                """
                SELECT i.content_fingerprint, ap.artist, ap.confidence
                FROM images i
                JOIN artist_predictions ap ON ap.image_id = i.id
                WHERE i.id = ?
                """,
                (image_id,),
            ).fetchone()
        assert row["content_fingerprint"] == "newer-fingerprint"
        assert row["artist"] == "newer_artist"
        assert row["confidence"] == 0.99

    def test_identify_batch_passes_model_configuration_to_background_task(self, test_client, monkeypatch, tmp_path):
        from routers import artists as artists_router

        captured = {}
        model_path = tmp_path / "artist.onnx"
        model_path.write_bytes(b"fake-model")
        service = artists_router.get_artist_service()
        service.set_batch_progress_state({
            "running": False,
            "total": 0,
            "processed": 0,
            "errors": 0,
            "results": [],
            "step": "idle",
            "message": "",
            "current_item": None,
            "started_at": None,
            "updated_at": None,
        })

        def fake_run_batch(image_ids, threshold, top_k, model_source, model_path, use_gpu=None):
            captured["image_ids"] = image_ids
            captured["threshold"] = threshold
            captured["top_k"] = top_k
            captured["model_source"] = model_source
            captured["model_path"] = model_path
            captured["use_gpu"] = use_gpu
            service.set_batch_progress_state({
                "running": False,
                "total": len(image_ids),
                "processed": len(image_ids),
                "errors": 0,
                "results": [],
                "step": "done",
                "message": "done",
                "current_item": None,
                "started_at": 0.0,
                "updated_at": 0.0,
            })

        monkeypatch.setattr(artists_router, "_run_batch_identification", fake_run_batch)

        response = test_client.post(
            "/api/artists/identify-batch",
            json={
                "image_ids": [1, 2],
                "threshold": 0.42,
                "top_k": 7,
                "model_source": "local",
                "model_path": str(model_path),
                "use_gpu": False,
            },
        )

        assert response.status_code == 200
        assert captured == {
            "image_ids": [1, 2],
            "threshold": 0.42,
            "top_k": 7,
            "model_source": "local",
            "model_path": str(model_path.resolve()),
            "use_gpu": False,
        }

    def test_identify_batch_uses_low_default_threshold(self, test_client, monkeypatch):
        from artist_identifier import ARTIST_THRESHOLD_DEFAULT
        from routers import artists as artists_router

        captured = {}
        service = artists_router.get_artist_service()
        service.set_batch_progress_state({
            "running": False,
            "total": 0,
            "processed": 0,
            "errors": 0,
            "results": [],
            "step": "idle",
            "message": "",
            "current_item": None,
            "started_at": None,
            "updated_at": None,
        })

        def fake_run_batch(image_ids, threshold, top_k, model_source, model_path, use_gpu=None):
            captured["image_ids"] = image_ids
            captured["threshold"] = threshold
            captured["top_k"] = top_k
            captured["model_source"] = model_source
            captured["model_path"] = model_path
            service.set_batch_progress_state({
                "running": False,
                "total": len(image_ids),
                "processed": len(image_ids),
                "errors": 0,
                "results": [],
                "step": "done",
                "message": "done",
                "current_item": None,
                "started_at": 0.0,
                "updated_at": 0.0,
            })

        monkeypatch.setattr(artists_router, "_run_batch_identification", fake_run_batch)

        response = test_client.post("/api/artists/identify-batch", json={"image_ids": [1, 2]})

        assert response.status_code == 200
        assert captured["threshold"] == ARTIST_THRESHOLD_DEFAULT
        assert captured["top_k"] == 5
        assert captured["model_source"] == "huggingface"
        assert captured["model_path"] is None

    def test_artist_diagnostics_returns_runtime_payload(self, test_client):
        response = test_client.get("/api/artists/diagnostics")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "available" in data
        assert "message" in data

    def test_artist_diagnostics_reports_ready_for_e2e_fake_runtime(self, test_client, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_E2E_FAKE_ARTIST", "1")

        response = test_client.get("/api/artists/diagnostics")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["runtime_loaded"] is True
        assert data["runtime_backend"] == "e2e-fixture"
        assert data["missing_dependencies"] == []

    def test_artist_stats_include_artist_confidence_summary(self, test_client, test_db):
        image_id = test_db.add_image(path="/tmp/artist-test.png", filename="artist-test.png", metadata_json="{}")
        with test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                VALUES (?, ?, ?, ?)
                """,
                (image_id, "sample_artist", 0.42, "[]"),
            )

        response = test_client.get("/api/artists/stats")

        assert response.status_code == 200
        data = response.json()
        assert "artist_stats" in data
        assert data["artist_stats"]["sample_artist"]["avg_confidence"] == 0.42
        assert data["artist_stats"]["sample_artist"]["max_confidence"] == 0.42

    def test_artist_clear_rejects_running_batch_and_preserves_predictions(self, test_client, test_db):
        from routers import artists as artists_router

        image_id = test_db.add_image(
            path="/tmp/artist-clear-conflict.png",
            filename="artist-clear-conflict.png",
            metadata_json="{}",
        )
        with test_db.get_db() as conn:
            conn.execute(
                """
                INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                VALUES (?, ?, ?, ?)
                """,
                (image_id, "sample_artist", 0.92, "[]"),
            )

        service = artists_router.get_artist_service()
        service.start_batch_progress(total=1)
        try:
            conflict = test_client.delete("/api/artists/clear")

            assert conflict.status_code == 409
            assert conflict.json()["error"] == (
                "Artist identification is already in progress; "
                "wait for it to finish before clearing predictions"
            )
            with test_db.get_db() as conn:
                assert conn.execute("SELECT COUNT(*) FROM artist_predictions").fetchone()[0] == 1
        finally:
            service.finish_batch_progress_done({"processed": 0, "total": 1, "errors": 0})

        cleared = test_client.delete("/api/artists/clear")

        assert cleared.status_code == 200
        with test_db.get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM artist_predictions").fetchone()[0] == 0

    def test_artist_images_support_offset_pagination(self, test_client, test_db):
        image_ids = [
            test_db.add_image(path=f"/tmp/artist-page-{index}.png", filename=f"artist-page-{index}.png", metadata_json="{}")
            for index in range(3)
        ]
        confidences = [0.9, 0.8, 0.7]
        with test_db.get_db() as conn:
            cursor = conn.cursor()
            for image_id, confidence in zip(image_ids, confidences):
                cursor.execute(
                    """
                    INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                    VALUES (?, ?, ?, ?)
                    """,
                    (image_id, "paged_artist", confidence, "[]"),
                )

        first_page = test_client.get("/api/artists/images/paged_artist?limit=2&offset=0")
        second_page = test_client.get("/api/artists/images/paged_artist?limit=2&offset=2")

        assert first_page.status_code == 200
        assert second_page.status_code == 200

        first_data = first_page.json()
        second_data = second_page.json()
        assert first_data["total"] == 3
        assert first_data["has_more"] is True
        assert first_data["offset"] == 0
        assert len(first_data["images"]) == 2
        assert second_data["total"] == 3
        assert second_data["has_more"] is False
        assert second_data["offset"] == 2
        assert len(second_data["images"]) == 1

    def test_model_manager_status_endpoint_lists_core_models(self, test_client):
        response = test_client.get("/api/models/status")

        assert response.status_code == 200
        data = response.json()
        model_ids = {item["id"] for item in data["models"]}
        assert {"wd14", "clip", "artist", "censor-legacy", "censor-nudenet", "sam3"}.issubset(model_ids)

    def test_bulk_bundle_endpoint_excludes_wenaka_and_toriigate(self, test_client):
        """The "Download all recommended models" button intentionally
        skips Wenaka Privacy YOLO (opt-in) and ToriiGate (5 GB
        alternative tagger). The default WD14 variant is selected
        instead of all WD14 models."""
        response = test_client.get("/api/models/bulk-bundle")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        item_ids = {item["id"] for item in data["items"]}
        # Must be present
        assert {
            "wd14",
            "censor-nudenet",
            "clip",
            "aesthetic",
            "artist",
            "sam3",
            "florence2",
            "lucida",
            "cl-tagger-v2",
        }.issubset(item_ids)
        # Must NOT be present (per user spec)
        assert "censor-legacy" not in item_ids
        assert "toriigate" not in item_ids
        # WD14 entry pins the default variant
        wd14 = next(it for it in data["items"] if it["id"] == "wd14")
        assert wd14["variant"] == "wd-swinv2-tagger-v3"
        assert wd14["recommended"] is True
        assert wd14["default_selected"] is True
        cl_tagger = next(it for it in data["items"] if it["id"] == "cl-tagger-v2")
        assert cl_tagger["recommended"] is False
        assert cl_tagger["default_selected"] is False
        assert cl_tagger["gated_download"] is True
        assert cl_tagger["requires_auth"] is True
        # Excluded list documents the rationale
        excluded_ids = {e["id"] for e in data.get("excluded", [])}
        assert {"censor-legacy", "toriigate"}.issubset(excluded_ids)
        # Total bytes reported
        assert isinstance(data.get("pending_total_bytes"), int)
        assert isinstance(data.get("recommended_pending_total_bytes"), int)
        assert isinstance(data.get("optional_pending_total_bytes"), int)
        assert isinstance(data.get("all_total_bytes"), int)
        assert data["all_total_bytes"] > 0


class TestDerivedWriterPathResolution:
    def test_aesthetic_service_resolves_windows_indexed_path_before_scoring(self, test_db, tmp_path, monkeypatch):
        from services import aesthetic_service as aesthetic_service_module
        from PIL import Image

        resolved_path = tmp_path / "resolved-aesthetic.png"
        Image.new("RGB", (64, 64), color="white").save(resolved_path)

        indexed_windows_path = r"L:\datasets\resolved-aesthetic.png"
        image_id = test_db.add_image(
            path=indexed_windows_path,
            filename="resolved-aesthetic.png",
            metadata_json="{}",
        )

        monkeypatch.setattr(
            aesthetic_service_module,
            "resolve_existing_indexed_image_path",
            lambda primary_path, *, backend_file: str(resolved_path) if primary_path == indexed_windows_path else None,
        )

        captured = {}

        def fake_predict(path: str):
            captured["path"] = path
            return 7.25

        service = aesthetic_service_module.AestheticService()
        result = service.score_single_image(image_id=image_id, predict_score=fake_predict)

        assert result["image_id"] == image_id
        assert result["aesthetic_score"] == 7.25
        assert captured["path"] == str(resolved_path)

    def test_aesthetic_single_rejects_pixels_changed_during_inference(
        self,
        test_db,
        tmp_path,
    ):
        from exceptions import ServiceError
        from image_fingerprint import compute_image_content_fingerprint
        from services.aesthetic_service import AestheticService

        image_path = tmp_path / "aesthetic-single-changed-during-inference.png"
        with Image.new("RGB", (64, 64), color="white") as original:
            original.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        def replace_source(_image_path: str) -> float:
            with Image.new("RGB", (64, 64), color="black") as replacement:
                replacement.save(image_path)
            return 9.75

        service = AestheticService()
        with pytest.raises(
            ServiceError,
            match="Image pixels changed while Aesthetic Scoring was running; rescan and retry",
        ):
            service.score_single_image(
                image_id=image_id,
                predict_score=replace_source,
            )

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT aesthetic_score, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert row["aesthetic_score"] is None
        assert row["content_fingerprint"] == original_fingerprint

    def test_aesthetic_rejects_known_database_fingerprint_mismatch_before_inference(
        self,
        test_db,
        tmp_path,
    ):
        from exceptions import ServiceError
        from services.aesthetic_service import AestheticService

        image_path = tmp_path / "aesthetic-index-stale.png"
        with Image.new("RGB", (64, 64), color="white") as image:
            image.save(image_path)
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint="outdated-fingerprint",
        )
        inference_called = False

        def unexpected_inference(_image_path: str) -> float:
            nonlocal inference_called
            inference_called = True
            return 9.75

        service = AestheticService()
        with pytest.raises(
            ServiceError,
            match="Image pixels changed since the library index was updated; rescan and retry",
        ):
            service.score_single_image(
                image_id=image_id,
                predict_score=unexpected_inference,
            )

        assert inference_called is False

    def test_aesthetic_single_preserves_newer_score_when_database_changes_before_publish(
        self,
        test_db,
        tmp_path,
    ):
        from exceptions import ServiceError
        from image_fingerprint import compute_image_content_fingerprint
        from services.aesthetic_service import AestheticService

        image_path = tmp_path / "aesthetic-single-publish-conflict.png"
        with Image.new("RGB", (64, 64), color="white") as image:
            image.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        def replace_database_state(_image_path: str) -> float:
            with test_db.get_db() as conn:
                conn.execute(
                    """
                    UPDATE images
                    SET content_fingerprint = ?, aesthetic_score = ?
                    WHERE id = ?
                    """,
                    ("newer-fingerprint", 9.5, image_id),
                )
            return 2.0

        service = AestheticService()
        with pytest.raises(
            ServiceError,
            match="Image changed before its Aesthetic score could be saved; rescan and retry",
        ):
            service.score_single_image(
                image_id=image_id,
                predict_score=replace_database_state,
            )

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT aesthetic_score, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert row["aesthetic_score"] == 9.5
        assert row["content_fingerprint"] == "newer-fingerprint"

    def test_aesthetic_batch_counts_pixels_changed_during_inference_only_as_an_error(
        self,
        test_db,
        tmp_path,
        caplog,
        monkeypatch,
    ):
        from image_fingerprint import compute_image_content_fingerprint
        from services.aesthetic_service import AestheticService

        image_path = tmp_path / "aesthetic-batch-changed-during-inference.png"
        with Image.new("RGB", (64, 64), color="white") as original:
            original.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        def replace_source(_image_path: str) -> float:
            with Image.new("RGB", (64, 64), color="black") as replacement:
                replacement.save(image_path)
            return 9.75

        progress_updates = []
        service = AestheticService()
        monkeypatch.setattr(service, "_gpu_cleanup", lambda: None)
        service.score_batch(
            force=False,
            predict_score=replace_source,
            progress_callback=progress_updates.append,
        )

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT aesthetic_score, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert row["aesthetic_score"] is None
        assert row["content_fingerprint"] == original_fingerprint
        assert [update["errors"] for update in progress_updates if "errors" in update] == [0, 1]
        assert max(int(update.get("completed", 0)) for update in progress_updates) == 1
        assert "Image pixels changed while Aesthetic Scoring was running; rescan and retry" in caplog.text

    def test_aesthetic_batch_counts_database_publish_conflict_once_and_preserves_newer_score(
        self,
        test_db,
        tmp_path,
        caplog,
        monkeypatch,
    ):
        from image_fingerprint import compute_image_content_fingerprint
        from services.aesthetic_service import AestheticService

        image_path = tmp_path / "aesthetic-batch-publish-conflict.png"
        with Image.new("RGB", (64, 64), color="white") as image:
            image.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        def replace_database_state(_image_path: str) -> float:
            with test_db.get_db() as conn:
                conn.execute(
                    """
                    UPDATE images
                    SET content_fingerprint = ?, aesthetic_score = ?
                    WHERE id = ?
                    """,
                    ("newer-fingerprint", 9.5, image_id),
                )
            return 2.0

        progress_updates = []
        service = AestheticService()
        monkeypatch.setattr(service, "_gpu_cleanup", lambda: None)
        service.score_batch(
            force=False,
            predict_score=replace_database_state,
            progress_callback=progress_updates.append,
        )

        with test_db.get_db() as conn:
            row = conn.execute(
                "SELECT aesthetic_score, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        assert row["aesthetic_score"] == 9.5
        assert row["content_fingerprint"] == "newer-fingerprint"
        assert [update["errors"] for update in progress_updates if "errors" in update] == [0, 1]
        assert max(int(update.get("completed", 0)) for update in progress_updates) == 1
        assert "Image changed before its Aesthetic score could be saved; rescan and retry" in caplog.text

    def test_artist_service_batch_resolves_windows_indexed_path_before_identify(self, test_db, tmp_path, monkeypatch):
        from services import artist_service as artist_service_module
        from PIL import Image

        resolved_a = tmp_path / "resolved-artist-a.png"
        resolved_b = tmp_path / "resolved-artist-b.png"
        Image.new("RGB", (64, 64), color="blue").save(resolved_a)
        Image.new("RGB", (64, 64), color="green").save(resolved_b)

        indexed_a = r"L:\datasets\resolved-artist-a.png"
        indexed_b = r"L:\datasets\resolved-artist-b.png"
        image_a = test_db.add_image(path=indexed_a, filename="resolved-artist-a.png", metadata_json="{}")
        image_b = test_db.add_image(path=indexed_b, filename="resolved-artist-b.png", metadata_json="{}")

        resolved_map = {
            indexed_a: str(resolved_a),
            indexed_b: str(resolved_b),
        }

        monkeypatch.setattr(
            artist_service_module,
            "resolve_existing_indexed_image_path",
            lambda primary_path, *, backend_file: resolved_map.get(primary_path),
        )

        captured_paths = []

        class FakeIdentifier:
            def identify(self, image_path, top_k=5):
                captured_paths.append(image_path)
                return {
                    "artist": "artist_x",
                    "confidence": 0.77,
                    "top_predictions": [{"artist": "artist_x", "confidence": 0.77}],
                    "model_loaded": True,
                }

            def identify_with_threshold(self, image_path, top_k, _threshold, **_kwargs):
                return self.identify(image_path, top_k=top_k)

        service = artist_service_module.ArtistService(identifier_getter=lambda **kwargs: FakeIdentifier())
        result = service.run_batch_identification(
            image_ids=[image_a, image_b],
            threshold=0.1,
            top_k=3,
        )

        assert result["errors"] == 0
        assert result["processed"] == 2
        assert captured_paths == [str(resolved_a), str(resolved_b)]

    def test_artist_batch_counts_pixels_changed_during_inference_only_as_an_error(
        self,
        test_db,
        tmp_path,
        caplog,
    ):
        from image_fingerprint import compute_image_content_fingerprint
        from services import artist_service as artist_service_module

        image_path = tmp_path / "artist-batch-changed-during-inference.png"
        with Image.new("RGB", (64, 64), color="white") as original:
            original.save(image_path)
        original_fingerprint = compute_image_content_fingerprint(str(image_path))
        image_id = test_db.add_image(
            path=str(image_path),
            filename=image_path.name,
            metadata_json="{}",
            content_fingerprint=original_fingerprint,
        )

        class ReplacingIdentifier:
            def identify_with_threshold(self, _image_path, _top_k, _threshold, **_kwargs):
                with Image.new("RGB", (64, 64), color="black") as replacement:
                    replacement.save(image_path)
                return {
                    "artist": "stale_artist",
                    "confidence": 0.91,
                    "top_predictions": [{"artist": "stale_artist", "confidence": 0.91}],
                    "model_loaded": True,
                }

        progress_updates = []
        service = artist_service_module.ArtistService(
            identifier_getter=lambda **_kwargs: ReplacingIdentifier()
        )

        result = service.run_batch_identification(
            image_ids=[image_id],
            threshold=0.1,
            top_k=3,
            progress_callback=progress_updates.append,
        )

        with test_db.get_db() as conn:
            prediction = conn.execute(
                "SELECT artist FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        assert result == {
            "total": 1,
            "processed": 0,
            "errors": 1,
            "results": [],
        }
        assert sum(int(update.get("processed_delta", 0)) for update in progress_updates) == 0
        assert sum(int(update.get("errors_delta", 0)) for update in progress_updates) == 1
        assert prediction is None
        assert "Image pixels changed while Artist Identification was running; rescan and retry" in caplog.text


class TestArtistServiceConcurrency:
    def test_concurrent_identify_requests_keep_threshold_request_local(
        self,
        monkeypatch,
        tmp_path,
    ):
        from services.artist_service import ArtistService

        image_paths = {}
        for image_id, name in ((1, "threshold-low.png"), (2, "threshold-high.png")):
            image_path = tmp_path / name
            Image.new("RGB", (32, 32), color="blue").save(image_path)
            image_paths[image_id] = str(image_path)

        identify_barrier = threading.Barrier(2)

        class SharedIdentifier:
            def __init__(self):
                self.threshold = 0.03

            @staticmethod
            def _result(threshold):
                confidence = 0.5
                artist = "artist_a" if confidence >= threshold else "undefined"
                return {
                    "artist": artist,
                    "confidence": confidence,
                    "top_predictions": [
                        {"artist": "artist_a", "confidence": confidence},
                    ],
                    "model_loaded": True,
                }

            def identify(self, _image_path, top_k=5):
                identify_barrier.wait(timeout=1)
                return self._result(self.threshold)

            def identify_with_threshold(self, _image_path, top_k, threshold, **_kwargs):
                identify_barrier.wait(timeout=1)
                return self._result(threshold)

        identifier = SharedIdentifier()

        def get_identifier(**kwargs):
            identifier.threshold = float(kwargs["threshold"])
            return identifier

        service = ArtistService(identifier_getter=get_identifier)
        monkeypatch.setattr(service, "_get_image_path", lambda image_id: image_paths[image_id])
        monkeypatch.setattr(
            service,
            "_prepare_source_fingerprint",
            lambda **_kwargs: "fixture-fingerprint",
        )
        monkeypatch.setattr(service, "_verify_source_fingerprint", lambda **_kwargs: None)
        monkeypatch.setattr(service, "_store_prediction", lambda **_kwargs: True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            low_future = executor.submit(
                service.identify_image,
                image_id=1,
                threshold=0.4,
                top_k=1,
            )
            high_future = executor.submit(
                service.identify_image,
                image_id=2,
                threshold=0.6,
                top_k=1,
            )
            low_result = low_future.result(timeout=3)
            high_result = high_future.result(timeout=3)

        assert low_result["artist"] == "artist_a"
        assert high_result["artist"] == "undefined"


class TestPromptGenerator:
    def test_generate_uses_manual_promptlab_categories_without_random_fallbacks(self):
        from prompt_generator import PromptGenerator

        generator = PromptGenerator()
        result = generator.generate(
            {
                "quality_preset": "low",
                "count_tag": "",
                "include_negative": False,
                "categories": {
                    "style": {"tags": ["cinematic_lighting"], "weight": 1.0, "locked": True},
                    "pose": {"tags": ["standing"], "weight": 0.5, "locked": False},
                    "background": {"tags": ["city_night"], "weight": 1.0, "locked": False},
                },
            }
        )

        assert result["positive_prompt"] == "cinematic_lighting, standing, city_night"
        assert result["negative_prompt"] == ""
        assert [tag["tag"] for tag in result["tags_used"]] == [
            "cinematic_lighting",
            "standing",
            "city_night",
        ]

    def test_load_from_db_resets_user_state_and_moves_recategorized_tags(self, test_db):
        from prompt_generator import PromptGenerator

        image_a = test_db.add_image(path="/tmp/a.png", filename="a.png", metadata_json="{}")
        image_b = test_db.add_image(path="/tmp/b.png", filename="b.png", metadata_json="{}")
        test_db.add_tags(image_a, [{"tag": "school_uniform", "confidence": 0.9}])
        test_db.add_tags(image_b, [{"tag": "school_uniform", "confidence": 0.8}])

        with test_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tag_categories (tag, category, is_user_defined) VALUES (?, ?, 1)",
                ("school_uniform", "style"),
            )
            cursor.execute(
                "INSERT INTO tag_sets (name, description, category) VALUES (?, ?, ?)",
                ("Uniform Combo", "test set", "outfit"),
            )
            set_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO tag_set_members (set_id, tag, weight, is_required) VALUES (?, ?, ?, ?)",
                (set_id, "school_uniform", 1.0, 1),
            )
            cursor.execute(
                "INSERT INTO tag_exclusions (rule_name, description) VALUES (?, ?)",
                ("No Uniform Clash", "test rule"),
            )
            rule_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO tag_exclusion_conditions (exclusion_id, condition_tag, condition_type) VALUES (?, ?, ?)",
                (rule_id, "school_uniform", "present"),
            )
            cursor.execute(
                "INSERT INTO tag_exclusion_targets (exclusion_id, excluded_tag, excluded_category) VALUES (?, ?, ?)",
                (rule_id, "swimsuit", "outfit"),
            )

        generator = PromptGenerator(db_module=test_db)
        generator.load_from_db()
        generator.load_from_db()

        pool = generator.get_tag_pool()
        assert any(tag["tag"] == "school_uniform" for tag in pool.get("style", []))
        assert not any(tag["tag"] == "school_uniform" for tag in pool.get("outfit", []))

        user_sets = [tag_set for tag_set in generator.get_all_tag_sets() if tag_set.get("id") == set_id]
        user_rules = [rule for rule in generator.get_all_rules() if rule.get("id") == rule_id]
        assert len(user_sets) == 1
        assert len(user_rules) == 1

    def test_resolve_tag_sets_supports_stable_ids_and_legacy_indexes(self):
        from prompt_generator import PromptGenerator

        generator = PromptGenerator()
        first_set = generator.get_all_tag_sets()[0]

        resolved = generator._resolve_tag_sets([first_set["id"], "1", first_set["name"]])

        assert resolved == [first_set]
