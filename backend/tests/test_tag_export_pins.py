"""Characterization pins for ``services/tag_export_service.py`` (TIER-2 step 0).

These lock the load-bearing behavior of the shared prompt/tag/caption export
helpers so a later decomposition of this 1.4k-line module can be proven
behavior-neutral. The existing reader suites (test_tag_export_nl_types /
test_tag_training_filters / test_resource_safety / test_routers/test_vlm)
cover the NL-compose join rule and a couple of db-seam calls; everything else
here is new coverage.

THE dominant decomposition hazard is the DOUBLE FACADE:

  * ``services/dataset_export_service.py`` is itself a FILE facade that
    re-binds ``NL_COMPOSE_MODES / VALID_OUTPUT_MODES / VALID_CONTENT_MODES /
    apply_caption_transforms / build_sidecar_content / compose_caption_with_nl``
    from this module.
  * The ``services/dataset_export/`` submodules (captions.py / engine.py /
    artifacts.py) ORIGIN-import the same names straight from
    services.tag_export_service.

  ``NL_COMPOSE_MODES`` in particular is shared BY OBJECT IDENTITY:
  ``dataset_export_service._NL_COMPOSE_MODES`` and
  ``dataset_export.captions._NL_COMPOSE_MODES`` are both ``is`` the origin set
  (test_dataset_export_pins already asserts the des side). A split that clones
  any of these into a fresh object with the same *value* would silently desync
  the two export engines' NL gating — the identity pins below trip first.

Statefulness verdict: STATELESS. No ``global`` statements, no runtime-mutable
module state, no caches. The only seams are the module-level ``db`` reference
(monkeypatched by callers), the lazy in-function imports, and the
``__file__``-derived combined-export directory. So the split pattern is
pure-helper-modules-behind-a-re-export-facade (much simpler than the dsexport
job registry). Every pin here runs with no model load, no network, and no
images.db writes (the single ``db`` seam is monkeypatched; the one combined
export write goes to the gitignored data dir and is cleaned up).
"""

from __future__ import annotations

import builtins
import errno
import inspect
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.tag_export_service as tes


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _img(**kw):
    base = {
        "id": 7,
        "filename": "a.png",
        "path": "/x/a.png",
        "prompt": "",
        "negative_prompt": "",
        "ai_caption": "",
        "nl_caption": "",
    }
    base.update(kw)
    return base


def _tags(*names):
    return [{"tag": n} for n in names]


class _HandleThatDiesOnWrite:
    """A text handle that writes a partial prefix and then fails.

    Models a disk-full / power-loss write: some bytes reach the file before the
    error, which is what turns a non-atomic sidecar write into a truncated one.
    """

    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._handle.__exit__(*exc_info)

    def write(self, text):
        self._handle.write(str(text)[:4])
        raise OSError(errno.ENOSPC, "simulated drive full mid-write")

    def __getattr__(self, name):
        return getattr(self._handle, name)


# =========================================================================== #
# 1. Module constants + the DOUBLE-FACADE identity chain (the #1 hazard).
#    A split that renames a constant, drops a re-export, or clones a shared
#    object trips here before either export engine misbehaves.
# =========================================================================== #


