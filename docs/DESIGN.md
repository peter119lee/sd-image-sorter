# SD Image Sorter — UI Design Rules

This document has two layers: **§principles** is the macro design philosophy every
feature and surface must serve; everything after it is a **micro-invariant** that
survived a "wait, why?" review. It is the source of truth for "why does this look
like that" questions. When a change conflicts with either layer, the change is
wrong — see `docs/AI_PRINCIPLES.md` for the authority order.

---

## §principles — The design principles (macro layer)

Distilled from owner directives 2025–2026; none of these are generic best-practice
imports — each one came from a real owner complaint or an explicit ruling. Read
this before adding any feature, entrance, or surface.

### Product layer (owner-set, highest authority)

1. **One-stop tool.** Managing / tagging / sorting / censoring / publishing SD
   images never requires a second program.
2. **Comfort > stability > speed** — in that order (owner ranking).
3. **Serve pros AND newcomers by layering, never by capping.** Do not limit or
   remove functionality for "safety" or "performance" without asking the owner.
4. **Desktop/laptop only (≥ ~1280px).** No mobile/tablet effort, ever
   (owner directive 2026-06-05, recorded in `CLAUDE.md`).

### Product narrative — what this app *is* (owner 2026-08, highest for library)

This section decides product direction when Gallery, workspaces, storage, or
"multiple sets of images" features conflict. UI copy, defaults, and new
architecture must obey it. Detailed rules: **§product-narrative** below.

**One-line pitch (EN):** A local multi-library workbench for SD images — files
stay on disk; each long-lived library is a switchable workspace you can clear
or delete without touching the others.

**One-line pitch (中文):** 本机多图库工作台：图片仍在你的文件夹里；每本图库是
可切换、可单独清空/删除的长期工作集，互不影响。

**Hard product truths:**
- **Libraries are long-term** — not process-lifetime "sessions." Restart must
  not wipe a library the user did not clear.
- **Multiple libraries (workspaces)** — user can hold several; **one is
  current**. Clear gallery = clear **current** library only. Delete library =
  remove that workspace; others remain.
- **Files stay where the user put them** — we index paths; we do not become the
  primary bulk store of original pixels.
- **No short-term gallery product** — "just scanned this pile" uses sort/filter
  (e.g. newest / folder), not a second disposable gallery world.
- **Within one library:** roots + folders + collections + tags. **Across
  isolation boundaries:** switch/create/delete libraries (not equal chrome to
  generator tabs).
- **Storage we own:** per-library index (+ thumbs strategy) and shared optional
  AI models. **Storage we do not own:** original image folders.

When in doubt: behave like local multi-catalog tools (Eagle libraries /
Lightroom catalogs class) — durable workspaces, explicit clear/delete — never
like a temporary viewer or two peer "session vs permanent" galleries.

### Entrance & information-architecture layer (owner FB 2026-07-06/07)

5. **Intent first.** The entry page asks "what are we organizing today":
   missions (outcome-oriented) above, tools (room-oriented) below.
6. **The Library is home** (see §product-narrative). Biggest button on the entry
   page; always one step away; its nav tab can never be hidden. Home opens the
   **current long-lived library** (workspace). Switching libraries is a
   workspace action, not a short-lived "session scope" peer to generator tabs.
7. **Missions are guided modes.** Picking a mission scopes the top bar to only
   that pipeline's tabs, in order, with step numbers — the bar itself answers
   "how do I go". A visible chip exits back to the full set.
8. **Never cage.** Every feature stays reachable through at least two paths
   (direct tab or More-menu mirror, plus the function catalog). ESC always goes
   up exactly one level and never loses progress.
9. **Newcomer defaults, pro overrides.** The default experience explains itself
   (badges, step numbers, catalog descriptions); power users can customize the
   tab bar, skip the entry page, change the cover mode — and every override is
   reversible.
10. **The app carries its own map.** The 所有功能 catalog lists every feature
    with a one-line usage; a feature that is not in the catalog effectively
    does not exist for new users.
11. **Entrances may duplicate; implementations must not.** A feature may be
    reachable from the entry page, the nav bar, the catalog, and a menu — but
    all entrances must proxy the SAME button/function (e.g. the entry page's
    language button clicks `#btn-language-toggle`). Never fork the behavior
    per entrance.

