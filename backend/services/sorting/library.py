"""Library-level operations: roots, analytics/stats, drop resolution,
uploads, and folder browsing.

Moved verbatim from services/sorting_service.py (decomposition 2026-07).
parse_metadata_job / add_images_batch and the facet-limit constants resolve
through the facade module at call time; clear_gallery
and get_library_health stay on the facade with the TTL cache they touch.
"""

import logging
import os
import platform
import string
from collections.abc import Mapping
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException

import database as db
from app_info import APP_VERSION, GITHUB_REPOSITORY_URL
from services.gallery_job_gate import gallery_job_transition
from services.sorting_models import (
    SCAN_ACTIVE_STATUSES,
    SCAN_SOURCE_LIBRARY_AUTO_REFRESH,
    SCAN_SOURCE_LIBRARY_RESCAN,
    SCAN_SOURCE_MANUAL,
    SCAN_TERMINAL_STATUSES,
    ScanRequest,
    ScanStartResult,
    ValidatePathRequest,
)
from utils.path_validation import normalize_user_path, validate_folder_path
from utils.source_paths import (
    indexed_image_path_match_key,
    indexed_path_for_runtime,
    is_case_insensitive_indexed_path,
)

# NOTE(decomposition): keep the historical logger channel — tests attach
# handlers / caplog filters to "services.sorting_service" (heartbeat pins),
# and log routing/output must stay byte-identical after the package split.
logger = logging.getLogger("services.sorting_service")


def _is_manual_completion_pending(status: Optional[str], source: Optional[str]) -> bool:
    """Return whether a manual terminal result still owns shared scan progress."""
    return status in SCAN_TERMINAL_STATUSES and source in {None, SCAN_SOURCE_MANUAL}


def _active_scan_targets_library_root(
    scan_progress: Mapping[str, object],
    root_id: int,
    root_path: str,
) -> bool:
    """Return whether active scan completion would register the selected root."""
    if scan_progress.get("status") not in SCAN_ACTIVE_STATUSES:
        return False
    if scan_progress.get("registered_root_id") == root_id:
        return True
    active_root_path = scan_progress.get("root_record_path")
    if not isinstance(active_root_path, str):
        return False
    root_key = indexed_image_path_match_key(root_path)
    return bool(root_key) and indexed_image_path_match_key(active_root_path) == root_key


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


def _indexed_path_object_for_runtime(indexed_path: Optional[str]) -> PurePath:
    """Build a path object without host-misparsing an unmounted UNC path."""
    runtime_path = indexed_path_for_runtime(indexed_path, os.name)
    if os.name != "nt" and runtime_path.startswith("\\\\"):
        return PureWindowsPath(runtime_path)
    return Path(runtime_path)


def _indexed_parent_folder_for_runtime(indexed_path: Optional[str]) -> Optional[str]:
    """Return a usable parent folder for a stored image path."""
    parent = str(_indexed_path_object_for_runtime(indexed_path).parent)
    if parent in {"", "."}:
        return None
    return parent


def _indexed_ancestor_folder_for_runtime(
    indexed_path: Optional[str],
    folder_name: str,
) -> Optional[str]:
    """Return the nearest indexed ancestor whose complete segment matches."""
    case_insensitive = is_case_insensitive_indexed_path(indexed_path)
    expected_name = folder_name.casefold() if case_insensitive else folder_name
    path_object = _indexed_path_object_for_runtime(indexed_path)

    for parent in path_object.parents:
        candidate_name = parent.name.casefold() if case_insensitive else parent.name
        if candidate_name == expected_name:
            return str(parent)

    if isinstance(path_object, PureWindowsPath) and path_object.drive.startswith("\\\\"):
        share_name = path_object.drive.rsplit("\\", 1)[-1]
        candidate_name = share_name.casefold() if case_insensitive else share_name
        if candidate_name == expected_name:
            return path_object.anchor
    return None


def parse_metadata_job(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.parse_metadata_job)."""
    return _svc().parse_metadata_job(*args, **kwargs)


def add_images_batch(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.add_images_batch)."""
    return _svc().add_images_batch(*args, **kwargs)


