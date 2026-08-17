"""
Prompt Lab service for category/tag-set/exclusion/preset/prompt workflows.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

import database as db
from prompt_generator import get_generator as default_get_generator
from tag_rules import categorize_tags_batch


_RECIPE_TOKEN_EXCLUDES = (
    "negative prompt",
    "steps:",
    "cfg scale",
    "cfg:",
    "sampler:",
    "scheduler:",
    "seed:",
    "size:",
    "model hash",
    "output format",
    "generation time",
)

# Minimum scored images sharing a checkpoint before its average score is
# reported. Exposed in the payload so an empty "Best Checkpoints" panel can
# say what it is still waiting for instead of guessing.
MIN_SCORED_IMAGES_PER_CHECKPOINT = 3

# Why a checkpoint panel is empty. Each one implies a different remedy — or
# honestly, no remedy at all — so they are never collapsed into one message.
REASON_NO_CHECKPOINT_METADATA = "no_checkpoint_metadata"
REASON_CHECKPOINTS_ONLY_ON_MISSING_FILES = "checkpoint_metadata_only_on_missing_files"
REASON_NO_SCORED_IMAGES = "no_scored_images"
REASON_NOT_ENOUGH_SCORED_PER_CHECKPOINT = "not_enough_scored_images_per_checkpoint"

# Generator ids the metadata parser records when no SD tool claimed the image:
# "unknown" means nothing was found, "others" means text was found but no
# detector recognized it (metadata_parser/__init__.py:1031-1033). Prompt text
# on such a row is therefore not evidence of an SD generation prompt.
_UNATTRIBUTED_GENERATORS = ("unknown", "others")


def _is_useful_recipe_token(token: str) -> bool:
    text = str(token or "").strip().lower()
    if not text or len(text) < 2:
        return False
    return not any(excluded in text for excluded in _RECIPE_TOKEN_EXCLUDES)


def _usable_clause(alias: str = "") -> str:
    """SQL for "this image is usable" — the project-wide readability guard."""
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}is_readable, 1) = 1"


def _column_exists(cursor: Any, table: str, column: str) -> bool:
    return any(row[1] == column for row in cursor.execute(f"PRAGMA table_info({table})").fetchall())


def _text_length_stats(cursor: Any, column: str, *, scope: str) -> Dict[str, Any]:
    """Length statistics for one text column over usable images only.

    ``sample`` travels with the numbers so the reader can tell an average of
    two rows from an average of two thousand.
    """
    row = cursor.execute(
        f"SELECT COUNT(*), AVG(LENGTH({column})), MAX(LENGTH({column})), MIN(LENGTH({column})) "
        f"FROM images WHERE {column} IS NOT NULL AND {column} != '' AND {_usable_clause()}"
    ).fetchone()
    return {
        "avg": round(row[1] or 0),
        "max": row[2] or 0,
        "min": row[3] or 0,
        "sample": row[0] or 0,
        "scope": scope,
    }


def _checkpoint_availability_reason(any_count: int, usable_count: int) -> Optional[str]:
    """Why no usable image can contribute a checkpoint, or None if some can."""
    if usable_count:
        return None
    if any_count:
        return REASON_CHECKPOINTS_ONLY_ON_MISSING_FILES
    return REASON_NO_CHECKPOINT_METADATA


def _missing_file_compare_detail(filenames: List[str]) -> str:
    """One short, path-free line naming the file(s) that are gone.

    Reaches the user verbatim: the API layer returns ``detail`` unchanged and
    Prompt Lab's compare handler toasts it.
    """
    quoted = [f"'{name}'" for name in filenames]
    if len(quoted) == 1:
        subject = f"{quoted[0]} is missing from disk"
        advice = "Rescan the folder or pick another image."
    else:
        subject = f"{' and '.join(quoted)} are missing from disk"
        advice = "Rescan the folder or pick other images."
    return f"Cannot compare: {subject}, so the prompts cannot be checked against the pictures. {advice}"


def _image_row_is_usable(row: Dict[str, Any]) -> bool:
    value = row.get("is_readable")
    return True if value is None else bool(value)


def _normalize_prompt_resource_ref(value: Any) -> str:
    return str(value or "").strip()


def _normalize_tag_lookup_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


class PromptService:
    """Service wrapper for Prompt Lab routes."""

    def __init__(self, generator_getter: Optional[Callable[..., Any]] = None) -> None:
        self._generator_getter = generator_getter or default_get_generator

    def set_generator_getter(self, generator_getter: Callable[..., Any]) -> None:
        self._generator_getter = generator_getter

    def _generator(self) -> Any:
        return self._generator_getter(db)

    def list_categories(self) -> Dict[str, Any]:
        gen = self._generator()
        pool = gen.get_tag_pool()
        result = {}
        for category, tags in pool.items():
            ordered_tags = sorted(tags, key=lambda x: x["count"], reverse=True)
            result[category] = [t["tag"] for t in ordered_tags]
        return {"categories": result}

    def get_category_tags(self, name: str, limit: int, offset: int) -> Dict[str, Any]:
        gen = self._generator()
        pool = gen.get_tag_pool()
        if name not in pool:
            raise HTTPException(status_code=404, detail=f"Category '{name}' not found")

        tags = sorted(pool[name], key=lambda x: x["count"], reverse=True)
        total = len(tags)
        page = tags[offset:offset + limit]
        return {
            "category": name,
            "total": total,
            "tags": page,
        }

    def categorize_tags(self, tags: List[str]) -> Dict[str, Any]:
        results = categorize_tags_batch(tags)
        requested_by_key: Dict[str, List[str]] = {}
        for tag in results:
            key = _normalize_tag_lookup_key(tag)
            if key:
                requested_by_key.setdefault(key, []).append(tag)

        if requested_by_key:
            with db.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT tag, category FROM tag_categories WHERE is_user_defined = 1")
                for row in cursor.fetchall():
                    override_key = _normalize_tag_lookup_key(row[0])
                    override_category = str(row[1] or "").strip()
                    if not override_key or not override_category:
                        continue
                    for requested_tag in requested_by_key.get(override_key, []):
                        results[requested_tag] = override_category

        return {"results": [{"tag": tag, "category": category} for tag, category in results.items()]}

    def recategorize_tag(self, tag: str, category: str) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO tag_categories (tag, category, is_user_defined)
                   VALUES (?, ?, 1)""",
                (tag, category),
            )
        gen = self._generator()
        gen.load_from_db()
        return {"tag": tag, "category": category, "saved": True}

    def list_tag_sets(self) -> Dict[str, Any]:
        gen = self._generator()
        all_sets = gen.get_all_tag_sets()
        return {
            "sets": [
                {
                    "id": item.get("id", index + 1),
                    "name": item["name"],
                    "category": item["category"],
                    "description": item.get("description", ""),
                    "tag_count": len(item["tags"]),
                    "members": [
                        {
                            "tag": member["tag"] if isinstance(member, dict) else member,
                            "category": item["category"],
                            "weight": member.get("weight", 1.0) if isinstance(member, dict) else 1.0,
                            "required": member.get("required", True) if isinstance(member, dict) else True,
                        }
                        for member in item["tags"]
                    ],
                    "tags": item["tags"],
                }
                for index, item in enumerate(all_sets)
            ],
            "total": len(all_sets),
        }

    def create_tag_set(
        self,
        *,
        name: str,
        description: str,
        category: str,
        tags: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tag_sets (name, description, category) VALUES (?, ?, ?)",
                (name, description, category),
            )
            set_id = cursor.lastrowid
            for member in tags:
                cursor.execute(
                    "INSERT INTO tag_set_members (set_id, tag, weight, is_required) VALUES (?, ?, ?, ?)",
                    (set_id, member["tag"], member.get("weight", 1.0), int(bool(member.get("required", True)))),
                )
        gen = self._generator()
        gen.load_from_db()
        return {"id": set_id, "name": name, "created": True}

    def delete_tag_set(self, set_ref: str) -> Dict[str, Any]:
        normalized_ref = _normalize_prompt_resource_ref(set_ref)
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name FROM tag_sets WHERE CAST(id AS TEXT) = ? OR name = ?",
                (normalized_ref, normalized_ref),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Tag set '{set_ref}' not found")
            set_id, set_name = row[0], row[1]
            cursor.execute("DELETE FROM tag_set_members WHERE set_id = ?", (set_id,))
            cursor.execute("DELETE FROM tag_sets WHERE id = ?", (set_id,))
        gen = self._generator()
        gen.load_from_db()
        return {"deleted": True, "id": set_id, "name": set_name}

    def list_exclusion_rules(self) -> Dict[str, Any]:
        gen = self._generator()
        all_rules = gen.get_all_rules()
        return {
            "rules": [
                {
                    "id": rule.get("id"),
                    "name": rule["name"],
                    "description": rule.get("description", ""),
                    "conditions": [
                        {"tag": condition.get("tag", condition.get("condition_tag", "")), "type": condition.get("type", condition.get("condition_type", "present"))}
                        for condition in rule.get("conditions", [])
                    ],
                    "targets": [
                        {"tag": target.get("tag", target.get("excluded_tag", "")), "category": target.get("category", target.get("excluded_category", ""))}
                        for target in rule.get("targets", [])
                    ],
                }
                for rule in all_rules
            ],
            "total": len(all_rules),
        }

    def create_exclusion_rule(
        self,
        *,
        rule_name: str,
        description: str,
        conditions: List[Dict[str, str]],
        targets: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tag_exclusions (rule_name, description) VALUES (?, ?)",
                (rule_name, description),
            )
            rule_id = cursor.lastrowid
            for condition in conditions:
                cursor.execute(
                    "INSERT INTO tag_exclusion_conditions (exclusion_id, condition_tag, condition_type) VALUES (?, ?, ?)",
                    (rule_id, condition["tag"], condition["type"]),
                )
            for target in targets:
                cursor.execute(
                    "INSERT INTO tag_exclusion_targets (exclusion_id, excluded_tag, excluded_category) VALUES (?, ?, ?)",
                    (rule_id, target.get("tag", ""), target.get("category", "")),
                )
        gen = self._generator()
        gen.load_from_db()
        return {"id": rule_id, "name": rule_name, "created": True}

    def delete_exclusion_rule(self, rule_ref: str) -> Dict[str, Any]:
        normalized_ref = _normalize_prompt_resource_ref(rule_ref)
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, rule_name FROM tag_exclusions WHERE CAST(id AS TEXT) = ? OR rule_name = ?",
                (normalized_ref, normalized_ref),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Exclusion rule '{rule_ref}' not found")
            rule_id, rule_name = row[0], row[1]
            cursor.execute("DELETE FROM tag_exclusion_conditions WHERE exclusion_id = ?", (rule_id,))
            cursor.execute("DELETE FROM tag_exclusion_targets WHERE exclusion_id = ?", (rule_id,))
            cursor.execute("DELETE FROM tag_exclusions WHERE id = ?", (rule_id,))
        gen = self._generator()
        gen.load_from_db()
        return {"deleted": True, "id": rule_id, "name": rule_name}

    def generate_prompt(self, config: Dict[str, Any]) -> Dict[str, Any]:
        gen = self._generator()

        try:
            count = int(config.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 20))
        base_seed = config.get("seed")

        results: List[Dict[str, Any]] = []
        for index in range(count):
            iteration_config = dict(config)
            if base_seed is not None:
                # Vary the seed per slot so a fixed seed still yields distinct
                # prompts, while the whole batch stays reproducible.
                iteration_config["seed"] = base_seed + index
            result = gen.generate(iteration_config)
            result.setdefault("prompt", result.get("positive_prompt", ""))
            results.append(result)

        response = dict(results[0])
        response["count"] = len(results)
        response["prompts"] = results
        return response

    def validate_prompt(self, tags: List[str]) -> Dict[str, Any]:
        gen = self._generator()
        return gen.validate_prompt(tags)

    def list_presets(self) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, config_json, created_at FROM prompt_presets ORDER BY created_at DESC")
            rows = cursor.fetchall()
        return {
            "presets": [
                {
                    "id": row[0],
                    "name": row[1],
                    "config": json.loads(row[2]),
                    "created_at": row[3],
                }
                for row in rows
            ],
        }

    def save_preset(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO prompt_presets (name, config_json) VALUES (?, ?)",
                (name, json.dumps(config)),
            )
            preset_id = cursor.lastrowid
        return {"id": preset_id, "name": name, "saved": True}

    def delete_preset(self, preset_id: int) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prompt_presets WHERE id = ?", (preset_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Preset not found")
        return {"deleted": True}

    def get_prompt_stats(
        self,
        *,
        tag_limit: int,
        high_tag_limit: int,
        checkpoint_limit: int,
        leader_limit: int,
        recipe_limit: int,
        scored_limit: int,
    ) -> Dict[str, Any]:
        with db.get_db() as conn:
            cursor = conn.cursor()
            effective_checkpoint_limit = max(checkpoint_limit, recipe_limit)
            effective_leader_limit = max(leader_limit, recipe_limit)

            total = cursor.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            usable_total = cursor.execute(
                f"SELECT COUNT(*) FROM images WHERE {_usable_clause()}"
            ).fetchone()[0]

            # Tagging never runs on a file that is gone, so a dead row can only
            # ever land in the denominator. Both halves of the fraction are
            # therefore restricted to usable images, and the denominator ships
            # with the payload so the label can name what it divided by.
            tagged_images = cursor.execute(
                "SELECT COUNT(DISTINCT t.image_id) FROM tags t "
                "INNER JOIN images i ON i.id = t.image_id "
                f"WHERE {_usable_clause('i')}"
            ).fetchone()[0]

            top_tags_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT t.tag FROM tags t INNER JOIN images i ON i.id = t.image_id "
                f"WHERE {_usable_clause('i')} GROUP BY t.tag"
                ")"
            ).fetchone()[0]
            top_tags = []
            for row in cursor.execute(
                "SELECT t.tag, COUNT(*) as cnt FROM tags t "
                "INNER JOIN images i ON i.id = t.image_id "
                f"WHERE {_usable_clause('i')} "
                "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?",
                (tag_limit,),
            ).fetchall():
                top_tags.append({
                    "tag": row[0],
                    "count": row[1],
                    "pct": round(row[1] / max(usable_total, 1) * 100, 1),
                })

            scored = cursor.execute(
                "SELECT COUNT(*) FROM images WHERE aesthetic_score IS NOT NULL"
            ).fetchone()[0]

            # Each example card renders a thumbnail and offers Build / Reader /
            # Preview on its id, so rows whose file is gone are left out and
            # counted separately — the headline "scored images" stat above still
            # reports every scored row on record.
            scored_available = cursor.execute(
                "SELECT COUNT(*) FROM images "
                f"WHERE aesthetic_score IS NOT NULL AND {_usable_clause()}"
            ).fetchone()[0]

            checkpoints_any = cursor.execute(
                "SELECT COUNT(*) FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != ''"
            ).fetchone()[0]
            checkpoints_usable = cursor.execute(
                "SELECT COUNT(*) FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' "
                f"AND {_usable_clause()}"
            ).fetchone()[0]

            top_checkpoints_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT checkpoint_normalized FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' "
                f"AND {_usable_clause()} "
                "GROUP BY checkpoint_normalized"
                ")"
            ).fetchone()[0]
            top_checkpoints = []
            for row in cursor.execute(
                "SELECT checkpoint_normalized, COUNT(*) as cnt FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' "
                f"AND {_usable_clause()} "
                "GROUP BY checkpoint_normalized "
                "ORDER BY cnt DESC, checkpoint_normalized COLLATE NOCASE ASC LIMIT ?",
                (effective_checkpoint_limit,),
            ).fetchall():
                checkpoint_name = str(row[0] or "").strip()
                if not checkpoint_name:
                    continue
                top_checkpoints.append({"name": checkpoint_name, "count": row[1]})

            checkpoint_score_leaders_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT checkpoint_normalized FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' AND aesthetic_score IS NOT NULL "
                f"AND {_usable_clause()} "
                "GROUP BY checkpoint_normalized "
                "HAVING COUNT(*) >= ?"
                ")",
                (MIN_SCORED_IMAGES_PER_CHECKPOINT,),
            ).fetchone()[0]
            checkpoint_score_leaders = []
            for row in cursor.execute(
                "SELECT checkpoint_normalized, AVG(aesthetic_score) as avg_score, COUNT(*) as cnt "
                "FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' AND aesthetic_score IS NOT NULL "
                f"AND {_usable_clause()} "
                "GROUP BY checkpoint_normalized "
                "HAVING COUNT(*) >= ? "
                "ORDER BY avg_score DESC, cnt DESC, checkpoint_normalized COLLATE NOCASE ASC "
                "LIMIT ?",
                (MIN_SCORED_IMAGES_PER_CHECKPOINT, effective_leader_limit),
            ).fetchall():
                checkpoint_name = str(row[0] or "").strip()
                if not checkpoint_name:
                    continue
                checkpoint_score_leaders.append({
                    "name": checkpoint_name,
                    "avg_score": round(row[1] or 0, 2),
                    "count": row[2],
                })

            recipe_sources = checkpoint_score_leaders[:recipe_limit] if checkpoint_score_leaders else top_checkpoints[:recipe_limit]
            checkpoint_recipes_total = checkpoint_score_leaders_total if checkpoint_score_leaders_total else top_checkpoints_total
            checkpoint_recipes = []
            for leader in recipe_sources:
                recipe_tags = []
                tag_query = (
                    "SELECT t.tag, COUNT(*) as cnt "
                    "FROM tags t "
                    "INNER JOIN images i ON t.image_id = i.id "
                    "WHERE i.checkpoint_normalized = ? COLLATE NOCASE "
                    f"AND {_usable_clause('i')} "
                )
                if leader.get("avg_score") is not None:
                    tag_query += "AND i.aesthetic_score IS NOT NULL "
                tag_query += "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?"

                for row in cursor.execute(tag_query, (leader["name"], recipe_limit)).fetchall():
                    if _is_useful_recipe_token(row[0]):
                        recipe_tags.append(row[0])

                if not recipe_tags:
                    # Deliberately mines `prompt` only. Sidecar caption text
                    # lives in `sidecar_caption` (migration 042) precisely so it
                    # is not mistaken for generation input, and presenting
                    # somebody's .txt tag list as "use this checkpoint with
                    # these tags" would undo that separation.
                    prompt_counts: Dict[str, int] = {}
                    for row in cursor.execute(
                        "SELECT prompt FROM images "
                        "WHERE checkpoint_normalized = ? COLLATE NOCASE "
                        f"AND {_usable_clause()} "
                        "AND prompt IS NOT NULL AND prompt != '' LIMIT 1000",
                        (leader["name"],),
                    ).fetchall():
                        for token in db.extract_prompt_tokens(row[0]):
                            if _is_useful_recipe_token(token):
                                prompt_counts[token] = prompt_counts.get(token, 0) + 1

                    recipe_tags = [
                        token for token, _count in sorted(
                            prompt_counts.items(),
                            key=lambda item: item[1],
                            reverse=True,
                        )[:recipe_limit]
                    ]

                checkpoint_recipes.append({
                    "name": leader["name"],
                    "avg_score": leader.get("avg_score"),
                    "count": leader["count"],
                    "tags": recipe_tags,
                })

            # Two different kinds of text, measured apart and never averaged
            # together: `prompt` is generation input, `sidecar_caption` is a
            # .txt/.json tag list the user happened to keep beside the image.
            prompt_length = _text_length_stats(
                cursor, "prompt", scope="usable_images_with_prompt_text"
            )
            # How much of that prompt text an SD generator actually claimed. On
            # a library of sidecar-derived tag text this is 0, which is the one
            # fact that stops the label reading as "your SD prompts".
            prompt_length["sd_attributed_sample"] = cursor.execute(
                "SELECT COUNT(*) FROM images "
                "WHERE prompt IS NOT NULL AND prompt != '' "
                f"AND {_usable_clause()} "
                "AND generator IS NOT NULL AND TRIM(generator) != '' "
                f"AND LOWER(TRIM(generator)) NOT IN ({','.join('?' * len(_UNATTRIBUTED_GENERATORS))})",
                _UNATTRIBUTED_GENERATORS,
            ).fetchone()[0]

            # Migration 042 is additive and writes no row, so a database can
            # reach here either without the column at all or with the column
            # present and still empty. Both must report honestly.
            if _column_exists(cursor, "images", "sidecar_caption"):
                caption_length = _text_length_stats(
                    cursor, "sidecar_caption", scope="usable_images_with_sidecar_caption"
                )
                caption_length["available"] = True
            else:
                caption_length = {
                    "avg": 0,
                    "max": 0,
                    "min": 0,
                    "sample": 0,
                    "scope": "usable_images_with_sidecar_caption",
                    "available": False,
                }

            high_score_tags_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT t.tag FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                f"WHERE i.aesthetic_score >= 7 AND {_usable_clause('i')} "
                "GROUP BY t.tag"
                ")"
            ).fetchone()[0]
            high_score_tags = []
            for row in cursor.execute(
                "SELECT t.tag, COUNT(*) as cnt FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                f"WHERE i.aesthetic_score >= 7 AND {_usable_clause('i')} "
                "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?",
                (high_tag_limit,),
            ).fetchall():
                high_score_tags.append({"tag": row[0], "count": row[1]})

            low_score_tags = []
            for row in cursor.execute(
                "SELECT t.tag, COUNT(*) as cnt FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                "WHERE i.aesthetic_score IS NOT NULL AND i.aesthetic_score < 4 "
                f"AND {_usable_clause('i')} "
                "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?",
                (high_tag_limit,),
            ).fetchall():
                low_score_tags.append({"tag": row[0], "count": row[1]})

            top_scored_images = []
            for row in cursor.execute(
                "SELECT id, filename, checkpoint, prompt, aesthetic_score "
                "FROM images "
                "WHERE aesthetic_score IS NOT NULL AND COALESCE(is_readable, 1) = 1 "
                "ORDER BY aesthetic_score DESC, id DESC "
                "LIMIT ?",
                (scored_limit,),
            ).fetchall():
                top_scored_images.append({
                    "id": row[0],
                    "filename": row[1],
                    "checkpoint": row[2],
                    "prompt": row[3] or "",
                    "aesthetic_score": row[4],
                })

        # An empty panel has to explain itself, because the remedies differ and
        # some panels have none: nothing in this app can invent a checkpoint for
        # an image that was never generated by Stable Diffusion, so telling the
        # user to import more images would cost him a long operation and fail.
        checkpoint_availability = _checkpoint_availability_reason(
            checkpoints_any, checkpoints_usable
        )
        top_checkpoints_empty_reason = None if top_checkpoints else checkpoint_availability
        if checkpoint_score_leaders:
            leaders_empty_reason = None
        elif checkpoint_availability is not None:
            leaders_empty_reason = checkpoint_availability
        elif not scored_available:
            leaders_empty_reason = REASON_NO_SCORED_IMAGES
        else:
            leaders_empty_reason = REASON_NOT_ENOUGH_SCORED_PER_CHECKPOINT

        return {
            "total_images": total,
            "usable_images": usable_total,
            "tagged_images": tagged_images,
            "scored_images": scored,
            "top_tags": top_tags,
            "top_tags_total": top_tags_total,
            "top_tags_has_more": top_tags_total > len(top_tags),
            # What `pct` was divided by, so the chart can state its scope
            # instead of implying a share of the whole library.
            "top_tags_denominator": usable_total,
            "top_tags_denominator_basis": "usable_images",
            "checkpoint_coverage": {
                "total_images": total,
                "usable_images": usable_total,
                "images_with_checkpoint": checkpoints_usable,
                "images_with_checkpoint_any": checkpoints_any,
                "scored_usable_images": scored_available,
                "min_scored_images_per_checkpoint": MIN_SCORED_IMAGES_PER_CHECKPOINT,
            },
            "top_checkpoints_empty_reason": top_checkpoints_empty_reason,
            "checkpoint_score_leaders_empty_reason": leaders_empty_reason,
            "checkpoint_recipes_empty_reason": (
                None if checkpoint_recipes else top_checkpoints_empty_reason
            ),
            "top_checkpoints": top_checkpoints,
            "top_checkpoints_total": top_checkpoints_total,
            "top_checkpoints_has_more": top_checkpoints_total > len(top_checkpoints),
            "checkpoint_score_leaders": checkpoint_score_leaders,
            "checkpoint_score_leaders_total": checkpoint_score_leaders_total,
            "checkpoint_score_leaders_has_more": checkpoint_score_leaders_total > len(checkpoint_score_leaders),
            "checkpoint_recipes": checkpoint_recipes,
            "checkpoint_recipes_total": checkpoint_recipes_total,
            "checkpoint_recipes_has_more": checkpoint_recipes_total > len(checkpoint_recipes),
            "prompt_length": prompt_length,
            "caption_length": caption_length,
            "high_aesthetic_tags": high_score_tags,
            "high_aesthetic_tags_total": high_score_tags_total,
            "high_aesthetic_tags_has_more": high_score_tags_total > len(high_score_tags),
            "low_aesthetic_tags": low_score_tags,
            "top_scored_images": top_scored_images,
            "top_scored_images_total": scored_available,
            "top_scored_images_has_more": scored_available > len(top_scored_images),
        }

    def compare_prompts(self, *, id_a: int, id_b: int) -> Dict[str, Any]:
        img_a = db.get_image_by_id(id_a)
        img_b = db.get_image_by_id(id_b)
        if not img_a or not img_b:
            raise HTTPException(status_code=404, detail="One or both images not found")

        # Compare renders both thumbnails and offers "Open in Build" on each id.
        # Against a file that is gone all three are broken, and the diff itself
        # invites the user to reason about a picture he cannot see — so refuse
        # and name the file instead of producing a confident-looking result.
        missing_filenames: List[str] = []
        for image_row in (img_a, img_b):
            if _image_row_is_usable(image_row):
                continue
            filename = str(image_row.get("filename") or "").strip() or "this image"
            if filename not in missing_filenames:
                missing_filenames.append(filename)
        if missing_filenames:
            raise HTTPException(
                status_code=409,
                detail=_missing_file_compare_detail(missing_filenames),
            )

        tags_a = set(tag["tag"] for tag in db.get_image_tags(id_a))
        tags_b = set(tag["tag"] for tag in db.get_image_tags(id_b))

        prompt_a = img_a.get("prompt") or ""
        prompt_b = img_b.get("prompt") or ""

        tokens_a = set(token.strip() for token in prompt_a.split(",") if token.strip())
        tokens_b = set(token.strip() for token in prompt_b.split(",") if token.strip())

        return {
            "image_a": {
                "id": id_a,
                "filename": img_a["filename"],
                "prompt": prompt_a,
                "checkpoint": img_a.get("checkpoint"),
                "aesthetic_score": img_a.get("aesthetic_score"),
            },
            "image_b": {
                "id": id_b,
                "filename": img_b["filename"],
                "prompt": prompt_b,
                "checkpoint": img_b.get("checkpoint"),
                "aesthetic_score": img_b.get("aesthetic_score"),
            },
            "tags_common": sorted(tags_a & tags_b),
            "tags_only_a": sorted(tags_a - tags_b),
            "tags_only_b": sorted(tags_b - tags_a),
            "prompt_common": sorted(tokens_a & tokens_b),
            "prompt_only_a": sorted(tokens_a - tokens_b),
            "prompt_only_b": sorted(tokens_b - tokens_a),
        }