### Visual-language layer (Aurora contract, v3.5.0 + de-AI craft)

12. **Color is semantics, not decoration — and only when needed.**
    Blue = next action, pink = user decision, purple = AI output.
    **One solid primary per screen. No brand gradients** (no blue→purple
    fills on buttons, progress, or export CTAs). In browse/idle chrome,
    prefer neutral surfaces; pink/purple appear at the interaction surface
    (selection, tags, scores), not as ambient marketing color.
    `frontend/css/tokens.css` is the single palette owner (see §css-ownership).
13. **Bilingual completeness.** en/zh key parity is audited; user-facing errors
    must have a Chinese variant. No zh-TW in the zh-CN pack.
14. **Dangerous operations sit far from common ones** (e.g. the danger divider
    in menus); icon-only buttons always carry a tooltip.
15. **Local darkroom, not AI SaaS skin (de-AI manifesto).**
    This is desktop software for sitting with images. The product stance:

    > **The image is the only thing that may glow. UI does not glow, does not
    > gradient-brand, and does not coach by default.**

    Craft rules that override trend:
    - **Matte solid panels** over glass/blur atmosphere (blur only for true
      overlays that sit on top of photos: peeks, modals).
    - **No decorative ambient** (radial orbs, hero-bleed wallpapers, glow
      shadows used as brand).
    - **No product-coach chrome by default** (daily-loop tips, celebratory
      chips). Opt-in only; status may be quiet and dismissible.
    - **Typography serves filing and reading**, not landing-page warmth.
    - **One sharp memory** beats five soft “premium” effects (e.g. Space
      light peek is enough; do not stack hero bleed + gradient CTA + purple
      coach chips on the same screen).

    When a change makes the UI look more like a generic AI dashboard
    (gradients, glow, multi-accent chrome, ambient blur), it is wrong even
    if tokens already exist for it.

Do NOT:
- Add an entrance whose behavior differs from the existing entrance to the
  same feature (rule 11).
- Ship a feature without a catalog row (rule 10).
- Hide or remove capability to simplify a surface — layer it instead (rule 3).
- Reintroduce blue→purple (or any) **brand gradients** on buttons/progress.
- Add ambient decorative blur/glow “for premium feel” on browse surfaces.

---

## §product-narrative — Multi long-lived libraries / workspaces

Owner decision context (2026-08): the product began as a one-shot "scan this
folder, work, restart clears memory" gallery, then grew a permanent
`images.db` index (Eagle/Billfish-class local library). A header
"current_session vs library" scope fought both stories. Owner ruling:

1. **No short-term gallery product** — batch focus = sort/filter (newest,
   folder), not a disposable second gallery.
2. **Yes multi long-lived libraries (workspaces)** — user can keep several;
   clear/delete applies to the **current** one (or a chosen one), not all.
3. **Cloud is not required** for long-term memory.

Naming: user-facing **图库 / Library** (or **工作区 / Workspace**). Avoid
**session** in UI — it collides with process-lifetime `gallery_session_*` and
implies data will vanish.

### What we are

- **Local-first multi-library workbench** for SD images on desktop/laptop.
- **Index and work**, do not hostage files: originals stay on disk; each
  library indexes membership, metadata, tags, and work state.
- **Many libraries, one current:** default first-run library e.g. **主图库**.
  User can create, switch, rename, clear, or delete libraries.
- **Clear gallery** = clear **current library** index/membership only; other
  libraries untouched. Confirm with the library name.
- **Delete library** = remove that workspace entirely; others remain. Default
  does **not** delete original files on disk.
- **Within one library:** roots, folder tree, collections, tags, sort/filter
  (including by import/index time for "what I just added").

### What we are not

- Not a process-lifetime session gallery (`gallery_session_images` as a user
  concept).
- Not two peer worlds "本次会话 / 永久图库" in the Gallery header.
- Not a cloud account product as the core promise.
- Not "one .db per source folder" by default.
- Not auto-wiping library rows on app exit.

### User-facing story (copy tone)

