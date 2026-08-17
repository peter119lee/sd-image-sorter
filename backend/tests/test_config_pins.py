"""Step-0 characterization pins for ``backend/config.py``.

These lock the *observable* behavior of the config namespace AS-IS so a future
split (or a decision to exempt the file) cannot silently change it.

Design constraints honored here:

* ``config`` is the single most origin-imported namespace in the backend and
  every module constant is evaluated at IMPORT time. Reloading the real
  ``config`` module in-process would rebind every consumer's import-time
  binding, so import-time *env* behavior is probed in an isolated subprocess
  (``_run_config_probe``) that imports a fresh ``config`` with a controlled
  environment -- never by reloading the live module.
* ``configure_runtime_temp_env`` / ``get_temp_dir`` / ``ensure_directories``
  mutate process-global state (``tempfile.tempdir`` and ``TMP``/``TEMP``/
  ``TMPDIR``). The ``_preserve_temp_globals`` fixture snapshots and restores it
  so these pins add zero suite pollution.
* ``_load_env_file`` writes straight into ``os.environ``; the
  ``_clean_env_pin_keys`` fixture guarantees the sentinel keys are popped.

Existing prior art: ``tests/test_config_env.py`` (env parser + thumbnail-cache
basics). These pins deepen, not duplicate, that coverage.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402


# ===========================================================================
# Fixtures for global-state safety
# ===========================================================================


@pytest.fixture
def _preserve_temp_globals():
    """Snapshot/restore the process-global temp state that
    ``configure_runtime_temp_env`` mutates, so temp-touching pins don't leak
    into the rest of the suite."""
    saved_tempdir = tempfile.tempdir
    saved_env = {k: os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP")}
    try:
        yield
    finally:
        tempfile.tempdir = saved_tempdir
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


_ENV_PIN_KEYS = ("SD_IMAGE_SORTER_PIN_A", "SD_IMAGE_SORTER_PIN_B", "SD_IMAGE_SORTER_PIN_C")


@pytest.fixture
def _clean_env_pin_keys():
    """Guarantee the sentinel env keys ``_load_env_file`` may set are removed
    on teardown (the function writes ``os.environ`` directly, bypassing
    monkeypatch's restore)."""
    for key in _ENV_PIN_KEYS:
        os.environ.pop(key, None)
    try:
        yield _ENV_PIN_KEYS
    finally:
        for key in _ENV_PIN_KEYS:
            os.environ.pop(key, None)


# ===========================================================================
# Subprocess helper: import-time env behavior without polluting live config
# ===========================================================================


def _run_config_probe(script: str, env_overrides: dict) -> subprocess.CompletedProcess:
    """Import a *fresh* ``config`` in a child process with a controlled env.

    The base env strips every ``SD_IMAGE_SORTER_*`` key and ``HF_ENDPOINT`` so
    the probe is deterministic regardless of the runner's environment (and no
    committed ``.env`` exists to re-inject them). ``cwd`` is the backend dir so
    ``import config`` / ``import app_info`` resolve.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SD_IMAGE_SORTER_") and key != "HF_ENDPOINT"
    }
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
    )


# ===========================================================================
# Group A -- env parser contracts (read_int_env / read_float_env / read_bool_env)
# ===========================================================================


class TestEnvParsers:
    def test_read_int_env_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_PIN_A", raising=False)
        assert config.read_int_env("SD_IMAGE_SORTER_PIN_A", 7) == 7

    def test_read_int_env_parses_value(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "123")
        assert config.read_int_env("SD_IMAGE_SORTER_PIN_A", 7) == 123

    def test_read_int_env_accepts_negative_and_zero(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "-4")
        assert config.read_int_env("SD_IMAGE_SORTER_PIN_A", 7) == -4
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "0")
        assert config.read_int_env("SD_IMAGE_SORTER_PIN_A", 7) == 0

    def test_read_int_env_invalid_raises_with_name(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "1.5")
        with pytest.raises(ValueError, match="Invalid SD_IMAGE_SORTER_PIN_A: expected integer"):
            config.read_int_env("SD_IMAGE_SORTER_PIN_A", 7)

    def test_read_float_env_default_and_parse(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_PIN_A", raising=False)
        assert config.read_float_env("SD_IMAGE_SORTER_PIN_A", 0.5) == 0.5
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "2")
        assert config.read_float_env("SD_IMAGE_SORTER_PIN_A", 0.5) == 2.0

    def test_read_float_env_invalid_raises_with_name(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "abc")
        with pytest.raises(ValueError, match="Invalid SD_IMAGE_SORTER_PIN_A: expected number"):
            config.read_float_env("SD_IMAGE_SORTER_PIN_A", 0.5)

    def test_read_bool_env_true_tokens_case_and_whitespace_insensitive(self, monkeypatch):
        for token in ("1", "true", "TRUE", "Yes", "on", "  true  "):
            monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", token)
            assert config.read_bool_env("SD_IMAGE_SORTER_PIN_A", False) is True

    def test_read_bool_env_false_tokens(self, monkeypatch):
        for token in ("0", "false", "NO", "off", " Off "):
            monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", token)
            assert config.read_bool_env("SD_IMAGE_SORTER_PIN_A", True) is False

    def test_read_bool_env_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_PIN_A", raising=False)
        assert config.read_bool_env("SD_IMAGE_SORTER_PIN_A", True) is True
        assert config.read_bool_env("SD_IMAGE_SORTER_PIN_A", False) is False

    def test_read_bool_env_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "maybe")
        with pytest.raises(ValueError, match="Invalid SD_IMAGE_SORTER_PIN_A: expected boolean"):
            config.read_bool_env("SD_IMAGE_SORTER_PIN_A", False)

    def test_read_bool_env_empty_string_raises(self, monkeypatch):
        """Sharp edge pinned AS-IS: a set-but-empty bool env var is NOT the
        default -- it normalizes to "" which is in neither token set and
        raises. (Report: dormant sharp-edge, not fixed here.)"""
        monkeypatch.setenv("SD_IMAGE_SORTER_PIN_A", "")
        with pytest.raises(ValueError, match="Invalid SD_IMAGE_SORTER_PIN_A: expected boolean"):
            config.read_bool_env("SD_IMAGE_SORTER_PIN_A", True)


# ===========================================================================
# Group B -- .env line parser (_parse_env_line)
# ===========================================================================


class TestParseEnvLine:
    def test_comment_blank_and_no_equals_return_none(self):
        assert config._parse_env_line("# a comment") is None
        assert config._parse_env_line("   ") is None
        assert config._parse_env_line("NOEQUALS") is None

    def test_export_prefix_stripped_and_quotes_removed(self):
        assert config._parse_env_line("export FOO=bar") == ("FOO", "bar")
        assert config._parse_env_line('FOO="bar"') == ("FOO", "bar")
        assert config._parse_env_line("FOO='bar'") == ("FOO", "bar")

    def test_empty_key_is_none_but_empty_value_is_kept(self):
        assert config._parse_env_line("=novalue") is None
        assert config._parse_env_line("FOO=") == ("FOO", "")

    def test_value_with_equals_splits_only_once(self):
        assert config._parse_env_line("URL=http://x/y?a=b") == ("URL", "http://x/y?a=b")

    def test_single_quote_char_value_is_not_stripped(self):
        # A lone quote char (len 1) fails the len>=2 guard, so it stays verbatim.
        assert config._parse_env_line('FOO="') == ("FOO", '"')


# ===========================================================================
# Group C -- .env file loader (_load_env_file / _INITIAL_ENV_KEYS gate)
# ===========================================================================


class TestLoadEnvFile:
    def test_missing_path_is_noop(self, tmp_path):
        # Must not raise for a path that does not exist.
        config._load_env_file(tmp_path / "does-not-exist.env")

    def test_sets_new_key_not_in_initial_env(self, tmp_path, monkeypatch, _clean_env_pin_keys):
        monkeypatch.setattr(config, "_INITIAL_ENV_KEYS", frozenset())
        env_file = tmp_path / ".env"
        env_file.write_text("SD_IMAGE_SORTER_PIN_A=fromfile\n", encoding="utf-8")

        config._load_env_file(env_file)

        assert os.environ["SD_IMAGE_SORTER_PIN_A"] == "fromfile"

    def test_keys_in_initial_env_are_never_overridden(self, tmp_path, monkeypatch, _clean_env_pin_keys):
        # Guards real process env: a key present at process start is skipped.
        monkeypatch.setattr(config, "_INITIAL_ENV_KEYS", frozenset({"SD_IMAGE_SORTER_PIN_A"}))
        env_file = tmp_path / ".env"
        env_file.write_text("SD_IMAGE_SORTER_PIN_A=fromfile\n", encoding="utf-8")

        config._load_env_file(env_file)

        assert "SD_IMAGE_SORTER_PIN_A" not in os.environ

    def test_override_flag_controls_replacing_already_loaded_value(
        self, tmp_path, monkeypatch, _clean_env_pin_keys
    ):
        monkeypatch.setattr(config, "_INITIAL_ENV_KEYS", frozenset())
        os.environ["SD_IMAGE_SORTER_PIN_C"] = "old"
        env_file = tmp_path / ".env"
        env_file.write_text("SD_IMAGE_SORTER_PIN_C=new\n", encoding="utf-8")

        config._load_env_file(env_file, override_loaded_values=False)
        assert os.environ["SD_IMAGE_SORTER_PIN_C"] == "old"

        config._load_env_file(env_file, override_loaded_values=True)
        assert os.environ["SD_IMAGE_SORTER_PIN_C"] == "new"

    def test_bootstrap_skips_package_env_files_when_explicitly_disabled(
        self, tmp_path, monkeypatch, _clean_env_pin_keys
    ):
        backend_dir = tmp_path / "backend"
        package_root = tmp_path / "package"
        backend_dir.mkdir()
        package_root.mkdir()
        (backend_dir / ".env").write_text(
            "SD_IMAGE_SORTER_PIN_A=backend-file-sentinel\n",
            encoding="utf-8",
        )
        (package_root / ".env").write_text(
            "SD_IMAGE_SORTER_PIN_B=package-file-sentinel\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "_INITIAL_ENV_KEYS", frozenset())
        monkeypatch.setattr(config, "_get_backend_dir", lambda: backend_dir)
        monkeypatch.setattr(config, "_get_project_root", lambda: package_root)
        monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_ENV_FILES", "1")

        config._bootstrap_package_env()

        assert "SD_IMAGE_SORTER_PIN_A" not in os.environ
        assert "SD_IMAGE_SORTER_PIN_B" not in os.environ

    def test_bootstrap_rejects_invalid_env_file_disable_flag(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_ENV_FILES", "sometimes")

        with pytest.raises(
            ValueError,
            match='Invalid SD_IMAGE_SORTER_DISABLE_ENV_FILES: expected "0" or "1"',
        ):
            config._bootstrap_package_env()


# ===========================================================================
# Group D -- download mirror JSON IO
# ===========================================================================


class TestDownloadMirror:
    def test_defaults_to_auto_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DOWNLOAD_MIRROR_CONFIG_PATH", tmp_path / "download-mirror.json")
        assert config.get_download_mirror() == "auto"

    def test_save_then_get_roundtrips_each_valid_mirror(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config, "DOWNLOAD_MIRROR_CONFIG_PATH", tmp_path / "download-mirror.json")
        for mirror in config.VALID_MIRRORS:
            config.save_download_mirror(mirror)
            assert config.get_download_mirror() == mirror

    def test_unknown_value_in_file_falls_back_to_auto(self, tmp_path, monkeypatch):
        path = tmp_path / "download-mirror.json"
        path.write_text(json.dumps({"mirror": "bogus"}), encoding="utf-8")
        monkeypatch.setattr(config, "DOWNLOAD_MIRROR_CONFIG_PATH", path)
        assert config.get_download_mirror() == "auto"

    def test_corrupt_json_falls_back_to_auto_without_raising(self, tmp_path, monkeypatch):
        path = tmp_path / "download-mirror.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(config, "DOWNLOAD_MIRROR_CONFIG_PATH", path)
        assert config.get_download_mirror() == "auto"

    def test_save_coerces_invalid_mirror_to_auto(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        path = tmp_path / "download-mirror.json"
        monkeypatch.setattr(config, "DOWNLOAD_MIRROR_CONFIG_PATH", path)
        config.save_download_mirror("not-a-mirror")
        assert json.loads(path.read_text(encoding="utf-8"))["mirror"] == "auto"


# ===========================================================================
# Group E -- app settings + thumbnail cache limit
# ===========================================================================


class TestThumbnailCacheSetting:
    def test_normalize_edges(self):
        default = config.DEFAULT_THUMBNAIL_CACHE_MAX_MB
        assert config._normalize_thumbnail_cache_max_mb(None) == default
        assert config._normalize_thumbnail_cache_max_mb("garbage") == default
        assert config._normalize_thumbnail_cache_max_mb(-1) == default
        assert config._normalize_thumbnail_cache_max_mb("256") == 256
        assert (
            config._normalize_thumbnail_cache_max_mb(config.MAX_THUMBNAIL_CACHE_MAX_MB + 10)
            == config.MAX_THUMBNAIL_CACHE_MAX_MB
        )

    def test_env_negative_raises(self, monkeypatch):
        monkeypatch.setenv("SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB", "-5")
        with pytest.raises(ValueError, match="expected integer >= 0"):
            config.get_thumbnail_cache_max_mb()

    def test_env_clamps_to_max(self, monkeypatch):
        monkeypatch.setenv(
            "SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB",
            str(config.MAX_THUMBNAIL_CACHE_MAX_MB + 999),
        )
        assert config.get_thumbnail_cache_max_mb() == config.MAX_THUMBNAIL_CACHE_MAX_MB

    def test_corrupt_app_settings_uses_default_limit(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB", raising=False)
        settings = tmp_path / "app-settings.json"
        settings.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(config, "APP_SETTINGS_CONFIG_PATH", settings)
        assert config.get_thumbnail_cache_max_mb() == config.DEFAULT_THUMBNAIL_CACHE_MAX_MB

    def test_app_settings_write_is_sorted_and_readable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB", raising=False)
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        settings = tmp_path / "app-settings.json"
        monkeypatch.setattr(config, "APP_SETTINGS_CONFIG_PATH", settings)

        saved = config.save_thumbnail_cache_max_mb(300)

        assert saved == 300
        assert json.loads(settings.read_text(encoding="utf-8")) == {"thumbnail_cache_max_mb": 300}


# ===========================================================================
# Group F -- directory getters + ensure_directories
# ===========================================================================

# Path-typed module globals (getters call ``.mkdir`` on these) and str-typed
# globals (getters wrap them in ``Path(...)``). ensure_directories touches all.
_PATH_DIR_GLOBALS = ("DATA_DIR", "CONFIG_DIR", "TEMP_DIR", "STATE_DIR", "UPDATE_DIR", "THUMBNAIL_DIR")
_STR_DIR_GLOBALS = (
    "WD14_MODEL_DIR",
    "YOLO_MODEL_DIR",
    "CLIP_MODEL_DIR",
    "ARTIST_MODEL_DIR",
    "SAM3_MODEL_DIR",
    "NUDENET_MODEL_DIR",
    "TORIIGATE_MODEL_DIR",
    "DEFAULT_CACHE_DIR",
    "FAVORITES_FOLDER_PATH",
)


class TestDirectoryGetters:
    def test_get_data_dir_creates_and_returns_str_idempotent(self, tmp_path, monkeypatch):
        target = tmp_path / "data"
        monkeypatch.setattr(config, "DATA_DIR", target)
        first = config.get_data_dir()
        assert first == str(target)
        assert target.is_dir()
        # Idempotent: a second call on an existing dir returns the same path.
        assert config.get_data_dir() == str(target)

    def test_get_wd14_model_dir_returns_existing_dir(self, tmp_path, monkeypatch):
        existing = tmp_path / "wd14"
        existing.mkdir()
        monkeypatch.setattr(config, "WD14_MODEL_DIR", str(existing))
        assert config.get_wd14_model_dir() == str(existing)

    def test_get_wd14_model_dir_creates_missing_dir(self, tmp_path, monkeypatch):
        missing = tmp_path / "wd14-new"
        monkeypatch.setattr(config, "WD14_MODEL_DIR", str(missing))
        assert config.get_wd14_model_dir() == str(missing)
        assert missing.is_dir()

    def test_configure_runtime_temp_env_sets_process_globals(
        self, tmp_path, monkeypatch, _preserve_temp_globals
    ):
        target = tmp_path / "tmp"
        monkeypatch.setattr(config, "TEMP_DIR", target)

        returned = config.configure_runtime_temp_env()

        assert returned == str(target)
        assert target.is_dir()
        assert tempfile.tempdir == str(target)
        assert os.environ["TMPDIR"] == str(target)
        assert os.environ["TEMP"] == str(target)
        assert os.environ["TMP"] == str(target)

    def test_ensure_directories_creates_full_set_idempotently(
        self, tmp_path, monkeypatch, _preserve_temp_globals
    ):
        for name in _PATH_DIR_GLOBALS:
            monkeypatch.setattr(config, name, tmp_path / name)
        for name in _STR_DIR_GLOBALS:
            monkeypatch.setattr(config, name, str(tmp_path / name))
        monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "db" / "images.db"))

        # Two calls: creation then idempotence (no raise on existing dirs).
        config.ensure_directories()
        config.ensure_directories()

        assert (tmp_path / "DATA_DIR").is_dir()
        assert (tmp_path / "CONFIG_DIR").is_dir()
        assert (tmp_path / "THUMBNAIL_DIR").is_dir()
        assert (tmp_path / "WD14_MODEL_DIR").is_dir()
        assert (tmp_path / "YOLO_MODEL_DIR").is_dir()
        assert (tmp_path / "FAVORITES_FOLDER_PATH").is_dir()
        assert (tmp_path / "db").is_dir()  # DATABASE_PATH parent


# ===========================================================================
# Group G -- validate_config
# ===========================================================================


class TestValidateConfig:
    def test_clean_config_returns_no_warnings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "nope.db"))
        monkeypatch.setattr(config, "FAVORITES_FOLDER_PATH", str(tmp_path / "nofav"))
        monkeypatch.setattr(config, "TAGGER_GENERAL_THRESHOLD", 0.35)
        monkeypatch.setattr(config, "TAGGER_CHARACTER_THRESHOLD", 0.85)
        monkeypatch.setattr(config, "CENSOR_CONFIDENCE_THRESHOLD", 0.6)
        monkeypatch.setattr(config, "SERVER_PORT", 8487)

        assert config.validate_config() == []

    def test_flags_out_of_range_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "nope.db"))
        monkeypatch.setattr(config, "FAVORITES_FOLDER_PATH", str(tmp_path / "nofav"))
        monkeypatch.setattr(config, "TAGGER_GENERAL_THRESHOLD", 1.5)

        warnings = config.validate_config()
        assert any("TAGGER_GENERAL_THRESHOLD" in w for w in warnings)

    def test_flags_out_of_range_port(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "nope.db"))
        monkeypatch.setattr(config, "FAVORITES_FOLDER_PATH", str(tmp_path / "nofav"))
        monkeypatch.setattr(config, "SERVER_PORT", 70000)

        warnings = config.validate_config()
        assert any("SERVER_PORT" in w for w in warnings)


# ===========================================================================
# Group H -- TAGGER_MODELS catalog: identity share + shape invariants
# ===========================================================================

# Keys every consumer relies on being present in every entry (indexed via
# ``[...]`` or ``.get`` across tagger.py, model_health.py, the tagging/ and
# smart_tag/ services, hardware_monitor.py, toriigate_tagger.py).
_UNIVERSAL_ENTRY_KEYS = (
    "repo_id",
    "model_file",
    "runtime_safety_tier",
    "default_threshold",
    "default_character_threshold",
    "default_max_tags_per_image",
)


class TestTaggerModelsCatalog:
    def test_catalog_value_shared_with_tagger_family(self):
        """In-process, every co-importer's catalog equals the live config
        catalog.

        NOTE (real finding, pinned deliberately): strict ``is`` identity is
        guaranteed only at a CLEAN import -- see
        ``TestImportTimeEnv.test_tagger_models_identity_on_clean_import``. An
        ``importlib.reload(config)`` elsewhere in the suite
        (``test_artist_gpu_toggle``, ``test_main_logging``) rebinds
        ``config.TAGGER_MODELS`` to a fresh-but-equal dict, after which
        co-importers that did ``from config import TAGGER_MODELS`` keep the
        pre-reload object. So in-process the robust invariant is EQUALITY. A
        split must preserve BOTH this value contract and the clean-import
        identity below; it must not make the reload divergence worse."""
        import tagger_tagtable
        import tagger_download

        assert tagger_tagtable.MODELS == config.TAGGER_MODELS
        assert tagger_download.MODELS == config.TAGGER_MODELS

    def test_from_import_yields_same_object(self):
        from config import TAGGER_MODELS as a
        from config import TAGGER_MODELS as b

        assert a is b is config.TAGGER_MODELS

    def test_expected_model_keys_and_default_present(self):
        assert set(config.TAGGER_MODELS.keys()) == {
            "wd-eva02-large-tagger-v3",
            "wd-swinv2-tagger-v3",
            "wd-convnext-tagger-v3",
            "wd-vit-tagger-v3",
            "wd-vit-large-tagger-v3",
            "camie-tagger-v2",
            "pixai-tagger-v0.9",
            "toriigate-0.5",
            "oppai-oracle-v1.1",
            "cl-tagger-v2",
        }
        assert config.DEFAULT_TAGGER_MODEL == "wd-swinv2-tagger-v3"
        assert config.DEFAULT_TAGGER_MODEL in config.TAGGER_MODELS

    def test_every_entry_has_the_universal_consumer_keys(self):
        for name, entry in config.TAGGER_MODELS.items():
            for key in _UNIVERSAL_ENTRY_KEYS:
                assert key in entry, f"{name} missing {key}"

    def test_default_model_entry_has_model_file_and_tags_file(self):
        # model_health.py indexes these two on the default model unconditionally.
        entry = config.TAGGER_MODELS[config.DEFAULT_TAGGER_MODEL]
        assert entry["model_file"] == "model.onnx"
        assert entry["tags_file"] == "selected_tags.csv"

    def test_default_model_revision_is_pinned_to_a_commit(self):
        revision = config.TAGGER_MODELS[config.DEFAULT_TAGGER_MODEL]["revision"]

        assert revision == "627aef95638667ddcaa3ac8ae625e88ea5b02f51"
        assert len(revision) == 40
        int(revision, 16)

    def test_load_bearing_output_quirks_pinned(self):
        # These specific values decode the correct ONNX output head; wrong
        # values silently produce garbage tags (see config.py comments).
        assert config.TAGGER_MODELS["camie-tagger-v2"]["output_index"] == 1
        assert config.TAGGER_MODELS["pixai-tagger-v0.9"]["output_index"] == 2
        assert config.TAGGER_MODELS["pixai-tagger-v0.9"]["output_activation"] == "identity"
        assert config.TAGGER_MODELS["toriigate-0.5"]["captioner_only"] is True

    def test_setitem_is_visible_across_import_sites(self, monkeypatch):
        """Because the catalog is a shared object, an in-place insert is seen by
        any module that imported it (the pattern test_tagging_pins_service uses).
        monkeypatch.setitem auto-removes the pin key on teardown."""
        from config import TAGGER_MODELS as seen_elsewhere

        monkeypatch.setitem(config.TAGGER_MODELS, "pin-probe-model", {"disabled": True})
        assert seen_elsewhere["pin-probe-model"] == {"disabled": True}


# ===========================================================================
# Group I -- IMPORT-TIME env behavior (isolated subprocess probes)
# ===========================================================================


class TestImportTimeEnv:
    def test_server_port_bound_from_env_at_import(self):
        result = _run_config_probe(
            "import config; print(config.SERVER_PORT)",
            {"SD_IMAGE_SORTER_PORT": "9911"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "9911"

    def test_default_server_port_is_8487(self):
        result = _run_config_probe("import config; print(config.SERVER_PORT)", {})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "8487"

    def test_invalid_port_crashes_import(self):
        result = _run_config_probe(
            "import config; print(config.SERVER_PORT)",
            {"SD_IMAGE_SORTER_PORT": "not-a-port"},
        )
        assert result.returncode != 0
        assert "Invalid SD_IMAGE_SORTER_PORT" in result.stderr

    def test_database_path_explicit_override(self, tmp_path):
        db_path = str(tmp_path / "custom" / "my.db")
        result = _run_config_probe(
            "import config; print(config.DATABASE_PATH)",
            {"SD_IMAGE_SORTER_DB_PATH": db_path},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == db_path

    def test_data_dir_derivation_chain_at_import(self, tmp_path):
        """Pins the load-bearing import-time derivation:
        DATA_DIR -> DATABASE_PATH / CONFIG_DIR -> APP_SETTINGS_CONFIG_PATH."""
        data_dir = tmp_path / "probe-data"
        script = (
            "import json, config\n"
            "print(json.dumps({"
            "'data': str(config.DATA_DIR),"
            "'db': config.DATABASE_PATH,"
            "'config': str(config.CONFIG_DIR),"
            "'app': str(config.APP_SETTINGS_CONFIG_PATH),"
            "}))\n"
        )
        result = _run_config_probe(script, {"SD_IMAGE_SORTER_DATA_DIR": str(data_dir)})
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout.strip())

        assert payload["data"] == str(data_dir)
        assert payload["db"] == str(data_dir / "images.db")
        assert payload["config"] == str(data_dir / "config")
        assert payload["app"] == str(data_dir / "config" / "app-settings.json")

    def test_hf_endpoint_propagates_into_process_env_at_import(self):
        script = (
            "import os, config\n"
            "print(config.HF_ENDPOINT + '|' + os.environ.get('HF_ENDPOINT', ''))\n"
        )
        result = _run_config_probe(script, {"HF_ENDPOINT": "https://hf-mirror.com"})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "https://hf-mirror.com|https://hf-mirror.com"

    def test_tagger_models_identity_on_clean_import(self):
        """The import-time identity contract a split MUST preserve: on a fresh
        import (no ``importlib.reload`` contamination), the tagger family and a
        direct ``from config import`` see the SAME dict object as config. This
        is what breaks in-process after a suite-wide config reload, so it is
        pinned here in an isolated child process where it robustly holds."""
        script = (
            "import config, tagger_tagtable\n"
            "from config import TAGGER_MODELS as direct\n"
            "print(tagger_tagtable.MODELS is config.TAGGER_MODELS "
            "and direct is config.TAGGER_MODELS)\n"
        )
        result = _run_config_probe(script, {})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"
