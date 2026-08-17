"""Characterization pins for ``artist_identifier`` (TIER-2 step 0).

These pins lock the split-critical contracts of ``backend/artist_identifier.py``
before it is decomposed. They deliberately avoid loading any real model or
touching the network (machine-state isolation): every heavy seam is stubbed and
the safe, stateless helpers are exercised directly.

Focus areas (per the decomposition campaign):
  * reload contract       — ``ARTIST_USE_GPU`` is a FACADE global read by both
                            ``ArtistIdentifier.__init__`` and
                            ``get_artist_identifier``; the module singletons that
                            ``importlib.reload`` re-executes.
  * singleton lifecycle   — ``get_artist_identifier`` rebuild-vs-reuse rules.
  * ``__file__`` paths    — ``_project_root`` / ``_resolve_lsnet_runtime_path``
                            resolve against the FACADE ``__file__`` (module must
                            stay one level under ``backend/``).
  * download safety caps  — zip entry count / uncompressed bytes / traversal /
                            URL scheme / pinned SHA-256 digests.
  * error paths           — placeholder mode, honest-refusal, threshold gating,
                            CSV validation.

No existing file is modified by this task.
"""

from __future__ import annotations

import importlib
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import artist_identifier as ai


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def _tiny_png(path: Path) -> str:
    Image.new("RGB", (8, 8), color="red").save(path)
    return str(path)


# --------------------------------------------------------------------------- #
# 1. Module constants + safety-cap values (the download defenses)
# --------------------------------------------------------------------------- #
class TestModuleConstantsAndCaps:
    def test_default_threshold_value(self):
        assert ai.ARTIST_THRESHOLD_DEFAULT == 0.03

    def test_zip_entry_cap_value(self):
        assert ai._MAX_ARTIST_RUNTIME_ZIP_ENTRIES == 1024

    def test_zip_uncompressed_byte_cap_value(self):
        assert ai._MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES == 256 * 1024 * 1024

    def test_runtime_revision_is_pinned_40_hex_commit(self):
        rev = ai.ARTIST_LSNET_RUNTIME_REVISION
        assert len(rev) == 40
        assert all(c in "0123456789abcdef" for c in rev)
        assert rev != "main"

    def test_runtime_zip_url_embeds_pinned_commit_not_a_branch(self):
        assert ai.ARTIST_LSNET_RUNTIME_REVISION in ai.ARTIST_LSNET_RUNTIME_ZIP_URL
        assert "refs/heads/" not in ai.ARTIST_LSNET_RUNTIME_ZIP_URL

    def test_digest_table_keys_and_shape(self):
        table = ai._EXPECTED_ARTIST_FILE_SHA256
        assert set(table) == {
            "448-90.13/best_checkpoint.pth",
            "best_checkpoint.pth",
            "class_mapping.csv",
        }
        # Every pin is a TUPLE of acceptable digests (never a bare string).
        assert all(isinstance(v, tuple) for v in table.values())

    def test_digest_checkpoint_identical_across_versioned_and_flat_layout(self):
        table = ai._EXPECTED_ARTIST_FILE_SHA256
        assert table["448-90.13/best_checkpoint.pth"] == table["best_checkpoint.pth"]

    def test_digest_class_mapping_pins_both_crlf_and_lf_variants(self):
        # HuggingFace serves CRLF, ModelScope LF: both digests must be accepted.
        assert len(ai._EXPECTED_ARTIST_FILE_SHA256["class_mapping.csv"]) >= 2