| Moment | Say this (sense) | Do not say this (sense) |
|--------|------------------|-------------------------|
| First open | 主图库 — import folders into this library | Temporary session |
| After scan | Added to **current library** · sort by newest if needed | Opened short-term gallery |
| Restart | All libraries still here; last current library restored | Sessions were wiped |
| Clear | Clear **this** library (name shown); others kept | Clear everything forever (unless only one exists and copy says so) |
| Delete library | Delete library "训练-2026"; files on disk kept | Delete session |
| Switch | Current library ▾ | Session scope toggle next to generators |
| Storage | Per-library index/thumbs + shared models; originals in your folders | We store all your pixels in the app |

Prefer **图库 / Library** over **永久图库**; prefer **当前图库** over **本次会话**.

### Information architecture

```
App
└── Libraries (workspaces) — long-lived, switchable
    ├── 主图库          ← default current
    ├── 训练-2026
    └── 私密
         └── inside each library:
              library roots · folder tree · collections · tags · sort/filter
```

| Need | Mechanism |
|------|-----------|
| Images on D: and E: | Library **roots** + folder tree **inside** current library |
| Curated training pack | **Collection** inside current library |
| Just scanned a batch | **Sort newest** / filter folder — not a new library |
| Work vs private isolation | **Separate libraries** + switch |
| Wipe only this project | **Clear current library** or **Delete library** |
| Nuke everything | Explicit multi-step; not the default Clear label |

### Clear vs delete (contract)

| Action | Affects | Survives | Originals on disk |
|--------|---------|----------|-------------------|
| Clear current library | Index/membership/tags-as-stored for **this** library | Other libraries | Kept (default) |
| Delete library | That workspace record + its index data | Other libraries | Kept (default) |
| Remove from library (single/batch) | Rows/membership in current library | Rest of library | Kept (default) |
| OS delete file | File gone; index may show missing → reconnect/remove | — | Gone |

**Clear gallery** button must mean **clear current library**, never "all
libraries" and never "only a process session."

### Storage narrative

**User-owned:** original files under user folders.

**App-owned:**
- Library index data (single-db multi-workspace **or** one db/dir per library —
  implementation choice; product speaks in libraries)
- Thumbnails (prefer partition by library id to bound growth)
- Shared optional AI models (not duplicated per library unless necessary)
- Temp / export scratch

Prioritize size honesty + cleanup (thumbs, models, vacuum) over cloud quotas.

### Mapping: current code → target product

| Current | Role today | Target |
|---------|------------|--------|
| `images.db` + `images` table | De-facto single long-term library | **Default / only library at first**; later rows scoped by `library_id` **or** one db per library |
| `gallery_session_images` + `scope=current_session` | Process-lifetime "this run" membership; header peer to library | **Retire as user-facing concept.** Optional internal use during a scan job only; do not restore dual-scope chrome. Batch focus → **newest / folder filter** |
| Header session \| library toggle | Two peer gallery worlds | **Remove.** Replace with **当前图库 ▾** (list, new, rename, clear, delete) in shell chrome (sidebar top / nav), not generator-tab row |
| Clear Current Library (`#btn-clear-db`) | Wipes the one global index | **Clear current library** only; confirm with name; other libraries untouched |
| Library roots (`library_roots` / 图库文件夹) | Source folders for the one DB | **Per-library** roots (or shared roots with membership filter — prefer per-library clarity) |
| Collections | Curated sets in the one DB | **Stay inside a library**; not a substitute for multi-library isolation |
| Favorites | Special collection | Stay inside a library (or explicit global favorites later — default: per library) |
| `SD_IMAGE_SORTER_DATA_DIR` / `DB_PATH` | Single data location | Remains app home; multi-library is **inside** that home (table or subfolders), not "user must set env vars" |
| Sort `newest` / `indexed_at` / `library_order_time` | Already supports recency | **Primary UX for "what I just imported"** once dual-scope is gone |
| Reconnect missing files | Path repair for indexed rows | Per current library (or all libraries in advanced repair later) |

### Implementation shape (guidance, not a forced schema)

**Phase 0 — narrative + UX honesty (minimal code):**  
Stop teaching short-term gallery. Prefer library-only language. Clear button
copy = current library. Dual-scope control is debt to remove.

**Phase 1 — single library behaves as "主图库":**  
Today's DB **is** the first long-lived library. No multi-switch UI required yet;
Clear = clear this one library (current behavior, correct story).