class TestConstantsAndFacade:
    def test_content_mode_sets_are_pinned(self):
        assert tes.VALID_CONTENT_MODES == {
            "tags",
            "prompt",
            "negative",
            "prompt_negative",
            "a1111",
            "caption_tags",
            "caption_merged",
            "json",
            "nl_caption",
            "tags_nl",
            "prompt_nl",
            "template",
        }
        assert tes.VALID_OVERWRITE_POLICIES == {"unique", "overwrite", "skip"}
        assert tes.VALID_OUTPUT_MODES == {"folder", "beside_image"}
        assert tes.DANBOORU_TAG_CONTENT_MODES == {
            "tags",
            "caption_tags",
            "caption_merged",
            "tags_nl",
        }
        assert tes.NL_COMPOSE_MODES == {"template", "tags"}

    def test_scalar_constants_are_pinned(self):
        assert tes.EXPORT_DB_CHUNK_SIZE == 500
        assert tes.COMBINED_EXPORT_RECENT_ERROR_LIMIT == 20
        assert tes.PROMPT_MATCH_MODE_EXACT == "exact"
        assert tes.PROMPT_MATCH_MODE_CONTAINS == "contains"
        assert tes.LORA_PRESERVE_UNDERSCORE_PREFIXES == ["score_"]

    def test_parameter_export_order_pinned(self):
        # a1111 param block ordering is user-visible in exported captions.
        assert tes.PARAMETER_EXPORT_ORDER[0] == ("steps", "Steps")
        assert tes.PARAMETER_EXPORT_ORDER[-1] == ("loras", "LoRAs")
        assert [k for k, _ in tes.PARAMETER_EXPORT_ORDER] == [
            "steps",
            "sampler",
            "schedule_type",
            "cfg_scale",
            "seed",
            "size",
            "model",
            "model_hash",
            "clip_skip",
            "denoising_strength",
            "loras",
        ]

    def test_nl_compose_modes_shared_by_identity_across_double_facade(self):
        # THE decomposition tripwire: origin set object is re-bound (facade) and
        # origin-imported (submodules) WITHOUT cloning. Cloning it desyncs the
        # two export engines' NL gating silently.
        import services.dataset_export_service as des
        import services.dataset_export.captions as cap

        assert des.NL_COMPOSE_MODES is tes.NL_COMPOSE_MODES
        assert des._NL_COMPOSE_MODES is tes.NL_COMPOSE_MODES
        assert cap.NL_COMPOSE_MODES is tes.NL_COMPOSE_MODES
        assert cap._NL_COMPOSE_MODES is tes.NL_COMPOSE_MODES

    def test_reexported_callables_keep_identity_through_facade(self):
        import services.dataset_export_service as des

        assert des.build_sidecar_content is tes.build_sidecar_content
        assert des.compose_caption_with_nl is tes.compose_caption_with_nl
        assert des.apply_caption_transforms is tes.apply_caption_transforms
        assert des.VALID_OUTPUT_MODES is tes.VALID_OUTPUT_MODES
        assert des.VALID_CONTENT_MODES is tes.VALID_CONTENT_MODES

    def test_submodules_origin_import_shared_sets_by_identity(self):
        import services.dataset_export.engine as eng
        import services.dataset_export.artifacts as art

        assert eng.VALID_CONTENT_MODES is tes.VALID_CONTENT_MODES
        assert eng.VALID_OUTPUT_MODES is tes.VALID_OUTPUT_MODES
        assert art.VALID_CONTENT_MODES is tes.VALID_CONTENT_MODES
        assert art.VALID_OUTPUT_MODES is tes.VALID_OUTPUT_MODES

    def test_module_is_stateless_no_global_statements(self):
        # Statefulness verdict guard: if a future edit introduces module-level
        # mutable state + `global`, the pure-helper split pattern no longer
        # holds and this pin forces a re-think.
        src = inspect.getsource(tes)
        assert re.findall(r"^\s*global\s+", src, re.M) == []


# =========================================================================== #
# 2. Reader patch-surface: names live readers monkeypatch / import MUST stay
#    resolvable ON the facade module object after a split.
# =========================================================================== #


class TestPatchSurface:
    def test_db_seam_is_the_database_module(self):
        # test_resource_safety patches tag_export_service.db.get_images_by_ids;
        # a split must keep `db` bound on this exact module.
        import database as real_db

        assert tes.db is real_db

    def test_selection_token_helpers_are_module_attributes(self):
        # test_routers/test_vlm does monkeypatch.setattr(tag_export_service,
        # "count_selection_token_ids", ...); routers/tags_bulk + tagging.exports
        # import these by name.
        for name in (
            "count_selection_token_ids",
            "iter_selection_token_id_chunks",
            "extract_generation_params",
            "build_sidecar_content",
            "compose_caption_with_nl",
            "apply_caption_transforms",
            "render_export_preview",
            "export_tags_batch_request",
            "export_tags_combined_request",
            "combined_export_path",
        ):
            assert callable(getattr(tes, name)), name


# =========================================================================== #
# 3. ID normalization + chunking.
# =========================================================================== #


