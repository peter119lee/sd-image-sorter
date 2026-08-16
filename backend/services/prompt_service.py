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


def _is_useful_recipe_token(token: str) -> bool:
    text = str(token or "").strip().lower()
    if not text or len(text) < 2:
        return False
    return not any(excluded in text for excluded in _RECIPE_TOKEN_EXCLUDES)


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

            top_tags_total = cursor.execute(
                "SELECT COUNT(*) FROM (SELECT tag FROM tags GROUP BY tag)"
            ).fetchone()[0]
            top_tags = []
            for row in cursor.execute(
                "SELECT tag, COUNT(*) as cnt FROM tags GROUP BY tag ORDER BY cnt DESC LIMIT ?",
                (tag_limit,),
            ).fetchall():
                top_tags.append({"tag": row[0], "count": row[1], "pct": round(row[1] / max(total, 1) * 100, 1)})

            top_checkpoints_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT checkpoint_normalized FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' "
                "GROUP BY checkpoint_normalized"
                ")"
            ).fetchone()[0]
            top_checkpoints = []
            for row in cursor.execute(
                "SELECT checkpoint_normalized, COUNT(*) as cnt FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' "
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
                "GROUP BY checkpoint_normalized "
                "HAVING COUNT(*) >= 3"
                ")"
            ).fetchone()[0]
            checkpoint_score_leaders = []
            for row in cursor.execute(
                "SELECT checkpoint_normalized, AVG(aesthetic_score) as avg_score, COUNT(*) as cnt "
                "FROM images "
                "WHERE checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '' AND aesthetic_score IS NOT NULL "
                "GROUP BY checkpoint_normalized "
                "HAVING COUNT(*) >= 3 "
                "ORDER BY avg_score DESC, cnt DESC, checkpoint_normalized COLLATE NOCASE ASC "
                "LIMIT ?",
                (effective_leader_limit,),
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
                )
                if leader.get("avg_score") is not None:
                    tag_query += "AND i.aesthetic_score IS NOT NULL "
                tag_query += "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?"

                for row in cursor.execute(tag_query, (leader["name"], recipe_limit)).fetchall():
                    if _is_useful_recipe_token(row[0]):
                        recipe_tags.append(row[0])

                if not recipe_tags:
                    prompt_counts: Dict[str, int] = {}
                    for row in cursor.execute(
                        "SELECT prompt FROM images "
                        "WHERE checkpoint_normalized = ? COLLATE NOCASE "
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

            prompt_stats_row = cursor.execute(
                "SELECT AVG(LENGTH(prompt)), MAX(LENGTH(prompt)), MIN(LENGTH(CASE WHEN prompt IS NOT NULL AND prompt != '' THEN prompt END)) "
                "FROM images WHERE prompt IS NOT NULL AND prompt != ''"
            ).fetchone()
            avg_len = round(prompt_stats_row[0] or 0)
            max_len = prompt_stats_row[1] or 0
            min_len = prompt_stats_row[2] or 0

            high_score_tags_total = cursor.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT t.tag FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                "WHERE i.aesthetic_score >= 7 "
                "GROUP BY t.tag"
                ")"
            ).fetchone()[0]
            high_score_tags = []
            for row in cursor.execute(
                "SELECT t.tag, COUNT(*) as cnt FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                "WHERE i.aesthetic_score >= 7 "
                "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?",
                (high_tag_limit,),
            ).fetchall():
                high_score_tags.append({"tag": row[0], "count": row[1]})

            low_score_tags = []
            for row in cursor.execute(
                "SELECT t.tag, COUNT(*) as cnt FROM tags t "
                "INNER JOIN images i ON t.image_id = i.id "
                "WHERE i.aesthetic_score IS NOT NULL AND i.aesthetic_score < 4 "
                "GROUP BY t.tag ORDER BY cnt DESC LIMIT ?",
                (high_tag_limit,),
            ).fetchall():
                low_score_tags.append({"tag": row[0], "count": row[1]})

            scored = cursor.execute("SELECT COUNT(*) FROM images WHERE aesthetic_score IS NOT NULL").fetchone()[0]

            # Each example card renders a thumbnail and offers Build / Reader /
            # Preview on its id, so rows whose file is gone are left out and
            # counted separately — the headline "scored images" stat above still
            # reports every scored row on record.
            scored_available = cursor.execute(
                "SELECT COUNT(*) FROM images "
                "WHERE aesthetic_score IS NOT NULL AND COALESCE(is_readable, 1) = 1"
            ).fetchone()[0]

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

        return {
            "total_images": total,
            "scored_images": scored,
            "top_tags": top_tags,
            "top_tags_total": top_tags_total,
            "top_tags_has_more": top_tags_total > len(top_tags),
            "top_checkpoints": top_checkpoints,
            "top_checkpoints_total": top_checkpoints_total,
            "top_checkpoints_has_more": top_checkpoints_total > len(top_checkpoints),
            "checkpoint_score_leaders": checkpoint_score_leaders,
            "checkpoint_score_leaders_total": checkpoint_score_leaders_total,
            "checkpoint_score_leaders_has_more": checkpoint_score_leaders_total > len(checkpoint_score_leaders),
            "checkpoint_recipes": checkpoint_recipes,
            "checkpoint_recipes_total": checkpoint_recipes_total,
            "checkpoint_recipes_has_more": checkpoint_recipes_total > len(checkpoint_recipes),
            "prompt_length": {"avg": avg_len, "max": max_len, "min": min_len},
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
