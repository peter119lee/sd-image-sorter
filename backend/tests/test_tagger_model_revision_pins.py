"""Every catalog model must resolve to a fixed commit, not a moving branch (audit F6).

Only 2 of the 10 entries carried a `revision`; the other 8 resolved `main`, so an
upstream re-upload silently changes what a user's tagger produces, and the
measured operating points recorded in the catalog comments (OppaiOracle's
published P=R point of 0.7927, Camie's output-head A/B, PixAI's output index)
can stop matching the weights they were measured on. Three of those repos were
modified during 2026, so `main` is demonstrably a moving target here.

The pins were taken from the installed state where a copy exists
(`data/models/**/.cache/huggingface/download/*.metadata` records the commit the
local file came from) and cross-checked against the HuggingFace model API; for
the entries with no local copy the pin is the upstream head, which is exactly
what `main` resolved to at the time of pinning, so behaviour is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tagger_models import TAGGER_MODELS

# toriigate-0.5 is pinned through toriigate_tagger.TORIIGATE_COMMIT_HASH because
# it downloads via snapshot_download rather than the shared tagger download
# path. The catalog now carries the same hash and the constant reads it back, so
# the two cannot drift apart.
_LOADER_OWNED_PINS = {"toriigate-0.5": "toriigate_tagger.TORIIGATE_COMMIT_HASH"}


@pytest.mark.parametrize("model_name", sorted(TAGGER_MODELS))
def test_every_catalog_model_pins_a_commit_hash(model_name):
    revision = TAGGER_MODELS[model_name].get("revision")

    assert isinstance(revision, str), (
        f"{model_name} has no pinned revision, so it resolves the repo's moving "
        f"main branch"
    )
    assert len(revision) == 40
    int(revision, 16)
    assert revision == revision.lower()


def test_toriigate_constant_and_catalog_cannot_drift_apart():
    import toriigate_tagger

    assert (
        toriigate_tagger.TORIIGATE_COMMIT_HASH
        == TAGGER_MODELS["toriigate-0.5"]["revision"]
    )
    assert set(_LOADER_OWNED_PINS) <= set(TAGGER_MODELS)


def test_oppai_oracle_downloads_at_its_pinned_revision(monkeypatch, tmp_path):
    """A pin nobody passes to the downloader is decoration, not a pin."""
    import oppai_oracle_tagger

    calls = []

    class _RecordingHub:
        @staticmethod
        def hf_hub_download(**kwargs):
            calls.append(kwargs)
            target = Path(kwargs["local_dir"]) / kwargs["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * (2 * 1024 * 1024))
            return str(target)

    monkeypatch.setattr(oppai_oracle_tagger, "hf_hub", _RecordingHub)
    tagger = oppai_oracle_tagger.OppaiOracleTagger.__new__(
        oppai_oracle_tagger.OppaiOracleTagger
    )
    tagger.model_name = "oppai-oracle-v1.1"
    tagger.model_dir = str(tmp_path)

    tagger._download_model()

    assert calls, "no download was attempted"
    expected = TAGGER_MODELS["oppai-oracle-v1.1"]["revision"]
    assert all(call.get("revision") == expected for call in calls), (
        f"OppaiOracle downloads ignored the catalog pin: "
        f"{[call.get('revision') for call in calls]}"
    )