class TestIdNormalization:
    def test_normalize_dedupes_drops_nonpositive_and_nonint_preserving_order(self):
        assert tes._normalize_export_image_ids([3, "3", 0, -1, "x", None, 5, 3]) == [
            3,
            5,
        ]

    def test_normalize_handles_none_input(self):
        assert tes._normalize_export_image_ids(None) == []

    def test_iter_id_list_chunks_batches_and_filters(self):
        assert list(tes._iter_id_list_chunks([1, 2, 3, 4, 5, "6", 0, -1], 2)) == [
            [1, 2],
            [3, 4],
            [5, 6],
        ]

    def test_iter_id_list_chunks_zero_size_coerces_to_default(self):
        # max(1, int(0 or DEFAULT)) -> never a zero-size infinite loop.
        out = list(tes._iter_id_list_chunks([1, 2, 3], 0))
        assert out == [[1, 2, 3]]  # falls back to EXPORT_DB_CHUNK_SIZE (500)


# =========================================================================== #
# 4. Caption token helpers (join / split / normalize / coerce).
# =========================================================================== #


class TestCaptionTokenHelpers:
    def test_join_caption_parts_dedupes_ci_and_flattens_ws_and_strips_commas(self):
        assert tes._join_caption_parts(["a  b", "A B", "", " , ", "c,"]) == "a b, c"

    def test_split_caption_transform_tokens_splits_newlines_and_commas(self):
        assert tes._split_caption_transform_tokens("a, b\nc ,, d") == [
            "a",
            "b",
            "c",
            "d",
        ]

    def test_normalize_caption_transform_token_lowercases_and_despaces_underscores(
        self,
    ):
        assert (
            tes._normalize_caption_transform_token("Long_Hair  Style")
            == "long hair style"
        )

    def test_coerce_transform_token_list_dedupes_by_normalized_key(self):
        # 'A' == 'a'; 'b c' normalizes equal to 'b_c' -> first spelling wins.
        assert tes._coerce_transform_token_list(["a", "A", "b_c", "b c"]) == [
            "a",
            "b_c",
        ]

    def test_coerce_transform_token_list_scalar_and_none(self):
        assert tes._coerce_transform_token_list(None) == []
        assert tes._coerce_transform_token_list("solo") == ["solo"]

    def test_coerce_int_str_map_keeps_intable_keys_only(self):
        assert tes._coerce_int_str_map({"5": "x", "bad": "y", 7: None}) == {
            5: "x",
            7: "",
        }

    def test_filter_tags_trims_and_drops_blacklisted_and_empty(self):
        rows = [{"tag": "A"}, {"tag": " b "}, {"tag": "bad"}, {"tag": ""}]
        assert tes._filter_tags(rows, {"bad"}) == ["A", "b"]

    def test_filter_text_caption_tokens_empty_blacklist_flattens_to_single_token(self):
        # No blacklist -> the whole value is one flattened token (NOT split).
        assert tes._filter_text_caption_tokens("a, b\nc", set()) == ["a, b c"]

    def test_filter_text_caption_tokens_with_blacklist_splits_and_filters(self):
        assert tes._filter_text_caption_tokens("a, bad, c", {"bad"}) == ["a", "c"]


# =========================================================================== #
# 5. apply_caption_transforms (prepend/append/remove/categories/dedupe rule).
# =========================================================================== #


class TestApplyCaptionTransforms:
    def test_none_and_empty_transforms_return_content_verbatim_no_dedupe(self):
        # Critical: an empty transform map must NOT silently dedupe existing dups.
        assert tes.apply_caption_transforms("a, a, b", None) == "a, a, b"
        assert tes.apply_caption_transforms("a, a, b", {}) == "a, a, b"

    def test_remove_drops_matching_tokens(self):
        assert tes.apply_caption_transforms("a, b, c", {"remove": ["b"]}) == "a, c"

    def test_prepend_and_append_wrap_and_enable_dedupe(self):
        assert (
            tes.apply_caption_transforms("a, b", {"prepend": ["z"], "append": ["q"]})
            == "z, a, b, q"
        )

    def test_dedupe_flag_alone_collapses_case_insensitive_dups(self):
        assert tes.apply_caption_transforms("a, A, b", {"dedupe": True}) == "a, b"

    def test_add_prepend_alias_is_accepted(self):
        assert tes.apply_caption_transforms("x", {"add_prepend": ["y"]}) == "y, x"


