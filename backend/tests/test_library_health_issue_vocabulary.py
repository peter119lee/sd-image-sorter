"""The invariant over ``issue_counts`` itself, so there is no fourth instance.

This payload has now produced the same defect three times, and each time it was
one key at a time:

* ``missing_prompt`` (``7c10fb6``) counted caption-only rows as broken forever;
* ``missing_checkpoint`` (``5332c02``) read 5,382 of 5,382 on the owner's library
  with a recommendation attached, when no action can add a checkpoint to an image
  Stable Diffusion never made;
* ``/api/metadata/health`` (``62dc568``) advertised 5,198 next to a button that
  could only reach 4,420.

The shape underneath all three: **a number the user is charged for, beside an
action that cannot move that number.** So the vocabulary is now declared rather
than assembled — :data:`db_facets.ISSUE_VOCABULARY` and
:data:`db_facets.ISSUE_REMEDIES` are what *build* ``issue_counts``,
``actionable_count``, the quality weights and the recommendations — and this file
tests the declaration as a property instead of listing today's keys.

What is enforced here
=====================
1. Every key the payload publishes is declared. The test reads
   ``report["issue_counts"].keys()``, never a literal list, so a key added to the
   payload without a declaration fails.
2. No charge without a remedy. A key with no remedy must record why it is
   reported anyway and may carry **no** quality weight and **no**
   ``actionable_count`` contribution. ``_validate_issue_vocabulary`` refuses the
   alternative at import; the tests below prove that guard actually bites by
   feeding it the two historical shapes.
3. Every published number equals the count of its own declared predicate, and
   every remedy advertises the number of **distinct rows** matched by the union of
   the keys it resolves — recomputed here from the keys, not from a second
   declaration. This is what caught the live ``reparse_or_reconnect`` double
   count (3,074 advertised against 1,537 rows).
   The quality score obeys the same rule: it charges each remedy's rows once, so
   at most one of a remedy's keys may declare a weight. The card was fixed and
   the score was left summing both, which is why a dead row still cost 4.0.
4. For the one remedy backed by an enumerable job, the advertised number equals
   what that job can still change, asked of the job itself.

What it cannot enforce, stated plainly
======================================
Whether a condition is a **defect at all** is a domain judgement no property test
can make: ``untagged`` at 100% of a fresh library is real work, and
``missing_checkpoint`` at 100% of an art library is not, and nothing structural
separates them. A declaration that claims a remedy which no code can perform
passes every check here — the ``action`` field exists to force the author to write
the claim down where a reviewer sees it, and that is a convention, not a test.
Instance 2 above is exactly that shape; it is pinned by the per-key domain tests
in ``test_library_health_checkpoint_scope.py`` and
``test_library_health_generator_attribution.py``, which each build a library where
the condition is universal and no repair is possible. A new key needs its own
such test; this file cannot write it for them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db
from db_facets import (
    ISSUE_REMEDIES,
    ISSUE_VOCABULARY,
    IssueRemedy,
    IssueSpec,
    _validate_issue_vocabulary,
)

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    generator: str = "unknown",
    checkpoint: Optional[str] = None,
    readable: bool = True,
    tagged: bool = False,
    dimensions: bool = True,
    file_size: Optional[int] = 1024,
    metadata_status: Optional[str] = None,
) -> int:
    image_id = int(
        db.add_image(
            path=str(folder / name),
            filename=name,
            generator=generator,
            prompt=prompt,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            width=64 if dimensions else None,
            height=64 if dimensions else None,
            file_size=file_size,
            metadata_json="{}",
        )
    )
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE images
            SET prompt = ?, sidecar_caption = ?, sidecar_caption_format = NULL
            WHERE id = ?
            """,
            (prompt, caption, image_id),
        )
        if not dimensions:
            conn.execute(
                "UPDATE images SET width = NULL, height = NULL WHERE id = ?", (image_id,)
            )
        if file_size is None:
            conn.execute("UPDATE images SET file_size = NULL WHERE id = ?", (image_id,))
        if tagged:
            conn.execute(
                "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?", (image_id,)
            )
        if metadata_status is not None:
            conn.execute(
                "UPDATE images SET metadata_status = ? WHERE id = ?",
                (metadata_status, image_id),
            )
    if not readable:
        db.mark_image_unreadable(image_id, "original file is gone")
    return image_id


