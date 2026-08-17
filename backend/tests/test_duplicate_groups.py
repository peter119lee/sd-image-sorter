"""Tests for the duplicate-group scan service (v3.5.0 Tier 1 cleanup workflow)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from services import duplicate_group_service as dgs
from services.bulk_job_service import (
    JOB_KIND_DUPLICATE_SCAN,
    BulkJobService,
)
from similarity import embedding_to_bytes


_THREAD_TIMEOUT_SECONDS = 3.0

# A previously persisted result that load_result() still accepts. Built from
# the live constant so "the last good result survived" stays the assertion
# even when the payload shape is versioned forward.
_PREVIOUS_RESULT = {
    "version": dgs._RESULT_VERSION,
    "groups": [],
    "marker": "previous",
}
_PREVIOUS_RESULT_JSON = json.dumps(_PREVIOUS_RESULT, separators=(",", ":"))


class FakeHandle:
    def __init__(self):
        self.cancel_flag = False
        self.progress = []
        self.result = None

    @property
    def cancelled(self):
        return self.cancel_flag

    def set_progress(self, *, processed=None, total=None, message=None):
        self.progress.append((processed, message))

    def set_result(self, result):
        self.result = result

    def commit_result(
        self,
        *,
        publish_callback,
        result,
        processed,
        total,
        message,
    ):
        if self.cancelled:
            return False
        publish_callback()
        self.progress.append((processed, message))
        self.result = dict(result)
        return True


def _release_active_scan_slot() -> None:
    job_id = dgs.get_active_job_id()
    if job_id is not None:
        assert dgs.release_active_job_id(job_id) is True


def _run_real_scan(threshold: float) -> dict[str, object]:
    service = BulkJobService()
    job_id = service.create_job(
        JOB_KIND_DUPLICATE_SCAN,
        total=100,
        message="Queued",
    )
    service.run_job(
        job_id,
        lambda handle: dgs.run_duplicate_scan(handle, threshold=threshold),
    )
    job = service.get_job(job_id)
    assert job is not None
    return job


def _join_thread(thread: threading.Thread) -> None:
    thread.join(timeout=_THREAD_TIMEOUT_SECONDS)
    assert thread.is_alive() is False


@pytest.fixture
def dup_env(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(dgs, "_state_path", lambda: tmp_path / "duplicate-groups.json")
    _release_active_scan_slot()
    yield test_db
    _release_active_scan_slot()


def _insert_image(conn, image_id, filename, vec, *, rating=0, aesthetic=None,
                  width=512, height=512, size=1000):
    conn.execute(
        """
        INSERT INTO images (id, path, filename, width, height, file_size,
                            aesthetic_score, user_rating, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (image_id, f"C:/t/{filename}", filename, width, height, size,
         aesthetic, rating, embedding_to_bytes(np.asarray(vec, dtype=np.float32))),
    )