# =========================================================================== #
# 6. NL compose + sidecar-content join rules.
# =========================================================================== #


class TestNlCompose:
    def test_compose_returns_rendered_verbatim_for_non_nl_types(self):
        raw = "  1girl, long_hair  "
        assert tes.compose_caption_with_nl(raw, "", "sentence") == raw
        assert tes.compose_caption_with_nl(raw, "booru", "sentence") == raw

    def test_compose_nl_prefers_sentence_falls_back_to_booru(self):
        assert tes.compose_caption_with_nl("1girl", "nl", "a girl") == "a girl"
        assert tes.compose_caption_with_nl("1girl", "nl", "   ") == "1girl"

    def test_compose_both_joins_tags_first_and_flattens_multiline_sentence(self):
        # kohya reads line 1 only -> the sentence is whitespace-flattened.
        assert (
            tes.compose_caption_with_nl("1girl", "both", "line1\nline2")
            == "1girl, line1 line2"
        )
        assert tes.compose_caption_with_nl("", "both", "s") == "s"

    def test_image_nl_source_text_override_then_stored_then_fused(self):
        img = _img(nl_caption="stored", ai_caption="fused")
        assert tes._image_nl_source_text(img, 7, {7: "edited"}) == "edited"
        assert (
            tes._image_nl_source_text(img, 7, {7: ""}) == ""
        )  # explicit empty suppresses
        assert tes._image_nl_source_text(img, 7, {}) == "stored"
        assert (
            tes._image_nl_source_text(_img(nl_caption="", ai_caption="fused"), 7, {})
            == "fused"
        )

    def test_compose_nl_for_image_gated_by_mode_and_type(self):
        img = _img(nl_caption="a girl")
        # tags mode + both -> composes
        assert (
            tes._compose_nl_for_image(
                "1girl",
                img,
                7,
                content_mode="tags",
                image_types={7: "both"},
                nl_overrides={},
            )
            == "1girl, a girl"
        )
        # NL-aware mode (tags_nl) -> compose skipped to avoid doubling
        assert (
            tes._compose_nl_for_image(
                "1girl",
                img,
                7,
                content_mode="tags_nl",
                image_types={7: "both"},
                nl_overrides={},
            )
            == "1girl"
        )
        # no type entry -> untouched
        assert (
            tes._compose_nl_for_image(
                "1girl", img, 7, content_mode="tags", image_types={}, nl_overrides={}
            )
            == "1girl"
        )

    def test_build_nl_sidecar_content_flattens_and_prepends_trigger(self):
        assert (
            tes._build_nl_sidecar_content("line1\nline2", "trg") == "trg, line1 line2"
        )
        assert (
            tes._build_nl_sidecar_content("has trg inside", "trg") == "has trg inside"
        )
        assert tes._build_nl_sidecar_content("", "trg") == "trg"
        assert tes._build_nl_sidecar_content("hello", "") == "hello"


# =========================================================================== #
# 7. build_sidecar_content — the content-mode matrix + underscore policy.
# =========================================================================== #


