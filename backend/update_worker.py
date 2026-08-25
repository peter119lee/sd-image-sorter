"""
Detached worker that applies a downloaded update archive after the main app exits.

This module intentionally uses only the Python standard library so it can run
while the application files are being replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

PACKAGE_MANIFEST_RELATIVE_PATH = Path("update") / "package-manifest.json"
INSTALLED_MANIFEST_RELATIVE_PATH = Path("update") / "installed-manifest.json"
# The in-app updater is only allowed to touch release-managed application files.
# User and runtime state must survive every update attempt, even if a future
# release manifest is built incorrectly. `data/` holds the database, favorites,
# downloaded models, caches, thumbnails, manual-sort session persistence, and
# other package-local state.
# The update subfolders below are runtime workspaces used by the updater itself
# and must never be replaced by release assets.
PROTECTED_RUNTIME_PREFIXES = (
    Path("data"),
    Path("update") / "backups",
    Path("update") / "downloads",
    Path("update") / "logs",
    Path("update") / "state",
    Path("update") / "worker",
)
ALLOWED_UPDATE_MANAGED_PATHS = {
    PACKAGE_MANIFEST_RELATIVE_PATH.as_posix(),
    INSTALLED_MANIFEST_RELATIVE_PATH.as_posix(),
}


def _log(message: str, *, log_path: Optional[Path] = None) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _windows_pid_exists(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_exists(pid)

    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float, log_path: Optional[Path]) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for process {pid} to exit")


def _safe_relative_path(name: str) -> Path:
    normalized = str(name or "").replace("\\", "/").strip()
    relative = PurePosixPath(normalized)
    if not normalized or relative.is_absolute() or normalized[:2].endswith(":"):
        raise ValueError(f"Archive entry must be relative: {name}")
    if ".." in relative.parts:
        raise ValueError(f"Archive entry escapes target root: {name}")
    return Path(*relative.parts)


def _extract_archive(archive_path: Path, target_dir: Path, *, log_path: Optional[Path]) -> None:
    if archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.infolist():
                relative = _safe_relative_path(member.filename)
                destination = target_dir / relative
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return

    if archive_path.name.lower().endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_relative_path(member.name)
                destination = target_dir / relative
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                with extracted, destination.open("wb") as dst:
                    shutil.copyfileobj(extracted, dst)
        return

    raise ValueError(f"Unsupported archive format: {archive_path.name}")


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"managed_paths": []}
    payload = _read_json(path)
    managed_paths = payload.get("managed_paths")
    if not isinstance(managed_paths, list):
        payload["managed_paths"] = []
    return payload


def _is_relative_to(path: Path, prefix: Path) -> bool:
    return path == prefix or prefix in path.parents


def _is_protected_runtime_path(relative: Path) -> bool:
    relative = _safe_relative_path(relative.as_posix())
    if relative.as_posix() in ALLOWED_UPDATE_MANAGED_PATHS:
        return False
    return any(_is_relative_to(relative, prefix) for prefix in PROTECTED_RUNTIME_PREFIXES)


def is_protected_runtime_path(relative_path: str | Path) -> bool:
    """Return whether a package-relative path is protected runtime state."""
    relative_text = relative_path.as_posix() if isinstance(relative_path, Path) else str(relative_path)
    return _is_protected_runtime_path(_safe_relative_path(relative_text))


def _extract_managed_paths(
    manifest: dict,
    *,
    manifest_label: str,
    reject_protected: bool,
    reject_invalid: bool,
    log_path: Optional[Path],
) -> set[str]:
    # We validate manifests here instead of trusting the packager because this
    # worker is the last line of defense before files are deleted or replaced.
    raw_paths = manifest.get("managed_paths") or []
    if not isinstance(raw_paths, list):
        raw_paths = []

    normalized_paths: set[str] = set()
    invalid_entries: list[str] = []
    protected_entries: list[str] = []

    for raw_entry in raw_paths:
        try:
            relative = _safe_relative_path(str(raw_entry))
        except Exception:
            invalid_entries.append(str(raw_entry))
            continue

        relative_text = relative.as_posix()
        if _is_protected_runtime_path(relative):
            protected_entries.append(relative_text)
            continue
        normalized_paths.add(relative_text)

    if invalid_entries:
        sample = ", ".join(invalid_entries[:5])
        message = f"{manifest_label} contains invalid managed path(s): {sample}"
        if reject_invalid:
            raise ValueError(message)
        _log(f"Ignored {len(invalid_entries)} invalid managed path(s) from {manifest_label}", log_path=log_path)

    if protected_entries:
        sample = ", ".join(protected_entries[:5])
        message = f"{manifest_label} tried to manage protected runtime path(s): {sample}"
        if reject_protected:
            raise ValueError(message)
        _log(f"Ignored {len(protected_entries)} protected runtime path(s) from {manifest_label}", log_path=log_path)

    return normalized_paths


def validate_update_manifest_managed_paths(manifest: dict, *, manifest_label: str = "update manifest") -> set[str]:
    """Strictly validate managed paths from a newly downloaded update manifest."""
    return _extract_managed_paths(
        manifest,
        manifest_label=manifest_label,
        reject_protected=True,
        reject_invalid=True,
        log_path=None,
    )


def _resolve_payload_root(extracted_root: Path) -> Path:
    direct_manifest = extracted_root / PACKAGE_MANIFEST_RELATIVE_PATH
    if direct_manifest.exists():
        return extracted_root

    matches = list(extracted_root.glob(f"*/{PACKAGE_MANIFEST_RELATIVE_PATH.as_posix()}"))
    if len(matches) == 1:
        return matches[0].parent.parent

    recursive_matches = list(extracted_root.rglob(PACKAGE_MANIFEST_RELATIVE_PATH.name))
    normalized_matches = [
        match for match in recursive_matches
        if match.relative_to(extracted_root).as_posix().endswith(PACKAGE_MANIFEST_RELATIVE_PATH.as_posix())
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0].parent.parent

    raise FileNotFoundError(
        f"Could not locate {PACKAGE_MANIFEST_RELATIVE_PATH.as_posix()} inside extracted update archive"
    )


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_managed_files(src_root: Path, dst_root: Path, managed_paths: set[str]) -> int:
    copied = 0
    for relative_text in sorted(managed_paths):
        relative = Path(relative_text)
        source = src_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Update payload is missing managed file: {relative_text}")
        _copy_file(source, dst_root / relative)
        copied += 1
    return copied


def _prune_empty_dirs(path: Path, *, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _backup_existing_files(package_root: Path, managed_paths: set[str], backup_root: Path, *, log_path: Optional[Path]) -> None:
    if not managed_paths:
        return
    backed_up = 0
    for relative_text in sorted(managed_paths):
        relative = Path(relative_text)
        source = package_root / relative
        if source.is_file():
            _copy_file(source, backup_root / relative)
            backed_up += 1
    if backed_up:
        _log(f"Backed up {backed_up} existing file(s) to {backup_root}", log_path=log_path)


def _remove_obsolete_files(package_root: Path, old_paths: set[str], new_paths: set[str], *, log_path: Optional[Path]) -> None:
    removed = 0
    for relative_text in sorted(old_paths - new_paths):
        relative = Path(relative_text)
        target = package_root / relative
        if target.is_file():
            target.unlink()
            removed += 1
            _prune_empty_dirs(target.parent, stop_at=package_root)
    if removed:
        _log(f"Removed {removed} obsolete file(s)", log_path=log_path)


def _write_installed_manifest(package_root: Path, manifest: dict) -> None:
    target = package_root / INSTALLED_MANIFEST_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _relaunch(launcher_path: Path, *, log_path: Optional[Path]) -> None:
    if sys.platform == "win32":
        command = ["cmd.exe", "/c", str(launcher_path)]
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            command,
            cwd=str(launcher_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        subprocess.Popen(
            ["bash", str(launcher_path)],
            cwd=str(launcher_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    _log(f"Relaunch requested via {launcher_path.name}", log_path=log_path)


def apply_update(manifest_path: Path) -> int:
    pending = _read_json(manifest_path)
    package_root = Path(pending["package_root"]).resolve()
    update_root = Path(pending["update_root"]).resolve()
    archive_path = Path(pending["archive_path"]).resolve()
    launcher_path = Path(pending["launcher_path"]).resolve()
    current_pid = int(pending["current_pid"])
    target_version = str(pending.get("target_version") or "latest")
    relaunch = bool(pending.get("relaunch", True))
    log_path = Path(str(pending.get("log_path") or update_root / "logs" / "update.log"))

    work_root = update_root / "worker"
    extracted_root = work_root / f"extract-{int(time.time())}"
    backup_root = update_root / "backups" / f"{int(time.time())}-{target_version}"

    if current_pid > 0:
        _log(f"Waiting for process {current_pid} to exit", log_path=log_path)
        _wait_for_pid_exit(current_pid, timeout_seconds=180, log_path=log_path)
    else:
        _log("No running app process was provided; applying update immediately", log_path=log_path)

    if extracted_root.exists():
        shutil.rmtree(extracted_root, ignore_errors=True)
    extracted_root.mkdir(parents=True, exist_ok=True)

    _log(f"Extracting {archive_path.name}", log_path=log_path)
    _extract_archive(archive_path, extracted_root, log_path=log_path)
    payload_root = _resolve_payload_root(extracted_root)
    if payload_root != extracted_root:
        _log(f"Normalized extracted payload root to {payload_root.name}", log_path=log_path)

    current_manifest = _load_manifest(package_root / INSTALLED_MANIFEST_RELATIVE_PATH)
    if not current_manifest["managed_paths"]:
        current_manifest = _load_manifest(package_root / PACKAGE_MANIFEST_RELATIVE_PATH)

    new_manifest = _load_manifest(payload_root / PACKAGE_MANIFEST_RELATIVE_PATH)
    # Old manifests may contain stale or buggy entries from previous versions.
    # Ignore invalid/protected paths there so an update never deletes runtime
    # state just because an older package manifest was wrong.
    old_paths = _extract_managed_paths(
        current_manifest,
        manifest_label="current manifest",
        reject_protected=False,
        reject_invalid=False,
        log_path=log_path,
    )
    # New manifests must be strict. If a release tries to manage `data/` or the
    # updater's own runtime folders, abort before any installed files change.
    new_paths = _extract_managed_paths(
        new_manifest,
        manifest_label="update manifest",
        reject_protected=True,
        reject_invalid=True,
        log_path=log_path,
    )

    _backup_existing_files(package_root, old_paths | new_paths, backup_root, log_path=log_path)
    _remove_obsolete_files(package_root, old_paths, new_paths, log_path=log_path)

    _log("Copying updated application files into package root", log_path=log_path)
    copied_files = _copy_managed_files(payload_root, package_root, new_paths)
    _log(f"Copied {copied_files} managed file(s) into package root", log_path=log_path)
    if new_manifest.get("managed_paths"):
        _write_installed_manifest(package_root, new_manifest)

    manifest_path.unlink(missing_ok=True)
    shutil.rmtree(extracted_root, ignore_errors=True)

    _log(f"Update to {target_version} applied successfully", log_path=log_path)
    if relaunch and launcher_path.exists():
        _relaunch(launcher_path, log_path=log_path)
    return 0


def restart_only(manifest_path: Path) -> int:
    """Relaunch the app without touching a single installed file.

    Same wait-then-relaunch dance as ``apply_update``, minus the patching. The
    caller (an in-app "Restart now" button) writes the manifest, spawns this
    worker detached, and then exits; we wait for that PID to disappear so the
    launcher does not race the old process for the port.
    """
    pending = _read_json(manifest_path)
    launcher_path = Path(pending["launcher_path"]).resolve()
    current_pid = int(pending.get("current_pid") or 0)
    log_path = Path(str(pending.get("log_path"))) if pending.get("log_path") else None
    package_root_raw = pending.get("package_root")
    if package_root_raw:
        try:
            launcher_path.relative_to(Path(str(package_root_raw)).resolve())
        except (OSError, ValueError):
            _log(
                f"Launcher {launcher_path} is outside the package root; refusing restart",
                log_path=log_path,
            )
            return 1

    if current_pid > 0:
        _log(f"Waiting for process {current_pid} to exit before restart", log_path=log_path)
        _wait_for_pid_exit(current_pid, timeout_seconds=180, log_path=log_path)

    manifest_path.unlink(missing_ok=True)

    if not launcher_path.is_file():
        _log(f"Launcher {launcher_path} is gone; cannot restart", log_path=log_path)
        return 1
    _relaunch(launcher_path, log_path=log_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a staged SD Image Sorter update")
    parser.add_argument("--manifest", required=True, help="Path to the pending update manifest")
    parser.add_argument(
        "--restart-only",
        action="store_true",
        help="Relaunch the app without applying any update payload.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    try:
        if args.restart_only:
            return restart_only(manifest_path)
        return apply_update(manifest_path)
    except Exception as exc:
        log_path = None
        try:
            payload = _read_json(manifest_path)
            log_path = Path(str(payload.get("log_path"))) if payload.get("log_path") else None
        except Exception:
            log_path = None
        _log(f"Update worker failed: {exc}", log_path=log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