def _report(sample_limit: int = 25) -> Dict[str, Any]:
    return db.get_library_health_report(sample_limit=sample_limit)


def _count_where(sql: str) -> int:
    with db.get_db() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM images WHERE {sql}").fetchone()[0])


def _clear_library() -> None:
    """Empty the library so one test can compare two whole libraries."""
    with db.get_db() as conn:
        conn.execute("DELETE FROM images")


@pytest.fixture
def library_with_every_issue(test_db, tmp_path: Path) -> Dict[str, Any]:
    """One library that trips every remediable key, with distinct counts.

    Distinct counts matter: if two keys held the same number, an assertion could
    pass while reading the wrong column. Fixture shape mirrors the owner's real
    library — dead rows, ``generator`` of both ``'unknown'`` and ``'others'``,
    caption-only rows via ``prompt IS NULL`` and ``TRIM(prompt) = ''``,
    ``sidecar_caption_format`` NULL for pre-044 rows, no checkpoint on the art.
    """
    folder = tmp_path / "everything"
    folder.mkdir()

    rows: Dict[str, List[int]] = {}
    # unreadable (and therefore metadata_error too: mark_image_unreadable sets
    # metadata_status = 'error', which is the overlap the advice used to double).
    rows["unreadable"] = [
        _seed(folder, f"gone-{index}.png", caption=DANBOORU_CAPTION, tagged=True, readable=False)
        for index in range(4)
    ]
    # A parse error on a file that still opens, so metadata_error is not simply a
    # second name for unreadable and an assertion cannot read the wrong column.
    rows["readable_metadata_error"] = [
        _seed(folder, "parse-failed.png", caption=DANBOORU_CAPTION, generator="others",
              tagged=True, metadata_status="error"),
    ]
    # missing_text: no prompt and no caption. Both spellings of "no prompt".
    rows["missing_text"] = [
        _seed(folder, "textless-null.png", tagged=True),
        _seed(folder, "textless-blank.png", prompt="", tagged=True),
        _seed(folder, "textless-third.png", tagged=True),
    ]
    # sd_missing_checkpoint: a generator claimed it, no model name recorded.
    rows["sd_missing_checkpoint"] = [
        _seed(folder, "webui-no-cp.png", prompt="1girl", generator="webui", tagged=True),
        _seed(folder, "comfy-no-cp.png", prompt="1boy", generator="comfyui", tagged=True),
        _seed(folder, "forge-no-cp.png", prompt="1other", generator="forge", tagged=True),
        _seed(folder, "nai-no-cp.png", prompt="1girl, rain", generator="nai", tagged=True),
        _seed(folder, "reforge-no-cp.png", prompt="scenery", generator="reforge", tagged=True),
        _seed(folder, "swarm-no-cp.png", prompt="portrait", generator="swarmui", tagged=True),
    ]
    # unattributed_sd_metadata: SD data against no generator at all.
    rows["unattributed_sd_metadata"] = [
        _seed(folder, "legacy-a.png", prompt="1girl, legacy", generator="unknown",
              checkpoint="ponyRealism.safetensors", tagged=True),
        _seed(folder, "legacy-b.png", prompt="1boy, legacy", generator="",
              checkpoint="ponyRealism.safetensors", tagged=True),
    ]
    # missing_dimensions only.
    rows["missing_dimensions"] = [
        _seed(folder, f"nodims-{index}.png", caption=DANBOORU_CAPTION, tagged=True,
              dimensions=False)
        for index in range(6)
    ]
    # missing_file_size only.
    rows["missing_file_size"] = [
        _seed(folder, f"nosize-{index}.png", caption=DANBOORU_CAPTION, tagged=True,
              file_size=None)
        for index in range(7)
    ]
    # Both at once, so the incomplete-record union is strictly smaller than the
    # sum of its two counters — the overlap the reconnect advice used to double.
    rows["incomplete_record"] = [
        _seed(folder, f"stub-{index}.png", caption=DANBOORU_CAPTION, tagged=True,
              dimensions=False, file_size=None)
        for index in range(2)
    ]
    # untagged.
    rows["untagged"] = [
        _seed(folder, f"untagged-{index}.png", caption=DANBOORU_CAPTION, generator="others")
        for index in range(10)
    ]
    # metadata_pending.
    rows["metadata_pending"] = [
        _seed(folder, "pending.png", caption=DANBOORU_CAPTION, tagged=True,
              metadata_status="pending"),
    ]
    # A row with nothing wrong, so no counter equals the library total.
    rows["clean"] = [
        _seed(folder, "clean.png", prompt="1girl", generator="webui",
              checkpoint="ponyRealism.safetensors", negative_prompt="blurry", tagged=True),
    ]
    return {"folder": folder, "rows": rows}