class TestBuildSidecarContent:
    def _rich(self):
        return _img(
            prompt="p1, p2",
            negative_prompt="neg",
            ai_caption="cap sentence",
            nl_caption="nl sentence",
            checkpoint="ckpt",
            width=512,
            height=768,
        )

    def _tagset(self):
        return _tags("multiple_girls", "score_5", "blocked", "")

    def test_tags_mode_normalizes_underscores_by_default_preserving_score(self):
        out = tes.build_sidecar_content(
            self._rich(), self._tagset(), content_mode="tags", blacklist={"blocked"}
        )
        assert out == "multiple girls, score_5"

    def test_tags_mode_explicit_false_keeps_underscores(self):
        out = tes.build_sidecar_content(
            self._rich(),
            self._tagset(),
            content_mode="tags",
            blacklist={"blocked"},
            normalize_tag_underscores=False,
        )
        assert out == "multiple_girls, score_5"

    def test_prompt_negative_and_prompt_negative_modes(self):
        img = self._rich()
        assert tes.build_sidecar_content(img, [], content_mode="prompt") == "p1, p2"
        assert tes.build_sidecar_content(img, [], content_mode="negative") == "neg"
        assert (
            tes.build_sidecar_content(img, [], content_mode="prompt_negative")
            == "p1, p2\nNegative prompt: neg"
        )

    def test_nl_and_tags_nl_and_prompt_nl_modes(self):
        img = self._rich()
        assert (
            tes.build_sidecar_content(img, [], content_mode="nl_caption")
            == "nl sentence"
        )
        assert (
            tes.build_sidecar_content(
                img, self._tagset(), content_mode="tags_nl", blacklist={"blocked"}
            )
            == "multiple girls, score_5, nl sentence"
        )
        assert (
            tes.build_sidecar_content(img, [], content_mode="prompt_nl")
            == "p1, p2\nnl sentence"
        )

    def test_caption_tags_mode_prefixes_caption_before_tags(self):
        out = tes.build_sidecar_content(
            self._rich(),
            self._tagset(),
            content_mode="caption_tags",
            blacklist={"blocked"},
        )
        assert out == "cap sentence, multiple girls, score_5"

    def test_json_mode_is_sorted_and_keeps_underscores(self):
        out = tes.build_sidecar_content(
            self._rich(), self._tagset(), content_mode="json", blacklist={"blocked"}
        )
        payload = json.loads(out)
        # json is NOT a danbooru-tag mode -> underscores preserved in tags list.
        assert payload["tags"] == ["multiple_girls", "score_5"]
        assert payload["id"] == 7
        assert payload["generation_params"]["size"] == "512x768"
        # sort_keys=True contract
        assert list(payload.keys()) == sorted(payload.keys())

    def test_invalid_content_mode_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            tes.build_sidecar_content(self._rich(), [], content_mode="bogus")
        assert exc.value.status_code == 400


# =========================================================================== #
# 8. Generation params + a1111 block.
# =========================================================================== #


class TestGenerationParams:
    def test_extract_from_metadata_json_with_row_fallbacks(self):
        img = {
            "metadata_json": json.dumps(
                {"_parsed": {"generation_params": {"steps": 20, "sampler": "euler"}}}
            ),
            "checkpoint": "CK",
            "width": 512,
            "height": 512,
            "loras": "lora1, lora2",
        }
        out = tes.extract_generation_params(img)
        assert out["steps"] == 20
        assert out["model"] == "CK"  # filled from checkpoint
        assert out["size"] == "512x512"  # filled from width/height
        assert out["loras"] == "lora1, lora2"

    def test_extract_returns_empty_for_no_metadata(self):
        assert tes.extract_generation_params({"id": 1}) == {}

    def test_a1111_block_orders_prompt_negative_then_params(self):
        img = {
            "prompt": "hi",
            "negative_prompt": "bye",
            "metadata_json": json.dumps(
                {"_parsed": {"generation_params": {"steps": 20, "sampler": "euler"}}}
            ),
            "width": 512,
            "height": 512,
        }
        assert tes.build_a1111_parameters_text(img) == (
            "hi\nNegative prompt: bye\nSteps: 20, Sampler: euler, Size: 512x512"
        )


# =========================================================================== #
# 9. Underscore-normalization resolution.
# =========================================================================== #


class TestUnderscoreResolution:
    def test_resolve_default_follows_per_mode_policy(self):
        assert tes._resolve_underscore_normalization("tags", None) is True
        assert tes._resolve_underscore_normalization("json", None) is False

    def test_resolve_explicit_override_wins(self):
        assert tes._resolve_underscore_normalization("json", True) is True
        assert tes._resolve_underscore_normalization("tags", False) is False

    def test_maybe_normalize_underscores_preserves_score_prefix(self):
        assert tes._maybe_normalize_underscores(
            ["multiple_girls", "score_5"], normalize=True
        ) == [
            "multiple girls",
            "score_5",
        ]
        assert tes._maybe_normalize_underscores(
            ["multiple_girls"], normalize=False
        ) == ["multiple_girls"]

    def test_merge_template_blacklist_options_unions_dedup_case_insensitive(self):
        merged = tes._merge_template_blacklist_options(
            {"blacklist": ["x"]}, {"y", "x"}
        )["blacklist"]
        assert sorted(merged) == ["x", "y"]