def _seed_library(db):
    """3 near-identical reds + 2 unrelated singletons."""
    base = np.zeros(8, dtype=np.float32)
    base[0] = 1.0
    near1 = base + np.array([0, 0.01, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    near2 = base + np.array([0, 0, 0.012, 0, 0, 0, 0, 0], dtype=np.float32)
    other1 = np.zeros(8, dtype=np.float32); other1[3] = 1.0
    other2 = np.zeros(8, dtype=np.float32); other2[5] = 1.0

    conn = db.get_connection()
    try:
        # id 1: low-res but 5 stars; id 2: high aesthetic; id 3: big file
        _insert_image(conn, 1, "a.png", base, rating=5, aesthetic=5.0, width=256, height=256, size=100)
        _insert_image(conn, 2, "b.png", near1, rating=0, aesthetic=9.0, width=1024, height=1024, size=500)
        _insert_image(conn, 3, "c.png", near2, rating=0, aesthetic=6.0, width=2048, height=2048, size=900)
        _insert_image(conn, 4, "d.png", other1)
        _insert_image(conn, 5, "e.png", other2)
        conn.commit()
    finally:
        conn.close()


def _angle_vector(degrees: float) -> np.ndarray:
    radians = np.deg2rad(degrees)
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = np.cos(radians)
    vector[1] = np.sin(radians)
    return vector


def _seed_transitive_chain(db) -> None:
    conn = db.get_connection()
    try:
        _insert_image(
            conn,
            30,
            "chain-a.png",
            _angle_vector(0.0),
            rating=5,
            size=100,
        )
        _insert_image(
            conn,
            31,
            "chain-b.png",
            _angle_vector(17.0),
            rating=0,
            size=200,
        )
        _insert_image(
            conn,
            32,
            "chain-c.png",
            _angle_vector(34.0),
            rating=0,
            size=300,
        )
        conn.commit()
    finally:
        conn.close()


def _assert_transitive_chain_is_safe(data) -> None:
    assert data is not None
    assert data["summary"]["group_count"] == 1
    assert data["summary"]["redundant_count"] == 1
    assert data["summary"]["reclaimable_bytes"] == 200

    group = data["groups"][0]
    assert [member["id"] for member in group["members"]] == [30, 31]
    assert group["members"][0]["suggested_keep"] is True
    assert group["members"][1]["suggested_keep"] is False
    assert group["similarity"] == pytest.approx(
        float(np.cos(np.deg2rad(17.0))), abs=1e-4
    )
    assert all(
        member["id"] != 32
        for candidate_group in data["groups"]
        for member in candidate_group["members"]
        if not member["suggested_keep"]
    )


def _disjoint_pair_graph(
    node_count: int,
) -> tuple[
    list[int],
    dict[int, dict[int, float]],
    dict[int, dict[str, int | float]],
]:
    ids = list(range(node_count))
    metadata = {
        index: {
            "id": index,
            "user_rating": 0,
            "aesthetic_score": 0.0,
            "width": 512,
            "height": 512,
            "file_size": node_count - index,
        }
        for index in ids
    }
    neighbors = {}
    for index in range(0, node_count, 2):
        neighbors[index] = {index + 1: 0.99}
        neighbors[index + 1] = {index: 0.99}
    return ids, neighbors, metadata


def test_partition_ranks_large_disjoint_pairs_once(monkeypatch):
    node_count = 1000
    ids, neighbors, metadata = _disjoint_pair_graph(node_count)

    original_rank_key = dgs._rank_key
    rank_calls = 0

    def counted_rank_key(member):
        nonlocal rank_calls
        rank_calls += 1
        return original_rank_key(member)

    monkeypatch.setattr(dgs, "_rank_key", counted_rank_key)

    groups = dgs._partition_direct_neighbor_groups(
        ids,
        neighbors,
        metadata,
        lambda: False,
    )

    assert groups is not None
    assert len(groups) == node_count // 2
    assert rank_calls == node_count


def test_partition_stops_when_cancellation_is_requested():
    node_count = 1024
    ids, neighbors, metadata = _disjoint_pair_graph(node_count)

    cancellation_checks = 0

    def is_cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    groups = dgs._partition_direct_neighbor_groups(
        ids,
        neighbors,
        metadata,
        is_cancelled,
    )

    assert groups is None
    assert cancellation_checks == 2


def test_cancel_during_grouping_preserves_last_persisted_result(
    dup_env, monkeypatch
):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_library(dup_env)
    state_path = dgs._state_path()
    state_path.write_text(_PREVIOUS_RESULT_JSON, encoding="utf-8")
    handle = FakeHandle()

    def cancel_on_clustering(
        *, processed: int, total: int, message: str
    ) -> None:
        handle.progress.append((processed, message))
        if message == "Clustering groups":
            handle.cancel_flag = True

    handle.set_progress = cancel_on_clustering

    dgs.run_duplicate_scan(handle, threshold=0.95)

    assert dgs.load_result() == _PREVIOUS_RESULT
    assert handle.result is None


def test_scan_builds_one_group_with_rating_first_keeper(dup_env, monkeypatch):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_library(dup_env)

    job = _run_real_scan(0.95)

    data = dgs.load_result()
    assert job["status"] == "done"
    assert job["processed"] == 100
    assert job["message"] == "Done"
    assert data is not None
    assert data["version"] == dgs._RESULT_VERSION
    assert data["summary"]["group_count"] == 1
    assert data["summary"]["redundant_count"] == 2
    group = data["groups"][0]
    member_ids = [m["id"] for m in group["members"]]
    assert sorted(member_ids) == [1, 2, 3]
    # user_rating outranks aesthetic and resolution: id 1 (5 stars) keeps.
    assert group["members"][0]["id"] == 1
    assert group["members"][0]["suggested_keep"] is True
    assert all(m["suggested_keep"] is False for m in group["members"][1:])
    # reclaimable = losers' file sizes (500 + 900)
    assert data["summary"]["reclaimable_bytes"] == 1400
    assert job["result"]["summary"]["group_count"] == 1


def test_scan_never_suggests_a_transitive_only_duplicate_for_deletion(
    dup_env, monkeypatch
):
    """Every suggested loser must directly meet the threshold against its keeper."""
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_transitive_chain(dup_env)

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    _assert_transitive_chain_is_safe(dgs.load_result())


def test_scan_splits_a_transitive_path_into_direct_keeper_groups(
    dup_env, monkeypatch
):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    conn = dup_env.get_connection()
    try:
        for image_id, degrees, rating, size in (
            (40, 0.0, 5, 100),
            (41, 17.0, 0, 200),
            (42, 34.0, 4, 300),
            (43, 51.0, 0, 400),
        ):
            _insert_image(
                conn,
                image_id,
                f"path-{image_id}.png",
                _angle_vector(degrees),
                rating=rating,
                size=size,
            )
        conn.commit()
    finally:
        conn.close()

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)

    data = dgs.load_result()
    assert data is not None
    assert [
        [member["id"] for member in group["members"]]
        for group in data["groups"]
    ] == [[40, 41], [42, 43]]
    assert data["summary"]["redundant_count"] == 2
    assert data["summary"]["reclaimable_bytes"] == 600


def test_aesthetic_breaks_ties_when_no_ratings(dup_env, monkeypatch):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    base = np.zeros(8, dtype=np.float32); base[0] = 1.0
    near = base + np.array([0, 0.01, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    conn = dup_env.get_connection()
    try:
        _insert_image(conn, 10, "x.png", base, aesthetic=4.0)
        _insert_image(conn, 11, "y.png", near, aesthetic=8.5)
        conn.commit()
    finally:
        conn.close()

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    group = dgs.load_result()["groups"][0]
    assert group["members"][0]["id"] == 11


def test_unrelated_images_form_no_groups(dup_env, monkeypatch):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    conn = dup_env.get_connection()
    try:
        v1 = np.zeros(8, dtype=np.float32); v1[0] = 1.0
        v2 = np.zeros(8, dtype=np.float32); v2[4] = 1.0
        _insert_image(conn, 20, "p.png", v1)
        _insert_image(conn, 21, "q.png", v2)
        conn.commit()
    finally:
        conn.close()

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    data = dgs.load_result()
    assert data["summary"]["group_count"] == 0
    assert data["groups"] == []


def test_scan_with_too_few_embeddings_persists_empty_result(dup_env):
    job = _run_real_scan(0.95)
    data = dgs.load_result()
    assert job["status"] == "done"
    assert job["processed"] == 100
    assert job["message"] == "Done"
    assert job["result"]["summary"]["embedded_count"] == 0
    assert data["version"] == dgs._RESULT_VERSION
    assert data["summary"]["embedded_count"] == 0
    assert data["summary"]["group_count"] == 0


def test_legacy_v1_group_state_requires_rescan(dup_env):
    state_path = dgs._state_path()
    state_path.write_text('{"version":1,"groups":[]}', encoding="utf-8")

    assert dgs.load_result() is None
    page = dgs.get_groups_page()
    assert page["available"] is False
    assert page["groups"] == []


def test_scan_surfaces_persistence_failure_instead_of_reporting_success(
    dup_env, tmp_path, monkeypatch
):
    blocking_parent = tmp_path / "not-a-directory"
    blocking_parent.write_text("block state directory creation", encoding="utf-8")
    monkeypatch.setattr(
        dgs,
        "_state_path",
        lambda: blocking_parent / "duplicate-groups.json",
    )
    handle = FakeHandle()

    with pytest.raises(dgs.DuplicateGroupPersistenceError, match="duplicate groups"):
        dgs.run_duplicate_scan(handle, threshold=0.95)

    assert handle.result is None


def test_cancel_before_result_commit_preserves_old_file_and_cleans_temp(
    dup_env, monkeypatch
):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_library(dup_env)
    state_path = dgs._state_path()
    state_path.write_text(_PREVIOUS_RESULT_JSON, encoding="utf-8")
    temp_path = state_path.with_suffix(".tmp")
    temp_written = threading.Event()
    allow_commit = threading.Event()
    original_write_text = Path.write_text

    def pause_after_temp_write(path: Path, data: str, *, encoding: str) -> int:
        written = original_write_text(path, data, encoding=encoding)
        if path == temp_path:
            temp_written.set()
            assert allow_commit.wait(timeout=_THREAD_TIMEOUT_SECONDS) is True
        return written

    monkeypatch.setattr(Path, "write_text", pause_after_temp_write)
    service = BulkJobService()
    job_id = service.create_job(
        JOB_KIND_DUPLICATE_SCAN,
        total=100,
        message="Queued",
    )
    worker = lambda handle: dgs.run_duplicate_scan(handle, threshold=0.95)
    worker_thread = threading.Thread(target=service.run_job, args=(job_id, worker))
    worker_thread.start()
    try:
        assert temp_written.wait(timeout=_THREAD_TIMEOUT_SECONDS) is True
        cancelled = service.cancel_job(job_id)
        assert cancelled is not None
        assert cancelled["status"] == "running"
    finally:
        allow_commit.set()
        _join_thread(worker_thread)

    job = service.get_job(job_id)
    assert job is not None
    assert job["status"] == "cancelled"
    assert job["result"] == {}
    assert dgs.load_result() == _PREVIOUS_RESULT
    assert temp_path.exists() is False


def test_replace_failure_preserves_old_file_marks_error_and_cleans_temp(
    dup_env, monkeypatch
):
    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_library(dup_env)
    state_path = dgs._state_path()
    state_path.write_text(_PREVIOUS_RESULT_JSON, encoding="utf-8")
    temp_path = state_path.with_suffix(".tmp")

    def fail_replace(source: Path, destination: Path) -> None:
        raise PermissionError(
            f"injected replace denial: source={source}, destination={destination}"
        )

    monkeypatch.setattr(dgs.os, "replace", fail_replace)

    job = _run_real_scan(0.95)

    assert job["status"] == "error"
    assert job["result"] == {}
    assert job["error_count"] == 1
    assert job["message"] == "Result publish failed"
    assert "injected replace denial" in job["error_samples"][0]
    assert dgs.load_result() == _PREVIOUS_RESULT
    assert temp_path.exists() is False


def test_temp_cleanup_failure_is_explicit(tmp_path):
    temp_directory = tmp_path / "orphan.tmp"
    temp_directory.mkdir()

    with pytest.raises(
        dgs.DuplicateGroupPersistenceError,
        match="Failed to clean duplicate-group temp file",
    ):
        dgs._cleanup_temp_file(temp_directory, "cancelled result commit")


def test_groups_page_pagination_and_missing_state(dup_env, monkeypatch):
    page = dgs.get_groups_page()
    assert page["available"] is False

    monkeypatch.setattr("similarity_ann.hnswlib_available", lambda: False)
    _seed_library(dup_env)
    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)

    page = dgs.get_groups_page(offset=0, limit=10)
    assert page["available"] is True
    assert page["total_groups"] == 1
    assert page["has_more"] is False
    assert len(page["groups"]) == 1

    empty_page = dgs.get_groups_page(offset=5, limit=10)
    assert empty_page["groups"] == []
    assert empty_page["has_more"] is False


@pytest.mark.skipif(
    not pytest.importorskip("similarity_ann").hnswlib_available(),
    reason="hnswlib not installed",
)
def test_ann_path_matches_exact_path(dup_env):
    _seed_library(dup_env)
    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    data = dgs.load_result()
    assert data["summary"]["group_count"] == 1
    assert sorted(m["id"] for m in data["groups"][0]["members"]) == [1, 2, 3]


@pytest.mark.skipif(
    not pytest.importorskip("similarity_ann").hnswlib_available(),
    reason="hnswlib not installed",
)
def test_ann_path_keeps_complete_dense_cluster_beyond_neighbor_window(dup_env):
    node_count = 64
    first_image_id = 1000
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1.0
    conn = dup_env.get_connection()
    try:
        for offset in range(node_count):
            image_id = first_image_id + offset
            _insert_image(
                conn,
                image_id,
                f"dense-{offset}.png",
                vector,
                rating=5 if offset == node_count - 1 else 0,
                size=1000,
            )
        conn.commit()
    finally:
        conn.close()

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)

    data = dgs.load_result()
    assert data is not None
    assert data["summary"]["group_count"] == 1
    assert data["summary"]["redundant_count"] == node_count - 1
    group = data["groups"][0]
    assert len(group["members"]) == node_count
    assert group["members"][0]["id"] == first_image_id + node_count - 1
    assert {member["id"] for member in group["members"]} == {
        first_image_id + offset for offset in range(node_count)
    }


def test_identical_candidates_verify_forced_fingerprint_collisions(monkeypatch):
    matrix = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 0.0],
            [-1.0, 0.0],
        ],
        dtype=np.float32,
    )
    monkeypatch.setattr(
        dgs,
        "_embedding_fingerprint",
        lambda vector: b"forced-collision",
    )

    candidates = dgs._find_identical_candidate_indices(
        matrix,
        dgs._embedding_fingerprint,
        lambda: False,
    )

    assert candidates == {0, 2}