class TestTheVocabularyIsClosed:
    def test_every_published_key_is_declared(self, library_with_every_issue):
        """Read off the payload, never listed here: a key added to
        ``issue_counts`` without a declaration has no remedy, no recorded reason,
        and would become a permanent bar the first time the UI iterates the dict.
        """
        published = set(_report()["issue_counts"])
        declared = {spec.key for spec in ISSUE_VOCABULARY}

        assert published == declared

    def test_no_statistic_is_also_an_issue(self, library_with_every_issue):
        """The two blocks mean opposite things; a key in both would let a
        consumer read the composition figure as a defect."""
        report = _report()

        assert set(report["statistics"]).isdisjoint(report["issue_counts"])

    def test_every_published_count_equals_its_own_declared_predicate(
        self, library_with_every_issue
    ):
        issue_counts = _report()["issue_counts"]

        mismatched = {
            spec.key: (issue_counts[spec.key], _count_where(spec.sql))
            for spec in ISSUE_VOCABULARY
            if issue_counts[spec.key] != _count_where(spec.sql)
        }

        assert mismatched == {}, f"published count disagrees with its predicate: {mismatched}"

    def test_the_fixture_gives_every_remediable_key_a_distinct_count(
        self, library_with_every_issue
    ):
        """Guard on the guards: identical counts would let the assertions above
        pass while reading the wrong column."""
        issue_counts = _report()["issue_counts"]
        remediable = [
            issue_counts[spec.key] for spec in ISSUE_VOCABULARY if spec.remedy is not None
        ]

        assert all(count > 0 for count in remediable), issue_counts
        assert len(set(remediable)) == len(remediable), issue_counts


class TestEveryNumberBesideAnActionIsWhatTheActionTargets:
    def test_every_remedy_advertises_the_distinct_rows_it_visits(
        self, library_with_every_issue
    ):
        """The identity that was the defect in all three fixed instances.

        Expected value is recomputed here from the keys the remedy resolves —
        one row counted once, however many of its keys match — not read back
        from a second declaration that could drift with the first.
        """
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}
        by_key = {spec.key: spec for spec in ISSUE_VOCABULARY}

        for remedy in ISSUE_REMEDIES:
            union = " OR ".join(f"({by_key[key].sql})" for key in remedy.keys)
            distinct_rows = _count_where(union)
            assert remedy.kind in by_kind, (
                f"{remedy.kind} covers {distinct_rows} rows and offers nothing"
            )
            assert by_kind[remedy.kind]["count"] == distinct_rows, (
                f"{remedy.kind} advertises {by_kind[remedy.kind]['count']} where "
                f"{distinct_rows} rows exist"
            )

    def test_a_multi_key_remedy_counts_rows_and_not_counters(
        self, library_with_every_issue
    ):
        """Made explicit because it is the live bug: summing two counters over
        overlapping rows is how the reconnect advice reached 3,074 on a library
        with 1,537 such rows."""
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}
        issue_counts = report["issue_counts"]
        multi_key = [remedy for remedy in ISSUE_REMEDIES if len(remedy.keys) > 1]

        assert multi_key, "the property only means something with a multi-key remedy"
        for remedy in multi_key:
            summed = sum(issue_counts[key] for key in remedy.keys)
            assert summed > by_kind[remedy.kind]["count"], (
                f"fixture must make {remedy.kind}'s keys overlap"
            )

    def test_no_advice_outgrows_the_library_it_describes(self, library_with_every_issue):
        report = _report()
        total = report["summary"]["total_images"]

        for item in report["recommendations"]:
            assert item["count"] <= total, item

    def test_the_recovery_advice_equals_what_the_job_can_still_change(
        self, library_with_every_issue
    ):
        """Asked of the job itself, so it is not a restatement of the payload.

        This is the check that would have caught ``missing_prompt``: its snapshot
        is deliberately the wider set (every promptless row, because a parser
        upgrade may yet crack one), and the reducible subset is what a run can
        turn from no text into text. The panel used to publish the wider one.
        """
        import services.metadata_repair_service as mrs

        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}
        snapshot = set(mrs.snapshot_missing_prompt_ids())
        with db.get_db() as conn:
            reducible = {
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM images "
                    "WHERE (prompt IS NULL OR TRIM(prompt) = '') "
                    "AND (sidecar_caption IS NULL OR TRIM(sidecar_caption) = '') "
                    "AND COALESCE(is_readable, 1) = 1"
                )
            }

        assert reducible < snapshot, "fixture must exercise the gap between the two sets"
        assert report["issue_counts"]["missing_text"] == len(reducible)
        assert by_kind["missing_text"]["count"] == len(reducible)


