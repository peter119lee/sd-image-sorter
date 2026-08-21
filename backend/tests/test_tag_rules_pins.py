"""Characterization pins for backend/tag_rules.py (decomposition step 0).

tag_rules.py is ~3,080 lines but overwhelmingly DATA TABLES; the load-bearing
LOGIC is `categorize_tag` (+ its lazy WD14/booru vocab caches and __file__-derived
CSV discovery), `get_exclusion_targets`, and a handful of import-time derived
constants. Statement coverage from the existing suites is already ~92%, but the
uncovered remainder is exactly the split-fragile seams: the booru/wd14 cache
branches, the __file__→repo_root anchor, and the exclusion absent/category
branches. These pins lock that behavior so the planned `tag_rules/` package split
cannot silently regress it.

Machine-state isolation (the commit-0edbb81 pattern): `categorize_tag` consults
lazy caches loaded from WD14/booru `selected_tags.csv` files THIS machine
happens to have, plus the bundled StoryAura character-name list. Tests that
assert categorize verdicts monkeypatch those module globals to controlled
containers so the result depends only on the hardcoded rules and can't flip
between the owner's box and a fresh checkout.

No existing file is modified; this file adds only tests.
"""

import csv
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tag_rules  # noqa: E402
from tag_rules import (  # noqa: E402
    BUILTIN_EXCLUSION_RULES,
    BUILTIN_TAG_SETS,
    WEIGHTED_GROUPS,
    categorize_tag,
    categorize_tags_batch,
    get_exclusion_targets,
)

# The 14 categories the frontend renders as pill colors
# (frontend/css/dataset-maker.css `dataset-tag-pill-category-*`, pinned by
# test_frontend_contract.py:878). No categorize_tag result may fall outside this
# set, and none of these strings may change.
FRONTEND_CATEGORIES = {
    "character",
    "artist",
    "outfit",
    "pose",
    "body",
    "expression",
    "background",
    "action",
    "style",
    "quality",
    "meta",
    "rating",
    "angle",
    "unknown",
}


@pytest.fixture
def isolated_caches(monkeypatch):
    """Neutralize the two machine-dependent lazy vocab caches.

    Empty containers (NOT None) short-circuit the CSV loaders, so there is no
    disk read and no per-machine flip. This is the seam that
    test_tag_training_filters.py already relies on (commit 0edbb81); the split
    must keep both globals patchable on whatever module ends up owning them.
    """
    monkeypatch.setattr(tag_rules, "_wd14_character_tags", set())
    monkeypatch.setattr(tag_rules, "_booru_tag_categories", {})
    monkeypatch.setattr(tag_rules, "_storyaura_character_tags", set())


# ---------------------------------------------------------------------------
# 1. The 14-category contract — one representative per category, isolated
# ---------------------------------------------------------------------------
class TestCategoryContract:
    # Each representative resolves via the HARDCODED rules (exact-set lookups or
    # keyword/suffix rules) that run before the vocab caches, so empty caches ==
    # production verdict.
    REPRESENTATIVES = {
        "1girl": "character",
        "artist:wlop": "artist",
        "school_uniform": "outfit",
        "standing": "pose",
        "blue_eyes": "body",
        "smile": "expression",
        "outdoors": "background",
        "holding": "action",
        "watercolor": "style",
        "masterpiece": "quality",
        "comic": "meta",
        "rating:safe": "rating",
        "from_above": "angle",
        "zzz_made_up_tag": "unknown",
    }

    def test_one_representative_per_category(self, isolated_caches):
        for tag, expected in self.REPRESENTATIVES.items():
            assert categorize_tag(tag) == expected, (
                f"{tag!r} -> {categorize_tag(tag)!r} (expected {expected!r})"
            )

    def test_every_representative_hits_a_distinct_category(self, isolated_caches):
        produced = {categorize_tag(tag) for tag in self.REPRESENTATIVES}
        assert produced == FRONTEND_CATEGORIES

    def test_result_is_always_a_frontend_category(self, isolated_caches):
        sample = [
            "1girl",
            "blue_eyes",
            "school_uniform",
            "holding_cup",
            "rating:safe",
            "score_9",
            "zzz_made_up_tag",
            "@someartist",
        ]
        for tag in sample:
            assert categorize_tag(tag) in FRONTEND_CATEGORIES

    def test_normalization_folds_case_and_spaces(self, isolated_caches):
        # tag.lower().replace(" ", "_") is the single normalization the 8
        # importers rely on — "Blue Eyes" and "blue_eyes" must categorize alike.
        assert categorize_tag("Blue Eyes") == categorize_tag("blue_eyes") == "body"
        assert categorize_tag("SCHOOL UNIFORM") == "outfit"


