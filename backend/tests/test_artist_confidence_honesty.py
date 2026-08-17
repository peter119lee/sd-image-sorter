"""Artist identification must not present a guess as a fact (audit F3).

Measured on 250 ground-truth images from a Danbooru-named library (filename
token = artist tag), using the shipped Kaloscope weights:

    top-1 >= 0.20 : 66 labels, 92% correct,  6% out-of-vocabulary
    0.03 .. 0.20  : 79 labels, 28% correct, 65% out-of-vocabulary
    top-1 <  0.03 : 105 labels, 2% correct, 97% out-of-vocabulary

The shipped pipeline committed every label at or above 0.03 straight into
``artist_predictions``, so roughly 4 in 10 of the names it wrote were wrong,
and those names feed the gallery's artist filter. Raising the threshold alone
cannot fix it - the correct and wrong score distributions overlap across almost
the whole range, so at 0.30 precision is still only 96% while 45% of the
correct answers are discarded.

These tests pin the honest behaviour instead: only the high tier is asserted as
an identification, the middle tier comes back as an explicitly unconfirmed
suggestion, and anything below the floor says "probably not in this model's
vocabulary" rather than naming the nearest wrong match.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

import artist_identifier as ai


def _identifier_returning(probabilities, artists):
    """A real ArtistIdentifier wired to a fixed probability vector.

    Uses the production ``identify_with_threshold`` decision path - only the
    tensor work is stubbed - so these tests exercise the shipped logic rather
    than a parallel reimplementation of it.
    """
    identifier = ai.ArtistIdentifier(artists_list=list(artists))
    identifier._model = "onnx"
    identifier._session = object()
    # Tolerates the AI-runtime ``priority`` the production path threads through.
    identifier._run_onnx = lambda image, *_a, **_k: np.array(
        probabilities, dtype=np.float32
    )
    return identifier


@pytest.fixture
def sample_image(tmp_path):
    path = tmp_path / "artist-sample.png"
    Image.new("RGB", (32, 32), color="teal").save(path)
    return str(path)


class TestConfidenceTiers:
    def test_high_confidence_prediction_is_still_asserted(self, sample_image):
        identifier = _identifier_returning([0.05, 0.55, 0.40], ["a0", "a1", "a2"])

        result = identifier.identify(sample_image, top_k=3)

        assert result["artist"] == "a1"
        assert result["confidence_level"] == ai.ARTIST_CONFIDENCE_HIGH
        assert result["candidate_artist"] == "a1"
        assert result["out_of_vocabulary_likely"] is False

    def test_low_confidence_prediction_is_a_suggestion_not_an_identification(
        self, sample_image
    ):
        # 0.10 sits above the old shipped 0.03 threshold, so this is exactly the
        # band that used to be written into the library as a fact. Measured
        # precision in this band is 28%.
        identifier = _identifier_returning([0.10, 0.03, 0.87], ["a0", "a1", "a2"])
        identifier._run_onnx = lambda image, *_a, **_k: np.array(
            [0.10, 0.03, 0.02], dtype=np.float32
        )

        result = identifier.identify(sample_image, top_k=3)

        assert result["artist"] == "undefined", (
            "a 0.10-confidence guess must not be asserted as the image's artist"
        )
        assert result["confidence_level"] == ai.ARTIST_CONFIDENCE_LOW
        assert result["candidate_artist"] == "a0", (
            "the guess must still be returned so the user can confirm it"
        )
        assert result["confidence"] == pytest.approx(0.10, abs=1e-4)
        assert result["out_of_vocabulary_likely"] is True

    def test_below_floor_reports_vocabulary_gap_and_names_nobody(self, sample_image):
        identifier = _identifier_returning([0.01, 0.005, 0.004], ["a0", "a1", "a2"])

        result = identifier.identify(sample_image, top_k=3)

        assert result["artist"] == "undefined"
        assert result["confidence_level"] == ai.ARTIST_CONFIDENCE_NONE
        assert result["candidate_artist"] is None, (
            "at this score the top-1 is right ~2% of the time; naming it is worse "
            "than saying nothing"
        )
        assert result["out_of_vocabulary_likely"] is True
        assert "vocabulary" in result["advisory"].lower()
        assert result["vocabulary_size"] == 3

    def test_caller_threshold_can_only_tighten_never_loosen(self, sample_image):
        identifier = _identifier_returning([0.10, 0.03, 0.02], ["a0", "a1", "a2"])

        loosened = identifier.identify_with_threshold(sample_image, top_k=3, threshold=0.0)
        assert loosened["artist"] == "undefined", (
            "an API caller must not be able to ask the backend to assert a guess"
        )
        assert loosened["candidate_artist"] == "a0"

        tightened = identifier.identify_with_threshold(sample_image, top_k=3, threshold=0.9)
        assert tightened["artist"] == "undefined"
        assert tightened["candidate_artist"] is None


class TestVocabularyVisibility:
    def test_identifier_reports_whether_a_name_is_in_the_answer_set(self):
        identifier = ai.ArtistIdentifier(artists_list=["ko_yu", "sakura_shiori"])

        assert identifier.knows_artist("sakura_shiori") is True
        assert identifier.knows_artist("SAKURA_SHIORI") is True
        assert identifier.knows_artist("someone_not_in_the_model") is False

    def test_vocabulary_endpoint_answers_can_this_model_ever_name_them(
        self, test_client, monkeypatch
    ):
        from routers import artists as artists_router

        identifier = ai.ArtistIdentifier(artists_list=["ko_yu", "sakura_shiori"])
        identifier._model = "onnx"
        identifier._session = object()
        monkeypatch.setattr(
            artists_router, "get_artist_identifier", lambda **_kwargs: identifier
        )

        response = test_client.get(
            "/api/artists/vocabulary",
            params={"name": ["sakura_shiori", "definitely_absent"]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["vocabulary_size"] == 2
        assert payload["known"] == {
            "sakura_shiori": True,
            "definitely_absent": False,
        }


class TestNothingUnconfirmedReachesTheLibrary:
    def test_low_confidence_identification_is_not_stored_as_an_artist_label(
        self, test_client, monkeypatch, tmp_path
    ):
        from routers import artists as artists_router

        image_path = tmp_path / "low-confidence.png"
        Image.new("RGB", (64, 64), color="maroon").save(image_path)
        image_id = test_client.test_db.add_image(
            path=str(image_path), filename=image_path.name, metadata_json="{}"
        )

        identifier = _identifier_returning([0.12, 0.04, 0.02], ["guessed_artist", "b", "c"])
        monkeypatch.setattr(
            artists_router, "get_artist_identifier", lambda **_kwargs: identifier
        )

        response = test_client.post("/api/artists/identify", json={"image_id": image_id})

        assert response.status_code == 200
        payload = response.json()
        assert payload["confidence_level"] == "low"
        assert payload["candidate_artist"] == "guessed_artist"
        assert payload["artist"] == "undefined"

        with test_client.test_db.get_db() as conn:
            row = conn.execute(
                "SELECT artist, confidence FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        assert row is not None
        assert row["artist"] == "undefined", (
            "an unconfirmed guess must not become a gallery artist filter value"
        )

    def test_stats_separate_confident_labels_from_low_confidence_ones(
        self, test_client, tmp_path
    ):
        rows = [("confident.png", "sure_artist", 0.71), ("weak.png", "weak_artist", 0.09)]
        for filename, artist, confidence in rows:
            path = tmp_path / filename
            Image.new("RGB", (16, 16), color="navy").save(path)
            image_id = test_client.test_db.add_image(
                path=str(path), filename=filename, metadata_json="{}"
            )
            with test_client.test_db.get_db() as conn:
                conn.execute(
                    "INSERT INTO artist_predictions (image_id, artist, confidence, "
                    "top_predictions) VALUES (?, ?, ?, ?)",
                    (image_id, artist, confidence, "[]"),
                )

        payload = test_client.get("/api/artists/stats").json()

        assert payload["confident_count"] == 1
        assert payload["low_confidence_count"] == 1
        assert "sure_artist" in payload["artist_counts"]
        assert "weak_artist" not in payload["artist_counts"], (
            "a 0.09-confidence row must not be presented as an identified artist"
        )
        assert payload["low_confidence_artist_counts"]["weak_artist"] == 1
