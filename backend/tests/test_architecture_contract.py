"""Architecture boundary tests for ongoing god-file reduction."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read_repo_file(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_main_delegates_security_static_and_diagnostics_helpers():
    """main.py should wire the app, not own every infrastructure helper."""
    source = _read_repo_file("backend", "main.py")

    forbidden_definitions = [
        r"^class\s+NoCacheStaticFiles\b",
        r"^def\s+_is_loopback_host\b",
        r"^def\s+rate_limit_middleware\b",
        r"^def\s+build_support_diagnostics\b",
        r"^def\s+open_support_log_file\b",
    ]
    for pattern in forbidden_definitions:
        assert not re.search(pattern, source, re.MULTILINE), pattern

    assert "from app_security import" in source
    assert "from app_static import" in source
    assert "from app_diagnostics import" in source


def test_backend_infrastructure_modules_own_extracted_main_concerns():
    security = _read_repo_file("backend", "app_security.py")
    static = _read_repo_file("backend", "app_static.py")
    diagnostics = _read_repo_file("backend", "app_diagnostics.py")

    assert "def _is_loopback_host" in security
    assert "def configure_security_middleware" in security
    assert "class NoCacheStaticFiles" in static
    assert "def serve_frontend_index" in static
    assert "def build_support_diagnostics" in diagnostics
    assert "def open_support_log_file" in diagnostics


def test_frontend_core_modules_load_before_app_script():
    index_html = _read_repo_file("frontend", "index.html")
    storage_script = '/static/js/modules/core/storage-utils.js'
    request_script = '/static/js/modules/core/request-manager.js'
    app_script = '/static/js/app.js'

    assert storage_script in index_html
    assert request_script in index_html
    assert index_html.index(storage_script) < index_html.index(app_script)
    assert index_html.index(request_script) < index_html.index(app_script)


def test_app_js_delegates_core_utilities_to_modules():
    source = _read_repo_file("frontend", "js", "app.js")

    assert "const RequestManager =" not in source
    assert "function readStoredJson" not in source
    assert "function writeStoredJson" not in source
    assert "function readStoredBoolean" not in source
    assert "function writeStoredBoolean" not in source


def test_image_service_delegates_reader_metadata_write_helpers():
    source = _read_repo_file("backend", "services", "image_service.py")
    helper_source = _read_repo_file("backend", "services", "image_metadata_writer.py")

    assert "def _normalize_edited_metadata" not in source
    assert "def _build_sd_parameters_text" not in source
    assert "def normalize_edited_metadata" in helper_source
    assert "def build_sd_parameters_text" in helper_source


def test_sorting_service_delegates_request_models():
    source = _read_repo_file("backend", "services", "sorting_service.py")
    models_source = _read_repo_file("backend", "services", "sorting_models.py")
    router_source = _read_repo_file("backend", "routers", "sorting.py")

    for model_name in (
        "ScanRequest",
        "ValidatePathRequest",
        "MoveRequest",
        "SortFilterRequest",
        "BatchMoveRequest",
        "ManualSortStartRequest",
        "FolderConfig",
        "BrowseFolderRequest",
    ):
        assert f"class {model_name}" not in source
        assert f"class {model_name}" in models_source

    assert "from services.sorting_models import" in router_source


def test_sorting_service_delegates_session_file_persistence():
    source = _read_repo_file("backend", "services", "sorting_service.py")
    store_source = _read_repo_file("backend", "services", "sorting_session_store.py")

    assert "from services.sorting_session_store import" in source
    assert "json.load(" not in source
    assert "json.dump(" not in source
    assert "def get_session_file_candidates" in store_source
    assert "def parse_persisted_session_version" in store_source
    assert "def read_persisted_session" in store_source
    assert "def write_persisted_session" in store_source


def test_a_second_data_access_style_is_not_shipped_without_callers():
    """`db_repos` shipped 1,544 lines no production module ever imported.

    It was added in v2.0 and recommended in ``database.py``'s docstring for
    "new code", but nothing adopted it, it was not in the release exclusion
    list, and its only exercise was one test asserting behavior already covered
    against the production function. A parallel data-access layer is fine —
    an unadopted one is dead weight in every download. If this is reintroduced,
    migrate real callers in the same change.
    """
    assert not (ROOT / "backend" / "db_repos").exists(), (
        "db_repos is back; migrate production callers to it or remove it"
    )

    database_source = _read_repo_file("backend", "database.py")
    assert "from db_repos import" not in database_source

    backend_dir = ROOT / "backend"
    importers: list[str] = []
    for path in backend_dir.rglob("*.py"):
        parts = set(path.parts)
        if parts & {"venv", "tests", "__pycache__", ".venv"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(?:from\s+db_repos\b|import\s+db_repos\b)", text, re.MULTILINE):
            importers.append(path.relative_to(ROOT).as_posix())

    assert not importers, (
        "these modules import db_repos, which no longer exists: " + ", ".join(importers)
    )