class LibraryMixin:
    """Library-ops slice of SortingService (assembled in services/sorting_service.py)."""

    def validate_path(self, request: ValidatePathRequest) -> Dict[str, Any]:
        """Validate a folder path for inline UI feedback."""
        normalized_path = normalize_user_path(request.path)
        is_valid, error = validate_folder_path(normalized_path)
        return {
            "valid": is_valid,
            "error": error,
            "normalized_path": normalized_path if is_valid else None,
        }

    def remove_library_root(self, root_id: int) -> Dict[str, Any]:
        """Unregister a library root. Indexed images are NOT deleted (v3.3.2)."""
        normalized_root_id = int(root_id)
        with gallery_job_transition(), self._scan_lock:
            root = db.get_library_root(normalized_root_id)
            if not root:
                raise HTTPException(status_code=404, detail="Library root not found")
            suppress_root_record = _active_scan_targets_library_root(
                self._scan_progress,
                normalized_root_id,
                root["path"],
            )
            if not db.remove_library_root(normalized_root_id):
                raise HTTPException(status_code=404, detail="Library root not found")
            if suppress_root_record:
                self._scan_progress = {
                    **self._scan_progress,
                    "suppress_root_record": True,
                }
        return {"status": "removed", "id": normalized_root_id}

    def rescan_library_root(self, root_id: int, background_tasks: BackgroundTasks) -> ScanStartResult:
        """Re-scan a registered root to pick up new/changed files (quick import)."""
        root = db.get_library_root(int(root_id))
        if not root:
            raise HTTPException(status_code=404, detail="Library root not found")
        request = ScanRequest(
            folder_path=indexed_path_for_runtime(root["path"], os.name),
            recursive=True,
            quick_import=True,
            cleanup_missing=False,
            force_reparse=False,
        )
        return self.start_registered_root_scan(
            request,
            background_tasks,
            SCAN_SOURCE_LIBRARY_RESCAN,
            root_record_path=root["path"],
            root_id=root["id"],
        )

    def auto_refresh_library(self, background_tasks: BackgroundTasks) -> Dict[str, Any]:
        """Idle-triggered quick-scan of the stalest enabled root (v3.3.2 Library Navigation).

        Safe by construction: a no-op while any scan is running (single-scan
        model) or when there are no enabled roots, and it always quick-imports —
        it NEVER runs AI tagging (GPU safety). Successive idle ticks cycle
        through roots oldest-first via ``last_scanned_at``.
        """
        with self._scan_lock:
            status = self._scan_progress.get("status")
            source = self._scan_progress.get("source")
        if status in SCAN_ACTIVE_STATUSES:
            return {"status": "skipped", "reason": "scan_in_progress"}
        if _is_manual_completion_pending(status, source):
            return {"status": "skipped", "reason": "manual_completion_pending"}

        roots = [r for r in db.list_library_roots() if r.get("enabled")]
        if not roots:
            return {"status": "idle", "reason": "no_enabled_roots"}

        # Oldest last_scanned_at first; never-scanned (None -> "") sorts first.
        target = min(roots, key=lambda r: r.get("last_scanned_at") or "")
        request = ScanRequest(
            folder_path=indexed_path_for_runtime(target["path"], os.name),
            recursive=True,
            quick_import=True,
            cleanup_missing=False,
            force_reparse=False,
        )
        try:
            scan = self.start_registered_root_scan(
                request,
                background_tasks,
                SCAN_SOURCE_LIBRARY_AUTO_REFRESH,
                root_record_path=target["path"],
                root_id=target["id"],
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                with self._scan_lock:
                    current_status = self._scan_progress.get("status")
                    current_source = self._scan_progress.get("source")
                reason = (
                    "manual_completion_pending"
                    if _is_manual_completion_pending(current_status, current_source)
                    else "scan_in_progress"
                )
                return {"status": "skipped", "reason": reason}
            return {
                "status": "skipped",
                "reason": "scan_start_failed",
                "detail": str(exc.detail),
                "status_code": exc.status_code,
            }
        return {"status": "started", "root": target["path"], "scan": scan}

    def get_analytics(
        self,
        facet: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get all tags, checkpoints, and loras with counts."""
        normalized_facet = str(facet or "").strip().lower()
        if normalized_facet in {"checkpoint", "checkpoints"}:
            return {"checkpoints": db.get_all_checkpoints(limit=limit, search_query=search_query)}
        if normalized_facet in {"lora", "loras"}:
            return db.get_all_loras(limit=limit, search_query=search_query)
        if normalized_facet in {"tag", "tags"}:
            return {"top_tags": db.search_tags(search_query, limit=limit).get("tags", [])}

        effective_limit = _svc().ANALYTICS_DEFAULT_LIMIT if limit is None else limit

        with db.get_db() as conn:
            cursor = conn.cursor()

            # Use the normalized image_loras table instead of full-table JSON scan
            cursor.execute("""
                SELECT lora_name AS lora, COUNT(*) as count
                FROM image_loras
                GROUP BY lora_name
                ORDER BY count DESC
                LIMIT ?
            """, (effective_limit,))
            loras = [dict(row) for row in cursor.fetchall()]

            tags = db.search_tags(None, limit=effective_limit).get("tags", [])

        return {
            "checkpoints": db.get_all_checkpoints(limit=effective_limit),
            "loras": loras,
            "top_tags": tags
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        analytics_data = self.get_analytics(limit=_svc().STATS_FACET_LIMIT)
        metadata_status = db.get_metadata_status_counts()
        metadata_pending = int(metadata_status.get("pending", 0) or 0)
        scan_progress = self.get_scan_progress()
        return {
            "total_images": db.get_image_count(),
            "generators": db.get_all_generators(),
            "top_tags": analytics_data["top_tags"],
            "checkpoints": analytics_data["checkpoints"],
            "loras": analytics_data["loras"],
            "metadata_status": metadata_status,
            "metadata_pending": metadata_pending,
            "metadata_resolving": metadata_pending > 0,
            "scan_status": scan_progress.get("status"),
            "scan_step": scan_progress.get("step"),
            "scan_library_ready": bool(scan_progress.get("library_ready", False)),
            "app_version": APP_VERSION,
            "github_url": GITHUB_REPOSITORY_URL,
        }

    def resolve_drop(self, folder_name: str, filenames: List[str], dropped_files: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Resolve browser-dropped folder name or filenames to a real filesystem path."""
        conn = db.get_connection()
        cursor = conn.cursor()

        files_info = dropped_files or []
        names = [f["name"] for f in files_info if f.get("name")] if files_info else filenames[:5]
        names = [n for n in names if n and isinstance(n, str)]

        if names:
            placeholders = ",".join("?" for _ in names)
            cursor.execute(
                f"SELECT path, filename, file_size FROM images WHERE filename IN ({placeholders})",
                names,
            )
            rows = cursor.fetchall()
            if rows:
                size_by_name = {}
                for f in files_info:
                    if f.get("name") and f.get("size"):
                        size_by_name[f["name"]] = int(f["size"])

                folder_scores: Dict[str, int] = {}
                for row in rows:
                    rpath = row[0] if isinstance(row, (tuple, list)) else row["path"]
                    rname = row[1] if isinstance(row, (tuple, list)) else row["filename"]
                    rsize = row[2] if isinstance(row, (tuple, list)) else row["file_size"]
                    parent = _indexed_parent_folder_for_runtime(rpath)
                    if parent is None:
                        continue
                    expected_size = size_by_name.get(rname)
                    if expected_size and rsize and abs(int(rsize) - expected_size) < 2:
                        folder_scores[parent] = folder_scores.get(parent, 0) + 10
                    else:
                        folder_scores[parent] = folder_scores.get(parent, 0) + 1

                if folder_scores:
                    best = max(folder_scores, key=folder_scores.get)
                    return {"folder_path": best}

        if folder_name and self._is_safe_folder_segment(folder_name):
            posix_segment = f"/{folder_name}/"
            windows_identity_segment = f"\\{folder_name.casefold()}\\"
            cursor.execute(
                """
                SELECT i.path
                FROM images AS i
                LEFT JOIN image_path_identities AS identity
                    ON identity.image_id = i.id
                WHERE INSTR(REPLACE(i.path, '\\', '/'), ?) > 0
                   OR INSTR(identity.path_key, ?) > 0
                ORDER BY i.id
                """,
                (posix_segment, windows_identity_segment),
            )
            for row in cursor:
                raw = row[0] if isinstance(row, (tuple, list)) else row["path"]
                resolved_folder = _indexed_ancestor_folder_for_runtime(raw, folder_name)
                if resolved_folder is not None:
                    return {"folder_path": resolved_folder}

            for base in self._common_image_roots():
                candidate = (Path(base) / folder_name).resolve()
                # Defense in depth: candidate must stay under the root we picked.
                try:
                    candidate.relative_to(Path(base).resolve())
                except ValueError:
                    continue
                if candidate.is_dir():
                    return {"folder_path": str(candidate)}

        return {"folder_path": ""}

    @staticmethod
    def _is_safe_folder_segment(name: str) -> bool:
        """Reject browser-supplied folder names that could escape a base dir."""
        if not name or not isinstance(name, str):
            return False
        if name in {".", ".."}:
            return False
        # Path separators or drive markers indicate the caller is trying to
        # supply a multi-segment path, not a single folder name.
        if "/" in name or "\\" in name or ":" in name:
            return False
        # Any control char or NUL byte → reject.
        if any(ord(ch) < 32 for ch in name):
            return False
        return True

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape SQL LIKE wildcards so user input matches literally."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def import_uploaded_files(self, files) -> Dict[str, Any]:
        """Save uploaded files to imports dir and add them to the gallery.

        Path-traversal hardening: the browser-supplied filename is reduced to
        its basename via ``Path(name).name`` so values like ``../../etc/x.png``
        cannot escape ``import_dir``. After constructing ``dest`` we also
        verify it resolves underneath ``import_dir.resolve()`` as a defense in
        depth against weird Windows path semantics.
        """
        from config import DATA_DIR
        import_dir = (Path(DATA_DIR) / "imports").resolve()
        import_dir.mkdir(parents=True, exist_ok=True)

        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
        saved_paths: List[Path] = []
        for upload in files:
            raw_name = upload.filename or ""
            ext = Path(raw_name).suffix.lower()
            if ext not in IMAGE_EXTS:
                continue
            # Basename only — strips any "../" or absolute paths the browser sent.
            safe_stem_name = Path(raw_name).name
            if not safe_stem_name or safe_stem_name in {".", ".."} or safe_stem_name.startswith("."):
                safe_stem_name = f"upload_{len(saved_paths)}{ext}"
            dest = (import_dir / safe_stem_name).resolve()
            counter = 1
            stem = Path(safe_stem_name).stem or "upload"
            while dest.exists():
                dest = (import_dir / f"{stem}_{counter}{ext}").resolve()
                counter += 1
            # Defense in depth: refuse anything that resolves outside import_dir.
            try:
                dest.relative_to(import_dir)
            except ValueError:
                logger.warning("Refusing upload with unsafe filename: %r", raw_name)
                continue
            content = await upload.read()
            dest.write_bytes(content)
            saved_paths.append(dest)

        records = []
        errors = 0
        image_ids = []
        for path in saved_paths:
            result = parse_metadata_job({
                "path": str(path),
                "filename": path.name,
                "compute_content_fingerprint": True,
                "validate_image_data": True,
            })
            if result.get("error"):
                errors += 1
            records.append(result["record"])

        if records:
            batch_result = add_images_batch(records, return_statuses=True)
            for path_str, status in (batch_result.get("statuses") or {}).items():
                image_ids.append(batch_result.get("ids", {}).get(path_str))

        return {
            "imported": len(records) - errors,
            "errors": errors,
            "total": len(records),
            "image_ids": [i for i in image_ids if i],
        }

    @staticmethod
    def _common_image_roots() -> List[str]:
        home = Path.home()
        roots = [
            home / "Pictures",
            home / "Desktop",
            home / "Downloads",
            home / "Documents",
        ]
        if platform.system() == "Windows":
            for drive in "CDEFGH":
                roots.append(Path(f"{drive}:\\"))
        return [str(r) for r in roots if r.exists()]

    def browse_folder(self, path: str) -> Dict[str, Any]:
        """
        Browse a folder and list its subdirectories.

        Args:
            path: The folder path to browse. Empty string or "/" on Windows
                  lists drive letters. On Linux, empty string lists "/".

        Returns:
            Dictionary with current path, parent path, and subdirectories.
        """
        # Special case: empty path or root-like paths -> list root/drives
        if not path or path.strip() in ("", "/", "\\"):
            if platform.system() == "Windows":
                drives = []
                for letter in string.ascii_uppercase:
                    drive_path = f"{letter}:\\"
                    if os.path.exists(drive_path):
                        try:
                            has_children = any(
                                entry.is_dir()
                                for entry in os.scandir(drive_path)
                                if not entry.name.startswith(".")
                            )
                        except (PermissionError, OSError):
                            has_children = False
                        drives.append({
                            "name": f"{letter}:\\",
                            "path": drive_path,
                            "has_children": has_children,
                        })
                return {
                    "current": "",
                    "parent": None,
                    "subdirs": drives,
                }
            else:
                # Linux/macOS: list "/"
                path = "/"

        # Validate the folder path (must exist)
        normalized_path = normalize_user_path(path)
        is_valid, error = validate_folder_path(normalized_path)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=error or "Invalid folder path",
            )

        resolved = os.path.realpath(normalized_path)

        # Determine parent
        parent = os.path.dirname(resolved)
        if parent == resolved:
            # We are at root (e.g., "/" on Linux or "C:\" on Windows)
            if platform.system() == "Windows":
                parent_result: Optional[str] = ""  # signal to list drives
            else:
                parent_result = None  # no parent above "/"
        else:
            parent_result = parent

        # List subdirectories
        subdirs: List[Dict[str, Any]] = []
        try:
            with os.scandir(resolved) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir():
                            continue
                        if entry.name.startswith("."):
                            continue
                        try:
                            child_has_children = any(
                                sub.is_dir()
                                for sub in os.scandir(entry.path)
                                if not sub.name.startswith(".")
                            )
                        except (PermissionError, OSError):
                            child_has_children = False
                        subdirs.append({
                            "name": entry.name,
                            "path": entry.path,
                            "has_children": child_has_children,
                        })
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError) as exc:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot read directory: {exc}",
            )

        # Sort alphabetically, case-insensitive
        subdirs.sort(key=lambda d: d["name"].lower())

        return {
            "current": resolved,
            "parent": parent_result,
            "subdirs": subdirs,
        }