# --------------------------------------------------------------------------- #
# 2. Download safety caps — behavioral (the priority pins)
# --------------------------------------------------------------------------- #
class TestDownloadSafetyCaps:
    def _install_fake_urlretrieve(self, monkeypatch, source_zip: Path) -> None:
        import shutil

        def fake(url, destination, reporthook=None):
            # Mirrors urlretrieve's contract: the size ceiling is enforced from
            # the reporthook, so a stub that ignored it would silently bypass
            # the download cap these fixtures share with the extraction caps.
            payload_size = source_zip.stat().st_size
            if reporthook is not None:
                reporthook(0, payload_size, payload_size)
            shutil.copyfile(source_zip, destination)
            return str(destination), None

        monkeypatch.setattr(ai.urllib.request, "urlretrieve", fake)

    def test_zip_entry_count_cap_rejects_too_many_members(self, monkeypatch, tmp_path):
        # Fills the previously-uncovered entry-count guard (the byte cap and
        # traversal guards are already covered by test_artist_identifier_runtime).
        source_zip = tmp_path / "r.zip"
        _write_zip(source_zip, {"root/a.py": b"a", "root/b.py": b"b"})
        self._install_fake_urlretrieve(monkeypatch, source_zip)
        monkeypatch.setattr(ai, "_MAX_ARTIST_RUNTIME_ZIP_ENTRIES", 1)

        with pytest.raises(ValueError, match="too many entries"):
            ai._download_and_extract_github_zip("https://e/x.zip", tmp_path / "out")

    def test_zip_multiple_top_level_roots_rejected(self, monkeypatch, tmp_path):
        source_zip = tmp_path / "r.zip"
        _write_zip(
            source_zip, {"rootA/lsnet_model/__init__.py": b"x", "rootB/y.py": b"y"}
        )
        self._install_fake_urlretrieve(monkeypatch, source_zip)

        with pytest.raises(ValueError, match="exactly one runtime root"):
            ai._download_and_extract_github_zip("https://e/x.zip", tmp_path / "out")

    def test_assert_http_download_url_rejects_ftp_scheme(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
        with pytest.raises(ValueError, match="Refusing to download"):
            ai._assert_http_download_url("ftp://host/x")

    def test_assert_http_download_url_allows_http_and_https(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
        ai._assert_http_download_url("https://modelscope.cn/x")
        ai._assert_http_download_url("http://localhost:8000/x")

    def test_verify_digest_is_noop_for_unpinned_file(self, tmp_path):
        target = tmp_path / "unpinned.bin"
        target.write_bytes(b"whatever")
        ai._verify_artist_file_digest("unpinned.bin", target)  # must not raise

    def test_verify_digest_rejects_pinned_mismatch(self, tmp_path):
        target = tmp_path / "best_checkpoint.pth"
        target.write_bytes(b"tampered")
        with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
            ai._verify_artist_file_digest("best_checkpoint.pth", target)


# --------------------------------------------------------------------------- #
# 3. __file__-derived paths (the location trap) — pinned by resolved value
# --------------------------------------------------------------------------- #
class TestFileLocationSemantic:
    def test_module_sits_one_level_under_backend(self):
        # A submodule landing one directory deeper would break the parent.parent
        # math in _project_root / _resolve_lsnet_runtime_path.
        assert Path(ai.__file__).resolve().parent.name == "backend"

    def test_project_root_is_grandparent_of_module_file(self):
        expected = Path(ai.__file__).resolve().parent.parent
        assert ai._project_root() == expected
        assert (expected / "backend").is_dir()

    def test_resolve_lsnet_runtime_path_reads_facade_file(self, monkeypatch, tmp_path):
        # The resolver must derive project_root from the FACADE __file__ (which is
        # what the diagnostics parity test patches). Prove it by pointing __file__
        # at a fake backend/ and staging a legacy repo runtime under its parent.
        project_root = tmp_path / "proj"
        backend = project_root / "backend"
        backend.mkdir(parents=True)
        fake_file = backend / "artist_identifier.py"
        fake_file.write_text("# placeholder\n", encoding="utf-8")
        artist_root = project_root / "data" / "models" / "artist"
        artist_root.mkdir(parents=True)
        legacy = project_root / "models" / "artist" / "comfyui-lsnet"
        (legacy / "model").mkdir(parents=True)

        monkeypatch.setattr(ai, "__file__", str(fake_file))
        monkeypatch.setattr(ai, "get_artist_model_dir", lambda: str(artist_root))
        monkeypatch.setattr(ai, "ARTIST_LSNET_CODE_PATH", "")
        monkeypatch.delenv("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY", raising=False)

        resolved = ai._resolve_lsnet_runtime_path()
        assert resolved is not None
        assert "comfyui-lsnet" in resolved

    def test_resolve_lsnet_runtime_path_skips_legacy_when_disable_flag_set(
        self, monkeypatch, tmp_path
    ):
        project_root = tmp_path / "proj"
        backend = project_root / "backend"
        backend.mkdir(parents=True)
        fake_file = backend / "artist_identifier.py"
        fake_file.write_text("# placeholder\n", encoding="utf-8")
        artist_root = project_root / "data" / "models" / "artist"
        artist_root.mkdir(parents=True)
        legacy = project_root / "models" / "artist" / "comfyui-lsnet"
        (legacy / "model").mkdir(parents=True)

        monkeypatch.setattr(ai, "__file__", str(fake_file))
        monkeypatch.setattr(ai, "get_artist_model_dir", lambda: str(artist_root))
        monkeypatch.setattr(ai, "ARTIST_LSNET_CODE_PATH", "")
        monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY", "1")

        assert ai._resolve_lsnet_runtime_path() is None


# --------------------------------------------------------------------------- #
# 4. Reload contract — ARTIST_USE_GPU is a facade global; module singletons
# --------------------------------------------------------------------------- #
class TestReloadContract:
    def test_init_reads_facade_artist_use_gpu(self, monkeypatch):
        # importlib.reload(artist_identifier) re-runs `from config import
        # ARTIST_USE_GPU`; __init__ must read that FACADE global (patch proves it).
        monkeypatch.setattr(ai, "ARTIST_USE_GPU", False)
        assert ai.ArtistIdentifier().use_gpu is False
        monkeypatch.setattr(ai, "ARTIST_USE_GPU", True)
        assert ai.ArtistIdentifier().use_gpu is True

    def test_get_artist_identifier_reads_facade_artist_use_gpu(self, monkeypatch):
        monkeypatch.setattr(ai, "_identifier", None)
        monkeypatch.setattr(ai, "ARTIST_USE_GPU", False)
        # use_gpu=None -> defaults from the facade global.
        assert ai.get_artist_identifier().use_gpu is False

    def test_explicit_use_gpu_overrides_facade_default(self, monkeypatch):
        monkeypatch.setattr(ai, "ARTIST_USE_GPU", True)
        assert ai.ArtistIdentifier(use_gpu=False).use_gpu is False

    def test_module_singletons_exist_for_reload_reset(self):
        # These names are what `importlib.reload` re-initializes; a split must keep
        # them resolvable (load() references _model_lock; get_artist_identifier
        # rebinds _identifier via `global`).
        import threading

        assert isinstance(ai._model_lock, type(threading.Lock()))
        assert hasattr(ai, "_identifier")
        # Declared-but-write-dead globals: present so a
        # `global _model, _processor, _model_source` in load() still binds.
        for name in ("_model", "_processor", "_model_source"):
            assert hasattr(ai, name)

    def test_reload_resets_identifier_singleton(self):
        ai.get_artist_identifier(model_source="huggingface")
        assert ai._identifier is not None
        importlib.reload(ai)
        assert ai._identifier is None


# --------------------------------------------------------------------------- #
# 5. Singleton lifecycle — rebuild vs reuse
# --------------------------------------------------------------------------- #
class TestSingletonLifecycle:
    def test_threshold_only_change_reuses_without_mutating_shared_state(self, monkeypatch):
        monkeypatch.setattr(ai, "_identifier", None)
        first = ai.get_artist_identifier(model_source="huggingface", threshold=0.03)
        same = ai.get_artist_identifier(model_source="huggingface", threshold=0.5)
        assert same is first
        assert same.threshold == 0.03

    def test_concurrent_same_config_constructs_one_identifier(self, monkeypatch):
        call_barrier = threading.Barrier(2)
        construction_barrier = threading.Barrier(2)
        counts = {"constructed": 0}
        counts_lock = threading.Lock()

        class ConcurrentIdentifier:
            def __init__(self, model_path, model_source, threshold, use_gpu):
                self.model_path = model_path
                self.model_source = model_source
                self.threshold = threshold
                self.use_gpu = use_gpu
                self._model = None
                with counts_lock:
                    counts["constructed"] += 1
                try:
                    construction_barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    assert counts["constructed"] == 1

            def set_threshold(self, threshold):
                self.threshold = threshold

        monkeypatch.setattr(ai, "_identifier", None)
        monkeypatch.setattr(ai, "ArtistIdentifier", ConcurrentIdentifier)

        def get_after_both_threads_start():
            call_barrier.wait(timeout=1)
            return ai.get_artist_identifier(
                model_path=None,
                model_source="huggingface",
                threshold=0.03,
                use_gpu=False,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(get_after_both_threads_start) for _ in range(2)]
            identifiers = [future.result(timeout=3) for future in futures]

        assert identifiers[0] is identifiers[1]
        assert counts == {"constructed": 1}

    def test_placeholder_singleton_is_rebuilt_for_retry(self, monkeypatch):
        failed = ai.ArtistIdentifier(
            model_source="huggingface",
            threshold=0.03,
            use_gpu=False,
        )
        failed._model = "placeholder"
        failed._load_error = "test load failure"
        monkeypatch.setattr(ai, "_identifier", failed)

        retry = ai.get_artist_identifier(
            model_path=None,
            model_source="huggingface",
            threshold=0.03,
            use_gpu=False,
        )

        assert retry is not failed
        assert retry._model is None

    def test_concurrent_config_switch_returns_each_selected_identifier(self, monkeypatch):
        class ConfiguredIdentifier:
            def __init__(self, model_path, model_source, threshold, use_gpu):
                self.model_path = model_path
                self.model_source = model_source
                self.threshold = threshold
                self.use_gpu = use_gpu
                self._model = None

            def set_threshold(self, threshold):
                self.threshold = threshold

        class PublishWindowLock:
            def __init__(self):
                self._lock = threading.Lock()
                self._state_lock = threading.Lock()
                self._exit_count = 0
                self.first_publish_exited = threading.Event()
                self.second_publish_exited = threading.Event()

            def __enter__(self):
                if not self._lock.acquire(timeout=3):
                    raise RuntimeError("Timed out acquiring artist identifier test lock")
                return self

            def __exit__(self, exc_type, exc, traceback):
                self._lock.release()
                with self._state_lock:
                    self._exit_count += 1
                    exit_count = self._exit_count
                if exit_count == 1:
                    self.first_publish_exited.set()
                    if not self.second_publish_exited.wait(timeout=3):
                        raise RuntimeError("Timed out waiting for second artist publish")
                elif exit_count == 2:
                    self.second_publish_exited.set()
                return False

        publish_lock = PublishWindowLock()
        monkeypatch.setattr(ai, "_identifier", None)
        monkeypatch.setattr(ai, "_identifier_lock", publish_lock, raising=False)
        monkeypatch.setattr(ai, "ArtistIdentifier", ConfiguredIdentifier)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                ai.get_artist_identifier,
                None,
                "huggingface",
                0.03,
                False,
            )
            assert publish_lock.first_publish_exited.wait(timeout=1)
            second_future = executor.submit(
                ai.get_artist_identifier,
                None,
                "modelscope",
                0.03,
                False,
            )
            first = first_future.result(timeout=3)
            second = second_future.result(timeout=3)

        assert first.model_source == "huggingface"
        assert second.model_source == "modelscope"
        assert first is not second

    def test_model_source_change_rebuilds(self, monkeypatch):
        monkeypatch.setattr(ai, "_identifier", None)
        first = ai.get_artist_identifier(model_source="huggingface")
        other = ai.get_artist_identifier(model_source="modelscope")
        assert other is not first

    def test_model_path_change_rebuilds_and_normalizes_whitespace(self, monkeypatch):
        monkeypatch.setattr(ai, "_identifier", None)
        first = ai.get_artist_identifier()
        with_path = ai.get_artist_identifier(model_path="  /tmp/x.onnx  ")
        assert with_path is not first
        assert with_path.model_path == "/tmp/x.onnx"

    def test_use_gpu_change_rebuilds(self, monkeypatch):
        monkeypatch.setattr(ai, "_identifier", None)
        gpu = ai.get_artist_identifier(use_gpu=True)
        cpu = ai.get_artist_identifier(use_gpu=False)
        assert cpu is not gpu
        assert cpu.use_gpu is False


# --------------------------------------------------------------------------- #
# 6. identify() error / refusal / threshold paths (no real model)
# --------------------------------------------------------------------------- #
class TestIdentifyPaths:
    def test_placeholder_mode_returns_error_without_loading(self, tmp_path):
        img = _tiny_png(tmp_path / "a.png")
        ident = ai.ArtistIdentifier()
        ident._model = "placeholder"  # load() no-ops because _model is not None
        result = ident.identify(img)
        assert result["artist"] == "undefined"
        assert result["model_loaded"] is False
        assert "error" in result

    def test_placeholder_mode_surfaces_load_error_message(self, tmp_path):
        img = _tiny_png(tmp_path / "a.png")
        ident = ai.ArtistIdentifier()
        ident._model = "placeholder"
        ident._load_error = "boom detail"
        assert ident.identify(img)["error"] == "boom detail"

    def test_honest_refusal_when_no_class_mapping(self, tmp_path):
        img = _tiny_png(tmp_path / "a.png")
        ident = ai.ArtistIdentifier()
        ident._model = "onnx"
        ident._session = object()  # routes identify() through _run_onnx
        ident._has_class_mapping = False
        ident._run_onnx = lambda image, *_a, **_k: np.array([0.1, 0.7, 0.2], dtype=np.float32)

        result = ident.identify(img, top_k=2)
        # Raw class indices must NOT be passed off as artist names.
        assert result["artist"] == "undefined"
        assert result["top_predictions"][0]["artist"] == "class_1"
        assert result["confidence"] == pytest.approx(0.7)
        assert "error" in result

    def test_threshold_gating_above_and_below(self, tmp_path):
        img = _tiny_png(tmp_path / "a.png")
        ident = ai.ArtistIdentifier(artists_list=["a0", "a1", "a2"], threshold=0.5)
        ident._model = "onnx"
        ident._session = object()
        ident._run_onnx = lambda image, *_a, **_k: np.array([0.1, 0.7, 0.2], dtype=np.float32)

        above = ident.identify(img, top_k=3)
        assert above["artist"] == "a1"
        assert above["confidence"] == pytest.approx(0.7)

        ident.threshold = 0.9
        below = ident.identify(img, top_k=3)
        assert below["artist"] == "undefined"
        # Confidence is still surfaced even when it fails the threshold.
        assert below["confidence"] == pytest.approx(0.7)

    def test_load_early_returns_when_model_already_set(self):
        ident = ai.ArtistIdentifier()
        ident._model = "already"
        ident.load()  # must not dispatch to any _load_* path
        assert ident._model == "already"

    def test_set_threshold_and_get_artists_list_copy(self):
        ident = ai.ArtistIdentifier(artists_list=["a", "b"])
        ident.set_threshold(0.42)
        assert ident.threshold == 0.42
        returned = ident.get_artists_list()
        returned.append("c")
        assert ident.artists == ["a", "b"]  # returned list is a copy

    def test_is_available_returns_bool(self):
        assert isinstance(ai.ArtistIdentifier.is_available(), bool)


# --------------------------------------------------------------------------- #
# 7. Class-mapping CSV parsing + pure helpers
# --------------------------------------------------------------------------- #
class TestClassMappingCsv:
    def _write_csv(self, path: Path, body: str) -> str:
        path.write_text(body, encoding="utf-8", newline="")
        return str(path)

    def test_parses_sorts_and_strips_quotes(self, tmp_path):
        ident = ai.ArtistIdentifier()
        csv_path = self._write_csv(
            tmp_path / "m.csv",
            "class_id,class_name\n2,'beta'\n0,\"alpha\"\n1,gamma\n",
        )
        assert ident._load_class_mapping_csv(csv_path) == ["alpha", "gamma", "beta"]

    def test_blank_name_becomes_unknown_index(self, tmp_path):
        ident = ai.ArtistIdentifier()
        csv_path = self._write_csv(tmp_path / "m.csv", "class_id,class_name\n0,\n")
        assert ident._load_class_mapping_csv(csv_path) == ["unknown_0"]

    def test_missing_columns_raise(self, tmp_path):
        ident = ai.ArtistIdentifier()
        csv_path = self._write_csv(tmp_path / "m.csv", "id,name\n0,x\n")
        with pytest.raises(RuntimeError, match="class_id and class_name"):
            ident._load_class_mapping_csv(csv_path)

    def test_empty_mapping_raises(self, tmp_path):
        ident = ai.ArtistIdentifier()
        csv_path = self._write_csv(tmp_path / "m.csv", "class_id,class_name\n")
        with pytest.raises(RuntimeError, match="empty"):
            ident._load_class_mapping_csv(csv_path)


class TestPureHelpers:
    def test_is_kaloscope_model_id_case_insensitive(self):
        assert ai._is_kaloscope_model_id("Heathcliff01/Kaloscope2.0") is True
        assert ai._is_kaloscope_model_id("heathcliff01/kaloscope2.0") is True

    def test_is_kaloscope_model_id_rejects_none_and_others(self):
        assert ai._is_kaloscope_model_id(None) is False
        assert ai._is_kaloscope_model_id("some/other-model") is False

    def test_normalize_state_dict_keys_strips_module_prefix(self):
        out = ai._normalize_state_dict_keys({"module.head.weight": 1, "body": 2})
        assert out == {"head.weight": 1, "body": 2}


# --------------------------------------------------------------------------- #
# 8. URL helpers + file materialization (stateless I/O helpers)
# --------------------------------------------------------------------------- #
class TestUrlHelpers:
    def test_artist_override_url_maps_env_by_filename(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL", "https://h/ckpt")
        monkeypatch.setenv("SD_IMAGE_SORTER_ARTIST_CLASS_MAPPING_URL", "https://h/map")
        assert (
            ai._artist_override_url(ai.ARTIST_KALOSCOPE_CHECKPOINT) == "https://h/ckpt"
        )
        assert (
            ai._artist_override_url(ai.ARTIST_KALOSCOPE_CLASS_MAPPING)
            == "https://h/map"
        )
        assert ai._artist_override_url("other.bin") is None

    def test_modelscope_resolve_url_default_and_base_override(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL", raising=False)
        default = ai._modelscope_resolve_url("owner/repo", "best_checkpoint.pth")
        assert default == (
            "https://modelscope.cn/models/owner/repo/resolve/master/best_checkpoint.pth"
        )
        monkeypatch.setenv(
            "SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL", "https://mirror/x/"
        )
        assert (
            ai._modelscope_resolve_url("owner/repo", "best_checkpoint.pth")
            == "https://mirror/x/best_checkpoint.pth"
        )


class TestFileMaterialization:
    def test_materialize_existing_file_links_or_copies(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dest = tmp_path / "sub" / "dst.bin"
        assert ai._materialize_existing_file(src, dest) is True
        assert dest.read_bytes() == b"data"

    def test_materialize_existing_file_missing_source_returns_false(self, tmp_path):
        assert ai._materialize_existing_file(tmp_path / "nope", tmp_path / "x") is False

    def test_copy_existing_tree_requires_marker(self, tmp_path):
        source = tmp_path / "tree"
        (source / "lsnet_model").mkdir(parents=True)
        (source / "lsnet_model" / "__init__.py").write_text("ok", encoding="utf-8")
        dest = tmp_path / "dest"
        assert ai._copy_existing_tree(source, dest, "lsnet_model") is True
        assert (dest / "lsnet_model" / "__init__.py").exists()

    def test_copy_existing_tree_without_marker_is_noop(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert (
            ai._copy_existing_tree(tmp_path / "empty", tmp_path / "d", "lsnet_model")
            is False
        )


# --------------------------------------------------------------------------- #
# 9. Kaloscope file location (tolerant detection) + asset-prep ordering
# --------------------------------------------------------------------------- #
class TestKaloscopeFileLocation:
    def test_locates_case_insensitive_kaloscope_dir(self, monkeypatch, tmp_path):
        root = tmp_path / "artist"
        checkpoint = root / "Kaloscope2.0" / "448-90.13" / "best_checkpoint.pth"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"c")
        (root / "Kaloscope2.0" / "class_mapping.csv").write_text(
            "class_id,class_name\n", encoding="utf-8"
        )
        monkeypatch.setattr(ai, "_get_artist_model_root", lambda: root)

        found = ai._locate_existing_kaloscope_files()
        assert found is not None
        assert found[0].endswith("best_checkpoint.pth")
        assert found[1].endswith("class_mapping.csv")

    def test_locates_by_recursive_basename_search(self, monkeypatch, tmp_path):
        root = tmp_path / "artist"
        checkpoint = root / "weird" / "deep" / "best_checkpoint.pth"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"c")
        (checkpoint.with_name("class_mapping.csv")).write_text(
            "class_id,class_name\n", encoding="utf-8"
        )
        monkeypatch.setattr(ai, "_get_artist_model_root", lambda: root)

        found = ai._locate_existing_kaloscope_files()
        assert found is not None
        assert found[0].endswith("best_checkpoint.pth")

    def test_returns_none_when_no_checkpoint(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ai, "_get_artist_model_root", lambda: tmp_path / "empty-artist"
        )
        assert ai._locate_existing_kaloscope_files() is None


class TestPrepareArtistAssetsOrdering:
    def test_huggingface_source(self, monkeypatch, tmp_path):
        runtime = tmp_path / "rt"
        runtime.mkdir()
        monkeypatch.setattr(ai, "_resolve_lsnet_runtime_path", lambda: str(runtime))
        monkeypatch.setattr(
            ai, "_ensure_kaloscope_hf_files", lambda: ("/ck.pth", "/map.csv")
        )

        result = ai.prepare_artist_assets("huggingface")
        assert result["source"] == "huggingface"
        assert result["runtime_path"] == str(runtime)
        assert result["checkpoint_path"] == "/ck.pth"
        assert result["class_mapping_path"] == "/map.csv"

    def test_auto_prefers_modelscope_when_mirror_and_repo_configured(
        self, monkeypatch, tmp_path
    ):
        import config

        runtime = tmp_path / "rt"
        runtime.mkdir()
        monkeypatch.setattr(ai, "_resolve_lsnet_runtime_path", lambda: str(runtime))
        monkeypatch.setattr(ai, "ARTIST_MODELSCOPE_MODEL_ID", "owner/k")
        monkeypatch.setattr(config, "get_download_mirror", lambda: "modelscope")
        monkeypatch.setattr(
            ai, "_ensure_kaloscope_modelscope_files", lambda: ("/ms.pth", "/ms.csv")
        )
        monkeypatch.setattr(
            ai,
            "_ensure_kaloscope_hf_files",
            lambda: pytest.fail(
                "HF must not be first when mirror=modelscope + repo set"
            ),
        )

        assert ai.prepare_artist_assets("auto")["source"] == "modelscope"

    def test_ensure_hf_files_short_circuits_on_existing(self, monkeypatch, tmp_path):
        root = tmp_path / "artist"
        canonical = root / "kaloscope2.0" / "448-90.13"
        canonical.mkdir(parents=True)
        (canonical / "best_checkpoint.pth").write_bytes(b"c")
        (root / "kaloscope2.0" / "class_mapping.csv").write_text(
            "class_id,class_name\n", encoding="utf-8"
        )
        monkeypatch.setattr(ai, "_get_artist_model_root", lambda: root)
        monkeypatch.setattr(
            ai,
            "_hf_download_with_fallback",
            lambda *a, **k: pytest.fail("must not download when files already present"),
        )

        checkpoint, mapping = ai._ensure_kaloscope_hf_files()
        assert Path(checkpoint).exists()
        assert Path(mapping).exists()


# --------------------------------------------------------------------------- #
# 10. Public API surface external readers bind to
# --------------------------------------------------------------------------- #
class TestPublicApiSurface:
    def test_public_names_are_module_attributes(self):
        # routers/artists.py, services/artist_service.py and services/model_service
        # import these by name — a split must keep them at artist_identifier.<name>.
        for name in (
            "get_artist_identifier",
            "prepare_artist_assets",
            "ArtistIdentifier",
            "ARTIST_THRESHOLD_DEFAULT",
        ):
            assert hasattr(ai, name)

    def test_get_artist_identifier_accepts_service_kwargs(self, monkeypatch):
        # ArtistService._identifier calls with exactly these keyword arguments.
        monkeypatch.setattr(ai, "_identifier", None)
        ident = ai.get_artist_identifier(
            model_path=None, model_source="huggingface", threshold=0.03, use_gpu=None
        )
        assert isinstance(ident, ai.ArtistIdentifier)

    def test_prepare_artist_assets_returns_expected_dict_shape(
        self, monkeypatch, tmp_path
    ):
        runtime = tmp_path / "rt"
        runtime.mkdir()
        monkeypatch.setattr(ai, "_resolve_lsnet_runtime_path", lambda: str(runtime))
        monkeypatch.setattr(
            ai, "_ensure_kaloscope_hf_files", lambda: ("/ck.pth", "/map.csv")
        )

        result = ai.prepare_artist_assets("huggingface")
        # model_service.prepare_model('artist') reads exactly these four keys.
        assert set(result) == {
            "runtime_path",
            "checkpoint_path",
            "class_mapping_path",
            "source",
        }