class TestOneBrokenRowCostsTheScoreOnce:
    """The distinct-row rule the cards follow, applied to the score too.

    ``mark_image_unreadable`` sets ``is_readable = 0`` **and**
    ``metadata_status = 'error'``, so a dead row trips ``unreadable`` and
    ``metadata_error`` both, and one re-scan clears both. Summing the two
    counters deducted twice for one row: 12,569.9 of weighted penalty on the
    owner's library where 9,495.9 is owed, publishing 60.0 for a library that
    scores 69.8. Same false count as the ``reparse_or_reconnect`` card before
    ``80734ce``, one layer down and left behind by it.
    """

    def test_a_dead_row_costs_what_a_parse_failure_costs(self, test_db, tmp_path: Path):
        """Two libraries, two rows each, one broken row in each.

        Both broken rows are offered the same single re-scan by the same card,
        so the score must not care that one of them trips two keys and the other
        trips one. Asserted as an equality between libraries rather than against
        a recomputed score, so it pins the composition without restating the
        curve — and so that merely zeroing ``metadata_error``'s weight, which
        would make the readable parse failure cost nothing at all, fails it too.
        """
        folder = tmp_path / "broken"
        folder.mkdir()

        # A file that still opens whose metadata parse failed: metadata_error
        # alone. reparse_image_metadata writes exactly this on a soft parse
        # error, with is_readable left true.
        _seed(folder, "healthy-a.png", prompt="1girl", generator="webui",
              checkpoint="ponyRealism.safetensors", tagged=True)
        _seed(folder, "parse-failed.png", caption=DANBOORU_CAPTION, generator="others",
              tagged=True, metadata_status="error")
        parse_failure = _report()

        _clear_library()

        # A file that is gone: unreadable, and metadata_error because marking it
        # unreadable sets that too.
        _seed(folder, "healthy-b.png", prompt="1girl", generator="webui",
              checkpoint="ponyRealism.safetensors", tagged=True)
        _seed(folder, "gone.png", caption=DANBOORU_CAPTION, tagged=True, readable=False)
        dead_row = _report()

        assert parse_failure["issue_counts"]["unreadable"] == 0
        assert parse_failure["issue_counts"]["metadata_error"] == 1
        assert dead_row["issue_counts"]["unreadable"] == 1
        assert dead_row["issue_counts"]["metadata_error"] == 1
        assert parse_failure["summary"]["total_images"] == dead_row["summary"]["total_images"]

        by_kind_parse = {item["kind"]: item["count"] for item in parse_failure["recommendations"]}
        by_kind_dead = {item["kind"]: item["count"] for item in dead_row["recommendations"]}
        assert by_kind_parse["reparse_or_reconnect"] == 1
        assert by_kind_dead["reparse_or_reconnect"] == 1, (
            "one row to re-scan in both libraries, so the score owes the same"
        )

        assert dead_row["summary"]["quality_score"] == parse_failure["summary"]["quality_score"], (
            "the same broken row costs more when the payload has two names for "
            "what is wrong with it"
        )

    def test_no_remedy_charges_for_its_rows_twice(self):
        """The declaration that made it possible, pinned where it is written.

        Two keys resolved by one remedy describe rows one action fixes, and the
        score charges that remedy's rows once — so at most one of its keys may
        carry the weight.
        """
        by_key = {spec.key: spec for spec in ISSUE_VOCABULARY}

        for remedy in ISSUE_REMEDIES:
            weighted = [key for key in remedy.keys if by_key[key].quality_weight]
            assert len(weighted) <= 1, (
                f"{remedy.kind} resolves {sorted(weighted)}, each carrying its own "
                "weight, so a row matching both is charged twice for one repair"
            )


