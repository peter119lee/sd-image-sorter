#!/usr/bin/env python3
"""Derive MIT-licensed bundled Danbooru assets from StoryAura CSVs.

Source: https://huggingface.co/datasets/StoryAura/Danbooru-Dataset-csv (MIT).
The raw dumps are NOT vendored. This script writes compact files that the
app actually loads:

- backend/assets/danbooru_zh.csv
- backend/assets/danbooru_characters.csv
- backend/assets/danbooru_character_names.txt
- backend/assets/danbooru_implications_ext.csv
- backend/assets/STORYAURA_LICENSE.txt

Usage:
  python scripts/build_danbooru_assets.py --source tmp/storyaura
  python scripts/build_danbooru_assets.py --download --source tmp/storyaura
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "backend" / "assets"

DATASET_BASE = "https://huggingface.co/datasets/StoryAura/Danbooru-Dataset-csv/resolve/main"
SOURCE_FILES = {
    "general.csv": "danbooru_dataset_general_260820.csv",
    "character.csv": "danbooru_character_tags.csv",
    "LICENSE": "LICENSE",
}

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
MAX_ALIASES = 8
CHAR_CJK_MIN_POSTS = 10
CHAR_META_MIN_POSTS = 20
CHAR_META_WITH_COPYRIGHT_MIN_POSTS = 5
CHAR_CLASSIFY_MIN_POSTS = 50
CHAR_IMPL_MIN_POSTS = 10
EXTRA_VOCAB_MIN_POSTS = 50

NON_CHARACTER_VOCAB_CODES = {0, 1, 5}


def _normalize_tag(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def _split_names(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        name = part.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _cjk_aliases(raw: str, cap: int = MAX_ALIASES) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in _split_names(raw):
        if not CJK_RE.search(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= cap:
            break
    return out


def _int_count(raw: str) -> int:
    try:
        return int(str(raw or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0


def _load_vocab_codes(path: Path) -> dict[str, int]:
    codes: dict[str, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            tag = _normalize_tag(row[0])
            if not tag:
                continue
            try:
                codes[tag] = int(row[1])
            except (TypeError, ValueError):
                codes[tag] = 0
    return codes


def _download(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    for local_name, remote_name in SOURCE_FILES.items():
        dest = source_dir / local_name
        url = f"{DATASET_BASE}/{remote_name}"
        print(f"Downloading {url} -> {dest}")
        with urllib.request.urlopen(url) as response, dest.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def _merge_aliases(store: dict[str, list[str]], tag: str, aliases: list[str]) -> None:
    if not tag or not aliases:
        return
    existing = store.setdefault(tag, [])
    seen = {name.casefold() for name in existing}
    for name in aliases:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        existing.append(name)
        if len(existing) >= MAX_ALIASES:
            break


def build(source_dir: Path) -> dict[str, int]:
    general_path = source_dir / "general.csv"
    character_path = source_dir / "character.csv"
    license_path = source_dir / "LICENSE"
    vocab_path = ASSETS / "danbooru_tags.csv"
    if not general_path.is_file() or not character_path.is_file():
        raise SystemExit(
            f"Missing StoryAura CSVs in {source_dir}. Pass --download or --source."
        )
    if not vocab_path.is_file():
        raise SystemExit(f"Missing bundled vocabulary at {vocab_path}")

    vocab_codes = _load_vocab_codes(vocab_path)
    zh: dict[str, list[str]] = {}
    implications: list[tuple[str, str]] = []
    impl_seen: set[tuple[str, str]] = set()

    def add_impl(child: str, parent_field: str) -> None:
        child_tag = _normalize_tag(child)
        if not child_tag:
            return
        for parent in _split_names(parent_field):
            parent_tag = _normalize_tag(parent)
            if not parent_tag or parent_tag == child_tag:
                continue
            pair = (child_tag, parent_tag)
            if pair in impl_seen:
                continue
            impl_seen.add(pair)
            implications.append(pair)

    with general_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tag = _normalize_tag(row.get("tag") or "")
            _merge_aliases(zh, tag, _cjk_aliases(row.get("other_names") or ""))
            add_impl(tag, row.get("parent_tag") or "")

    characters: list[tuple[str, str, str, int]] = []
    classify_names: list[str] = []
    with character_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tag = _normalize_tag(row.get("character_tag") or "")
            if not tag:
                continue
            aliases = _cjk_aliases(row.get("other_names") or "")
            copyright_ = (row.get("copyright") or "").strip()
            parent = (row.get("parent_tag") or "").strip()
            post_count = _int_count(row.get("post_count") or "0")
            if aliases and post_count >= CHAR_CJK_MIN_POSTS:
                _merge_aliases(zh, tag, aliases)
            keep_meta = post_count >= CHAR_META_MIN_POSTS or (
                bool(copyright_ or parent)
                and post_count >= CHAR_META_WITH_COPYRIGHT_MIN_POSTS
            )
            if keep_meta:
                characters.append((tag, copyright_, parent, post_count))
            if post_count >= CHAR_IMPL_MIN_POSTS:
                add_impl(tag, parent)
            vocab_code = vocab_codes.get(tag)
            if post_count >= CHAR_CLASSIFY_MIN_POSTS and vocab_code not in NON_CHARACTER_VOCAB_CODES:
                classify_names.append(tag)

    ASSETS.mkdir(parents=True, exist_ok=True)

    zh_path = ASSETS / "danbooru_zh.csv"
    with zh_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "cn_name"])
        for tag in sorted(zh):
            writer.writerow([tag, ",".join(zh[tag])])

    characters.sort(key=lambda item: (-item[3], item[0]))
    char_path = ASSETS / "danbooru_characters.csv"
    with char_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "copyright", "parent_tag", "post_count"])
        writer.writerows(characters)

    names_path = ASSETS / "danbooru_character_names.txt"
    unique_names = sorted(set(classify_names))
    names_path.write_text("\n".join(unique_names) + "\n", encoding="utf-8")

    impl_path = ASSETS / "danbooru_implications_ext.csv"
    with impl_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "# StoryAura/Danbooru-Dataset-csv parent_tag edges (child,parent). MIT.\n"
        )
        writer = csv.writer(handle)
        for child, parent in implications:
            writer.writerow([child, parent])

    if license_path.is_file():
        shutil.copyfile(license_path, ASSETS / "STORYAURA_LICENSE.txt")

    extra_vocab = sum(
        1
        for tag, _copyright, _parent, count in characters
        if count >= EXTRA_VOCAB_MIN_POSTS and tag not in vocab_codes
    )
    stats = {
        "zh_rows": len(zh),
        "character_rows": len(characters),
        "character_names": len(unique_names),
        "implication_edges": len(implications),
        "extra_vocab_candidates": extra_vocab,
    }
    print("Wrote StoryAura-derived assets:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    for path in (zh_path, char_path, names_path, impl_path, ASSETS / "STORYAURA_LICENSE.txt"):
        if path.is_file():
            print(f"  {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "tmp" / "storyaura")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(argv)
    if args.download:
        _download(args.source)
    build(args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
