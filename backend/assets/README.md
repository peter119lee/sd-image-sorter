# backend/assets — bundled data files

## danbooru_tags.csv (bundled)

Danbooru tag vocabulary used by `GET /api/tags/suggest` (tag autocomplete).

- Source: [DominikDoom/a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete)
  (`tags/danbooru.csv`), MIT License.
- Shape: `tag,category_code,post_count,"alias1,alias2,..."` sorted by post
  count descending. Category codes: 0 general, 1 artist, 3 copyright,
  4 character, 5 meta.
- Loaded lazily by `services/tag_suggest_service.py`. If the file is
  missing the endpoint degrades to library-tags-only suggestions.
- Popular character tags missing from this dump are merged at load time
  from `danbooru_characters.csv` (StoryAura, MIT).

## danbooru_zh.csv (bundled, MIT)

Chinese / Japanese tag aliases enabling CJK fuzzy queries (typing 长发
suggests `long_hair`) and zh subtitles in suggestion dropdowns.

Derived by `scripts/build_danbooru_assets.py` from
[StoryAura/Danbooru-Dataset-csv](https://huggingface.co/datasets/StoryAura/Danbooru-Dataset-csv)
(MIT License; see `STORYAURA_LICENSE.txt`). Tags and wiki text originate
from [Danbooru](https://danbooru.donmai.us/); original images are not
included and remain copyright of their authors.

- Search order: `<DATA_DIR>/danbooru_zh.csv` (survives upgrades — preferred),
  then `backend/assets/danbooru_zh.csv`.
- Accepted shape: CSV where column 1 is the tag name and column 2 is a
  comma-joined list of CJK aliases. A header row is auto-detected.
- A personal `tags_enhanced.csv` drop-in from the DanbooruSearch HF space
  still works if the user prefers that file; it is GPL-3.0 so we do not
  ship it. The bundled StoryAura table is the default.

## danbooru_characters.csv / danbooru_character_names.txt (bundled, MIT)

Character metadata from the same StoryAura dump:

- `danbooru_characters.csv` — `tag,copyright,parent_tag,post_count` for
  `GET /api/tags/info` (series / parent) and for extra vocabulary rows.
- `danbooru_character_names.txt` — popular character names used by
  `tag_rules.categorize_tag` so Dataset Maker can color character tags
  before any WD14 model is downloaded.

Regenerate all StoryAura-derived files with:

```
python scripts/build_danbooru_assets.py --download --source tmp/storyaura
```

## danbooru_implications.csv (bundled) + danbooru_implications_ext.csv

Curated high-confidence core implication pairs, plus parent_tag edges
derived from StoryAura (MIT). A present child collapses its (transitive)
parents at export when the user enables the toggle. Extend or override by
dropping a CSV at `data/danbooru_implications.csv` (same 2-column format;
all three files are merged). Keep the graph acyclic.