**Phase 2 — multi-library:**  
- `libraries`/`workspaces` registry (id, name, created_at, last_opened).  
- Membership: `library_id` on images **or** `library_images` M2M **or**
  separate db file per library under `data/libraries/<id>/`.  
- Switch current library id in app state; Gallery queries filter by it.  
- UI: **当前图库 ▾** — switch / 新建 / 重命名 / 清空 / 删除.  
- Thumbs: `thumbnails/<library_id>/` when partitioned.

**Do not** implement Phase 2 by overloading `gallery_session_images` (wrong
lifetime and wrong name).

### Decisions this narrative freezes

- Short-term gallery / process session as a **product** → **no**.
- Long-lived multi-library workspaces → **yes** (Phase 1 = one named library;
  Phase 2 = many).
- Clear gallery → **current library only**.
- Delete library → **explicit**; keeps other libraries; defaults keep files.
- "Just imported" → **sort/filter**, not scope world.
- Cloud → **out of core** until explicitly prioritized.
- Restart → restore **last current library**; no silent wipe.

### Do NOT

- Ship header "本次会话 | 永久图库" as two equal galleries.
- Use the word **session** in user-facing library switching.
- Make Clear wipe all libraries without naming them.
- Auto-create one library per scanned folder.
- Require cloud for durable libraries.
- Clear library data on process exit as a feature.

### Follow-on implementation order

1. Copy + defaults: one library story; retire dual-scope UX.  
2. Clear button semantics + confirm copy = current library.  
3. Multi-library registry + switcher UI.  
4. Storage panel (index/thumbs/models per library where applicable).  
5. Optional: export/backup one library folder.

**Status (P0–P3, 2026-08):** multi-library registry + `library_id` isolation,
nav chip, path non-steal + claim/move, per-library `library_roots`, scan skip
messaging, index export JSON, and disk panel library breakdown are implemented.
Thumbs remain path-keyed (path is globally unique); clear does best-effort thumb
delete. Full `thumbnails/<library_id>/` partition is optional later polish.

Track remaining polish in release plans; this section is the **product law**.

---

## §filter-sidebar — Filter summary rows must stay single-line

Each row in `.filter-summary > .summary-row` shows a label (e.g. `生成器`, `Tags`) and a value (e.g. `14/14`, `0`, `Any`). These rows MUST render on ONE visual line. Long values truncate with `text-overflow: ellipsis`, never wrap.

Rationale:
- The sidebar is a dense scannable summary, not a body of prose.
- Wrapping makes label and value look like separate items rather than a key-value pair.
- Users complained at 1366×768 that the rows broke into "label on top / value below" stacks because of `word-break: break-word`. Confirmed regression caused by a generic `.summary-value` rule in `ui-refresh.css`.

Implementation:
- `.filter-summary .summary-row { flex-wrap: nowrap; align-items: center; }`
- `.filter-summary .summary-label { flex: 0 0 auto; white-space: nowrap; }`
- `.filter-summary .summary-value { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }`
- The generic `.summary-value` rule (which wraps for prose-like uses elsewhere) is preserved separately so it does not affect the filter sidebar.

Do NOT:
- Add `flex-wrap: wrap` to `.filter-summary .summary-row`.
- Set `word-break: break-word` or `overflow-wrap: anywhere` directly on `.filter-summary .summary-value`.
- Stack label and value as `<div>` blocks — they must remain inline children of a flex row.

---

## §gallery-toolbar — All buttons fit a 1366×768 laptop without wrapping

The gallery toolbar (`.gallery-header`) and generator tabs (`.generator-tabs`) MUST remain on one line at 1366×768. This is the lowest-resolution consumer laptop the project supports.

Implementation:
- `.gallery-header { flex-wrap: nowrap; }`
- `.generator-tabs { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }`
- Below 1500px, secondary actions (Random, Reconnect) are hidden via `@media (max-width: 1500px)`.
- Below 1600px, the "X images" count is hidden. Generator tab counts stay on every tab so switching All / NovelAI / ComfyUI does not resize the pills; extra tabs scroll.

Do NOT:
- Use `flex-wrap: wrap` on these containers; users have explicitly rejected line breaks here.
- Hide gen-tab labels — only the count badges are removable.

---

## §nav-bar — Tab visibility has three layers; nothing becomes unreachable