class TestNothingChargesTheUserWithoutOfferingAnAction:
    def test_the_summary_totals_come_only_from_declared_contributors(
        self, library_with_every_issue
    ):
        report = _report()
        issue_counts = report["issue_counts"]
        duplicates = report["duplicate_filenames"]["images"]

        expected = sum(
            issue_counts[spec.key] for spec in ISSUE_VOCABULARY if spec.feeds_actionable
        ) + duplicates

        assert report["summary"]["actionable_count"] == expected

    def test_a_reported_only_key_costs_nothing(self, test_db, tmp_path: Path):
        """Behavioural, and the shape that caught ``missing_checkpoint``: give
        every row the thing the key says is missing and the two numbers the panel
        leads with must not move. If they did, the key would be charging for a
        condition it offers no way to fix.
        """
        folder = tmp_path / "coverage"
        folder.mkdir()
        rows = [
            _seed(folder, f"art-{index}.png", caption=DANBOORU_CAPTION, tagged=True)
            for index in range(5)
        ]
        reported_only = [spec for spec in ISSUE_VOCABULARY if spec.remedy is None]
        assert reported_only, "the property only means something with such a key"

        before = _report()
        assert all(before["issue_counts"][spec.key] == len(rows) for spec in reported_only), (
            before["issue_counts"]
        )

        placeholders = ",".join("?" for _ in rows)
        with db.get_db() as conn:
            conn.execute(
                f"UPDATE images SET embedding = X'00', aesthetic_score = 5.0 "
                f"WHERE id IN ({placeholders})",
                rows,
            )
        after = _report()

        assert all(after["issue_counts"][spec.key] == 0 for spec in reported_only), (
            after["issue_counts"]
        )
        assert after["summary"]["actionable_count"] == before["summary"]["actionable_count"]
        assert after["summary"]["quality_score"] == before["summary"]["quality_score"]

    def test_every_reported_only_key_records_why(self):
        for spec in ISSUE_VOCABULARY:
            if spec.remedy is None:
                assert spec.reported_only_reason.strip(), spec.key

    def test_every_remedy_names_the_control_the_user_reaches(self):
        """A written convention, not a proof — see this module's docstring. What
        it does guarantee is that nobody adds a remedy without stating one."""
        for remedy in ISSUE_REMEDIES:
            assert remedy.action.strip(), remedy.kind


