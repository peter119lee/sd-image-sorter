"""Clearing gallery records whose image files are gone.

The gallery hides unreadable rows and a banner counts them, but the only remedy
it offered was "Find Moved Files" — which relinks a file that *moved*. For a file
that was deleted that finds nothing, and nothing anywhere let the user see or
clear those rows, so the banner could only ever be hidden, not resolved.

Clearing rows is the missing action and the only irreversible one here, so two
rules shape this module.

**Never offer to clear a location we cannot currently see.** An unplugged
external drive marks every row on it unreadable while the files are perfectly
fine; clearing those would destroy tags and ratings for images that still exist.
Reachability is decided by walking up from the file to the deepest ancestor that
is actually a readable directory:

- the containing folder is readable -> the file itself is gone
- some ancestor is readable -> the volume is mounted, the folder really was removed
- nothing above it is readable -> the location is offline, refuse to clear it

**State the cost before asking.** Whether user work hangs off these rows is a
fact in the database, so it is measured and reported rather than assumed by the
UI or guessed at by the user.

Files are never touched. Only rows are removed, and a rescan of the folder adds
them back if the files reappear.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import database as db

logger = logging.getLogger("services.image_service")

# A group is described by the folder its rows claim to live in. Enough groups to
# be actionable, not so many that the panel becomes its own scrolling problem;
# the rest are folded into the summary totals, which stay exact.
MAX_REPORTED_GROUPS = 12

REASON_FILE_DELETED = "file_deleted"
REASON_FOLDER_DELETED = "folder_deleted"
REASON_LOCATION_UNREACHABLE = "location_unreachable"


def _dir_exists(candidate: Path) -> bool:
    try:
        return candidate.is_dir()
    except OSError:
        return False


def _is_readable_dir(candidate: Path) -> bool:
    """A directory we can actually list, not merely a name that resolves."""
    try:
        return candidate.is_dir() and os.access(candidate, os.R_OK)
    except OSError:
        return False


def _has_any_entry(candidate: Path) -> bool:
    """Does this directory hold anything at all?

    An empty directory cannot be told apart from an unmounted mount point: on
    Linux a disconnected ``/mnt/usb`` is exactly a readable directory holding
    nothing. A directory that still holds *something* proves the volume is
    really there and the missing files really are missing.
    """
    try:
        with os.scandir(candidate) as entries:
            for _ in entries:
                return True
        return False
    except OSError:
        return False


def classify_missing_location(raw_path: str) -> tuple:
    """Return ``(reason, location)`` for a row whose file is not on disk.

    ``location`` is the folder the row claims to live in, which is what the user
    recognises and what clearing is scoped to. ``reason`` says whether we can
    currently see that place at all.

    The bias is deliberate and asymmetric. Refusing to clear something that is
    genuinely gone leaves a banner the user can resolve by reconnecting and
    re-scanning. Clearing something that is merely unreachable destroys tags and
    ratings for files that still exist. So every ambiguous case resolves to
    ``location_unreachable``.
    """
    if not raw_path or not str(raw_path).strip():
        # A blank path resolves to the working directory, which is emphatically
        # not where this row lives. Never offer to clear on that basis.
        return REASON_LOCATION_UNREACHABLE, ""

    try:
        path = Path(raw_path)
    except (TypeError, ValueError):
        return REASON_LOCATION_UNREACHABLE, ""

    parent = path.parent
    location = str(parent)

    if _is_readable_dir(parent):
        # The folder is right there. Only trust it if it still holds something,
        # so an unmounted mount point is not mistaken for an emptied folder.
        if _has_any_entry(parent):
            return REASON_FILE_DELETED, location
        return REASON_LOCATION_UNREACHABLE, location

    if _dir_exists(parent):
        # It exists but we cannot read it — permission denied, not absence. The
        # files are very likely fine and we simply cannot look.
        return REASON_LOCATION_UNREACHABLE, location

    for ancestor in parent.parents:
        if not _is_readable_dir(ancestor):
            if _dir_exists(ancestor):
                return REASON_LOCATION_UNREACHABLE, location
            continue
        if _has_any_entry(ancestor):
            # We can read live content on this volume, so it is mounted and the
            # missing folder really was removed rather than disconnected.
            return REASON_FOLDER_DELETED, location
        return REASON_LOCATION_UNREACHABLE, location

    return REASON_LOCATION_UNREACHABLE, location


class MissingFilesMixin:
    """Missing-file cleanup slice of ImageService (assembled in services/image_service.py)."""

    def _collect_missing_groups(self) -> Dict[str, Dict[str, Any]]:
        """Group every unreadable row by claimed folder, carrying ids and cost."""
        groups: Dict[str, Dict[str, Any]] = {}
        # One filesystem probe per distinct folder, not per row: a 1,600-row
        # library sits in a handful of folders and stat calls on a slow or
        # disconnected volume are the expensive part.
        probed: Dict[str, tuple] = {}

        for row in db.get_unreadable_images_with_user_work():
            raw_path = row.get("path") or ""
            parent_key = str(Path(raw_path).parent) if raw_path else ""
            if parent_key not in probed:
                probed[parent_key] = classify_missing_location(raw_path)
            reason, location = probed[parent_key]

            group = groups.get(location)
            if group is None:
                group = {
                    "location": location,
                    "reason": reason,
                    "clearable": reason != REASON_LOCATION_UNREACHABLE,
                    "count": 0,
                    "with_tags": 0,
                    "with_rating": 0,
                    "in_collection": 0,
                    "in_dataset": 0,
                    "user_work_total": 0,
                    "image_ids": [],
                    "sample_filenames": [],
                }
                groups[location] = group

            group["count"] += 1
            group["image_ids"].append(int(row["id"]))
            has_tags = bool(row.get("has_tags"))
            has_rating = bool(row.get("has_rating"))
            in_collection = bool(row.get("in_collection"))
            in_dataset = bool(row.get("in_dataset"))
            group["with_tags"] += 1 if has_tags else 0
            group["with_rating"] += 1 if has_rating else 0
            group["in_collection"] += 1 if in_collection else 0
            group["in_dataset"] += 1 if in_dataset else 0
            # Count the row once however many kinds of work it carries: this is
            # "how many images would lose something", not a sum of properties.
            if has_tags or has_rating or in_collection or in_dataset:
                group["user_work_total"] += 1
            if len(group["sample_filenames"]) < 3:
                filename = row.get("filename")
                if filename:
                    group["sample_filenames"].append(str(filename))

        return groups

    def summarize_missing_images(self) -> Dict[str, Any]:
        """Describe every gallery record whose file is not on disk.

        Reports what can be cleared, what is only unreachable right now, and how
        many of the affected images carry work a rescan cannot restore.
        """
        groups = self._collect_missing_groups()

        total = sum(group["count"] for group in groups.values())
        clearable_total = sum(
            group["count"] for group in groups.values() if group["clearable"]
        )
        blocked_total = total - clearable_total
        user_work_total = sum(group["user_work_total"] for group in groups.values())
        # What clearing would actually cost, which is not the same number.
        # Blocked rows keep their tags precisely because they are not cleared, so
        # quoting the library-wide total in a confirmation warns about a loss
        # that cannot happen.
        clearable_user_work_total = sum(
            group["user_work_total"] for group in groups.values() if group["clearable"]
        )
        # A location we cannot read is the one case a user can fix by plugging
        # something in, so name them even when they exceed the group cap.
        unreachable_locations = sorted(
            group["location"]
            for group in groups.values()
            if not group["clearable"] and group["location"]
        )

        ordered = sorted(
            groups.values(),
            key=lambda group: (-group["count"], group["location"].lower()),
        )
        reported = [
            {key: value for key, value in group.items() if key != "image_ids"}
            for group in ordered[:MAX_REPORTED_GROUPS]
        ]

        return {
            "total": total,
            "clearable_total": clearable_total,
            "blocked_total": blocked_total,
            "user_work_total": user_work_total,
            "clearable_user_work_total": clearable_user_work_total,
            "location_total": len(groups),
            "groups": reported,
            "groups_truncated": len(ordered) > MAX_REPORTED_GROUPS,
            "unreachable_locations": unreachable_locations,
        }

    def clear_missing_images(self, location: Optional[str] = None) -> Dict[str, Any]:
        """Remove gallery records for missing files. Files on disk are untouched.

        ``location`` clears one folder's records; ``None`` clears every reachable
        location and reports how many rows were skipped because their location is
        offline. Clearing a location we cannot read is refused rather than
        silently skipped, because the caller asked for that specific place.
        """
        groups = self._collect_missing_groups()

        if location is not None:
            target = groups.get(str(location))
            if target is None:
                # Try a normalised match before giving up: the caller echoes a
                # location string we produced, but separators can round-trip.
                wanted = str(Path(str(location)))
                target = next(
                    (
                        group
                        for group in groups.values()
                        if str(Path(group["location"])) == wanted
                    ),
                    None,
                )
            if target is None:
                return {
                    "status": "nothing_to_clear",
                    "removed": 0,
                    "skipped_unreachable": 0,
                    "location": location,
                }
            if not target["clearable"]:
                logger.info(
                    "Refused to clear %d missing record(s): location is unreachable (%s)",
                    target["count"],
                    target["location"],
                )
                return {
                    "status": "refused",
                    "reason": REASON_LOCATION_UNREACHABLE,
                    "removed": 0,
                    "skipped_unreachable": target["count"],
                    "location": target["location"],
                }
            selected = [dict(target)]
        else:
            selected = [group for group in groups.values() if group["clearable"]]

        skipped_unreachable = sum(
            group["count"] for group in groups.values() if not group["clearable"]
        ) if location is None else 0

        image_ids: List[int] = []
        for group in selected:
            image_ids.extend(group["image_ids"])

        if not image_ids:
            return {
                "status": "nothing_to_clear",
                "removed": 0,
                "skipped_unreachable": skipped_unreachable,
                "location": location,
            }

        # Reuse the existing remove-from-gallery path: it deletes rows by id in
        # bounded chunks, never touches files, and already drops the cached
        # library-health counts the banner reads.
        result = self.remove_selected_images_from_gallery(image_ids)
        removed = int(result.get("removed", 0) or 0)
        logger.info(
            "Cleared %d missing gallery record(s) across %d location(s); files untouched",
            removed,
            len(selected),
        )

        return {
            "status": "cleared",
            "removed": removed,
            "skipped_unreachable": skipped_unreachable,
            "location": location,
            "locations_cleared": [group["location"] for group in selected],
            "permanent_delete": False,
        }