# ---------------------------------------------------------------------------
# 2. The booru / WD14 vocab cache seam (uncovered branches 2389-2401)
# ---------------------------------------------------------------------------
class TestVocabCacheSeam:
    def test_booru_category_maps_to_semantic_bucket(self, monkeypatch):
        monkeypatch.setattr(tag_rules, "_wd14_character_tags", set())
        monkeypatch.setattr(
            tag_rules,
            "_booru_tag_categories",
            {
                "zrat": "rating",
                "zart": "artist",
                "zchar": "character",
                "zcopy": "copyright",
                "zmeta": "meta",
            },
        )
        # copyright folds into character; the rest map straight through.
        assert categorize_tag("zrat") == "rating"
        assert categorize_tag("zart") == "artist"
        assert categorize_tag("zchar") == "character"
        assert categorize_tag("zcopy") == "character"
        assert categorize_tag("zmeta") == "meta"

    def test_booru_general_falls_through_to_shape_classifier(self, monkeypatch):
        # A booru vocab tag flagged "general" that matches no hardcoded rule must
        # NOT stay "unknown"; it is bucketed by token shape
        # (_fallback_known_booru_general_category), defaulting to "meta".
        monkeypatch.setattr(tag_rules, "_wd14_character_tags", set())
        monkeypatch.setattr(
            tag_rules, "_booru_tag_categories", {"zzz_made_up_tag": "general"}
        )
        assert categorize_tag("zzz_made_up_tag") == "meta"

    def test_wd14_character_cache_forces_character(self, monkeypatch):
        # An otherwise-unknown token present in the WD14 character cache is a
        # character (the seam that stops keyword rules mislabeling char names).
        monkeypatch.setattr(tag_rules, "_booru_tag_categories", {})
        monkeypatch.setattr(tag_rules, "_wd14_character_tags", {"zzz_made_up_tag"})
        assert categorize_tag("zzz_made_up_tag") == "character"

    def test_shape_fallback_buckets_by_token(self):
        # _fallback_known_booru_general_category is a pure (tag_lower, tokens) fn.
        fb = tag_rules._fallback_known_booru_general_category
        assert fb("bad_hands", {"bad", "hands"}) == "quality"
        assert fb("x", {"style"}) == "style"
        assert fb("x", {"smile"}) == "expression"
        assert fb("x", {"standing"}) == "pose"
        assert fb("x", {"holding"}) == "action"
        assert fb("x", {"skin"}) == "body"
        assert fb("x", {"shirt"}) == "outfit"
        assert fb("x", {"room"}) == "background"
        assert fb("x", {"girl"}) == "character"
        assert fb("x", {"unrelatedtoken"}) == "meta"  # default bucket