# =========================================================================== #
# 10. _allocate_output_path — the unique/overwrite/skip policy matrix.
# =========================================================================== #


class TestAllocateOutputPath:
    def test_output_path_key_preserves_case_for_nonexistent_targets(self, tmp_path):
        from services.tag_export.sidecars import _output_path_key

        upper = tmp_path / "Dup.txt"
        lower = tmp_path / "dup.txt"

        assert not upper.exists()
        assert not lower.exists()
        assert _output_path_key(str(upper)) != _output_path_key(str(lower))

    def test_stem_derives_from_on_disk_path_without_sanitizing(self, tmp_path):
        img = {
            "id": 1,
            "path": str(tmp_path / "my (test).png"),
            "filename": "my (test).png",
        }
        alloc = tes._allocate_output_path(
            str(tmp_path), dict(img), "tags", "unique", {}
        )
        assert alloc.outcome == "write"
        assert Path(alloc.path).name == "my (test).txt"  # parens preserved for pairing

    def test_stem_override_is_popped_and_used(self, tmp_path):
        img = {
            "id": 2,
            "path": "/x/y.png",
            "filename": "y.png",
            "_sidecar_stem_override": "custom",
        }
        alloc = tes._allocate_output_path(str(tmp_path), img, "tags", "overwrite", {})
        assert Path(alloc.path).name == "custom.txt"
        assert "_sidecar_stem_override" not in img  # consumed

    def test_unique_in_run_collision_is_error_not_rename(self, tmp_path):
        img = {"id": 1, "path": str(tmp_path / "dup.png")}
        used = {
            os.path.realpath(os.path.abspath(tmp_path / "dup.txt")): "/other.png"
        }
        alloc = tes._allocate_output_path(
            str(tmp_path), dict(img), "tags", "unique", used
        )
        assert alloc.outcome == "error"
        assert "already taken" in alloc.message
        assert "same stem" in alloc.message
        assert "overwrite/skip" not in alloc.message

    def test_overwrite_in_run_collision_is_error_not_rename(self, tmp_path):
        img = {"id": 1, "path": str(tmp_path / "dup.png")}
        used = {
            os.path.realpath(os.path.abspath(tmp_path / "dup.txt")): "/other.png"
        }
        alloc = tes._allocate_output_path(
            str(tmp_path), dict(img), "tags", "overwrite", used
        )
        assert alloc.outcome == "error"
        assert alloc.path is None
        assert "dup.txt" in alloc.message
        assert "/other.png" in alloc.message
        assert "already taken" in alloc.message
        assert "same stem" in alloc.message

    def test_skip_leaves_existing_disk_file(self, tmp_path):
        (tmp_path / "s.txt").write_text("existing", encoding="utf-8")
        img = {"id": 1, "path": str(tmp_path / "s.png")}
        alloc = tes._allocate_output_path(str(tmp_path), dict(img), "tags", "skip", {})
        assert alloc.outcome == "skip"

    def test_unique_existing_disk_file_folder_is_error_beside_is_skip(self, tmp_path):
        (tmp_path / "s.txt").write_text("existing", encoding="utf-8")
        img = {"id": 1, "path": str(tmp_path / "s.png")}
        folder = tes._allocate_output_path(
            str(tmp_path), dict(img), "tags", "unique", {}, output_mode="folder"
        )
        beside = tes._allocate_output_path(
            str(tmp_path), dict(img), "tags", "unique", {}, output_mode="beside_image"
        )
        assert folder.outcome == "error"
        assert beside.outcome == "skip"

    def test_sidecar_extension_and_fallback_stem(self):
        assert tes._sidecar_extension("json") == ".json"
        assert tes._sidecar_extension("tags") == ".txt"
        assert tes._sidecar_extension(None) == ".txt"
        assert tes._sanitized_fallback_stem({"id": 9}) == "image_9"


# =========================================================================== #
# 11. combined_export_path token validation.
# =========================================================================== #


