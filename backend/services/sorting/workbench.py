"""Workbench sort modes: A/B bracket + keep/reject cull (v3.3.2).

Moved verbatim from services/sorting_service.py (decomposition 2026-07).
verify_image_readable resolves through the facade module at call time.
"""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import database as db
from services.sorting_models import (
    SORT_MODE_BRACKET,
    SORT_MODE_CULL,
    VALID_BRACKET_ACTIONS,
    VALID_CULL_ACTIONS,
)


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


def verify_image_readable(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.verify_image_readable)."""
    return _svc().verify_image_readable(*args, **kwargs)


class WorkbenchMixin:
    """Bracket/cull slice of SortingService (assembled in services/sorting_service.py)."""

    def _resolve_readable_sort_image(self, image_id: int) -> Optional[Dict[str, Any]]:
        """Return {image, tags} for a sortable image, or None if missing/unreadable.

        Marks the row unreadable as a side effect (mirrors the slot path's lazy
        verification) so the caller can advance past it.
        """
        current = db.get_image_by_id(image_id)
        if not current:
            return None
        current_path = self._resolve_image_path(current.get("path") or "")
        if not current_path:
            db.mark_image_unreadable(image_id, "File not found")
            return None
        readable, read_error = verify_image_readable(current_path)
        if not readable:
            db.mark_image_unreadable(image_id, read_error or "Unreadable image")
            return None
        return {"image": current, "tags": db.get_image_tags(image_id)}

    def _get_current_bracket_image(self) -> Dict[str, Any]:
        """Return the current champion/challenger pair for A/B bracket mode.

        Skips unreadable challengers (advance) and unreadable champions (the
        challenger is promoted uncontested), so the user never lands on a broken
        image — mirroring the slot path's lazy readability handling.
        """
        while True:
            with self._sort_session_lock:
                if not self._sort_session.get("active"):
                    return {
                        "active": False,
                        "done": True,
                        "mode": SORT_MODE_BRACKET,
                        "message": "No active sort session",
                        "champion": None,
                        "challenger": None,
                        "winner": None,
                        "total": 0,
                        "remaining": 0,
                        **self._get_sort_session_flags([], []),
                    }
                image_ids = self._sort_session["image_ids"]
                total = len(image_ids)
                champion_index = int(self._sort_session.get("champion_index", 0) or 0)
                challenger_index = int(self._sort_session.get("current_index", 0) or 0)
                history_snapshot = list(self._sort_session.get("history", []))
                redo_snapshot = list(self._sort_session.get("redo_stack", []))

            if total == 0 or challenger_index >= total:
                winner_payload = None
                if total and 0 <= champion_index < total:
                    winner_payload = self._resolve_readable_sort_image(image_ids[champion_index])
                return {
                    "active": True,
                    "done": True,
                    "mode": SORT_MODE_BRACKET,
                    "winner": winner_payload,
                    "champion": winner_payload,
                    "challenger": None,
                    "total": total,
                    "remaining": 0,
                    "message": "Bracket complete" if winner_payload else "No images to compare",
                    **self._get_sort_session_flags(history_snapshot, redo_snapshot),
                }

            champion = self._resolve_readable_sort_image(image_ids[champion_index])
            if champion is None:
                # Champion is broken → the current challenger takes the crown.
                with self._sort_session_lock:
                    self._sort_session["champion_index"] = challenger_index
                    self._sort_session["current_index"] = challenger_index + 1
                    self._save_session_to_disk()
                continue

            challenger = self._resolve_readable_sort_image(image_ids[challenger_index])
            if challenger is None:
                with self._sort_session_lock:
                    self._sort_session["current_index"] = challenger_index + 1
                    self._save_session_to_disk()
                continue

            return {
                "active": True,
                "done": False,
                "mode": SORT_MODE_BRACKET,
                "champion": champion,
                "challenger": challenger,
                "champion_index": champion_index,
                "challenger_index": challenger_index,
                "index": challenger_index,
                "total": total,
                "comparisons_total": max(0, total - 1),
                "remaining": total - challenger_index,
                "image_ids": list(image_ids),
                **self._get_sort_session_flags(history_snapshot, redo_snapshot),
            }

    def _bracket_action(self, action: str) -> Dict[str, Any]:
        """Apply an A/B bracket action (champion/challenger/skip/undo/redo)."""
        if action not in VALID_BRACKET_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid bracket action. Must be one of: {', '.join(VALID_BRACKET_ACTIONS)}",
            )

        with self._sort_session_lock:
            if not self._sort_session.get("active"):
                raise HTTPException(status_code=400, detail="No active sort session")

            total = len(self._sort_session["image_ids"])
            champion_index = int(self._sort_session.get("champion_index", 0) or 0)
            challenger_index = int(self._sort_session.get("current_index", 0) or 0)
            history = self._sort_session.setdefault("history", [])
            redo_stack = self._sort_session.setdefault("redo_stack", [])

            if action == "undo":
                if not history:
                    return {"status": "nothing_to_undo", **self._get_sort_session_flags(history, redo_stack)}
                last = history.pop()
                redo_stack.append(last)
                self._sort_session["champion_index"] = int(last.get("prev_champion_index", 0))
                self._sort_session["current_index"] = int(last.get("prev_challenger_index", 0))
                self._save_session_to_disk()
                return {"status": "undone", **self._get_sort_session_flags(history, redo_stack)}

            if action == "redo":
                if not redo_stack:
                    return {"status": "nothing_to_redo", **self._get_sort_session_flags(history, redo_stack)}
                entry = redo_stack.pop()
                prev_challenger = int(entry.get("prev_challenger_index", 0))
                if entry.get("action") == "challenger":
                    self._sort_session["champion_index"] = prev_challenger
                else:  # champion / skip → champion stays
                    self._sort_session["champion_index"] = int(entry.get("prev_champion_index", 0))
                self._sort_session["current_index"] = prev_challenger + 1
                history.append(entry)
                self._save_session_to_disk()
                return {"status": "redone", **self._get_sort_session_flags(history, redo_stack)}

            # Forward action.
            if challenger_index >= total:
                raise HTTPException(status_code=400, detail="Bracket already complete")
            entry = {
                "action": action,
                "mode": SORT_MODE_BRACKET,
                "prev_champion_index": champion_index,
                "prev_challenger_index": challenger_index,
            }
            if action == "challenger":
                self._sort_session["champion_index"] = challenger_index
            # champion / skip: champion stays
            self._sort_session["current_index"] = challenger_index + 1
            history.append(entry)
            # A fresh forward choice invalidates any redo branch.
            self._sort_session["redo_stack"] = []
            self._save_session_to_disk()

            new_challenger = self._sort_session["current_index"]
            return {
                "status": "ok",
                "done": new_challenger >= total,
                "mode": SORT_MODE_BRACKET,
                "champion_index": self._sort_session["champion_index"],
                "challenger_index": new_challenger,
                **self._get_sort_session_flags(history, self._sort_session["redo_stack"]),
            }

    @staticmethod
    def _cull_decisions_from_history(history: List[Dict[str, Any]]) -> Dict[str, str]:
        """Map image_id → final keep/reject decision from cull history.

        Lets a resumed cull session rebuild its client-side decision map so the
        keep/reject routing at finish covers decisions made before a reload
        (history is the server-side source of truth). Later entries win; skips
        are not decisions, so they are omitted.
        """
        decisions: Dict[str, str] = {}
        for entry in history or []:
            action = entry.get("action")
            image_id = entry.get("image_id")
            if image_id is None or action not in ("keep", "reject"):
                continue
            decisions[str(image_id)] = action
        return decisions

    def _get_current_cull_image(self) -> Dict[str, Any]:
        """Return the current single image for 留/汰 Keep-Reject (cull) mode.

        Walks past unreadable images (marking them) so the user never lands on a
        broken file — mirroring the slot/bracket lazy readability handling. Keep
        and reject decisions live in the session history (non-destructive: the
        frontend routes kept→Collection / rejected→opt-in target at finish), so
        the kept/rejected tallies are derived from history.
        """
        while True:
            with self._sort_session_lock:
                if not self._sort_session.get("active"):
                    return {
                        "active": False,
                        "done": True,
                        "mode": SORT_MODE_CULL,
                        "message": "No active sort session",
                        "image": None,
                        "index": 0,
                        "total": 0,
                        "remaining": 0,
                        "kept": 0,
                        "rejected": 0,
                        "decisions": {},
                        **self._get_sort_session_flags([], []),
                    }
                image_ids = self._sort_session["image_ids"]
                total = len(image_ids)
                current_index = int(self._sort_session.get("current_index", 0) or 0)
                history_snapshot = list(self._sort_session.get("history", []))
                redo_snapshot = list(self._sort_session.get("redo_stack", []))

            kept = sum(1 for h in history_snapshot if h.get("action") == "keep")
            rejected = sum(1 for h in history_snapshot if h.get("action") == "reject")
            decisions = self._cull_decisions_from_history(history_snapshot)

            if total == 0 or current_index >= total:
                return {
                    "active": True,
                    "done": True,
                    "mode": SORT_MODE_CULL,
                    "image": None,
                    "index": min(current_index, total),
                    "total": total,
                    "remaining": 0,
                    "kept": kept,
                    "rejected": rejected,
                    "message": "Cull complete" if total else "No images to cull",
                    "decisions": decisions,
                    **self._get_sort_session_flags(history_snapshot, redo_snapshot),
                }

            current = self._resolve_readable_sort_image(image_ids[current_index])
            if current is None:
                with self._sort_session_lock:
                    self._sort_session["current_index"] = current_index + 1
                    self._save_session_to_disk()
                continue

            return {
                "active": True,
                "done": False,
                "mode": SORT_MODE_CULL,
                "image": current,
                "index": current_index,
                "total": total,
                "remaining": total - current_index,
                "kept": kept,
                "rejected": rejected,
                "image_ids": list(image_ids),
                "decisions": decisions,
                **self._get_sort_session_flags(history_snapshot, redo_snapshot),
            }

    def _cull_action(self, action: str) -> Dict[str, Any]:
        """Apply a 留/汰 cull action (keep/reject/skip/undo/redo).

        Non-destructive: keep/reject only record the decision + advance the
        cursor; routing kept→Collection / rejected→opt-in target happens
        client-side at finish (mirrors the bracket winner routing). Decisions
        live in history so undo/redo restore both the cursor and the tally.
        """
        if action not in VALID_CULL_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cull action. Must be one of: {', '.join(VALID_CULL_ACTIONS)}",
            )

        with self._sort_session_lock:
            if not self._sort_session.get("active"):
                raise HTTPException(status_code=400, detail="No active sort session")

            image_ids = self._sort_session["image_ids"]
            total = len(image_ids)
            current_index = int(self._sort_session.get("current_index", 0) or 0)
            history = self._sort_session.setdefault("history", [])
            redo_stack = self._sort_session.setdefault("redo_stack", [])

            if action == "undo":
                if not history:
                    return {"status": "nothing_to_undo", **self._get_sort_session_flags(history, redo_stack)}
                last = history.pop()
                redo_stack.append(last)
                self._sort_session["current_index"] = int(last.get("prev_index", 0))
                self._save_session_to_disk()
                return {
                    "status": "undone",
                    "decision": last.get("action"),
                    "image_id": last.get("image_id"),
                    **self._get_sort_session_flags(history, redo_stack),
                }

            if action == "redo":
                if not redo_stack:
                    return {"status": "nothing_to_redo", **self._get_sort_session_flags(history, redo_stack)}
                entry = redo_stack.pop()
                self._sort_session["current_index"] = int(entry.get("prev_index", 0)) + 1
                history.append(entry)
                self._save_session_to_disk()
                return {
                    "status": "redone",
                    "decision": entry.get("action"),
                    "image_id": entry.get("image_id"),
                    **self._get_sort_session_flags(history, redo_stack),
                }

            # Forward action (keep / reject / skip).
            if current_index >= total:
                raise HTTPException(status_code=400, detail="Cull already complete")
            image_id = image_ids[current_index]
            entry = {
                "action": action,
                "mode": SORT_MODE_CULL,
                "image_id": image_id,
                "prev_index": current_index,
            }
            self._sort_session["current_index"] = current_index + 1
            history.append(entry)
            # A fresh forward choice invalidates any redo branch.
            self._sort_session["redo_stack"] = []
            self._save_session_to_disk()

            new_index = self._sort_session["current_index"]
            kept = sum(1 for h in history if h.get("action") == "keep")
            rejected = sum(1 for h in history if h.get("action") == "reject")
            return {
                "status": "ok",
                "done": new_index >= total,
                "mode": SORT_MODE_CULL,
                "decision": action,
                "image_id": image_id,
                "index": new_index,
                "total": total,
                "kept": kept,
                "rejected": rejected,
                **self._get_sort_session_flags(history, self._sort_session["redo_stack"]),
            }