# ---------------------------------------------------------------------------
# 3. The __file__ -> repo_root CSV discovery anchor (HAZARD #1)
# ---------------------------------------------------------------------------
class TestCsvDiscoveryAnchor:
    def test_loader_module_sits_directly_under_backend(self):
        """LOCATION CONTRACT for `_iter_tagger_selected_tag_csvs`.

        The loader computes `repo_root = Path(__file__).resolve().parent.parent`,
        so the module that DEFINES it must live at `backend/<file>.py` for
        `parent.parent` to be the repo root. If the split relocates this loader
        into a subpackage (e.g. `backend/tag_rules/csv_discovery.py`),
        `parent.parent` becomes `backend/` and tagger-CSV discovery SILENTLY
        degrades (character/booru classification quietly downgrades, no raise).

        If you intentionally relocate the loader, re-anchor it on a shared
        backend-root constant (the image_service `_BACKEND_FILE` technique) and
        update this pin in the same commit.
        """
        loader_file = Path(
            inspect.getsourcefile(tag_rules._iter_tagger_selected_tag_csvs)
        ).resolve()
        assert loader_file.parent.name == "backend", (
            "tagger-CSV loader must live directly under backend/ so "
            "Path(__file__).parent.parent is the repo root; "
            f"found parent={loader_file.parent.name!r}"
        )
        assert (loader_file.parent.parent / "backend").is_dir()

    def test_repo_root_derived_discovery_finds_dropped_csv(self, tmp_path, monkeypatch):
        """Behavioral pin for the __file__->repo_root->data/models plumbing.

        Simulate the module living at <tmp>/backend/<file>.py, force the config
        roots to a nonexistent dir so only the __file__-derived roots remain,
        drop a selected_tags.csv under <tmp>/data/models/wd14-tagger/, and assert
        the loader discovers it via rglob. Exercised without touching the real
        repo tree.
        """
        import config

        defining_mod = sys.modules[tag_rules._iter_tagger_selected_tag_csvs.__module__]
        fake_backend = tmp_path / "backend"
        fake_backend.mkdir()
        monkeypatch.setattr(
            defining_mod, "__file__", str(fake_backend / "tag_rules.py")
        )
        nonexistent = str(tmp_path / "nope")
        monkeypatch.setattr(config, "get_wd14_model_dir", lambda: nonexistent)
        monkeypatch.setattr(config, "get_oppai_oracle_model_dir", lambda: nonexistent)

        csv_dir = tmp_path / "data" / "models" / "wd14-tagger" / "sub"
        csv_dir.mkdir(parents=True)
        target = csv_dir / "selected_tags.csv"
        self._write_csv(target, [("foo", "4"), ("bar", "0")])

        found = tag_rules._iter_tagger_selected_tag_csvs()
        assert target.resolve() in {p.resolve() for p in found}

    def test_character_loader_reads_category_four_rows(self, tmp_path, monkeypatch):
        self._point_discovery_at(tmp_path, monkeypatch)
        self._write_csv(
            tmp_path / "data" / "models" / "wd14-tagger" / "selected_tags.csv",
            [("miku_char", "4"), ("blue_sky", "0"), ("wlop_art", "1")],
        )
        # Force a reload past the cache, then assert only category-4 names load.
        monkeypatch.setattr(tag_rules, "_wd14_character_tags", None)
        assert tag_rules._load_wd14_character_tags() == {"miku_char"}

    def test_booru_loader_maps_category_ids(self, tmp_path, monkeypatch):
        self._point_discovery_at(tmp_path, monkeypatch)
        self._write_csv(
            tmp_path / "data" / "models" / "wd14-tagger" / "selected_tags.csv",
            [
                ("gen_tag", "0"),
                ("art_tag", "1"),
                ("char_tag", "4"),
                ("rat_tag", "9"),
                ("junk_tag", "not_an_int"),
            ],
        )
        monkeypatch.setattr(tag_rules, "_booru_tag_categories", None)
        cats = tag_rules._load_booru_tag_categories()
        assert cats["gen_tag"] == "general"
        assert cats["art_tag"] == "artist"
        assert cats["char_tag"] == "character"
        assert cats["rat_tag"] == "rating"
        # Non-integer category id is skipped, not crashed on.
        assert "junk_tag" not in cats

    # -- helpers --
    @staticmethod
    def _write_csv(path: Path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["name", "category"])
            for name, category in rows:
                writer.writerow([name, category])

    @staticmethod
    def _point_discovery_at(tmp_path, monkeypatch):
        import config

        defining_mod = sys.modules[tag_rules._iter_tagger_selected_tag_csvs.__module__]
        fake_backend = tmp_path / "backend"
        fake_backend.mkdir(exist_ok=True)
        monkeypatch.setattr(
            defining_mod, "__file__", str(fake_backend / "tag_rules.py")
        )
        nonexistent = str(tmp_path / "nope")
        monkeypatch.setattr(config, "get_wd14_model_dir", lambda: nonexistent)
        monkeypatch.setattr(config, "get_oppai_oracle_model_dir", lambda: nonexistent)


# ---------------------------------------------------------------------------
# 4. Import-time derived constants (HAZARD #3)
# ---------------------------------------------------------------------------
class TestDerivedConstants:
    def test_garment_veto_keywords_derived_from_outfit_keywords(self):
        # `_GARMENT_VETO_KEYWORDS = tuple(k for k in OUTFIT_KEYWORDS if len(k) > 3)`.
        # If a data-table split defines this before OUTFIT_KEYWORDS is available,
        # or drops the len>3 guard, the garment-veto regression fix breaks.
        assert all(
            kw in tag_rules.OUTFIT_KEYWORDS and len(kw) > 3
            for kw in tag_rules._GARMENT_VETO_KEYWORDS
        )
        # "bra" (len 3) must be excluded so it cannot veto "branch"/"branches".
        assert "bra" not in tag_rules._GARMENT_VETO_KEYWORDS

    def test_garment_veto_tokens_are_the_short_words(self):
        assert tag_rules._GARMENT_VETO_TOKENS == {"bra", "hat", "cap", "tie", "bow"}

    def test_franchise_pattern_built_from_suffixes(self):
        # `_FRANCHISE_PATTERN` is compiled from `_FRANCHISE_SUFFIXES`; it matches
        # a cleaned `name(franchise)` string only for a known franchise.
        assert tag_rules._FRANCHISE_PATTERN.match("ganyu(genshin_impact)")
        assert tag_rules._FRANCHISE_PATTERN.match("kongou(touhou)")
        assert not tag_rules._FRANCHISE_PATTERN.match("foo(not_a_real_franchise)")

    def test_franchise_suffix_tag_categorizes_as_character(self, isolated_caches):
        # End-to-end: the franchise heuristic (not the vocab cache) classifies a
        # `name_(franchise)` tag as a character.
        assert categorize_tag("ganyu_(genshin_impact)") == "character"