class TestTheGuardActuallyBites:
    """Five or more guards in this project had silently stopped checking, so the
    validator is exercised against the shapes it exists to reject rather than
    only against a vocabulary that already passes.
    """

    def test_the_shipped_vocabulary_validates(self):
        _validate_issue_vocabulary()

    def test_two_weighted_keys_under_one_remedy_are_rejected(self):
        """``unreadable`` + ``metadata_error``'s exact shape: one repair, one
        row, charged twice because each key carried its own weight."""
        vocabulary = tuple(
            spec._replace(quality_weight=2.0) if spec.key == "metadata_error" else spec
            for spec in ISSUE_VOCABULARY
        )

        with pytest.raises(ValueError, match="charges for its rows twice"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_key_that_charges_the_user_with_no_remedy_is_rejected(self):
        """``unknown_generator``'s exact shape: a 0.6 quality penalty and no
        recommendation anywhere in the payload."""
        vocabulary = ISSUE_VOCABULARY + (
            IssueSpec(
                key="unknown_generator",
                sql="COALESCE(is_readable, 1) = 1 AND generator = 'unknown'",
                remedy=None,
                reported_only_reason="the parser could not attribute this image",
                quality_weight=0.6,
            ),
        )

        with pytest.raises(ValueError, match="charges the user"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_key_that_feeds_actionable_with_no_remedy_is_rejected(self):
        vocabulary = ISSUE_VOCABULARY + (
            IssueSpec(
                key="missing_negative_prompt",
                sql="COALESCE(is_readable, 1) = 1 AND negative_prompt IS NULL",
                remedy=None,
                reported_only_reason="nothing renders it today",
                feeds_actionable=True,
            ),
        )

        with pytest.raises(ValueError, match="charges the user"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_key_with_neither_remedy_nor_reason_is_rejected(self):
        vocabulary = ISSUE_VOCABULARY + (
            IssueSpec(key="missing_thumbnail", sql="1 = 0", remedy=None),
        )

        with pytest.raises(ValueError, match="no recorded reason"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_key_naming_a_remedy_nothing_emits_is_rejected(self):
        """``missing_prompt``'s shape after ``7c10fb6`` removed its card: a bar
        pointing at advice the payload never produces."""
        vocabulary = ISSUE_VOCABULARY + (
            IssueSpec(
                key="missing_prompt",
                sql="COALESCE(is_readable, 1) = 1 AND prompt IS NULL",
                remedy="missing_prompt",
                quality_weight=1.4,
                feeds_actionable=True,
            ),
        )

        with pytest.raises(ValueError, match="which no recommendation emits"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_key_pointing_at_a_remedy_that_does_not_resolve_it_is_rejected(self):
        """The divergence trap in miniature: the count would be right while the
        card beside it described different rows."""
        vocabulary = ISSUE_VOCABULARY + (
            IssueSpec(
                key="missing_checkpoint",
                sql="COALESCE(is_readable, 1) = 1 AND checkpoint_normalized IS NULL",
                remedy="sd_missing_checkpoint",
                quality_weight=0.8,
                feeds_actionable=True,
            ),
        )

        with pytest.raises(ValueError, match="does not resolve it"):
            _validate_issue_vocabulary(vocabulary, ISSUE_REMEDIES)

    def test_a_remedy_naming_no_action_is_rejected(self):
        remedies = ISSUE_REMEDIES + (
            IssueRemedy(kind="untagged_again", keys=("untagged",), severity="info", action="  "),
        )

        with pytest.raises(ValueError, match="names no action"):
            _validate_issue_vocabulary(ISSUE_VOCABULARY, remedies)

    def test_a_remedy_resolving_an_undeclared_key_is_rejected(self):
        remedies = ISSUE_REMEDIES + (
            IssueRemedy(
                kind="tidy_up",
                keys=("a_key_nobody_declared",),
                severity="info",
                action="do something",
            ),
        )

        with pytest.raises(ValueError, match="undeclared keys"):
            _validate_issue_vocabulary(ISSUE_VOCABULARY, remedies)

    def test_a_duplicate_key_is_rejected(self):
        with pytest.raises(ValueError, match="duplicate keys"):
            _validate_issue_vocabulary(
                ISSUE_VOCABULARY + (ISSUE_VOCABULARY[0],), ISSUE_REMEDIES
            )

    def test_a_duplicate_remedy_kind_is_rejected(self):
        with pytest.raises(ValueError, match="duplicate kinds"):
            _validate_issue_vocabulary(
                ISSUE_VOCABULARY, ISSUE_REMEDIES + (ISSUE_REMEDIES[0],)
            )


class TestAnEmptyLibrary:
    def test_the_vocabulary_still_publishes_every_key_at_zero(self, test_db):
        """A consumer that maps over the dict must not have to guess whether a
        key is absent or zero."""
        report = _report()

        assert set(report["issue_counts"]) == {spec.key for spec in ISSUE_VOCABULARY}
        assert set(report["issue_counts"].values()) == {0}
        assert report["recommendations"] == []
        assert report["summary"]["quality_score"] == 100.0