def test_identical_candidate_scan_stops_after_cancellation():
    node_count = dgs._GROUP_CANCEL_CHECK_INTERVAL + 1
    matrix = np.arange(node_count * 2, dtype=np.float32).reshape(node_count, 2)
    cancellation_checks = 0
    fingerprint_calls = 0

    def is_cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    def fingerprint(vector: np.ndarray) -> bytes:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return vector.tobytes()

    candidates = dgs._find_identical_candidate_indices(
        matrix,
        fingerprint,
        is_cancelled,
    )

    assert candidates is None
    assert cancellation_checks == 2
    assert fingerprint_calls == dgs._GROUP_CANCEL_CHECK_INTERVAL


def test_exact_candidate_rescoring_stops_after_cancellation(monkeypatch):
    matrix = np.zeros((8, 512), dtype=np.float32)
    matrix[:, 0] = 1.0
    cancellation_checks = 0

    def is_cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    monkeypatch.setattr(dgs, "_MATMUL_CHUNK", 2)
    matches = dgs._find_exact_direct_matches(
        0,
        matrix,
        list(range(matrix.shape[0])),
        set(range(matrix.shape[0])),
        0.95,
        is_cancelled,
    )

    assert matches is None
    assert cancellation_checks == 2


@pytest.mark.skipif(
    not pytest.importorskip("similarity_ann").hnswlib_available(),
    reason="hnswlib not installed",
)
def test_ann_path_never_suggests_transitive_only_duplicate_for_deletion(dup_env):
    _seed_transitive_chain(dup_env)

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)

    _assert_transitive_chain_is_safe(dgs.load_result())