# ---------------------------------------------------------------------------
# 5. The v3.3.4 garment-veto regression (derived constants + rule ordering)
# ---------------------------------------------------------------------------
class TestGarmentVetoRegression:
    @pytest.mark.parametrize(
        "tag",
        ["tank_top", "pencil_skirt", "key_necklace", "winter_coat", "moon_print"],
    )
    def test_generic_object_compounds_stay_outfit(self, isolated_caches, tag):
        # Generic nouns (tank, pencil, key, winter, moon) live in the background
        # keyword tables; the garment veto must keep these compounds as outfit.
        assert categorize_tag(tag) == "outfit"

    @pytest.mark.parametrize(
        "tag",
        ["house", "indoors", "tank", "winter", "moon", "branch", "branches"],
    )
    def test_genuine_object_tags_stay_background(self, isolated_caches, tag):
        assert categorize_tag(tag) == "background"


# ---------------------------------------------------------------------------
# 6. get_exclusion_targets — present / absent / substring / category branches
# ---------------------------------------------------------------------------
class TestExclusionTargets:
    def test_present_condition_excludes_named_targets(self):
        excluded = get_exclusion_targets({"from_behind"}, BUILTIN_EXCLUSION_RULES)
        assert "looking_at_viewer" in excluded
        assert "blue_eyes" in excluded
        assert "smile" in excluded

    def test_condition_uses_substring_and_is_case_insensitive(self):
        # The condition test is `cond_tag in t.lower().replace(" ", "_")`, so
        # "nude" fires on "completely_nude", and matching folds case/spaces.
        assert "school_uniform" in get_exclusion_targets(
            {"completely_nude"}, BUILTIN_EXCLUSION_RULES
        )
        assert "looking_at_viewer" in get_exclusion_targets(
            {"From Behind"}, BUILTIN_EXCLUSION_RULES
        )

    def test_absent_condition_branch(self):
        rule = [
            {
                "name": "x",
                "conditions": [{"tag": "solo", "type": "absent"}],
                "targets": [{"tag": "foo"}],
            }
        ]
        # Present -> condition NOT met -> nothing excluded.
        assert get_exclusion_targets({"solo"}, rule) == set()
        # Absent -> condition met -> target excluded.
        assert get_exclusion_targets({"1girl"}, rule) == {"foo"}

    def test_category_only_target_is_a_noop(self):
        # A `{"category": ...}` target with no "tag" is deliberately a no-op
        # (the categorize_tag-backed category exclusion is not implemented);
        # named-tag targets in the same rule still apply.
        rule = [
            {
                "name": "c",
                "conditions": [{"tag": "solo"}],
                "targets": [{"category": "body"}, {"tag": "bar"}],
            }
        ]
        assert get_exclusion_targets({"solo"}, rule) == {"bar"}

    def test_targets_are_underscore_normalized(self):
        rule = [
            {
                "name": "n",
                "conditions": [{"tag": "solo"}],
                "targets": [{"tag": "Looking At Viewer"}],
            }
        ]
        assert get_exclusion_targets({"solo"}, rule) == {"looking_at_viewer"}


# ---------------------------------------------------------------------------
# 7. Public-surface shapes the 8 importers + prompt_generator depend on
# ---------------------------------------------------------------------------
class TestPublicSurfaceShape:
    def test_categorize_tags_batch_returns_mapping(self, isolated_caches):
        assert categorize_tags_batch(["1girl", "smile"]) == {
            "1girl": "character",
            "smile": "expression",
        }

    def test_weighted_groups_shape(self):
        assert set(WEIGHTED_GROUPS) == {"pose", "expression", "angle"}
        for name, choices in WEIGHTED_GROUPS.items():
            assert choices, f"{name} group is empty"
            for value, weight in choices:
                assert isinstance(value, str)
                assert isinstance(weight, int) and weight > 0

    def test_builtin_tag_sets_shape(self):
        assert BUILTIN_TAG_SETS
        for tag_set in BUILTIN_TAG_SETS:
            assert {"name", "category", "tags"} <= set(tag_set)
            assert tag_set["tags"]
            for entry in tag_set["tags"]:
                assert {"tag", "weight", "required"} <= set(entry)

    def test_builtin_exclusion_rules_shape(self):
        assert BUILTIN_EXCLUSION_RULES
        for rule in BUILTIN_EXCLUSION_RULES:
            assert {"name", "conditions", "targets"} <= set(rule)
            for cond in rule["conditions"]:
                assert "tag" in cond
