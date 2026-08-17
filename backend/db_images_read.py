"""Image read/query facade. Split (2026-07) into three sibling modules:

* ``db_images_query``    — ``get_images`` / ``get_filtered_image_count`` /
  ``get_filtered_image_ids`` (offset listing, standalone count, id-only reads)
* ``db_images_paginate`` — ``get_images_paginated`` + its private first-page
  COUNT ``_get_filtered_count`` (co-located: the paginated function calls it
  as a module-local name, which is the only patch-safe shape — tests patch
  behavior via ``database.X`` aliases that never reach origin-module bindings)
* ``db_images_lookup``   — folder-scope / library-folder / reconnect reads and
  the single-image / by-ids / untagged / id-chunk light readers

This file stays the import surface: ``database.py`` re-exports all 21 names
below BY REFERENCE (database.py:249, incl. the private ``_get_filtered_count``),
and the identity contract ``database.X is db_images_read.X`` plus the shared
connection-provider pin ``db_images_read.get_db is db_core.get_db`` are locked
by tests/test_db_images_read_pins.py (TestReExportContract).
"""
from db_core import get_db
from db_images_lookup import (
    get_images_in_folder_scope,
    count_prompt_coverage_in_folder_scope,
    get_library_folders,
    get_missing_image_reconnect_candidates,
    get_image_by_id,
    get_images_missing_color_data,
    count_images_missing_color_data,
    get_image_by_path,
    get_images_by_ids,
    get_untagged_images,
    get_all_image_ids,
    get_untagged_image_ids,
    count_all_image_ids,
    count_untagged_image_ids,
    iter_all_image_id_chunks,
    iter_untagged_image_id_chunks,
    get_image_count,
)
from db_images_paginate import (
    get_images_paginated,
    _get_filtered_count,
)
from db_images_query import (
    get_images,
    get_filtered_image_count,
    get_filtered_image_ids,
)
