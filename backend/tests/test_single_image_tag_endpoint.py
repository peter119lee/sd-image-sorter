"""POST /api/tag/single — WD14 on one arbitrary file, no database row.

``tagger.tag_image(path)`` has always been database-free, but the only HTTP
surface for WD14 was the bulk job runner over ``images`` rows, so the
capability was unreachable for a file the owner had not scanned. These tests
pin the properties that make the endpoint useful and safe:

* real tags come back for a real file with **zero** database writes;
* a never-indexed file works;
* a file that arrived through the Reader's ``POST /api/parse-image`` intake
  works, which is the question ``findings/assessment-prompt-features.md``
  flagged as untested (does path validation accept the reader temp dir?);
* a traversal attempt is refused, like every other file-accepting endpoint.

The tagger itself is stubbed: the real one would download ~450 MB of ONNX
weights on a machine that has none.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import single_image_tag_service


def _fake_result() -> dict:
    return {
        "general_tags": [
            {"tag": "1girl", "confidence": 0.98, "category": "general"},
            {"tag": "solo", "confidence": 0.91, "category": "general"},
        ],
        "character_tags": [
            {"tag": "hatsune_miku", "confidence": 0.88, "category": "character"}
        ],
        "copyright_tags": [
            {"tag": "vocaloid", "confidence": 0.77, "category": "copyright"}
        ],
        "rating": "general",
        "rating_confidences": {"general": 0.95, "sensitive": 0.04},
        "all_tags": [
            {"tag": "1girl", "confidence": 0.98, "category": "general"},
            {"tag": "solo", "confidence": 0.91, "category": "general"},
            {"tag": "hatsune_miku", "confidence": 0.88, "category": "character"},
            {"tag": "vocaloid", "confidence": 0.77, "category": "copyright"},
        ],
        # The batch path also carries the tag_scores payload; the endpoint must
        # not leak it into an interactive response.
        "raw_scores": [{"tag": "1girl", "score": 0.98, "category": "general"}],
    }


class _StubTagger:
    """Records how it was configured; returns a fixed result."""

    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.model_name = "wd-swinv2-tagger-v3"
        self._result = result if result is not None else _fake_result()

    def tag(self, image_path, **kwargs):
        self.calls.append({"image_path": image_path, **kwargs})
        return self._result


@pytest.fixture
def stub_tagger(monkeypatch) -> _StubTagger:
    tagger = _StubTagger()
    monkeypatch.setattr(
        single_image_tag_service,
        "_load_tagger",
        lambda **kwargs: tagger,
    )
    return tagger


def _png(path: Path, size=(64, 64), color="red") -> Path:
    Image.new("RGB", size, color=color).save(path)
    return path


# Every table the batch tag writer touches. Counting all four makes "no
# database row" mean the whole write path stayed idle, not just ``images``.
_WRITE_TABLES = ("images", "tags", "tag_scores", "tag_writer_provenance")


def _row_counts(db) -> dict:
    with db.get_db() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _WRITE_TABLES
        }


# ---------------------------------------------------------------------------
# The core promise: real tags, no database row
# ---------------------------------------------------------------------------


def test_returns_tags_for_a_real_image_without_creating_a_database_row(
    test_client, stub_tagger, tmp_path
):
    image = _png(tmp_path / "dropped.png")
    before = _row_counts(test_client.test_db)

    response = test_client.post("/api/tag/single", json={"image_path": str(image)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["tag"] for entry in body["general_tags"]] == ["1girl", "solo"]
    assert [entry["tag"] for entry in body["character_tags"]] == ["hatsune_miku"]
    assert [entry["tag"] for entry in body["copyright_tags"]] == ["vocaloid"]
    assert body["rating"] == "general"
    assert body["rating_confidences"]["general"] == pytest.approx(0.95)
    assert body["tags"] == ["1girl", "solo", "hatsune_miku", "vocaloid"]
    assert body["stored"] is False
    assert body["image_path"] == str(image.resolve())
    assert "raw_scores" not in body

    assert _row_counts(test_client.test_db) == before


def test_works_on_a_file_that_was_never_indexed(test_client, stub_tagger, tmp_path):
    image = _png(tmp_path / "never-scanned.jpg")

    response = test_client.post("/api/tag/single", json={"image_path": str(image)})

    assert response.status_code == 200, response.text
    assert response.json()["tags"]
    # The tagger saw the real file, not a database-resolved substitute.
    assert stub_tagger.calls[0]["image_path"] == str(image.resolve())
    with test_client.test_db.get_db() as conn:
        matched = conn.execute(
            "SELECT COUNT(*) FROM images WHERE path = ?", (str(image.resolve()),)
        ).fetchone()[0]
    assert matched == 0


def test_thresholds_reach_the_tagger_instead_of_being_accepted_and_dropped(
    test_client, stub_tagger, tmp_path
):
    image = _png(tmp_path / "thresholds.png")

    response = test_client.post(
        "/api/tag/single",
        json={
            "image_path": str(image),
            "general_threshold": 0.5,
            "character_threshold": 0.7,
            "copyright_threshold": 0.6,
        },
    )

    assert response.status_code == 200, response.text
    call = stub_tagger.calls[0]
    assert call["threshold"] == pytest.approx(0.5)
    assert call["character_threshold"] == pytest.approx(0.7)
    assert call["copyright_threshold"] == pytest.approx(0.6)


def test_requested_tagger_model_is_reported_back(test_client, monkeypatch, tmp_path):
    image = _png(tmp_path / "model.png")
    seen: dict = {}

    def _load(**kwargs):
        seen.update(kwargs)
        return _StubTagger()

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _load)

    response = test_client.post(
        "/api/tag/single",
        json={
            "image_path": str(image),
            "tagger_model": "wd-vit-tagger-v3",
            "use_gpu": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "wd-vit-tagger-v3"
    assert seen["model_name"] == "wd-vit-tagger-v3"
    assert seen["use_gpu"] is False


# ---------------------------------------------------------------------------
# The Reader intake question the assessment flagged as untested
# ---------------------------------------------------------------------------


def test_works_on_a_file_that_arrived_through_the_reader_upload_path(
    test_client, stub_tagger, tmp_path
):
    """POST /api/parse-image retains its temp file and returns its absolute
    path; that path must be taggable without weakening path validation."""
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color="blue").save(buffer, format="PNG")
    buffer.seek(0)

    parsed = test_client.post(
        "/api/parse-image",
        files={"file": ("upload.png", buffer, "image/png")},
    )
    assert parsed.status_code == 200, parsed.text
    temp_path = parsed.json()["source_temp_path"]
    assert temp_path
    assert Path(temp_path).is_file()
    assert Path(temp_path).parent.name == "reader_uploads"

    before = _row_counts(test_client.test_db)
    response = test_client.post("/api/tag/single", json={"image_path": temp_path})

    assert response.status_code == 200, response.text
    assert response.json()["tags"] == ["1girl", "solo", "hatsune_miku", "vocaloid"]
    assert _row_counts(test_client.test_db) == before


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../../../Windows/System32/drivers/etc/hosts",
        "..\\..\\..\\..\\secrets.png",
        "%2e%2e%2f%2e%2e%2fsecret.png",
    ],
)
def test_rejects_a_traversal_attempt(test_client, stub_tagger, hostile):
    response = test_client.post("/api/tag/single", json={"image_path": hostile})

    assert response.status_code == 400, response.text
    assert not stub_tagger.calls


def test_rejects_a_path_with_a_null_byte(test_client, stub_tagger):
    response = test_client.post(
        "/api/tag/single", json={"image_path": "C:/pictures/a\x00.png"}
    )

    assert response.status_code == 400, response.text
    assert not stub_tagger.calls


def test_missing_file_is_404_not_a_500(test_client, stub_tagger, tmp_path):
    response = test_client.post(
        "/api/tag/single", json={"image_path": str(tmp_path / "absent.png")}
    )

    assert response.status_code == 404, response.text
    assert not stub_tagger.calls


def test_rejects_a_non_image_extension(test_client, stub_tagger, tmp_path):
    payload = tmp_path / "payload.exe"
    payload.write_bytes(b"MZ")

    response = test_client.post("/api/tag/single", json={"image_path": str(payload)})

    assert response.status_code == 400, response.text
    assert not stub_tagger.calls


def test_rejects_an_empty_path(test_client, stub_tagger):
    response = test_client.post("/api/tag/single", json={"image_path": "   "})

    assert response.status_code in (400, 422), response.text
    assert not stub_tagger.calls


def test_a_tagger_failure_is_a_clean_error_not_an_unhandled_500(
    test_client, monkeypatch, tmp_path
):
    image = _png(tmp_path / "broken-runtime.png")

    def _explode(**_kwargs):
        raise RuntimeError("ONNX Runtime session could not be created")

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _explode)

    response = test_client.post("/api/tag/single", json={"image_path": str(image)})

    assert response.status_code == 503, response.text
    # main.py's HTTPException handler renders the detail under "error".
    assert "ONNX Runtime" in response.json()["error"]


def test_a_tagger_result_carrying_an_error_is_surfaced_as_a_failure(
    test_client, monkeypatch, tmp_path
):
    """The batch engine returns an empty result with an ``error`` key instead of
    raising; reporting that as a successful "no tags found" would be a false
    success."""
    image = _png(tmp_path / "undecodable.png")
    failed = {
        "general_tags": [],
        "character_tags": [],
        "copyright_tags": [],
        "rating": "unknown",
        "rating_confidences": {},
        "all_tags": [],
        "error": "cannot identify image file",
    }
    monkeypatch.setattr(
        single_image_tag_service,
        "_load_tagger",
        lambda **_kwargs: _StubTagger(result=failed),
    )

    response = test_client.post("/api/tag/single", json={"image_path": str(image)})

    assert response.status_code == 422, response.text
    assert "cannot identify image file" in response.json()["error"]


def test_an_image_with_no_confident_tags_is_a_success_with_an_empty_list(
    test_client, monkeypatch, tmp_path
):
    image = _png(tmp_path / "featureless.png")
    empty = {
        "general_tags": [],
        "character_tags": [],
        "copyright_tags": [],
        "rating": "general",
        "rating_confidences": {"general": 0.9},
        "all_tags": [],
    }
    monkeypatch.setattr(
        single_image_tag_service,
        "_load_tagger",
        lambda **_kwargs: _StubTagger(result=empty),
    )

    response = test_client.post("/api/tag/single", json={"image_path": str(image)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tags"] == []
    assert body["stored"] is False