# ---------------------------------------------------------------------------
# Coverage: "0 duplicates" must not be readable as a whole-library verdict
# ---------------------------------------------------------------------------

def _insert_unembedded_image(conn, image_id: int, filename: str) -> None:
    conn.execute(
        """
        INSERT INTO images (id, path, filename, width, height, file_size, is_readable)
        VALUES (?, ?, ?, 512, 512, 1000, 1)
        """,
        (image_id, f"C:/t/{filename}", filename),
    )


def _seed_mostly_unembedded_library(db, *, embedded: int, unembedded: int) -> None:
    """Mirror the real library: a handful embedded, almost everything not."""
    conn = db.get_connection()
    try:
        for index in range(embedded):
            _insert_image(conn, 100 + index, f"emb-{index}.png", _angle_vector(index * 40.0))
        for index in range(unembedded):
            _insert_unembedded_image(conn, 200 + index, f"plain-{index}.png")
        conn.commit()
    finally:
        conn.close()


def test_scan_result_states_how_much_of_the_library_was_compared(dup_env):
    """2 of 10 images have embeddings and neither matches.

    Reporting only "0 groups" here reads as "your library has no
    duplicates" when 80% of it was never looked at.
    """
    _seed_mostly_unembedded_library(dup_env, embedded=2, unembedded=8)

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    summary = dgs.load_result()["summary"]

    assert summary["group_count"] == 0
    assert summary["embedded_count"] == 2
    # The denominator, so the numerator can be read at all.
    assert summary["total_images"] == 10
    assert summary["pending_count"] == 8
    assert summary["coverage"] == 20.0
    # Same partial-result vocabulary the gallery count endpoint already uses.
    assert summary["exact"] is False


def test_scan_result_reports_coverage_even_when_nothing_is_embedded(dup_env):
    """The worst case must be the loudest, not the quietest.

    With fewer than two embeddings the scan short-circuits to an empty
    result — the exact path where "0 duplicates found" is least earned.
    """
    _seed_mostly_unembedded_library(dup_env, embedded=0, unembedded=6)

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    summary = dgs.load_result()["summary"]

    assert summary["group_count"] == 0
    assert summary["embedded_count"] == 0
    assert summary["total_images"] == 6
    assert summary["pending_count"] == 6
    assert summary["coverage"] == 0
    assert summary["exact"] is False


def test_fully_embedded_library_reports_an_exact_verdict(dup_env):
    """A complete scan must not be hedged."""
    _seed_mostly_unembedded_library(dup_env, embedded=4, unembedded=0)

    dgs.run_duplicate_scan(FakeHandle(), threshold=0.95)
    summary = dgs.load_result()["summary"]

    assert summary["total_images"] == 4
    assert summary["embedded_count"] == 4
    assert summary["pending_count"] == 0
    assert summary["coverage"] == 100.0
    assert summary["exact"] is True
