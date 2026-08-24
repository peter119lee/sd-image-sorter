"""TIPO tag-upsampling assist (roadmap #8, v1): POST /api/tags/suggest-upsample.

The generator itself is an opt-in llama.cpp GGUF runtime, so these tests
monkeypatch ``tipo_service._generate_candidates`` (the documented stub seam)
and lock the endpoint contract around it: input-tag dedup, the shared vocab
gate, the 40-proposal cap, request validation, and the actionable 400 when
the dependency pair is missing (precedent: rembg in test_mask_service.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import tipo_service


@pytest.fixture
def stub_generator(monkeypatch):
    """Replace the TIPO runtime with a canned candidate list; records calls."""
    calls = []

    def install(candidates):
        def fake_generate(input_tags, rating, aspect_ratio, target, model_key):
            calls.append(
                {
                    "input_tags": list(input_tags),
                    "rating": rating,
                    "aspect_ratio": aspect_ratio,
                    "target": target,
                    "model_key": model_key,
                }
            )
            return list(candidates)

        monkeypatch.setattr(tipo_service, "_generate_candidates", fake_generate)
        return calls

    return install


class TestMissingDependency:
    def test_missing_deps_yield_actionable_400(self, test_client, monkeypatch):
        # Force the opt-in dependency pair absent even if the dev env has it.
        monkeypatch.setitem(sys.modules, "llama_cpp", None)
        monkeypatch.setitem(sys.modules, "kgen", None)
        response = test_client.post(
            "/api/tags/suggest-upsample", json={"tags": ["1girl", "solo"]}
        )
        assert response.status_code == 400
        error = response.json()["error"]
        assert "--only-binary=llama-cpp-python" in error
        assert "abetlen.github.io/llama-cpp-python/whl/cpu" in error
        assert "tipo-kgen" in error
        assert "未安装" in error, "message must be bilingual"


class TestProposalPostProcessing:
    def test_input_echo_hallucination_and_rating_are_dropped(
        self, test_client, stub_generator
    ):
        stub_generator(
            [
                "Long_Hair",  # input echo, case-folded
                "long hair",  # input echo, underscore-folded
                "1girl",  # input echo, verbatim
                "smile",  # legit new vocab tag
                "blue sky",  # legit new vocab tag (space spelling)
                "smile",  # duplicate of an accepted proposal
                "explicit",  # rating word — gate always drops
                "blorbo_snorf_xyz",  # out-of-vocab hallucination — gate drops
            ]
        )
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl", "long_hair"], "target": "short"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        proposed = [entry["tag"] for entry in body["proposed_tags"]]
        assert proposed == ["smile", "blue_sky"]
        for entry in body["proposed_tags"]:
            assert entry["category"], "every proposal carries a category"
        assert body["model"] == "v2.1"
        assert body["input_tags"] == 2
        assert isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0

    def test_proposals_capped_at_40(self, test_client, stub_generator):
        from services.tag_suggest_service import get_vocab_tag_index
        from services.vlm_tag_gate import _is_rating_word

        vocab = [
            tag
            for tag in get_vocab_tag_index().keys()
            if tag not in {"1girl"} and not _is_rating_word(tag)
        ][:60]
        assert len(vocab) == 60, "bundled vocabulary must be available for this test"
        stub_generator(vocab)
        response = test_client.post(
            "/api/tags/suggest-upsample", json={"tags": ["1girl"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["proposed_tags"]) == tipo_service.MAX_PROPOSALS == 40

    def test_generator_receives_stripped_inputs_and_options(
        self, test_client, stub_generator
    ):
        calls = stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={
                "tags": ["  1girl  ", "", "long_hair"],
                "rating": "sensitive",
                "aspect_ratio": 1.5,
                "target": "long",
                "model": "100m",
            },
        )
        assert response.status_code == 200, response.text
        assert calls == [
            {
                "input_tags": ["1girl", "long_hair"],
                "rating": "sensitive",
                "aspect_ratio": 1.5,
                "target": "long",
                "model_key": "100m",
            }
        ]
        assert response.json()["model"] == "100m"

    def test_v21_maps_danbooru_general_rating_to_safe(
        self, test_client, stub_generator
    ):
        calls = stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "rating": "general"},
        )
        assert response.status_code == 200, response.text
        assert calls[0]["rating"] == "safe"
        assert calls[0]["model_key"] == "v2.1"

    def test_v21_maps_explicit_rating_to_nsfw_explicit(
        self, test_client, stub_generator
    ):
        calls = stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "rating": "explicit"},
        )
        assert response.status_code == 200, response.text
        assert calls[0]["rating"] == "nsfw, explicit"

    def test_legacy_model_keeps_danbooru_rating_words(
        self, test_client, stub_generator
    ):
        calls = stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "rating": "general", "model": "200m-ft"},
        )
        assert response.status_code == 200, response.text
        assert calls[0]["rating"] == "general"
        assert calls[0]["model_key"] == "200m-ft"

    def test_whitespace_only_tags_400(self, test_client, stub_generator):
        stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample", json={"tags": ["   ", "\t"]}
        )
        assert response.status_code == 400
        assert "输入标签" in response.json()["error"]


class TestRequestValidation:
    def test_empty_tag_list_maps_to_400(self, test_client):
        response = test_client.post("/api/tags/suggest-upsample", json={"tags": []})
        assert response.status_code == 400

    def test_over_200_tags_maps_to_400(self, test_client):
        response = test_client.post(
            "/api/tags/suggest-upsample", json={"tags": ["tag"] * 201}
        )
        assert response.status_code == 400

    def test_unknown_model_maps_to_400(self, test_client):
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "model": "500m"},
        )
        assert response.status_code == 400

    def test_unknown_image_id_404(self, test_client, stub_generator):
        stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "image_id": 987654},
        )
        assert response.status_code == 404


class TestImageBackfill:
    def test_aspect_ratio_derived_from_image_record(
        self, test_client, test_db, tmp_path, stub_generator
    ):
        from PIL import Image

        import database as db

        src = tmp_path / "tipo-src"
        src.mkdir()
        path = src / "wide.png"
        Image.new("RGB", (64, 32), color=(10, 20, 30)).save(path)
        image_id = db.add_image(
            path=str(path), filename="wide.png", width=64, height=32
        )

        calls = stub_generator(["smile"])
        response = test_client.post(
            "/api/tags/suggest-upsample",
            json={"tags": ["1girl"], "image_id": image_id},
        )
        assert response.status_code == 200, response.text
        assert calls[0]["aspect_ratio"] == pytest.approx(2.0)