class TestCombinedExportPath:
    @pytest.mark.parametrize(
        "token",
        [
            "x" * 32,  # right length, non-hex char
            "g" * 32,  # non-hex
            "a" * 31,  # too short
            "abc",  # too short
            "",  # empty
            "0" * 33,  # too long
        ],
    )
    def test_bad_tokens_raise_404(self, token):
        with pytest.raises(HTTPException) as exc:
            tes.combined_export_path(token)
        assert exc.value.status_code == 404

    def test_valid_but_missing_token_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            tes.combined_export_path("0123456789abcdef0123456789abcdef")
        assert exc.value.status_code == 404


# =========================================================================== #
# 12. export_tags_batch_request — db-seam integration (DB monkeypatched).
# =========================================================================== #


class TestExportTagsBatch:
    def _patch_db(self, monkeypatch, images, tags_map):
        monkeypatch.setattr(
            tes.db,
            "get_images_by_ids",
            lambda ids: {i: images[i] for i in ids if i in images},
        )
        monkeypatch.setattr(
            tes.db,
            "get_image_tags_map",
            lambda ids: {i: tags_map.get(i, []) for i in ids},
        )

    def test_batch_writes_sidecars_and_returns_shape(self, monkeypatch, tmp_path):
        images = {
            1: _img(
                id=1, filename="one.png", path=str(tmp_path / "one.png"), prompt="a, b"
            ),
            2: _img(
                id=2, filename="two.png", path=str(tmp_path / "two.png"), prompt="c"
            ),
        }
        self._patch_db(monkeypatch, images, {1: _tags("1girl"), 2: _tags("solo")})
        req = SimpleNamespace(
            image_ids=[1, 2],
            output_folder=str(tmp_path),
            blacklist=[],
            prefix="",
            content_mode="tags",
            overwrite_policy="unique",
        )
        result = tes.export_tags_batch_request(req, id_chunks=iter([[1, 2]]), total=2)
        assert result["exported"] == 2
        assert result["error_count"] == 0
        assert result["content_mode"] == "tags"
        assert result["overwrite_policy"] == "unique"
        assert result["output_mode"] == "folder"
        assert "validation" in result and "nl_sidecars_written" in result
        assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "1girl"

    def test_batch_missing_image_becomes_row_error(self, monkeypatch, tmp_path):
        self._patch_db(monkeypatch, {}, {})
        req = SimpleNamespace(
            image_ids=[99],
            output_folder=str(tmp_path),
            blacklist=[],
            prefix="",
            content_mode="tags",
            overwrite_policy="unique",
        )
        result = tes.export_tags_batch_request(req, id_chunks=iter([[99]]), total=1)
        assert result["exported"] == 0
        assert result["error_count"] == 1
        assert "not found" in " ".join(result["error_messages"])

    def test_batch_invalid_output_mode_raises_400(self, tmp_path):
        req = SimpleNamespace(
            image_ids=[1],
            output_folder=str(tmp_path),
            blacklist=[],
            prefix="",
            content_mode="tags",
            overwrite_policy="unique",
            output_mode="cloud",
        )
        with pytest.raises(HTTPException) as exc:
            tes.export_tags_batch_request(req, id_chunks=iter([[1]]), total=1)
        assert exc.value.status_code == 400

    def test_beside_image_write_that_dies_midway_keeps_the_existing_caption(
        self, monkeypatch, tmp_path
    ):
        """beside_image writes into the user's own image folders, so an
        interrupted write must not truncate a caption already sitting there."""
        library = tmp_path / "library"
        library.mkdir()
        image_path = library / "hero.png"
        image_path.write_bytes(b"the writer never opens the image itself")
        sidecar = library / "hero.txt"
        sidecar.write_text("a caption the user wrote by hand", encoding="utf-8")
        original_bytes = sidecar.read_bytes()

        images = {1: _img(id=1, filename="hero.png", path=str(image_path))}
        self._patch_db(monkeypatch, images, {1: _tags("1girl")})

        real_open = builtins.open

        def open_that_dies_writing_into_the_library(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if "w" in str(mode) and Path(str(file)).parent == library:
                return _HandleThatDiesOnWrite(handle)
            return handle

        monkeypatch.setattr(builtins, "open", open_that_dies_writing_into_the_library)

        req = SimpleNamespace(
            image_ids=[1],
            output_folder="",
            blacklist=[],
            prefix="",
            content_mode="tags",
            overwrite_policy="overwrite",
            output_mode="beside_image",
        )
        result = tes.export_tags_batch_request(req, id_chunks=iter([[1]]), total=1)

        assert result["exported"] == 0
        assert result["error_count"] == 1
        assert sidecar.read_bytes() == original_bytes
        assert sorted(entry.name for entry in library.iterdir()) == [
            "hero.png",
            "hero.txt",
        ]

    def test_batch_cancel_check_short_circuits(self, monkeypatch, tmp_path):
        images = {1: _img(id=1, filename="one.png", path=str(tmp_path / "one.png"))}
        self._patch_db(monkeypatch, images, {1: _tags("x")})
        req = SimpleNamespace(
            image_ids=[1],
            output_folder=str(tmp_path),
            blacklist=[],
            prefix="",
            content_mode="tags",
            overwrite_policy="unique",
        )
        result = tes.export_tags_batch_request(
            req, id_chunks=iter([[1]]), total=1, cancel_check=lambda: True
        )
        assert result["exported"] == 0  # never entered the chunk loop body


# =========================================================================== #
# 13. export_tags_combined_request — server-side single-file render.
# =========================================================================== #


class TestExportCombined:
    def test_combined_render_roundtrips_through_token_file(self, monkeypatch, tmp_path):
        images = {
            1: _img(id=1, prompt="a, b"),
            2: _img(id=2, prompt="c, d"),
        }
        monkeypatch.setattr(
            tes.db,
            "get_images_by_ids",
            lambda ids: {i: images[i] for i in ids if i in images},
        )
        monkeypatch.setattr(
            tes.db, "get_image_tags_map", lambda ids: {i: [] for i in ids}
        )
        req = SimpleNamespace(
            image_ids=[1, 2],
            blacklist=[],
            prefix="",
            content_mode="prompt",
        )
        result = tes.export_tags_combined_request(
            req, id_chunks=iter([[1, 2]]), total=2
        )
        assert result["status"] == "ok"
        assert result["exported"] == 2
        assert result["download_url"].endswith(result["token"])
        path = tes.combined_export_path(result["token"])
        try:
            assert path.read_text(encoding="utf-8") == "a, b\nc, d"
        finally:
            path.unlink(missing_ok=True)


# =========================================================================== #
# 14. render_export_preview — native vs template branch + blacklist leak scan.
# =========================================================================== #


class TestRenderExportPreview:
    def _patch_db(self, monkeypatch, images, tags_map):
        monkeypatch.setattr(
            tes.db,
            "get_images_by_ids",
            lambda ids: {i: images[i] for i in ids if i in images},
        )
        monkeypatch.setattr(
            tes.db,
            "get_image_tags_map",
            lambda ids: {i: tags_map.get(i, []) for i in ids},
        )

    def test_native_mode_renders_and_scans_blacklist(self, monkeypatch):
        self._patch_db(monkeypatch, {1: _img(id=1)}, {1: _tags("1girl", "bad")})
        req = SimpleNamespace(
            image_ids=[1],
            content_mode="tags",
            blacklist=["bad"],
            prefix="",
            normalize_tag_underscores=False,
            caption_transforms=None,
        )
        out = tes.render_export_preview(req)
        row = out["results"][0]
        assert row["rendered"] == "1girl"
        assert row["blacklist_leaks"] == []
        assert row["error"] is None

    def test_missing_image_yields_not_found_row(self, monkeypatch):
        self._patch_db(monkeypatch, {}, {})
        req = SimpleNamespace(
            image_ids=[42],
            content_mode="tags",
            blacklist=[],
            prefix="",
            normalize_tag_underscores=None,
            caption_transforms=None,
        )
        out = tes.render_export_preview(req)
        assert out["results"][0]["error"] == "not_found"

    def test_over_500_ids_raises_400(self):
        req = SimpleNamespace(image_ids=list(range(1, 502)))
        with pytest.raises(HTTPException) as exc:
            tes.render_export_preview(req)
        assert exc.value.status_code == 400