(Rewritten 2026-07-07 — the old rule "all tabs always visible at 1366×768"
predates mission mode and the customize checklist. Deliberate tucking is now a
feature; INVOLUNTARY hiding is still the bug.)

Three layers decide which direct tabs show (`frontend/js/modules/nav-missions.js`):
1. **Mission mode** (`aurora-nav-mission`): an entry mission scopes the bar to
   its pipeline tabs with step badges + an exit chip.
2. **Base set** (`aurora-nav-tabs`): the 自定义标签栏 checklist under More.
   Gallery is locked in. Dataset is out of the DEFAULT set (owner 2026-07-07).
3. **Width-degradation ladder** (`updateNavigationOverflowState` in `app.js`):
   involuntary, width-driven — labels/brand compact before any tab vanishes;
   Prompt Helper / Style Finder tuck first into their More mirrors.

Invariants:
- Every tucked view (any layer) must have a More-menu mirror (`#nav-tools-{view}`)
  that is visible exactly while its direct tab is hidden.
- Mirrors carry `data-mirror-view`, NEVER `data-view` — Playwright page objects
  click plain `[data-view=...]` locators; a duplicate trips strict mode.
  (`#nav-tools-promptlab`/`-artist` predate this rule and are grandfathered.)
- The active view's tab is always contextually revealed, so an open view never
  lacks its highlighted tab.
- The DEFAULT base set must fit at 1366×768 without the ladder eating tabs.
- Mirror-like new elements need a `[hidden]{display:none}` guard — `.nav-tab`'s
  own display rule beats the UA `[hidden]` rule (recurring trap).

Nav actions (right side):
- Below 1500px, `.nav-actions .btn:not(.btn-icon-only)` shows icons only (label hidden).
- Below 1500px, secondary icon buttons (`#btn-refresh-ui`, `#btn-mass-tag-editor`, `#btn-app-update`) are hidden to free space.

Do NOT:
- Give a More-menu mirror a `data-view` attribute.
- Add a view to the default base set without verifying the 1366×768 fit.
- Add new always-visible icon buttons to `.nav-actions` without first verifying 1366×768 still fits.
- Render the same Help/Guide button both in nav-bar and inside a view (`.gallery-header`, `.censor-toolbar-v2`, etc.) at the same time. The nav-bar `#btn-help` covers all views via `Guide.getCurrentTab()`.

---

## §progress-toast — Background work must show a clear "Done" state

Long-running background jobs (color analysis, scanning, tagging, similarity build) MUST surface a clear completion state, not silently disappear.

Pattern (see `frontend/js/color-backfill.js`):
1. Detect `running` → `idle` transition (use a `wasRunning` flag).
2. On transition, show a "Done — N items processed" banner via the in-app toast.
3. Update the nav chip from `N%` to `✓` and keep it visible for ~5 s.
4. Auto-hide both chip and toast after 5 s.

Do NOT:
- Hide the chip immediately when polling sees `running=false` (user has no time to see completion).
- Leave the toast showing the last in-progress filename forever.

---

## §css-ownership — Shared tokens and feature layouts must have one owner

The frontend still uses plain CSS with multiple layered stylesheets. Keep the ownership boundary explicit so broad UI refresh work does not become override-only churn.

Ownership:
- `tokens.css`: THE palette owner (Aurora canonical tokens + legacy variable
  remap + prefers-contrast re-assertion). Loaded LAST in `index.html` — it must
  stay last or the high-contrast a11y re-assertion breaks (v3.5.0 Aurora Phase 1).
- `styles.css`: legacy/base layout foundation and broad compatibility rules.
- `ui-refresh.css`: current theme/chrome, shared controls, and cross-view refresh overrides (its color literals defer to `tokens.css` vars).
- Feature stylesheets (`censor-v2.css`, `dataset-maker.css`, `vlm.css`, etc.): feature-local layout and controls only.

Do NOT:
- Load any stylesheet after `tokens.css`, or define palette values outside it.
- Add a third stylesheet that competes with `ui-refresh.css` for global tokens or nav/gallery chrome.
- Put feature-specific layout fixes in `ui-refresh.css` when a feature stylesheet already owns that surface.
- Change the same shell from both `styles.css` and a feature stylesheet without documenting which layer wins.
- Add broad selectors that wrap or resize toolbar/nav/filter text without checking the 1366x768 desktop contract.

---

## §color-exemptions — Data-viz palettes are exempt from the accent semantics

(v3.5.0 color audit, 2026-07-07. Rule 12 in §principles says blue/pink/purple
are semantic accents. A hardcoded hue is legitimate ONLY when it encodes data,
not UI meaning — these registered palettes may keep literal hex values.)

Registered data-viz palettes:
- **WASD direction coding** (`styles.css` folder slots / sort folders):
  up=green, left=indigo `#3b82f6`, down=red, right=amber `#f59e0b`. The left
  key is deliberately indigo, NOT `var(--blue)` — a direction must not read as
  "the next action". The right key is pinned literal amber because it predates
  Aurora: it was written as `var(--accent-primary)` when that token WAS orange,
  and the remap silently turned it blue (fixed 2026-07-07).
- **Cull flash/stamps**: keep/reject/skip map to `--success`/`--danger`/`--blue`
  (semantic tokens, not literals — they ARE state feedback).
- **Danbooru category dots** (`caption-autocomplete.css`): 14 fixed hues,
  annotated in-file as "data-viz hue, not a UI accent".
- **Queue Solitaire section colors** (`queue-solitaire.css`): user-picked
  labels, a data palette by definition.
- **Generator badges** (`image-reader.css` `.gen-*`): third-party branding hues.
- **Prompt Lab diff coding** (`ui-refresh.css`): common=green, A=blue tint,
  B=amber tint (only-b was another accent-primary remap casualty, fixed).

Audit checklist for new hardcoded colors:
1. Does it encode data (category, direction, diff-side, brand)? → register here.
2. Is it a status (info/success/warning/danger)? → use the semantic tokens.
3. Is it an accent (action/selection/AI)? → `--blue`/`--pink`/`--purple` only.
4. Grep trap: any pre-Aurora `var(--accent-primary)` paired with warm colors
   was probably orange-intent — the remap turned those blue silently.

Do NOT:
- Introduce a new blue/purple/pink hex for UI chrome — that is what rule 12's
  "one solid-blue primary per screen" exists to protect.
- "Fix" the WASD left key to `var(--blue)` — the collision with the semantic
  blue is exactly why it stays indigo.

---

## §motion — Motion clarifies state; it never decorates

(v3.5.0, 2026-07-07. Written down from what the code already does, so new
surfaces stop inventing their own timing.)

The vocabulary (tokens in `tokens.css`):
- `--duration-fast` (150ms) — hover, focus, toggles, chips, tab underlines.
  Usually paired with plain `ease` (legacy `--transition-fast` bundles both).
- `--duration-normal` (250ms) — panels, modals, view reveals, collapses.
- `--ease-out` (`cubic-bezier(0.16, 1, 0.3, 1)`) — entrances only (dropdown
  menus, toasts, popovers): fast start, soft landing. Exits may simply fade;
  nothing bounces.

Rules:
1. Animate compositor-friendly properties (`transform`, `opacity`) for
   anything that runs repeatedly or over a large area. Color/border
   transitions are fine at `--duration-fast` on small controls.
2. One duration per interaction: an element's hover state and its container's
   reveal must not race two different clocks on the same property.
3. Ambient/long-running animation is BUDGETED like the gradient: the entry
   film strip is the one sanctioned ambient loop, and it stops under
   `prefers-reduced-motion` with a static fallback that still shows content.
4. The global reduced-motion kill-switch in `styles.css` (`0.01ms` everything)
   stays; a new long-running animation must ALSO ship its own semantic
   fallback (what does the user see instead?), not just rely on the kill.
5. No scroll-jacking, no parallax, no attention-seeking idle loops — this is a
   desktop work tool; motion answers "what just changed", nothing else.

Do NOT:
- Introduce a new easing curve or a 400ms+ transition without adding it here.
- Use `var(--duration-fast, …)`-style fallbacks as a substitute for defining
  the token — two undefined-token bugs (`--accent`, `--ease-out`) shipped that
  way before the 2026-07-07 audit caught them.

---

## Maintenance

- Update this file when reverting or revising any rule above.
- Add a new section every time a UI rule survives a "wait, why?" review.
- Cross-reference rules from CSS comments via the `§<slug>` anchor.
