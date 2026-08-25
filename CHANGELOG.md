# Changelog

All notable changes to SD Image Sorter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.5.0-beta.6] - 2026-08-25

ComfyUI / WebUI / WebP embedded prompts recover more reliably (UI workflow, Get/Set, Flux t5xxl, EXIF `Workflow:`, UTF-16, NovelAI stealth WebP). Existing library rows need a re-scan to refresh stored prompt fields.

ComfyUI、WebUI、WebP 图里嵌的 prompt 现在读得到（UI workflow、Get/Set、Flux t5xxl、EXIF Workflow:、UTF-16、NovelAI stealth WebP）。旧图库要重扫才会更新已存栏位。

### Fixed / 修复
- **ComfyUI UI workflow and hybrid geninfo / ComfyUI UI workflow 与 hybrid 参数**: parse UI `workflow` when the API `prompt` chunk is missing or is only an upscale subgraph; keep `generator=comfyui` when a graph is present; use a full A1111 `parameters` trailer as executed text or a Reader edit, without letting a truncated decoy replace a fuller encoder prompt.
  - 没有 API `prompt`、或只有放大 subgraph 时，从 UI `workflow` 取字。有 ComfyUI graph 时 generator 保持 comfyui。完整 `Steps`/`Sampler` trailer 当 executed text 或 Reader 编辑；截断 decoy 不会盖掉更完整的 encoder prompt。
- **Off-PNG carriers / 非 PNG 载体**: promote EXIF/WebP `Workflow:` / `Prompt:` prefixes (including NUL-separated fragments) and repair UTF-16 UserComment into the same parse path.
  - EXIF/WebP 的 `Workflow:` / `Prompt:`（含 NUL 分段）与 UTF-16 UserComment 提升到同一条解析路径。
- **InvokeAI not stolen as ComfyUI / InvokeAI 不再被当成 ComfyUI**: `invokeai_graph` uses a nodes object; it is no longer promoted as a ComfyUI UI workflow.
  - `invokeai_graph` 的 nodes 是 object，不再被提升成 ComfyUI UI workflow。
- **Natural-language and Flux encoder keys / 自然语言与 Flux 编码器字段**: graph position wins over format scoring for `t5xxl` / `text_g` / `text_l` / `populated_text`; harvest still accepts NL sentences without commas and rejects bus titles, paths, and sampler names.
  - `t5xxl` / `text_g` / `text_l` / `populated_text` 以图位置为准，不靠格式打分。harvest 接受无逗号自然语言，拒绝总线标题、路径、sampler 名。
- **NovelAI Diffusion V5 character prompts / NovelAI Diffusion V5 角色提示词**: V5 still stores PNG Comment / WebP EXIF in the V4 shape (`v4_prompt`, `params_version` 4). The parser now reads top-level `characterPrompts` and `caption.char_captions` (up to 22 slots) and recognizes `nai-diffusion-5-*` Source names.
  - V5 仍用 V4 的 Comment/`v4_prompt`。现在会读 `characterPrompts` 与 `char_captions`（最多 22 个），并识别 `nai-diffusion-5-*`。
- **NovelAI / A1111 stealth WebP / NovelAI 与 A1111 的 WebP stealth**: lossless RGBA/RGB WebP now uses the same signed LSB decoder as PNG (`stealth_pngcomp` / `stealth_pnginfo` / RGB variants). Pixiv-saved NovelAI WebP with no EXIF is readable. Default scan re-parses existing rows (`PARSED_METADATA_VERSION` 10).
  - 无损 WebP 的 alpha/RGB LSB stealth 与 PNG 同一套解码。没有 EXIF 的 NovelAI WebP（常见于 Pixiv 转档）现在读得到。默认扫描会重解析已索引列。
- **ComfyUI settings-only parameters / 只有 Model hash 的 ComfyUI PNG**: a `parameters` chunk that is only `Clip skip` / `Model hash` / `Version: ComfyUI` no longer invents a prompt; the checkpoint is recovered for filters.
  - 只有设定行、没有正向 prompt 的 ComfyUI PNG 不会把 `Clip skip` 当 prompt，但会记下 checkpoint 供筛选。
- **Gallery uses scanned metadata / 扫完图库就能用新 metadata**: after Scan, gallery search, prompt filters, Auto-Separate selection, and the image detail modal use stored compact `_parsed` (including NovelAI character slots). Slot text is indexed; it is not written into the base `prompt` column.
  - 扫描后，搜索 / prompt 筛选 / 自动分类选中集 / 详情弹窗都用已存 `_parsed`（含 NovelAI 角色槽）。角色槽会进检索，不会改写正向 `prompt` 栏。
- **Reader character slots / Reader 角色槽**: NovelAI V4/V5 character prompts now show in Image Reader as cards, matching the gallery preview.
  - NovelAI 角色提示词在 Reader 里也以卡片显示，和图库预览一致。
- **Dataset review keys / Dataset 审阅快捷键**: on the Dataset Maker workbench, `X` drops the current image from the set without a confirm dialog, and `Z` undoes the last drop. `A`/`D` still step. Tag pills keep category colors.
  - 工作台用 `X` 剔除当前图（不弹确认），`Z` 撤销；`A`/`D` 仍翻图。标签颜色分类不变。
- **Lucida auto-mask batch / Lucida 批量遮罩**: Dataset Maker can generate and save Lucida/rembg masks for the whole set (`POST /api/masks/auto-batch`). Existing masks are skipped so the job is resumable.
  - Dataset Maker 可对整批图生成并保存 Lucida/rembg 遮罩；已有遮罩会跳过，可中途再跑。

## [3.5.0-beta.5] - 2026-08-25

Most optional package installs continue downloading model weights in the same click. A restart is required only when a loaded module was replaced, a Windows DLL is locked, or import works only in a clean process — and then Model Center offers Restart now and continue. TIPO ships v2.1 with a public CPU wheel; Gallery chrome stops jumping.

多数套件安装后可立刻继续下载模型；只有覆盖已加载模块、DLL 被锁、或干净进程才能 import 时才要重开，并提供「立即重启并继续」。TIPO 升到 v2.1 且走官方 CPU wheel；图库顶栏不再乱跑。

### Added / 新增
- **Bundled MIT Chinese/Japanese tag aliases + character catalog / 内置 MIT 中日文标签别名与角色目录**: tag autocomplete now resolves CJK queries out of the box (长发 → `long_hair`, 初音未来-style aliases → the canonical character tag) using [StoryAura/Danbooru-Dataset-csv](https://huggingface.co/datasets/StoryAura/Danbooru-Dataset-csv) (MIT). Tag details show series/copyright and parent tags; export implication-dedup uses the catalog's parent_tag graph on top of the curated core table; Dataset Maker can color popular character tags before any WD14 model is downloaded. A personal `data/danbooru_zh.csv` still overrides the bundled table.
  - 标签补全开箱即可用中日文别名（输入「长发」会提示 `long_hair`）。标签资料显示作品 copyright 与父标签；导出蕴含去重叠上 StoryAura 的 parent_tag 图；即使尚未下载 WD14，热门角色标签也能正确着色。`data/danbooru_zh.csv` 仍可覆盖内置表。
- **Accepting a character tag also inserts its series / 接受角色标签时一并写入作品**: choosing `hatsune_miku` from autocomplete (comma-separated tag boxes, not Prompt Lab insert mode) also writes `vocaloid` when that copyright is known and not already in the field. The dropdown shows the series before you accept.
  - 在逗号分隔的标签框里选中 `hatsune_miku` 时，若词表知道作品且栏里还没有，会一并写入 `vocaloid`。下拉里会先看到作品名。Prompt Lab 插入模式只补全当前词，不会加逗号。

### Changed / 变更
- **Prepare no longer restarts after every pip install / 安装后不再每次都要重开**: optional packages that import in this process continue to model-weight download in the same click. A restart is required only when a loaded module was replaced, Windows locked a DLL, or import works only in a clean interpreter. When that happens, Model Center offers Restart now and continue; remaining downloads resume after relaunch.
  - 能在当前进程 import 的套件会立刻继续下载模型权重。只有覆盖了已加载模块、Windows DLL 被锁、或只有干净进程能 import 时才要重开。这时模型中心提供「立即重启并继续」，剩下的下载会在重开后自动接上。
- **TIPO v2.1 plus a lighter 200M-ft choice / TIPO 升级 v2.1，并保留轻量 200M-ft**: Reverse Prompt and Dataset Maker let you pick `v2.1` (TIPO-v2.1-1B-A200M Q8_0, ~1.1 GB, default) or `200M-ft` (~210 MB). Both run on CPU; no dedicated GPU. v2.1 and the older v2 1B checkpoint are the same RAM class (~1–2 GB), so v2 is not offered separately. First use of a missing variant asks before downloading, and Danbooru rating words are mapped for v2.1 (`general` → `safe`).
  - 反推和数据集可在 v2.1（默认，约 1.1 GB）与较轻的 200M-ft（约 210 MB）之间选择，都走 CPU，不必独显。v2.1 与旧 v2 1B 内存需求相同，所以不再单独提供 v2。缺权重时会先确认再下载；v2.1 会把 Danbooru 分级词映射到它的词表。
- **TIPO runtime installs a prebuilt CPU wheel / TIPO 运行时改装官方预编译 CPU wheel**: Model Center Prepare is the public-install path. It installs `llama-cpp-python` from the first-party extra-index (`whl/cpu`) with `--only-binary` (py3-none wheels: Windows portable 3.12 and Linux portable 3.13), then `tipo-kgen`. It will not compile from the PyPI sdist, and the card no longer tells users to pip into a backend they do not own. Covered hosts: Windows x86_64, Linux x86_64, Linux aarch64, macOS Apple Silicon. Other hosts fail closed.
  - 模型中心「准备」是公开发布的安装路径：先装官方 CPU wheel（禁止源码编译；`py3-none` 覆盖 Windows 便携 3.12 与 Linux 便携 3.13），再装 tipo-kgen。缺依赖文案改为点「准备」，不再叫用户自己 pip。支持 Windows x86_64、Linux x86_64 / aarch64、macOS Apple Silicon；其他平台直接拒绝而不是现场编译。
- **Desktop chrome is a gallery workbench / 顶栏按图库工作台收**：Import and AI Tag stay on Gallery only. Reverse Prompt, Prompt Helper, and Style Finder default into More (catalog and entry tiles still reach them). Prompt Lab / Style Finder no longer open a Start-here lecture; Help unhides it. Hard refresh moved to Settings. Update buttons use the SVG sprite.
  - 「导入 / AI 打标」只留在图库。反推、提示词助手、画风识别默认进「更多」。进阶房间不再一进门上课。强制刷新进设置。

### Fixed / 修复
- **Nav status chips stay icon-sized / 顶栏状态不再撑开按钮**: the AI-busy and color-analysis chips no longer expand to a 220px label that shoves Import/Tag every 1.5s. Elapsed time and percent sit on the icon; the full sentence stays in the tooltip. Filter Apply and Scan Cancel keep a reserved width so “Apply · ~1,234 images” / “Stopping...” do not resize the footer.
  - AI 忙碌与色彩分析芯片不再拉成 220px 把「导入/打标」挤走。耗时和百分比叠在图标上，完整句子放 tooltip。筛选「应用」和扫描「取消」预留宽度，文案变长不会改按钮尺寸。
- **Gallery chrome no longer jumps while you use it / 图库工具条不再边用边跳**: generator tab counts stay visible on every pill (instead of appearing only on the selected one). Scan/metadata status sits under the toolbar, not between the buttons. Sort stays a fixed width. Buttons no longer shrink on press, and hover on Grid/Large/Waterfall no longer looks like the selected view.
  - 生成器分页的数字一直显示，不再只在选中时冒出来把 pill 撑宽。扫描/读元数据状态改到工具条下方，不再插在按钮中间。排序下拉固定宽度。按钮按下不再缩小；网格/大图/瀑布的 hover 不再长得像已选中。
- **Mode switches keep their size when selected / 模式开关选中不再跳位**: segmented controls no longer bold or lift the chosen option. Manual Sort Slot / Showdown / Cull, Auto-Separate vs Manual Sort, Original / Censored, caption-type (booru / both / nl), Sync zoom, and radio segmented pills keep the same font-weight and position; only color and fill change.
  - 分段开关不再靠加粗或上移表示选中。手动排序三模式、自动分类/手动、原图/打码、caption 类型、同步放大、以及单选分段条，选中只改颜色与底，不再把旁边的按钮挤走。
- **Gallery gives vertical space back to images / 图库把垂直空间还给图**：the missing-files banner is one compact row; the All/None filter summary starts collapsed so Folders is first; Clear library sits in the sidebar footer instead of the thumbnail toolbar. Nav chrome is 56px.
  - 缺档横幅收成一行；筛选摘要默认折叠，文件夹树在前；「清空图库」放到侧栏底部，不再跟缩图列抢。顶栏高度 56px。
- **Censor workspace matches Graphite / 打码工作台去玻璃**：pill toolbars, glow, and hover-lift are gone; panels use the same 4–6px hairlines as Gallery.
  - 去掉胶囊工具条、光晕和 hover 上移，侧栏和画布跟图库同一套石墨发丝线。
- **Folder browser no longer punches a blue-glass hole / 文件夹浏览不再漏出蓝玻璃**：the post-tokens inline palette (orange + cyan blur) moved into Graphite tokens.
  - 写在 tokens.css 之后的 inline 橙/青玻璃已收回 Graphite token。
- **Entry cover-mode buttons stay put / 入口页封面模式按钮不再乱跑**: the status caption, mode switch, and local-run note are separate grid slots. Switching Off / Single / Slideshow / Film no longer shoves the buttons sideways when the caption changes length.
  - 状态文案、模式开关、本地说明分成固定格子。切换无 / 单张 / 轮播 / 胶卷时，文案变长不会把按钮挤走。
- **Product claims match the shipped app / 对外说法对齐现货**: artist identification is no longer labelled experimental or backed by a hardcoded sample list; a failed Kaloscope load is retryable instead of locking a placeholder. README / Why Choose Us stop claiming to be the only AI-art manager, stop marking Allusion as having no SD metadata, and cite Eagle and Billfish. First use of a feature downloads that model with a progress overlay (about 1 GB asks first) instead of a frozen or permanently disabled button. Agent docs drop glassmorphism and the wrong database path.
  - 画师识别不再标成实验功能、也不再靠一份写死的样本名单；Kaloscope 加载失败可以重试，不会锁成 placeholder。README / 为什么选我们 不再自称「唯一」，不再把 Allusion 写成没有 SD 元数据，并点名 Eagle 与 Billfish。第一次用某个功能会下载对应模型并显示进度（约 1 GB 先确认），按钮不再永久禁用。代理文档去掉 glassmorphism 与错误的数据库路径。

## [3.5.0-beta.4] - 2026-08-04

Beta 4 introduces durable, isolated multi-library workspaces and restores the Gallery comfort features that previously looked present but silently did nothing. Long-distance virtual scrolling now stays aligned across all desktop Gallery modes and UI scales, while NovelAI V4/V4.5 WebP files retain their positive and negative prompts without weakening corrupt-EXIF validation. Similarity setup, Auto-Separate destinations, and VLM concurrency guidance are also clearer.

Beta 4 新增持久且严格隔离的多图库工作区，并修复此前看似存在却静默失效的图库续看与每日进度功能。长距离虚拟滚动现可在所有桌面图库模式与 UI 缩放下保持对齐；NovelAI V4/V4.5 WebP 会保留正向和负向提示词，同时不放宽损坏 EXIF 的拒绝规则。相似度准备、自动分类目标目录与 VLM 并发建议也更加清晰。

## [3.5.0-beta.3] - 2026-08-01

Beta 3 restores startup for legacy Windows portable installations that received Beta 2 through the in-app updater. The app patch is again parseable by the bundled Python 3.11 runtime, release tests now enforce that compatibility across every shipped Python source, and isolated Playwright transform caches remove an intermittent CI collection race.

Beta 3 修复旧版 Windows portable 通过应用内更新安装 Beta 2 后无法启动的问题。应用补丁重新兼容内置 Python 3.11，发布测试会检查补丁内每个 Python 源文件的语法兼容性；Playwright 分片也使用独立转换缓存，消除间歇性的 CI 测试收集竞态。

## [3.5.0-beta.2] - 2026-07-31

Beta 2 closes two high-impact correctness and desktop UX defects found after the first public beta. Signed Stealth PNG Info carriers are now decoded across alpha/RGB and compressed/uncompressed variants, existing incomplete PNG records are re-parsed after the parser revision bump, and Queue Solitaire keeps the user's scroll position across drag, selection, sorting, collapsing, and other same-session re-renders. The release pipeline is also stabilized across Windows, macOS, Linux, and both Linux portable architectures.

Beta 2 修复首个公开测试版之后发现的两个高影响正确性与桌面体验问题：元数据解析器现可读取 alpha/RGB、压缩/未压缩的签名 Stealth PNG Info 载体，并通过解析器修订号自动重解析旧的 PNG 不完整记录；队列接龙在拖拽、选择、排序、折叠及其他同会话重渲染后保持用户当前滚动位置。发布流水线同时已在 Windows、macOS、Linux 与两种 Linux portable 架构上稳定通过。

## [3.5.0] - 2026-07-19

v3.5.0 stable 落地 v4.0「清爽极光」重设计：新增任务入口页与可定制导航，图库/排序/打码/打标四大工作流同步升级，并重做 LoRA/Dataset 导出、完整搜索语法、查重清理、颜色搜索和大图库后台任务。beta 到 stable 的收口补齐 Krea 2 自然语言优先 Smart Tag、Qwen3-VL Instruct 推荐路径、AI/Mass Tag 生命周期竞态、扫描与元数据边界、打码输出完整性、模型运行时诊断，以及 Model Manager 等高风险模块拆分与桌面回归覆盖。

v3.5.0 stable ships the v4.0 "Fresh Aurora" redesign with a mission entry page and customizable navigation, coordinated Gallery / Sort / Censor / Tagging upgrades, rebuilt LoRA/Dataset export, a complete search language, Duplicate Cleanup, color search, and durable large-library jobs. Beta-to-stable closure adds Krea 2 natural-language-first Smart Tag guidance, an explicit Qwen3-VL Instruct recommendation, AI/Mass Tag lifecycle race fixes, scan and metadata edge-case hardening, censor output integrity, model-runtime diagnostics, and desktop regression coverage around the final high-risk module splits.

### Changed / 变更 (function entrances rework, owner feedback 2026-07-07)
- **Mission-scoped smart nav bar / 任务智能导航栏**: clicking a mission tile on the entry page now scopes the top bar to only that pipeline's tabs, in order and with step numbers (LoRA → ①Library ②Dataset; Pixiv → ①Library ②Censor; Organize → ①Library ②Sort), plus a mission chip whose ✕ restores your full bar. The choice persists across restarts.
  - 在入口页点任务磁贴后，顶栏只显示该任务需要的页面（带 ①② 步骤编号），配一个任务 chip，点 ✕ 恢复完整标签栏。选择跨重启记住。
- **Customizable tab bar / 自定义标签栏**: a new checklist under the More menu decides which views stay in the top bar; everything tucked stays reachable through More-menu mirrors. Dataset leaves the default bar (reached via the LoRA mission, its mirror, or the catalog), and 成套发布 leaves the More menu (the Pixiv mission, the gallery publish button, and the catalog are its entrances).
  - 「更多」菜单新增「自定义标签栏」勾选清单；被收起的页面都能从「更多」的镜像入口到达。数据集移出默认顶栏（走 LoRA 任务/镜像/功能清单），成套发布移出「更多」（走 Pixiv 任务/图库发布按钮/功能清单）。
- **Function catalog / 所有功能清单**: 自由模式 and 全部工具 were the same thing — the survivor is a catalog modal listing every feature with a one-line usage, grouped into core views / pipelines / tools; each row jumps straight there. 隐私处理 (buried inside the Reader's tool tabs) also gets its own entry tile.
  - 自由模式与全部工具重复——合并为「所有功能」清单弹窗：核心页面/成套流程/工具三组，每行一句用途、点击直达。藏在读图页里的「隐私处理」同时升级为入口页磁贴。
- **Entry tile hierarchy / 入口磁贴层级**: the Library is the app's home and gets the biggest full-width tile; the Model Center moves to the bottom row (its wd14-missing badge still glows) and now deep-links to the AI Models tab instead of landing on Settings. Language switch + update check join the entry page's corner.
  - 图片库升级为最大的通栏磁贴；模型中心移到底排（缺核心模型时徽章依旧高亮），点开直达「AI 模型」页签而不再落在设置页。语言切换和检查更新加入入口页右上角。

### Changed / 变更 (entry page facelift, owner feedback 2026-07-06)
- **Cover art now shows the whole image / 门面图完整显示**: the entry cover switched from a hard crop (only a slice of the artwork visible) to a two-layer fit — the full image letterboxed in the left canvas over a blurred full-bleed echo of itself, so nothing is cut off and the page still feels covered edge to edge.
  - 入口页门面从硬裁切（只能看到画的一角）改为双层贴合：完整原图以适应模式放在左侧画布，底下垫一层它自己的全屏模糊回声——画面一点不切，页面依旧铺满。
- **Cover display-mode switch (off / single / slideshow / film) / 门面展示模式四挡开关**: the one-way 不想展示 link becomes a four-state switch — 无 (blank), 单张 (one image, 换一张 to swap manually), 轮播 (auto-rotating slideshow), 胶卷 (four oblique sprocket-framed film strips rolling your works across the canvas, alternating directions, reduced-motion aware). The choice persists and stays in sync with the settings toggle. A new `GET /api/entry/hero-pool` feeds the rotating modes: ★5 works first, then newest — so a fresh unrated library still gets a living wall, and single mode falls back to latest works instead of a blank half-canvas.
  - 原来只有单向的「不想展示」，现在是四挡开关：无 / 单张（手动换一张）/ 轮播（自动换）/ 胶卷（四条斜向胶卷框滚动播放你的作品，双向交错，尊重系统减弱动效设置）。选择会记住并与设置里的开关保持同步。新增 `GET /api/entry/hero-pool` 供轮播/胶卷取图：★5 优先、其余按最新排序——没打过分的新库也有滚动墙，单张模式也会回退到最新作品而不是留一片黑。
- **Model Center is now a first-class tile / 模型中心升级为一级入口**: new users start by downloading models, so the Tools group leads with a full-width 模型中心 tile showing live ready/total counts; while the core WD14 tagger is missing it glows blue with a 从这里开始 badge. Same model manager the gear icon opens.
  - 新用户的第一步是下载模型——工具区第一排新增通栏「模型中心」磁贴，实时显示就绪/总数；核心打标模型（WD14）未装时高亮蓝框并挂「从这里开始」徽章。打开的就是右上角齿轮同一个模型管理器。
- **Right column tightened / 右侧栏收紧**: the mission/tools column is vertically centered with tighter row gaps instead of top-hugging with a dead lower half.
  - 任务/工具栏改为垂直居中、行距收紧，不再顶着上沿留一大片下方空白。

### Fixed / 修复 (Aurora color audit, 2026-07-07)
- **Sort-view WASD "right" color restored to amber / 排序视图 WASD 右键颜色恢复琥珀色**: the four direction colors are green/blue/red/amber, but the right key and right folder slot were written as `var(--accent-primary)` back when that token was orange — the Aurora palette remap silently turned them blue, colliding with the left key. Pinned to the intended amber. Same class of remap casualty fixed in the Prompt Lab diff view (the "only B" chip's background had turned blue, same as "only A"; now amber to match its text).
  - 四个方向色本是 绿/蓝/红/琥珀，但右键与右侧文件夹当年写的是 `var(--accent-primary)`（当时它是橙色）——极光配色重映射后悄悄变成了蓝色，和左键撞色。已钉回琥珀色。Prompt Lab 对比视图的「仅 B」标签底色是同类事故（曾和「仅 A」一样变蓝），也已修复。
- **Long-tail color drift swept to Aurora tokens / 长尾漂移色收敛到极光 token**: header info chips, VLM status chips, cull keep/reject/skip flashes, the Reader's LoRA chips (now purple = AI/model domain), and a never-defined `--accent` variable whose off-palette fallback was actually rendering — all now use the semantic tokens. Data-viz palettes (direction keys, danbooru category dots, Queue Solitaire section colors, generator badges) are documented as exemptions in `docs/DESIGN.md` §color-exemptions.
  - 顶栏信息角标、VLM 状态条、快速筛选的保留/淘汰/跳过闪光、读图页的 LoRA 标签（改为紫色=AI/模型域）、以及一个从未定义导致回退色实际生效的 `--accent` 变量——全部收敛到语义 token。数据可视化配色（方向键、danbooru 分类圆点、接龙分区色、生成器徽章）已在 `docs/DESIGN.md` §color-exemptions 登记为豁免。

### Fixed / 修复 (tagger quality round)
- **Camie Tagger v2 read the wrong ONNX output head — now fixed and pinned by tests**: the camie-v2 export has three outputs (`initial_predictions`, `refined_predictions`, `selected_candidates`); without an `output_index` the runtime silently read index 0, a coarse intermediate head. Real-image A/B: the shipped head missed every character (0/4 known characters), missed halo/horns/guitar, emitted `open_mouth`+`closed_mouth` together, and rated an explicit image `sensitive`; the refined head scores `kayoko_(blue_archive)` 0.99 / `1girl` 1.00 / `halo` 0.88. After the one-line fix (`output_index: 1`) a full 29-image re-run restored camie to WD-family quality (ground-truth recall 35/52 → 48/52, characters 4/4). The official model metadata confirms the app's ImageNet preprocessing was always correct — the damage was purely the output head.
  - Camie Tagger v2 此前静默读取了错误的 ONNX 输出头（index 0 的粗预测中间头），导致角色全部认不出（4 个已知角色 0 命中）、halo/horns/吉他全漏、`张嘴`+`闭嘴`同时出现、explicit 图被评成 sensitive。修复为 `output_index: 1`（refined 头）后,29 张真实图片复测恢复到 WD 系列同级水平（真值命中 35/52 → 48/52,角色 4/4）,并加入行为级回归测试（mock 按真实三头结构、粗头故意放垃圾,一旦回退立刻红灯）。官方模型元数据证实 App 的 ImageNet 预处理一直是对的——问题全在输出头。

### Fixed / 修复 (tagger audit fix-all batch)
- **ToriiGate is a captioner, not a tagger / ToriiGate 定位为字幕模型，不再当打标器 (owner decision)**: measured as a gallery tagger it emitted 5-7 loose non-danbooru tags per image with invented details, so tag mode is now disabled everywhere — `/api/tag` rejects it with a clear 400 pointing at Smart Tag's natural-language mode, the gallery model dropdown hides it, and the Smart Tag consensus slots refuse it (config-driven, so any future captioner-only model is blocked automatically). It stays in the catalog for Smart Tag captions and model downloads. Its caption grounding also switches to the official model-card format (`# Booru tags for the image\n[tags]` ahead of the query) instead of improvised phrasing the model was never trained on.
  - 实测把 ToriiGate 当图库打标器用时，每张只吐 5-7 个松散的非 danbooru 词还会编造细节——因此打标模式全面关闭：`/api/tag` 会返回明确的 400 并指引改用智能打标的自然语言模式，图库模型下拉隐藏它，Smart Tag 共识槽位也拒绝它（按配置驱动，未来任何 captioner-only 模型都会自动被拦）。它仍保留在目录中供智能打标字幕与模型下载使用。字幕的标签引导词也改为官方模型卡格式（`# Booru tags for the image\n[tags]` 放在问题之前），不再用模型没训练过的自创措辞。
- **Tag provenance: re-tagging never destroys your manual tags / 标签来源追踪：重新打标不再毁掉手动标签**: every tag row now records `source` (tagger / vlm / manual / trigger) and `category` (general / character / copyright / artist / rating / meta) via migration 024. Pipeline re-tags replace only their own rows — tags you added by hand survive, and when a re-tag emits a tag you already added manually, your row wins. Tag import marks rows `manual`; VLM tag merges keep existing provenance and mark additions `vlm`.
  - 每条标签现在记录来源（tagger / vlm / manual / trigger）与类别（general / character / copyright / artist / rating / meta）（migration 024）。管线重新打标只替换自己写的行——你手动加的标签会保留；重打标出现你已手动添加的同名标签时，以你的为准。标签导入的行记为 manual；VLM 标签合并保留原有来源、新增行记为 vlm。
- **VLM tagging hardened: vocabulary gate + reasoning strip / VLM 打标加固：词表闸门 + 思维链剥离**: VLM-generated tags now pass a danbooru-vocabulary gate before persisting — hallucinated non-vocabulary tags and rating words are dropped (with a count surfaced) instead of becoming permanent library tags; kaomoji vocabulary passes. `<think>…</think>` reasoning blocks (DeepSeek-R1 / QwQ-style models) are stripped from output before parsing, sibling `reasoning_content` fields are ignored, and `finish_reason=length` truncation is flagged with a warning. The tags-format prompt no longer asks the model for rating words.
  - VLM 生成的标签落库前先过 danbooru 词表闸门——幻觉出的非词表标签与分级词会被丢弃（并统计数量），不再变成永久库标签；颜文字词表可通过。`<think>…</think>` 思维链（DeepSeek-R1 / QwQ 系模型）在解析前剥离，`reasoning_content` 字段直接忽略，`finish_reason=length` 截断会记警告。tags 格式的提示词也不再要求模型输出分级词。
- **Kaomoji tags survive intact / 颜文字标签完整保留 (P1-4)**: `^_^`, `0_0`, `@_@`, `:3`, `^^^` and friends are real WD14 expression vocabulary, but the pipeline used to delete them as "symbolic noise" and every underscore formatter corrupted the survivors (`^_^` → `^ ^`). A shared curated set + shape heuristic now exempts emoticons from noise-stripping and underscore→space conversion across the export engine, Smart Tag captions, and the LoRA sidecar path; prompt-syntax junk (`::`, `--`, `//`) is still stripped.
  - `^_^`、`0_0`、`@_@`、`:3`、`^^^` 等都是真实的 WD14 表情词表，但管线此前把它们当「符号噪声」删除，各处下划线格式化还会把幸存者写坏（`^_^` → `^ ^`）。现在共享的白名单 + 形状启发式让颜文字在导出引擎、智能打标字幕、LoRA sidecar 全路径豁免噪声清洗与下划线转空格；提示词语法垃圾（`::`、`--`、`//`）照常清除。
- **Diffusion-pipe split export / diffusion-pipe 双文件导出 (P0-3)**: batch export gains an "NL twin" option — tags land in `image.txt` and the natural-language caption in `image_nl.txt` (suffix configurable), single-line with the trigger injected, sharing the same stem so trainer pairing always holds. Under the `unique` policy a clash on the twin fails that row atomically (never a tag file without its NL half). Response reports `nl_sidecars_written`; the export modal gains the checkbox.
  - 批量导出新增「NL 副档」选项——标签写进 `image.txt`，自然语言字幕写进 `image_nl.txt`（后缀可配置），单行且自动注入触发词，与主档同名词干保证训练器配对。`unique` 策略下副档撞名时该行原子性报错——绝不会只写出标签一半。响应带 `nl_sidecars_written` 计数；导出弹窗新增勾选框。
- **Export preview can no longer lie / 导出预览不再骗人 (P1-7)**: the caption-editor preview used to approximate non-template content modes with a synthesized template, so the preview could disagree with the exported file. It now renders through `build_sidecar_content` — the exact engine the export writes with (`content_mode` on `/api/tags/export-preview`). The Booru/NL/Both compose rule also whitespace-flattens the NL sentence on both backend and frontend (kohya reads line 1 only), and sidecar files are always written with LF newlines on Windows.
  - Caption 编辑器预览此前用合成模板「近似」非模板内容模式，预览可能与实际导出文件不一致。现在预览直接走 `build_sidecar_content`——与导出写盘完全同一引擎（`/api/tags/export-preview` 新增 `content_mode`）。Booru/NL/两者的合成规则前后端同步把 NL 句子压平成单行（kohya 只读第一行），Windows 上 sidecar 一律以 LF 换行写出。
- **Sidecar name clashes are reported, not papered over / sidecar 撞名如实报告，不再悄悄改名 (P1-6)**: under the default `unique` policy a name collision used to rename the caption to `{stem}_1.txt` — a file that pairs with no image, silently corrupting the training set. Collisions now fail that image with a message naming the sidecar and its owner (`beside_image` mode treats a caption already sitting next to the image as "already exported" and skips). `overwrite`/`skip` behave exactly as before.
  - 默认 `unique` 策略下撞名此前会把标注改名成 `{stem}_1.txt`——一个不与任何图片配对的文件，训练集被悄悄污染。现在撞名会让该图报错，消息指明被占用的文件名与占用者（`beside_image` 模式下图片旁已有标注视为「已导出」而跳过）。`overwrite`/`skip` 行为不变。
- **Tagger preprocessing parity + Anima category sections / 打标预处理对齐官方 + Anima 分类段落 (P2-13a, P3-11, P3-14)**: transparent PNGs are now composited onto white before tagging (bare RGB conversion turned transparency into a black background) and the letterbox resample matches SmilingWolf's official BICUBIC. The Anima presets follow the official model-card order with real category sections — `{quality}, {safety}, {count}, {trigger}, {characters}, {copyright}, {artists:@}, {general}. {nl_caption}` — driven by the recorded tag categories (heuristic fallback for legacy rows); `{count}` accepts `6+girls`; the tags-only preset's quality default is fixed; and a fused-caption fallback no longer echoes the whole tag list into the sentence. Model cards in the tagger UI now state the measured verdicts (eva02 best, pixai high-recall-but-hallucinates → consensus recommended, oppai ratings run loose).
  - 透明 PNG 现在先合成到白底再打标（裸 RGB 转换会把透明区变成黑背景），letterbox 缩放对齐 SmilingWolf 官方的 BICUBIC。Anima 预设按官方模型卡顺序输出真实分类段落——`{quality}, {safety}, {count}, {trigger}, {characters}, {copyright}, {artists:@}, {general}. {nl_caption}`——由已记录的标签类别驱动（旧数据回退启发式）；`{count}` 支持 `6+girls`；tags-only 预设的画质默认值修正；融合字幕回退不再把整串标签重复进句子。打标 UI 的模型卡现在写明实测结论（eva02 最准、pixai 召回高但有自信幻觉→建议共识模式、oppai 分级偏松一级）。
- **Caption editor consistency batch / Caption 编辑器一致性批次 (P3-15)**: find/replace now matches whole tags by default (`_`/space/case folded) with an opt-in substring mode — replacing `girl` no longer mangles `2girls`; the batch-export editor defaults NL-bearing images to Both exactly like the Dataset Maker (explicit choices still win) and the export payload matches the preview; the fragile global fetch monkey-patch that injected caption fields is replaced by explicit payload plumbing.
  - 查找/替换默认整标签匹配（折叠 `_`/空格/大小写），子串模式改为可选——替换 `girl` 不再误伤 `2girls`；批量导出编辑器对带 NL 句子的图默认「两者」，与 Dataset Maker 一致（显式选择仍优先），导出内容与预览一致；此前注入字幕字段的全局 fetch 补丁替换为显式传参。

### Added / 新增
- **Training-purpose export filter / 训练目的导出过滤 (tagger audit P2-19)**: the batch-export modal's Advanced panel gains a purpose dropdown sharing Smart Tag's vocabulary — Style LoRA drops style/artist tags so the target style stays unnamed; Character LoRA drops detected character-name tags when a trigger word (or Class Token) carries the identity. Applied inside the real export engine so every content mode (including templates) inherits it, and the live preview shows exactly what will be written. Off by default — no silent tag removal.
  - 批量导出「高级选项」新增训练目的下拉，与智能打标共用同一词表——画风 LoRA 删除画风/画师标签（目标画风不被点名）；角色 LoRA 在有触发词（或 Class Token）承载身份时删除识别到的角色名标签。过滤发生在真正的导出引擎里，所有内容模式（含模板）统一生效，实时预览所见即所得。默认关闭——不做静默删标签。
- **Danbooru implication dedup on export / 导出时的 danbooru 蕴含去重 (tagger audit P2-18)**: an opt-in checkbox collapses redundant parent tags when a more specific child is present — `cat_ears` drops `animal_ears`, transitively (`school_swimsuit` drops both `one-piece_swimsuit` and `swimsuit`). Ships with a curated ~180-pair table; drop a `danbooru_implications.csv` into `data/` to extend it.
  - 新增可选勾选框：当更具体的子标签存在时折叠冗余的父标签——有 `cat_ears` 就删 `animal_ears`，且可传递（有 `school_swimsuit` 时 `one-piece_swimsuit` 和 `swimsuit` 一起删）。内置约 180 对精选蕴含表；把 `danbooru_implications.csv` 放进 `data/` 即可扩充。
- **Character-trait pruning checklist / 角色特征修剪清单 (tagger audit P1-17)**: a 🎯 button next to the export blacklist (both the batch-export modal and the Dataset Maker) asks the new `POST /api/tags/trait-candidates` endpoint for innate traits — hair / eyes / skin / body markers — shared across the selected images, ranked by frequency. You review the checklist and the picked tags append to the blacklist, so the trigger word absorbs the character's identity the way community character-LoRA practice expects. Never deletes anything silently.
  - 导出黑名单旁（批量导出弹窗和数据集制作页都有）新增 🎯 按钮，调用新的 `POST /api/tags/trait-candidates` 端点，找出选中图片共有的先天特征——发/眼/肤/体貌——按出现频率排列成清单。你勾选审核后加入黑名单，让触发词按社区角色 LoRA 惯例承载角色身份。绝不静默删除。
- **LoRA training-data correctness batch / LoRA 训练数据正确性批次 (tagger audit P0)**: exported captions now tell the truth. (1) *Per-image rating*: `{rating}`/`{safety}` template slots resolve from each image's actual tagger rating instead of a hardcoded `safe` — an explicit-rated image exports `explicit`, the Anima preset speaks the model card's vocabulary (`questionable`→`nsfw`), rating-marker tags are removed from `{tags:filtered}` when the template has a rating slot (no more `safe, …, explicit` in one caption), and unrated images honestly render nothing. (2) *Per-image quality*: the hardcoded `score_5` is gone from the Anima presets; `{quality}` now prefers the image's aesthetic-score bucket (masterpiece ≥7 … worst quality <3, empty in the normal band) and falls back to the preset default only when unscored. (3) *Single-line guarantee*: built-in presets flatten multi-paragraph NL captions and multi-line prompts into one line — kohya-style trainers read only the first line, so a 3-line caption silently trained on a third of its text; deliberate multi-line custom templates are untouched. (4) *Smart Tag persistence*: the trigger word is now written as a top-confidence tag row (character mode used to delete the character name AND keep the trigger only inside the caption string — a later tags-mode export had no subject token at all), and the tagger's rating verdict is persisted as a tag row like the plain pipeline does (it used to be silently dropped). (5) *Export health check*: every batch export returns a trainer-consumability report shown as a toast — unpaired caption filenames (`_1` collision renames), multi-line captions, missing trigger, conflicting rating tokens, empty captions.
  - 导出的训练标注现在句句属实。(1) *按图分级*：`{rating}`/`{safety}` 模板槽从每张图自己的打标分级解析，不再硬编码 `safe`——explicit 图导出 `explicit`，Anima 预设使用官方模型卡词汇（`questionable`→`nsfw`），模板带分级槽时分级标签会从 `{tags:filtered}` 中移除（同一行 caption 里不会再同时出现 `safe` 和 `explicit`），未评级的图诚实地不输出分级词。(2) *按图画质*：Anima 预设里硬编码的 `score_5` 已删除；`{quality}` 优先用图片的美学分档位（≥7 masterpiece … <3 worst quality，普通档为空），只有未评分时才回退到预设默认值。(3) *单行保证*：内置预设会把多段自然语言字幕和多行提示词压平成一行——kohya 系训练器只读第一行，三行的 caption 等于只用三分之一文本在训练；自定义多行模板不受影响。(4) *Smart Tag 落库*：触发词现在会写成置顶置信度的标签行（此前角色模式删掉角色名后触发词只存在于 caption 字符串里——之后用 tags 模式导出时整行没有任何主体词），打标器的分级结论也会像普通打标一样落库（此前被悄悄丢弃）。(5) *导出体检*：每次批量导出返回训练可用性报告并以提示条展示——标注文件名与图片不配对（`_1` 碰撞改名）、多行标注、缺少触发词、分级词互相矛盾、空标注。
- **Metadata "never lose a prompt" architecture / 元数据「永不失联」四层架构 (owner request)**: metadata parsing moves from case-by-case node fixes to a general system that makes misses rare, visible, and retroactively fixable. (1) *Generic prompt harvest*: when precise graph tracing fails, ALL text in the workflow is scored for prompt-likeness against the bundled 140k danbooru vocabulary (a string whose comma tokens are mostly booru tags IS a prompt, whatever custom node holds it) — no node knowledge needed, decoys like file paths / sampler names / model files are rejected, and natural-language prompts pass on comma structure alone. (2) *Raw retention + one-click repair*: when a scan still can't find a positive prompt, the image's original metadata chunks are stored gzipped in the DB (migration 023); Settings → Dataset Audit gains a "Re-parse Missing Prompts" button that replays stored envelopes — and, as fallback, the files themselves — through the current parser as a cancellable background job, so **every future parser upgrade repairs your whole library without re-scanning folders, even for files that were moved or deleted**. New endpoints: `GET /api/metadata/health`, `POST /api/metadata/reparse`, `GET /api/metadata/reparse-status`. (3) *Regression corpus*: real-world workflow shapes are frozen as fixtures (`backend/tests/fixtures/comfyui_workflows/`) with a one-command extractor (`scripts/extract_workflow_fixture.py`) — every future bug report becomes a permanent test. The detail modal now explains *why* when a prompt genuinely isn't in the file (runtime wildcards / stripped on export) instead of a bare "No prompt". Live-verified: a 657-image owner folder that previously parsed 0 prompts recovered **657/657** through the repair job in 53 seconds.
  - 元数据解析从「逐个节点修 bug」升级为通用体系，让漏解析变得罕见、可见、且可事后补救。(1) *通用文本收割*：精确图追踪失败时，对 workflow 里的**所有**文本按内置 14 万 danbooru 词表打「提示词相似度」分（逗号分词命中率过半的就是提示词，不管它在哪个自定义节点里）——无需认识节点，文件路径/采样器名/模型文件等诱饵会被排除，自然语言提示词靠逗号结构也能过关。(2) *原文留底 + 一键修复*：扫描仍找不到正向提示词时，把图片原始元数据块 gzip 存进数据库（migration 023）；设置 → 数据集体检新增「重新解析缺失提示词」按钮，把留底原文（以及还在的文件）用当前解析器重放，作为可取消的后台任务——**以后每次解析器升级，一键即可修复整个图库，无需重扫文件夹，文件被移动或删除也能修**。新端点：`GET /api/metadata/health`、`POST /api/metadata/reparse`、`GET /api/metadata/reparse-status`。(3) *回归语料库*：真实 workflow 形态冻结为 fixture（`backend/tests/fixtures/comfyui_workflows/`），配一条命令的提取工具（`scripts/extract_workflow_fixture.py`）——以后每个 bug 报告都变成永久回归测试。详情弹窗在提示词确实不在文件里时（运行时 wildcard / 导出被去除）会说明原因，不再只显示「无提示词」。实测：所有者一个曾经 0 解析的 657 张文件夹，修复任务 53 秒 **657/657** 全部找回。
- **Top-bar brand → entry page / 顶栏品牌区一键回入口页**: navigation stays the classic fixed top bar (an Aurora left-rail experiment was reverted on owner review — "make it back to the toppest bar"), and the brand block now returns to the mission entry page on click / Enter / Space, even for users who skip the entry at launch.
  - 导航保持经典的顶部固定栏（Aurora 左侧导航栏实验按所有者反馈已回退——"回到最顶上的栏"），品牌区现在点击 / Enter / 空格即可回到任务入口页，即使设置了「跳过入口页」也有效。
- **Search bar v2: full filter query language / 搜索栏 v2：完整筛选查询语言 (owner request)**: the key:value search now covers every filter the app has — 21 keys with English + Chinese aliases (tag / prompt / checkpoint / lora / generator / rating / score / stars / width / height / size / aspect / color / brightness / saturation / seed / artist / folder / has / no), comparison operators (`score>=7`, spaces OK), ranges (`score:6..8`), exact resolution (`size:1024x1536`), and negation (`-tag:blurry`). A live "Understood as:" chip line narrates every parse with ⚠ chips naming bad values AND the legal ones; a ? button opens a syntax-reference modal rendered from the parser's own table; a filter-modal button sits right next to the box. Values fuzzy-autocomplete Danbooru-search style from your own library (tags / checkpoints / LoRAs / prompt tokens with usage counts) or from the legal enum values, with full keyboard navigation.
  - key:value 搜索升级为覆盖全部筛选能力的查询语言——21 个键位、中英文别名（tag 标签 / prompt 提示词 / checkpoint 模型 / lora / generator 生成器 / rating 分级 / score 美学 / stars 星级 / width 宽 / height 高 / size 尺寸 / aspect 比例 / color 主题·色温 / brightness 亮度 / saturation 饱和度 / seed 种子 / artist 画师 / folder 文件夹 / has·no），支持比较运算（`score>=7`，可带空格）、区间（`score:6..8`）、精确分辨率（`size:1024x1536`）、排除（`-tag:blurry`）。输入框下方实时显示「解析为：」chip 行，格式错误会用 ⚠ 标出并直接告诉你合法值；旁边新增 ? 语法帮助按钮（内容由解析器自己的语法表渲染，文档永不漂移）和筛选弹窗按钮（不用再开侧栏）。输入值时按 Danbooru 模糊搜索的方式从你自己的库里补全（标签/模型/LoRA/提示词，带使用次数），枚举键本地补全，全程支持键盘操作。
- **Duplicate Cleanup workflow / 查重清理工作流 (Tier 1)**: a new whole-library near-duplicate review lives under nav Tools → 查重清理. One click scans every CLIP-embedded image into duplicate GROUPS as a cancellable background job with live progress — hnswlib ANN when available, exact chunked comparison otherwise, and **no library-size cap** (the old pair-based duplicates endpoint refused >5000 images; Debt-17 closed). Each group shows its members side by side, best-first, with a suggested keeper (your star rating first, then aesthetic score, resolution, file size) highlighted and the rest pre-checked. "Keep best, trash rest" per group, or "Keep best everywhere…" across all groups; the summary shows groups / redundant images / reclaimable bytes. Deletion reuses the existing Recycle-Bin pipeline (big batches auto-switch to the durable background job); results persist to disk so the review survives restarts. New endpoints: `POST /api/duplicates/scan`, `GET /api/duplicates/scan-status`, `GET /api/duplicates/groups`.
  - 导航「工具 → 查重清理」新增全库近似重复审查。一键把所有已建 CLIP 向量的图片扫描成重复分组，后台任务可取消、有实时进度——有 hnswlib 就走 ANN 加速，没有就走精确分块比对，且**不再限制图库大小**（旧的成对查重端点超过 5000 张直接拒绝；Debt-17 关闭）。每组成员并排展示、按优先级排序，建议保留的一张高亮（优先你的星级，其次美学分、分辨率、文件大小），其余默认勾选。可单组「保留最佳，清理其余」，也可「全部保留最佳…」一键处理所有组；摘要行显示组数 / 冗余张数 / 可释放空间。删除完全复用现有回收站管线（大批量自动转后台任务）；结果落盘，重启后审查进度不丢。新端点：`POST /api/duplicates/scan`、`GET /api/duplicates/scan-status`、`GET /api/duplicates/groups`。
- **Color filter completed: search by dominant hue / 颜色筛选补完：按主色搜图 (Tier 1)**: `color:red`, `color:蓝`, `-color:粉` now work — 12 hue values (red / orange / yellow / green / cyan / blue / purple / pink / brown / white / black / gray, zh aliases included) alongside the existing warm/cool/neutral temperatures. The filter modal's Colors panel gains a Dominant Colors multi-select with color dots. Powered by a new `dominant_color_tags` column classified at analysis time from the already-stored top-5 dominant colors (skin tones deliberately carry no tag so anime close-ups don't flood "orange"); migration 022 backfills every analyzed image from the stored JSON in seconds — no image files are reopened. New API params `color_hues` / `exclude_color_hues` thread through gallery, count, selection, bulk tag scope, VLM batch scope, export scope, and auto-separate.
  - `color:red`、`color:蓝`、`-color:粉` 现在真的能用了——12 个主色值（红/橙/黄/绿/青/蓝/紫/粉/棕/白/黑/灰，中文别名可用）与原有的暖/冷/中性色温并存。筛选弹窗「颜色」面板新增带色点的「主色」多选。底层是新的 `dominant_color_tags` 列：分析时从已存的前 5 主色分类（肤色刻意不打标签，避免动漫特写把"橙色"淹没）；migration 022 直接用已存 JSON 秒级回填所有已分析图片，不重新打开图片文件。新 API 参数 `color_hues` / `exclude_color_hues` 贯通图库、计数、选择、批量标签范围、VLM 批次范围、导出范围与自动分类。
- **Publish Set workbench / 成套发布工作台 (Tier 1)**: the entry page's Pixiv mission tile finally does what it promised (Pick → Censor → Rename → Export). Select images in the Gallery → batch bar → More → "Publish set…" (also under nav Tools → Publish Set): drag rows into publish order, and each image auto-pairs with its censored version (`{stem}_censored.*` — found next to the original first, then anywhere in the library; the suffix is configurable). A master "use censored where available" toggle plus a per-image Original/Censored segment control what gets published; export copies the set into any folder as `01.png`, `02.jpg`, … (prefix / start number / digit count configurable, source extension kept) with an optional `caption.txt`. Safety rule: an item set to Censored whose pair is missing FAILS that item — the uncensored original is never silently substituted; existing files are skipped unless Overwrite is checked, and numbering is positional so a fix-and-retry keeps every file's number stable. New endpoints: `POST /api/publish/censor-pairs`, `POST /api/publish/export`.
  - 入口页的「Pixiv 成套发布」任务卡终于名副其实（挑选 → 打码 → 重命名 → 导出）。在图库选中图片 → 底部操作条 → 更多 → 「发布这组…」（导航「工具 → 成套发布」也能进）：拖拽排出发布顺序，每张自动配对打码版（`{stem}_censored.*`——先找原图旁边，再全库按文件名找；后缀可配置）。「有打码版时优先使用」总开关 + 每张的「原图/打码」切换决定发布内容；导出把整组按 `01.png`、`02.jpg`… 顺序命名复制到任意文件夹（前缀/起始编号/位数可调，保留源文件扩展名），并可附带 `caption.txt` 文案。安全规则：选了「打码」但配对丢失的图片会直接报错——绝不悄悄用未打码原图顶替；已存在的文件默认跳过（可勾选覆盖）；编号按位置分配，修完错误重试时每张图的编号保持不变。新端点：`POST /api/publish/censor-pairs`、`POST /api/publish/export`。
- **Tag autocomplete v2 everywhere / 标签补全 v2 全面铺开 (owner request)**: every comma-separated tag input now shares one danbooru-grade type-ahead — the Dataset Maker caption editor, the image-detail tag editor, the mass tag add/remove boxes, the batch-export caption textareas, all three blacklist boxes (Dataset Maker / AI tagging / batch export), and the Prompt Lab writing boxes (insert mode: completes the word under the caret without adding commas, respects `(tag:1.2)` weight syntax). A new `GET /api/tags/suggest` merges your own library tags (frequency-ranked, highlighted counts) with a bundled 140k-tag danbooru vocabulary (MIT-licensed, popularity-ranked, alias-aware — typing `boobs` suggests `breasts`), each suggestion carrying its 14-category color dot (same palette as the tag pills). Drop an optional `danbooru_zh.csv` into `data/` (see `backend/assets/README.md`; not bundled for license reasons) and CJK queries fuzzy-match Chinese aliases with zh subtitles in the dropdown — the DanbooruSearch-style 中文模糊搜索. Suggestions never block typing; Enter/Tab accepts, and surfaces with their own Enter handlers (detail modal) yield cleanly to the accept.
  - 所有逗号分隔的标签输入框现在共用同一套 danbooru 级补全——Dataset Maker caption 编辑器、图片详情标签编辑、批量标签的添加/移除框、批量导出 caption 编辑框、三个黑名单框（Dataset Maker / AI 打标 / 批量导出）、以及 Prompt Lab 写作框（插入模式：只补全光标下的词、不加逗号，兼容 `(tag:1.2)` 权重语法）。新增 `GET /api/tags/suggest`：合并你自己的库内标签（按使用次数排序、次数高亮）与内置 14 万条 danbooru 词表（MIT 许可、按热度排序、别名可搜——输入 `boobs` 会提示 `breasts`），每条建议带 14 类彩色圆点（与标签药丸同一调色板）。在 `data/` 放入可选的 `danbooru_zh.csv`（见 `backend/assets/README.md`；因许可证原因不随包附带）后，中文查询可模糊匹配中文别名并在下拉里显示中文副标——即 DanbooruSearch 式的中文模糊搜索。补全永不阻挡打字；Enter/Tab 接受，自带 Enter 行为的输入框（详情弹窗）会正确让位。
- **Gallery thumbnail-size control / 图库缩图大小控制 (owner FB-3)**: the gallery toolbar gains a visible − slider + control next to the view buttons — drag to fit more or fewer images per page (120–400px), live in grid, large, and waterfall modes, persisted across restarts. The `[` `]` shortcuts step the same value (they were previously wired to a control that didn't exist in the page, so nothing worked).
  - 图库工具栏在视图按钮旁新增可见的「− 滑杆 +」缩图大小控制：拖动即可一页看更多或更少（120–400px），网格/大图/瀑布流三种视图实时生效，重启后记住。`[` `]` 快捷键与滑杆同一份数值（此前它们挂在一个页面里根本不存在的控件上，等于全部失效）。
- **Entry page recomposition / 入口页重排 (owner FB-2)**: mission and tool tiles align into calm equal-geometry rows under Missions / Tools group labels (the "乱而不散" offsets read as noisy misalignment); with no ★5 cover the canvas shows a quiet aurora gradient instead of a black void; and the bottom-left corner grows into a greeting plus a live stat row — library total, added today, handled today, day streak.
  - 入口页任务/工具卡片在「任务 / 工具」分组标签下等宽等高对齐（原「乱而不散」的错位设计实际读起来就是乱）；没有 ★5 门面图时画布显示安静的极光渐层而不是一片黑；左下角扩展为问候语 + 实时统计行——库内图片、今日新增、今日已处理、连续整理天数。
- **Gallery toolbar search + quick chips / 图库工具栏搜索与快捷筛选 (Aurora Phase 3, #25a)**: a key:value search box (`tag:silver_hair` `checkpoint:` `lora:` `seed:314159` + free text) feeding the same filter store as the filter modal, plus one-click chips for 有参数 / 美学 7+ / 无字幕. The `[` `]` thumbnail-size keys now drive the same value as the size slider (they previously fought over separate saved states).
  - 新增 key:value 搜索框（`tag:银发` `checkpoint:` `lora:` `seed:314159` + 自由文本），与筛选弹窗共用同一份筛选状态；另有「有参数 / 美学 7+ / 无字幕」一键快捷片。`[` `]` 缩略图快捷键现在与大小滑杆同源（此前两者各存各的会互相打架）。
- **Gallery bottom action bar / 图库底部批量操作条 (Aurora Phase 3, #25a)**: batch actions move from the left panel to a floating bottom bar — Move / Tag / Censor Edit / Add to collection up front, everything else (copy, dataset, mass tag, exports) under More ▾ with the destructive pair separated at the bottom; a stats line shows selected count · matched/total · thumbnail cache size · AI queue depth. The new Tag button runs AI tagging on JUST the selected images (the tag modal shows the scope and can switch back to whole-library).
  - 批量操作从左栏移到底部悬浮操作条：移动 / 打标 / 打码编辑 / 加入合集直接可点，其余（复制、数据集、批量标签、导出）收进「更多 ▾」，危险操作（移除/回收站）用分隔线隔离在最底部；右侧统计行显示已选数 · 命中/总数 · 缩略图缓存 · AI 队列。新的「打标」按钮只对选中的图片跑 AI 打标（打标弹窗会显示范围，可一键切回全库）。
- **Pink selection + pick order / 粉色选中与挑选顺序**: selected tiles now carry the pink ring + a ♥ pick-order badge (1st, 2nd, …) per the Aurora color contract (pink = your decisions, purple = AI output, blue = next action); favorites and the active Select toggle go pink too, and the aesthetic badge turns purple.
  - 按 Aurora 配色契约（粉=你的决定、紫=AI 产物、蓝=下一步），选中的图现在是粉色描边 + ♥ 挑选顺序徽章（第 1、2…张）；收藏心、选择模式开关同步变粉，美学分徽章改为紫色。
- **Filter modal: live hit count, Unscored tier, saturation range / 筛选弹窗：实时命中数、未评分档、饱和度区间 (24d)**: the Apply button now previews "应用筛选 · 预计 N 张" as you change filters (new `GET /api/images/count`); the aesthetic "Unscored" quick tier is a real filter (`aesthetic_unscored`, previously a dead button); and a min/max saturation range joins brightness. New gallery filters also include `no_caption` and `seed`.
  - 「应用筛选」按钮现在随改动实时预览「预计 N 张」（新增 `GET /api/images/count`）；美学「未评分」快捷档从死按钮变成真筛选（`aesthetic_unscored`）；亮度旁新增饱和度 min/max 区间。图库筛选还新增 `no_caption`（无字幕）与 `seed`（种子）。
- **Sort stage: live count, focus mode, named presets / 排序台：实时计数、专注模式、命名预设 (Aurora Phase 3, #25e)**: the setup card shows a live "≈N images in scope" count that follows your folder and filter choices; a 🧘 focus mode hides the top nav bar so the WASD stage fills the screen; named presets save/load/delete the entire setup (folders, collection slots, slot layout, mode, action, filters); the sorting HUD gains a mute toggle and the progress line now shows percent + images/min throughput.
  - 排序设置卡新增「范围内约 N 张图片」实时计数，随文件夹/筛选变化自动刷新；新增 🧘 专注模式（隐藏顶部导航栏，WASD 舞台占满全屏）；命名预设可保存/载入/删除整套配置（文件夹、收藏夹槽位、槽位布局、模式、动作、筛选）；分拣 HUD 新增静音开关，进度行显示百分比与「张/分」速度。
- **Censor sidebar tabs + review conveyor / 打码侧栏分页 + 审核流水线 (Aurora Phase 3, #25d)**: the right sidebar becomes three tabs — 画笔 Brush (all existing manual + auto-detect tools, unchanged), 调整 Adjust (photo filters), and the new 审核 Review conveyor: detect the current image, check/uncheck each found region (unchecked stays uncensored), then Approve & Next bakes the kept regions and auto-advances through the queue (Prev / Next / Skip included). Detection boxes draw on a separate preview layer that is never written into the saved image.
  - 打码右侧栏改为三个分页：画笔（原有手动与自动检测工具全部保留）、调整（照片滤镜）、审核（新流水线：检测当前图 → 逐个区域勾选/取消，取消勾选=该处保留不打码 → 「通过并下一张」烘焙勾选区域并自动前进，含上一张/下一张/跳过导航）。检测框画在独立预览层上，永不写入保存结果。
- **Tagger 智能一趟 landing tab / 打标弹窗「智能一趟」落地页 (Aurora Phase 3, #25b)**: the AI tag modal gains a Smart Tag one-pass tab in first position — a guided entry that opens the full Smart Tag workspace (booru taggers with optional voting, noise cleanup, trigger word, optional natural-language caption) and forwards the armed Gallery selection scope. Selecting images in Gallery and pressing 打标 lands here; the global AI Tag button still opens the familiar Local tagger tab directly.
  - 打标弹窗新增第一个分页「智能一趟」：引导式入口一键打开完整 Smart Tag 工作区（booru 打标器可选投票、噪声清洗、触发词、可选自然语言描述），并自动带上图库已选范围。在图库选中图片后点「打标」默认落在这里；全局「AI 打标」按钮仍直达熟悉的本地打标分页。
- **Caption preview health strip + trigger check / Caption 预览健康条 + 触发词检查 (Aurora Phase 3)**: the batch-export caption preview always shows a checks strip — edited / empty / blacklist hits / duplicates / max tokens, plus a missing-trigger count when a LoRA trigger word is set — and images whose final caption is missing the trigger word carry a ⚑ badge in the queue.
  - 批量导出的 caption 预览常驻「检查」健康条：已编辑 / 空 caption / 黑名单命中 / 重复词 / 最多标签，设置 LoRA 触发词时还会统计「缺触发词」；最终 caption 缺触发词的图片在队列里带 ⚑ 徽章。
- **Caption editors consolidated: two-box model everywhere / Caption 编辑器统一：双框模型全覆盖 (Aurora #25c)**: the batch-export Caption Editor now speaks the same per-image caption model as the Dataset Maker's two-box editor — a Booru / Both / NL type segment per image, an editable natural-language box seeded from the stored VLM sentence, a live "Will export" composed line, B+N / NL queue chips, and bulk "set loaded" / "auto-assign" actions; the health strip and ⚑ check now measure the composed final caption. `/api/tags/export-batch` and `/api/tags/export-combined` accept the same `image_types` + `image_nl_overrides` fields the dataset export already speaks (absent fields = byte-identical pre-feature output), and both export engines share one compose rule so the preview text is exactly what lands in the sidecar.
  - 批量导出的 Caption 编辑器与 Dataset Maker 双框编辑器正式统一为同一套逐图 caption 模型：每张图有 Booru / 两者 / NL 分段开关、可编辑的自然语言框（自动带出已存的 VLM 句子）、实时「导出效果」合成行、队列 B+N / NL 徽章、「已载入批量设置 / 自动分配」按钮；健康条与 ⚑ 检查改按合成后的最终 caption 统计。`/api/tags/export-batch` 与 `/api/tags/export-combined` 现在接受与数据集导出相同的 `image_types` + `image_nl_overrides`（不传=输出与从前逐字节一致），两套导出引擎共用同一条合成规则——预览看到的文本就是写进 sidecar 的文本。
- **Dataset export manifest / 数据集导出清单**: every dataset export now writes an `export_manifest.json` next to the captions — a settings snapshot (output mode, naming pattern, trigger word, prefix, blacklist, common tags…), per-image results (source/output/caption paths, skip reason, error), and total/exported/skipped/failed counts, so a training set's provenance is reproducible.
  - 数据集导出现在会在输出目录写入 `export_manifest.json`：本次导出的设置快照（输出模式、命名规则、触发词、前缀、黑名单、通用标签等）、逐图结果（源/输出/caption 路径、跳过原因、错误）与总数/成功/跳过/失败统计，训练集来源可复现。
- **Missing-file repair review / 移动文件修复审查 (Roadmap-C)**: when Find Moved Images hits ambiguous matches (one found file, several same-name-same-size records), they now persist as reviewable items — a new modal previews the found file, lists the candidate records, and commits your per-row choice: relink / relink+remove-others / skip. New endpoints `GET /api/images/repair-candidates`, `POST /api/images/repair-confirm`, `GET /api/image-preview-by-path`.
  - 「找回移动的图片」遇到不确定匹配（一个文件对应多条同名同大小记录）时，现在会保存下来供审查——新弹窗预览找到的文件、列出候选记录，逐条确认：重连 / 重连并移除其余 / 跳过。新增端点 `GET /api/images/repair-candidates`、`POST /api/images/repair-confirm`、`GET /api/image-preview-by-path`。
- **Background bulk jobs for huge selections / 超大选择的后台批量任务**: token-scoped (or ≥500-image) Gallery delete, remove-from-gallery, and same-name sidecar export now run as durable background jobs instead of one long blocking request: real progress (processed/total), cooperative cancel, bounded error samples, and IDs snapshotted server-side before any mutation. New unified endpoints `GET /api/bulk-jobs`, `GET /api/bulk-jobs/{id}`, `POST /api/bulk-jobs/{id}/cancel`; existing endpoints accept opt-in `background: true`. Small selections keep the original instant path.
  - 整库筛选范围（或 ≥500 张）的删除、移出图库、同名 sidecar 导出改为可靠的后台任务，不再占用一个漫长的阻塞请求：真实进度（已处理/总数）、可取消、有限错误样本、且在任何改动前先在服务端固定 ID 快照。新增统一端点 `GET /api/bulk-jobs`、`GET /api/bulk-jobs/{id}`、`POST /api/bulk-jobs/{id}/cancel`；原端点支持 `background: true` 选择加入。小量选择仍走原来的即时路径。
- **AI job queue survives restarts / AI 任务队列跨重启持久化**: the FIFO queue for gallery tagging / Smart Tag / VLM caption batches is now write-through persisted to `data/state/ai-job-queue.json` (atomic writes, corrupt files degrade to an empty queue with a log). Jobs queued — including one that was running — at shutdown are re-queued in order on next launch instead of silently vanishing.
  - 图库打标 / Smart Tag / VLM 描述批次共用的 FIFO 队列现在实时落盘到 `data/state/ai-job-queue.json`（原子写入，文件损坏时降级为空队列并记录日志）。关机时还在排队的任务——包括正在运行的那个——下次启动会按原顺序重新入队，不再无声消失。
- **Mission entry page / 任务入口页 (Aurora Phase 2)**: launch surface with the four mission lanes (LoRA dataset, Pixiv set publishing, batch organize, free mode), live-count function tiles, a resume slab for saved manual-sort sessions, a daily ★5 full-bleed cover (换一张 / 不想展示), an activity streak line, and top-level ESC returning to the entry without losing view state. New `GET /api/entry/summary` + `activity_log` daily counters (migration 020). Settings gains 跳过入口页 and ★5 门面 toggles.
  - 新增任务入口页：四条任务动线（LoRA 数据集 / Pixiv 成套发布 / 批量整理 / 自由模式）、带实时数字的功能马赛克、手动分拣「接着上次」锚块、每日 ★5 全屏门面（换一张 / 不想展示）、连续整理天数；顶层 ESC 随时回入口且不丢视图状态。新增 `GET /api/entry/summary` 与 `activity_log` 日计数（migration 020）；设置里新增「跳过入口页」「★5 门面」开关。
- **Frontend control audit / 前端控件审计**: `scripts/audit_frontend_controls.py` parses `frontend/index.html`, scans `frontend/js/**/*.js`, and classifies controls as `referenced-by-id`, `referenced-by-data`, `delegate-only`, `native-control`, `static-only`, or `needs-runtime-check`. It reports evidence only; it never recommends deleting controls.
  - 新增 `scripts/audit_frontend_controls.py`：解析 `frontend/index.html`，扫描 `frontend/js/**/*.js`，把控件归类为 `referenced-by-id`、`referenced-by-data`、`delegate-only`、`native-control`、`static-only`、`needs-runtime-check`。它只输出证据，不给删除建议。
- **Contract tests for delegated controls / 委托控件契约测试**: regression coverage confirms known delegated controls such as Reader tabs, Dataset tabs, Dataset queue mode buttons, and Censor filter presets are not misreported as static-only.
  - 新增契约测试，锁住 Reader tabs、Dataset tabs、Dataset queue mode、Censor filter presets 等已知委托控件，避免被误报为静态按钮。
- **v3.5.0 release plan skeleton / v3.5.0 计划骨架**: `.plans/sd-image-sorter-release/v3.5.0-plan.md` records the phase gates, assumptions, and current first-stage scope.
  - 新增 `.plans/sd-image-sorter-release/v3.5.0-plan.md`，记录阶段门、假设和当前首阶段范围。
- **Smart Tag VLM grounding toggle / Smart Tag VLM 标签辅助开关**: VLM captioning can now explicitly disable sending booru tags as captioner context while keeping the default on.
  - Smart Tag 的 VLM 描述现在可显式关闭“把 booru 标签作为上下文发给 captioner”，默认仍保持开启。
- **Dataset caption polish quick actions / Dataset caption 微调快捷动作**: Caption polish now exposes Clear prefix, Reset template, and Refresh Chinese reading-aid actions with real handlers.
  - Dataset Caption 微调补上真实可用的清空前缀、重置模板、刷新中文阅读辅助按钮。

### Changed / 变更
- **Copies are no longer indexed into the library / 复制不再写入图库 (owner decision)**: every copy flow (Manual Sort copy mode, Auto-Separate copy, Gallery batch copy) used to insert a full DB row for each copied file — after one copy-based sort session the gallery showed every image twice. A copy is now a plain file output: the source row is untouched, the copy gets NO row (`new_image_id` is always null in move/copy results), and the copied file only enters the library if you scan its folder later. Sort-session undo still removes the copied file; old saved sessions with indexed copies keep their original undo behavior.
  - 所有复制流（手动排序复制模式、自动分类复制、图库批量复制）此前会为每个副本写入完整数据库行——用复制模式整理一轮后，图库每张图都变两张。现在复制就是纯文件输出：原图记录不动、副本不建行（move/copy 结果里的 `new_image_id` 恒为 null），副本只有在以后扫描它所在文件夹时才进入图库。排序会话的撤销仍会删掉副本文件；改动前保存的旧会话保持原有撤销行为。
- **Fresh Aurora design tokens / 「清爽极光」设计 token (Aurora Phase 1)**: the new last-loaded `tokens.css` owns the palette — blue-tinted dark surfaces, a three-step text ramp, three semantic accents (blue #5CC8FF = next action, pink #FF8FC0 = user decisions, purple #A78BFF = AI output), unified 2px blue focus rings, solid-blue primary buttons with dark ink, a flat canvas (neon washes/grid retired), and Noto Sans SC + IBM Plex Mono + Oswald typography. Every legacy token is remapped and ~470 hardcoded legacy colors across 13 stylesheets now reference tokens.
  - 全新最后加载的 `tokens.css` 接管全局配色：蓝调暗色表面、三阶文字色、三个各司其职的强调色（蓝 #5CC8FF=下一步、粉 #FF8FC0=用户决定、紫 #A78BFF=AI 产物）、统一 2px 蓝色焦点圈、实心蓝主按钮配深色文字、平坦画布（霓虹光斑与网格纹理退役）、思源黑体 + IBM Plex Mono + Oswald 字体组。所有旧 token 已重映射，13 个样式表中约 470 处硬编码旧色改为 token 引用。
- **Module extraction completed / 模块拆分收尾**: `app.js` now delegates RequestManager and localStorage helpers to `modules/core/` (the duplicated inline copies are removed), matching the architecture contract added with the main.py / sorting_service / image_service split.
  - `app.js` 的 RequestManager 与 localStorage 工具收束到 `modules/core/`（删除内联重复实现），与 main.py / sorting_service / image_service 拆分一起满足架构契约测试。
- **Dependency security / 依赖安全**: python-multipart bumped 0.0.27 → 0.0.31 (fixes CVE-2026-53538/53539/53540); the four starlette advisories whose fixes only exist on the 1.x line (incompatible with fastapi 0.136) are now reviewed-and-documented ignores alongside the existing entries — the dependency audit gate is green again.
  - python-multipart 升级 0.0.27 → 0.0.31（修复 CVE-2026-53538/53539/53540）；四条只在 starlette 1.x 才修复（与 fastapi 0.136 不兼容）的公告按既有惯例记录为已审阅忽略，依赖审计闸门恢复绿色。
- **Global component pass / 全局组件首轮收束**: nav tabs, buttons, danger actions, inputs, gallery toolbar, shared panels, modals, model cards, empty states, progress bars, toasts, and the Gallery selection panel now share the same visual language. Every primary action button across every view is now the single clean Aurora blue — the legacy orange primaries are fully retired.
  - 导航 tabs、按钮、危险动作、输入框、图库工具栏、共享面板、弹窗、模型卡、空状态、进度条、toast、图库多选面板完成首轮统一。全部视图的主操作按钮统一为干净的 Aurora 蓝，旧橙色主按钮全面退役。
- **Version metadata / 版本元数据**: app metadata, README download links, and release note scaffolding now target `3.5.0`.
  - app metadata、README 下载链接和 release notes 骨架已同步到 `3.5.0`。
- **Settings toggle rows: whole-row click + visible state / 设置开关行：整行可点 + 明确的开关状态 (owner feedback)**: the Sound / Entry page / ★5 cover rows only reacted on their small ghost button, whose on/off change was nearly invisible — they read as dead buttons. The whole row is now the click target and the button shows an unmistakable accent-on / dimmed-off state.
  - 声音 / 入口页 / ★5 门面三行此前只有右侧的小按钮能点，且按下后几乎看不出状态变化——被当成假按钮。现在整行都是点击区域，按钮有清晰的「亮=开 / 暗=关」状态。
- **Style Finder grid packs denser / 风格识别网格更密 (owner feedback, design rule: 网格=快速扫视)**: grid cards drop from 260px to 190px minimum with compact padding and a smaller avatar — roughly 2x more artists per row; list mode stays the detail view.
  - 网格卡片最小宽从 260px 降到 190px、内距收紧、头像改小——一行大约能放两倍的画师；列表模式继续承担详情浏览。
- **Smart Tag modal compacted / 智能标注弹窗收紧 (owner feedback)**: width capped at 880px; the booru section packs the two tagger pickers on one row and the four numeric fields on the next; number inputs are capped at 120px — a threshold box holds "0.35", not prose.
  - 弹窗宽度收到 880px；Booru 区第一行放两个 tagger 选择器、第二行放四个数字字段；数字输入框最宽 120px——阈值框装的是「0.35」，不是文章。

### Fixed / 修复
- **ComfyUI prompts behind custom conditioning nodes / ComfyUI 自定义 conditioning 节点后的提示词**: workflows that route the prompt through custom conditioning processors (e.g. the Anima node pack: `KSampler.positive → AnimaArtistCrossAttn → AnimaArtistPack → ShowText → Concatenate → Text`) parsed with an EMPTY positive prompt — the tracer only followed a fixed list of text-input keys and dead-ended on node-specific link names like `artist_pack`/`base_prompt`. The tracer now knows `base_prompt`/`positive`/`conditioning` channels and, when every known key misses, bridges through the remaining links (model/clip/vae/latent plumbing excluded) until a text chain resolves. An owner folder where **all 657 images** parsed without positive prompts now parses 657/657 with both positive and negative. Re-scan affected folders to refresh stored metadata.
  - 提示词经过自定义 conditioning 节点的 workflow（如 Anima 节点包：`KSampler.positive → AnimaArtistCrossAttn → AnimaArtistPack → ShowText → Concatenate → Text`）此前解析出的正向提示词是空的——追踪器只认固定的文字键位清单，遇到 `artist_pack`/`base_prompt` 这类节点私有的连线名就断链。现在追踪器认识 `base_prompt`/`positive`/`conditioning` 通道，且当所有已知键位都落空时会沿其余连线桥接（model/clip/vae/latent 等管线通道除外）直到找到文字链。所有者提供的一个 **657 张全军覆没**的文件夹现在 657/657 全部解析出正负提示词。受影响的文件夹重新扫描即可刷新已存的 metadata。
- **Whole-graph text fallback could shadow the real prompt / 全图文本回退会被负向提示词遮蔽**: the last-resort text harvest collected CLIPTextEncode nodes first and returned as soon as it found ANY — so a graph whose only literal encoder held the NEGATIVE prompt returned that negative as the positive, and the second-stage scan of other text nodes was dead code. The fallback is now a single scored pass over every text-bearing input (see the L2 scorer above), and it only fills the side (positive/negative) that tracing actually failed to resolve instead of overwriting a good traced value.
  - 兜底文本收割先收 CLIPTextEncode、收到任何结果就提前返回——如果图里唯一的字面 encoder 装的是负向提示词，它会被当成正向返回，而第二阶段对其他文本节点的扫描是永远跑不到的死代码。现在兜底是对所有含文本输入的单趟打分收割（见上方 L2 判分器），且只补追踪真正失败的那一侧（正向/负向），不再覆盖已追踪到的正确值。
- **Negative prompt could resolve to POSITIVE text through ControlNet chains / ControlNet 链上负向提示词可能解析成正向文本**: the tracer's generic key list contained `positive`, and first-hit-wins meant tracing `KSampler.negative` through a ControlNetApply node could walk the node's positive input and return the positive prompt as the negative. Tracing is now side-aware: the negative trace prefers `negative` channels and never bridges through the opposite side's conditioning link. Caught by the new workflow regression corpus on day one.
  - 追踪器的通用键位清单里有 `positive`，加上先到先得，`KSampler.negative` 经过 ControlNetApply 节点时可能沿其正向输入把正向提示词当成负向返回。现在追踪带方向：负向追踪优先 `negative` 通道，且永不桥接到对侧 conditioning 连线。这是新回归语料库上线第一天就抓到的 bug。
- **Search-syntax help stuck in its first language / 搜索语法帮助卡在首次打开的语言**: the help modal's rows are JS-rendered and cached, but the cache listened for the browser's OS-level `languagechange` event instead of the app's `languageChanged` — switching the interface language never re-rendered the rows. They now re-render on the app event (immediately if the modal is open).
  - 搜索语法帮助的行是 JS 渲染并缓存的，但缓存监听的是浏览器 OS 级的 `languagechange` 事件而不是应用自己的 `languageChanged`——切换界面语言后行内容永远不刷新。现在监听应用事件（弹窗开着时立即重渲染）。
- **Style Finder "use GPU" ignored on the ONNX path / 风格识别「可用时使用 GPU」在 ONNX 路径被忽略**: local `.onnx` artist models were loaded with onnxruntime's default provider order, which prefers CUDA when onnxruntime-gpu is installed — the toggle only ever affected the Kaloscope torch path. ONNX sessions now build their provider list from the toggle (CPU-only when off; intersected with actually-available providers so CPU-only installs never break).
  - 本地 `.onnx` 画师模型此前用 onnxruntime 默认 provider 顺序加载——装了 onnxruntime-gpu 时默认就是 CUDA 优先，开关只对 Kaloscope torch 路径有效。现在 ONNX session 的 provider 列表跟随开关（关=纯 CPU；并与实际可用 provider 求交集，纯 CPU 安装不会因此报错）。
- **Black flash when switching images in the detail view / 详情页切图黑闪**: navigating prev/next hid the current image (opacity 0 + gray skeleton block) until the next one finished loading — every switch flashed. The previous image now stays on screen and the next one swaps in only after it has decoded; the skeleton is reserved for the initial cold open.
  - 详情页上一张/下一张时会先把当前图隐藏（透明度 0 + 灰色骨架块）等下一张加载完——每次切换都闪一下。现在上一张图保持显示，下一张解码完成后瞬间替换；骨架屏只保留给首次冷打开。
- **Scrollbar no longer re-wraps panel text / 滚动条不再挤压文字换行**: expanding a section (e.g. LoRAs in the gallery sidebar) made the panel overflow, the appearing scrollbar stole 8px of width, and every label re-wrapped. Scroll containers with user-driven content height (gallery sidebar, modal bodies, detail info column, tools menu) now reserve the scrollbar gutter permanently, so content width never changes.
  - 展开某个区块（如图库侧栏的 LoRA 列表）导致面板超高时，突然出现的滚动条会吃掉 8px 宽度、所有文字重新换行。内容高度随操作变化的滚动容器（图库侧栏、弹窗主体、详情信息栏、工具菜单）现在永久保留滚动条位置，内容宽度不再变化。
- **WASD slot cards: folder buttons outside the box / WASD 槽位卡的文件夹按钮跑出卡片**: the A/D cards' path inputs refused to shrink below the browser's ~170px input minimum, pushing the 📁 browse button clean out of the card border. Inputs may now shrink (`min-width: 0`) and the button never compresses.
  - A/D 槽位卡的路径输入框不肯缩到浏览器默认的约 170px 以下，把 📁 浏览按钮整个挤出了卡片边框。现在输入框允许收缩（`min-width: 0`），按钮永不被压扁。
- **UI text coverage + Simplified-Chinese purity / 界面文案补全与简体统一**: 13 toast/button strings that silently fell back to English (collection-picker errors, the aesthetic-failure toast, dataset retry/status labels, remove-background Preview/Apply, the Smart-Tag progress error, the large-export notice, sort resume counters) now have proper entries in both language packs, and ~20 Dataset template-help strings that shipped in Traditional Chinese are converted to Simplified Chinese.
  - 13 条此前静默回退英文的提示/按钮文案（合集选择器错误、美学评分失败提示、数据集重试/状态、去背景预览/应用、Smart Tag 进度错误、大文件导出提示、排序恢复计数等）补上双语词条；Dataset 模板帮助区约 20 条繁体中文全部转换为简体。
- **Linux GPU tagging / Linux GPU 打标修复**: Linux installs only ever got the CPU-only `onnxruntime` (pip platform markers cannot select by hardware), so WD14/NudeNet/CLIP stayed on CPU even with an NVIDIA card — as a Linux portable user reported. `repair_onnxruntime.py` now supports Linux: it detects NVIDIA via `nvidia-smi` and swaps in `onnxruntime-gpu[cuda,cudnn]` (x86_64; bundles the CUDA 12 + cuDNN 9 runtime wheels). The Linux portable launcher runs the repair unconditionally at startup (matching Windows portable), and WD14 Prepare in Feature Setup triggers it on Linux too. Non-NVIDIA machines keep the small CPU runtime; aarch64 is skipped (no PyPI wheels).
  - Linux 此前只会装 CPU 版 `onnxruntime`（pip 平台标记无法按硬件选包），有 NVIDIA 卡也只能 CPU 打标——正如一位 Linux portable 用户报告的那样。`repair_onnxruntime.py` 现已支持 Linux：用 `nvidia-smi` 检测到 NVIDIA 后换装 `onnxruntime-gpu[cuda,cudnn]`（x86_64；自带 CUDA 12 + cuDNN 9 运行库 wheel）。Linux portable 启动器现在启动时无条件运行该修复（与 Windows portable 一致），Feature Setup 里的 WD14 Prepare 在 Linux 上同样会触发。非 NVIDIA 机器保持小体积 CPU 运行时；aarch64 无 PyPI wheel，自动跳过。
- **Dataset Workbench right-pane reachability / Dataset 工作台右侧栏可达性**: the right operation pane now scrolls in Workbench mode, so optional caption-polish controls are reachable instead of being clipped below the viewport.
  - Dataset Workbench 右侧操作栏现在可滚动，Caption 微调里的可选控件不会被裁在视口外不可达。
- **ESC no longer hijacks open menus or selection mode / ESC 不再劫持打开的菜单与选择模式**: the ESC-to-entry shortcut fired from a capture-phase listener that could run before an open dropdown or the gallery selection mode handled the same keypress (listener registration order), so ESC with the More ▾ menu open could bounce you to the entry page. The entry shortcut now declaratively defers to the gallery More menu, the nav tools menu, and active selection mode — ESC closes/clears those first; only a bare ESC goes home.
  - 「任意位置 ESC 回入口」此前在捕获阶段监听，可能抢在打开的下拉菜单 / 图库选择模式之前处理同一次按键（取决于监听器注册顺序）——开着「更多 ▾」按 ESC 会被弹回入口页。现在入口快捷键会显式让位给图库「更多」菜单、导航工具菜单与选择模式：ESC 先关菜单/清选择，空手再按才回入口。
- **Tools-menu modal entries no longer black out the views / 工具菜单的弹窗入口不再黑屏**: every `.nav-tab` click ran `switchView(tab.dataset.view)`, but the Tools-menu entries that open modals (Duplicate Cleanup, and now Publish Set) carry no `data-view` — `switchView(undefined)` hid every view and left the background black behind the modal. The shared binding now ignores tabs without a target view.
  - 所有 `.nav-tab` 点击都会执行 `switchView(tab.dataset.view)`，但工具菜单里打开弹窗的入口（查重清理、新增的成套发布）没有 `data-view`——`switchView(undefined)` 会把所有视图藏起来，弹窗背后一片黑。共享绑定现在会忽略没有目标视图的按钮。
- **Gallery reflows when the sidebar collapses / 收合侧栏后图库即时重排 (owner FB-4)**: virtual-list items were positioned once at creation and never moved again — collapsing the filter sidebar (or any width change) left a dead band where the sidebar used to be, with the grid stuck at its old column count. Layout refresh now repositions every rendered tile in place (loaded thumbnails don't flash) and re-evaluates the visible range, driven by both the collapse button and the container ResizeObserver.
  - 虚拟列表的图块此前只在创建时定位一次、之后永不移动——收合筛选侧栏（或任何宽度变化）后，原侧栏位置留下一条空带，网格卡在旧列数。现在布局刷新会就地重排所有已渲染图块（已加载的缩略图不闪烁）并重算可见范围，收合按钮与容器 ResizeObserver 两条路径都生效。
- **4-agent UX audit, round 1: one-page rule for the owner-named disasters / 4 agent UX 审计第一轮：所有者点名灾难的一页化 (design rule 6)**: owner added design rule #6 — *if it can be one page, widen the window instead of scrolling*. The dataset 分屏 compare panel no longer auto-flows into the editor grid's 640px image column (full-width now, and its placeholder title is a real explanation instead of the literal key text); the Smart Tag modal grows 880→1360px into a two-column zero-scroll layout; the VLM settings modal becomes the same 1360px two-column grid (the prompt template details opens by default); Settings & Models reorganizes into 4 tabs (General / Models / Disk / Audit — was a single 3000-4700px scroll). Also: the nav More ▾ menu is ALWAYS visible (Duplicate Cleanup and Publish Set were unreachable at ≥1920px where the overflow ladder never engaged), dataset caption textareas grow to fill their 470px void, an empty trigger word now renumbers files as `001.png` instead of `_001.png`, and the similarity tab's ready-state header no longer contradicts its own index count.
  - 所有者新增设计规则 #6——**能一页就一页，把窗口做宽而不是让人滚动**。数据集「分屏」对比面板不再流进编辑器网格 640px 的图片列（现在全宽，占位标题也从字面量键名换成真正的说明）；Smart Tag 弹窗 880→1360px 两栏零滚动；VLM 设置弹窗同样改为 1360px 两栏网格（提示词模板默认展开）；「设置与模型」重组为 4 个分页（通用 / 模型 / 磁盘 / 体检——此前是一条 3000-4700px 的长滚动）。另外：导航「更多 ▾」菜单永远可见（≥1920px 时溢出阶梯不触发，查重清理和成套发布曾经完全无法到达）、数据集 caption 输入框自动填满原本 470px 的空洞、触发词为空时导出编号从 `_001.png` 修正为 `001.png`、相似页「模型已就绪」标题不再与索引计数自相矛盾。
- **4-agent UX audit, round 2: the WASD stage HUD was invisible / 4 agent UX 审计第二轮：WASD 舞台 HUD 整组隐形**: the sort stage's 🧘 focus and 🔊 mute buttons plus the progress bar/stats were unpositioned flow children rendered UNDER the z-indexed image — invisible and unclickable (the segmented sorted/skipped bar never even had CSS). The HUD is now pinned to stage corners: mute + focus top-left (mirroring the working 退出 top-right), progress + throughput bottom-right, and the minimap moves bottom-left — it used to sit 800px wide dead-center ON TOP of the S drop zone. All verified by live hit-testing at 1920×1080.
  - 排序舞台的 🧘 专注、🔊 静音按钮和进度条/统计原是无定位的流式元素，被带 z-index 的图片盖住——看不见也点不到（已排序/已跳过分段条更是从未有过 CSS）。HUD 现在钉在舞台四角：静音+专注在左上（与右上正常工作的「退出」对称）、进度+速度在右下、缩略图导航条移到左下——它此前 800px 宽横在正中央、正好压住 S 投放区。全部经 1920×1080 实测点击命中验证。
- **UX audit round 2, sort setup & filter modal one-paged / 第二轮：排序设置与筛选弹窗一页化 (rule 6)**: the Manual Sort setup was a 1900px+ single column inside a 980px card — it now splits into destinations (left, with A|D side by side and the start CTA) and scope/behavior (right) inside a 1460px card; the shared filter modal grows 1100→1560px and its primary column flows panels two-up (scroll height 3006→1514px, roughly one screen at 1440p). The entry page's "Manual Sort" tile now actually lands on the Manual Sort sub-tab instead of whatever sorting tab was last active, and the filter modal footer note is mode-aware — it no longer promises to "refresh the gallery" when opened from Manual Sort / Auto-Separate / the censor queue, where it does no such thing.
  - 手动排序设置页原是 980px 卡片里 1900px+ 的单列长卷——现在 1460px 卡片内拆成左（目标槽位，A|D 并排 + 开始按钮）右（筛选范围与行为）两栏；共用筛选弹窗 1100→1560px，主列面板两两并排（滚动高度 3006→1514px，1440p 下约一屏）。入口页「手动排序」磁贴现在真的落在手动排序子页（此前落在上次激活的整理子页上）；筛选弹窗底部说明按入口模式切换文案——从手动排序/自动分类/打码队列打开时不再谎称「会刷新图库结果」。
- **Style Finder stats no longer surface sub-threshold noise / 风格识别统计不再泛起低于阈值的噪声**: legacy `artist_predictions` rows written before the identify pipeline enforced its confidence floor could surface a "found artist" at 0.1% confidence on a library that never ran identification. The stats aggregation now folds rows below the default threshold (3%) into 未定 instead of listing them as discoveries.
  - 旧版识别管线未强制置信度下限时写入的 `artist_predictions` 行，会让从未跑过识别的图库显示一个 0.1% 置信度的「已发现画师」。统计聚合现在把低于默认阈值（3%）的行并入「未定」，不再当成发现列出。
- **Similar tab now bridges to Duplicate Cleanup / 相似页新增「查重清理」入口**: the Similar tab's pair-based Find Duplicates and the whole-library group-based Duplicate Cleanup didn't know about each other; the Duplicates panel now carries a one-line explanation of the difference plus a button that opens Duplicate Cleanup directly.
  - 相似页的成对查重与整库分组的「查重清理」互不知情；查重面板现在有一行差异说明 + 直达「查重清理」的按钮。
- **UX audit round 2, polish batch / 第二轮杂项**: scan/move/batch-move progress and completion messages from the backend are now bilingual (they surfaced raw English in the progress bars and toasts — "Reading image details…", "Completed! Copied N images."); one feature had three Chinese names (手动分拣/手动排序/手动分类) and is now 手动排序 everywhere, matching 自动分类; the global 色彩分析 toast no longer opens (or lingers) as a stuck-looking "0/0 0%" when there is nothing to analyze; tiny scans no longer stack two info toasts within a second; the Auto-Separate action pane loses its ~240px dead gap above the run button; the sort resume banner's Resume/Discard buttons are finally translatable.
  - 后端扫描/移动/批量移动的进度与完成消息全部改为双语（此前进度条和提示直接冒英文——"Reading image details…"、"Completed! Copied N images."）；同一个功能的三个中文名（手动分拣/手动排序/手动分类）统一为「手动排序」，与「自动分类」对仗；全局「色彩分析」悬浮条在没有可分析图片时不再以「0/0 0%」的假死样子打开或滞留；小图库扫描不再一秒内叠两条提示；自动分类执行栏的运行按钮上方去掉约 240px 空洞；排序恢复横幅的 Resume/Discard 按钮终于可翻译。

### Notes / 注意
- No user workflow, default sort/copy behavior, backend API contract, DOM id, or destructive action default was changed in this first stage.
  - 首阶段未改变用户工作流、排序/复制默认行为、后端 API 契约、DOM id 或危险动作默认值。
- Release package build, `lazy_release_qa.py`, and real portable boot smoke are still pending the next phase gate.
  - release package 构建、`lazy_release_qa.py`、真实 portable 启动 smoke 仍等待下一阶段门。

## [3.4.3] - 2026-06-12

ToriiGate「详细NL」不再输出半截 JSON：模型原始 JSON 在写入点解析为纯句子，旧的脏数据由迁移自动清洗；WD14+ToriiGate 改为两阶段流水线（先全部打标→卸载→再描述），修复整机黑屏/崩溃。合集支持批量加入（多选面板按钮 + 扫描完成一键建合集），Dataset Maker 补上 Smart Tag 并重做 Split 比较编辑器，浮层定位和扫描刷新也完成修复。

ToriiGate "detailed NL" captions no longer leak truncated raw JSON: model output is parsed to plain prose at the write point and a migration heals previously stored rows. WD14+ToriiGate now runs as a two-phase pipeline (tag everything, unload, then caption) — fixing whole-machine black screens. Collections gain bulk add (selection-panel button + one-click collection after scan), Dataset Maker gains Smart Tag and a rebuilt Split comparison editor, and popup positioning plus scan refresh behavior are fixed.

### Fixed / 修复
- **"Detailed NL" captions were truncated raw JSON / 「详细NL」输出半截 JSON**: ToriiGate is JSON-finetuned and often answers `{"description": ..., "tags": ...}`; the pipeline stored that raw text (cut off at 160 tokens) into `nl_caption`/`ai_caption`, so exports contained broken JSON. Output is now sanitized at the write point (full parse → truncated-string recovery → wrapper strip), prompts explicitly forbid JSON, the detailed token budget is 512, and the cloud-VLM `nl_caption` path parses JSON-shaped replies too. **Migration 019** cleans previously stored rows (idempotent, conservative — only JSON-shaped text is touched).
  - ToriiGate 是 JSON 重度微调模型，常回 `{"description": ..., "tags": ...}`；流水线把这段原文（且在 160 token 处截断）直接存进 `nl_caption`/`ai_caption`，导出自然是烂的。现在写入点强制解析为纯句子（完整解析→半截字符串恢复→剥壳兜底），提示词明确禁止 JSON，详细模式 token 上限提到 512，云端 VLM 的 `nl_caption` 路径同样解析 JSON 形态回复。**迁移 019** 自动清洗历史脏数据（幂等、保守，只动 JSON 形态文本）。
- **Whole-machine black screens during WD14+ToriiGate / WD14+ToriiGate 跑图整机黑屏**: both models used to load up front and stay resident together; on mid-VRAM cards ToriiGate's GPU load failed and fell back to CPU **float32 (~20+GB RAM)**, taking the whole OS down. The pipeline is now two-phase — WD14 tags everything, releases its session, only then does ToriiGate load with the full GPU. Plus three guards: a GPU pre-flight headroom check, a CPU dtype guard (fp32 only with ~24GB+ free RAM, bf16 with ~13GB+, otherwise a clear error instead of an OS crash), and periodic memory-pressure checks during captioning. Booru tags are persisted even if the caption phase fails to load. Thresholds tunable via `SD_TORIIGATE_*` env vars.
  - 此前两个模型开跑就同时驻留；中等显存的卡上 ToriiGate GPU 加载失败后以 **float32 回退到 CPU（吃 20+GB 内存）**，直接把系统干崩。现在改为两阶段：WD14 全部打完标→释放会话→ToriiGate 独占全部显存再加载。另加三道防护：GPU 余量预检、CPU 精度守门（空闲内存 ~24GB+ 才用 fp32，~13GB+ 用 bf16，再不够给出明确报错而不是让系统崩溃）、描述阶段周期性内存压力检查。描述阶段加载失败时已打的 booru 标签照常落库。阈值可用 `SD_TORIIGATE_*` 环境变量调整。
- **Caption editor: NL text missing, tag colors missing / 字幕编辑器看不到 NL、标签没颜色**: "Both"/"Natural Language" modes showed only tags because one seeding path ignored the stored `ai_caption` fallback; fixed. Tag chips in the caption editor now carry the 14-category danbooru colors (artist/character/copyright/...), and switching content mode refreshes the preview immediately.
  - 「两者」/「自然语言」模式只显示标签——其中一条数据填充路径漏掉了 `ai_caption` 兜底，已修。字幕编辑器里的标签芯片现在带 14 类 danbooru 分类颜色，切换内容模式即时刷新预览。
- **Popup positioning + collection picker / 浮层定位与加入合集**: selection-panel "Add to collection" now opens the picker, and Gallery right-click menus / collection pickers / Tools / update popups / autocomplete share one viewport-safe positioner that handles UI zoom and screen edges.
  - 多选面板「加入合集」现在会打开选择器；图库右键菜单、合集选择器、Tools、更新弹窗、自动完成统一使用视窗安全定位，处理 UI 缩放与屏幕边界。
- **Dataset Maker Split UI / Dataset Maker 分割比较界面**: Split is now a two-card comparison editor with image previews, separate Booru and natural-language fields, labels, open-next/close actions, and no overlap with the base editor controls.
  - 「分割」改成双卡比较编辑器：图片预览、Booru 与自然语言独立字段、清楚标签、打开下一张/关闭操作，并且不会被原编辑器按钮遮挡。
- **Disk usage and scan completion refresh / 磁盘占用与扫描完成刷新**: cache/runtime usage reports exact byte counts from an iterative `os.scandir` scan instead of "Large / not fully scanned"; completed scans refresh the folder tree automatically.
  - 缓存/运行时占用改用迭代式 `os.scandir` 精确扫描并显示真实数值，不再显示「较大 / 未完整扫描」；扫描完成后 Folders 树自动刷新。
- **Training-purpose tag filtering / 训练用途标签过滤**: Smart Tag purpose filtering now applies to the final caption and stored tag rows. The rule is conservative because official Kohya/Diffusers docs define caption mechanics, not a universal LoRA-purpose deletion table: Style removes only clearly style/artist-like general tags; Character removes detected character names only when a trigger word is set; General/Concept preserve context.
  - Smart Tag「训练用途」现在真正作用到最终 caption 与落库 tag rows。规则保守，因为 Kohya/Diffusers 官方文档定义的是 caption 机制，不是 LoRA 用途删标表：Style 只移除明确像风格/画师的 general tags；Character 只有设置 trigger word 时才移除检测到的角色名；General/Concept 保留上下文。
- **`@xxx` / `artist:xxx` recognized as artist tags / `@xxx`、`artist:xxx` 识别为画师标签**: Anima-style `@name` and SDXL-style `artist:name` prompts now categorize (and color) as artist/style instead of "general".
  - Anima 的 `@名字`、SDXL 的 `artist:名字` 风格提示词现在归入画师分类并显示对应颜色，不再当普通标签。

### Added / 新增
- **ToriiGate options + tag grounding / ToriiGate 参数与标签辅助**: Smart Tag's ToriiGate mode gains a "description length" select (detailed by default, brief available) and a default-on "ground with booru tags" toggle that feeds phase-one WD14 tags into ToriiGate for more accurate descriptions.
  - Smart Tag 的 ToriiGate 模式新增「描述长度」选择（默认详细，可选简短）和默认开启的「以 booru 标签辅助」开关——把第一阶段 WD14 的标签喂给 ToriiGate 做参照，描述更准。
- **Bulk add to collection / 批量加入合集**: new `POST /api/collections/{id}/items/bulk` accepts explicit ids or a selection token (whole filtered scope); the multi-select panel gains an "Add to collection" button; the right-click picker now uses one bulk call instead of N requests.
  - 新增批量接口，支持显式 id 列表或筛选范围 token；多选面板新增「加入合集」按钮；右键加入合集也改为一次批量调用。
- **One-click collection after scan / 扫描完成一键建合集**: when a scan imports new images, the "what's next" banner offers "Create collection" — names it after the folder and bulk-adds everything just imported, so separate datasets stop blurring together in the gallery.
  - 扫描导入新图后，完成横幅新增「建立合集」按钮——按文件夹命名并把刚导入的图片批量加入，不同数据集不再混在一起。
- **VLM caption generation parameters in UI / VLM 描述生成参数进界面**: caption `max_tokens` (was hardcoded 1024) and `temperature` (was 0.3) are now configurable in VLM Advanced Settings, alongside previously backend-only `retry_delay`, `max_image_size`, and the NSFW retry prompt.
  - 描述用的 `max_tokens`（此前写死 1024）和 `temperature`（0.3）现在可在 VLM 高级设置里调，同时补上后端早已支持但界面缺失的重试间隔、最大图片尺寸、NSFW 重试提示词。
- **Multi-line, persistent custom templates / 自订模板多行+记忆**: export template override fields are now textareas — free words, spaces, and blank lines around `{placeholders}` survive rendering; the field content persists across reloads instead of resetting to default.
  - 模板覆盖输入框改为多行——`{占位符}` 周围的自由文字、空格、空行都会保留进输出；内容跨刷新记忆，不再每次回到默认。
- **Smart Tag VLM jobs force `nl_caption` output / Smart Tag 强制自然语言输出格式**: per-job VLM config now pins `output_format=nl_caption`, so presets configured for JSON analysis can't corrupt caption runs.
  - 每个任务的 VLM 配置强制 `output_format=nl_caption`，为 JSON 分析配置的预设不会再污染描述任务。
- **Dataset Maker Smart Tag + clearer entry points / Dataset Maker Smart Tag 与入口整理**: Dataset Maker's right side now exposes Smart Tag. Mass Tag Editor opens with the expected scope from its entry point (current selection from the selection panel, current filter from the filter modal), and Prompt Helper / Style Finder move under Tools to reduce navbar crowding.
  - Dataset Maker 右侧补上 Smart Tag。Mass Tag Editor 会按入口使用预期范围（多选面板=当前选择、Filter 视窗=当前筛选），Prompt Helper / Style Finder 移入 Tools，减少导航栏拥挤。

### Upgrading / 升级注意
- Migration 019 runs automatically on first start and only rewrites JSON-shaped `nl_caption`/`ai_caption` text; clean rows are untouched. ToriiGate now defaults to **detailed** descriptions (~3× slower per image than before) — switch to "brief" in Smart Tag's ToriiGate options if you preferred the old speed.
  - 迁移 019 首次启动自动执行，只改写 JSON 形态的描述文本，干净数据不动。ToriiGate 默认改为**详细**描述（单张比以前慢约 3 倍）——想要旧速度可在 Smart Tag 的 ToriiGate 选项里切回「简短」。

## [3.4.2] - 2026-06-12

AI jobs now queue instead of failing: starting tagging / Smart Tag / VLM captioning while another AI job runs enqueues it (FIFO) and it auto-starts when the current job finishes — no more 409 "busy, come back later". The Clear Library button is back on the gallery page where you can see it. Filter presets finally have their UI, the WASD combo counter is visible again, and `/api/prompts/generate` honors `count`.

AI 任务现在会排队而不是报错：当另一个 AI 任务在跑时启动打标 / Smart Tag / VLM 描述，会自动加入队列（先进先出），当前任务结束后自动开始——不再弹 409"忙碌请稍后"。清空图库按钮回到图库页面一眼可见的位置。筛选预设终于有了界面入口，WASD 连击计数重新可见，`/api/prompts/generate` 的 `count` 参数真正生效。

### Added / 新增
- **AI job queue / AI 任务队列**: gallery tagging, Smart Tag, and VLM caption batches share a FIFO queue. A busy runtime returns `{"status": "queued", "queue_position": N}` (duplicate consecutive submits are merged with `duplicate: true`); the queue drains automatically after success, error, or cancel; each kind's cancel endpoint also removes its queued entries; pollers show "Queued #N / 排队中 #N" and an F5 while queued re-attaches the progress UI. The queue is in-memory and does not survive a restart. 409 remains only for the fail-closed sibling-status-unknown case.
  - 图库打标、Smart Tag、VLM 批量描述共用一个先进先出队列。运行中再启动会返回排队状态（连续重复提交自动合并）；当前任务成功、出错或取消后队列自动继续；各自的取消接口同时清掉排队中的同类任务；进度条显示"排队中 #N"，排队期间 F5 后进度界面自动恢复。队列在内存中，重启服务后不保留。仅"无法确认兄弟任务状态"的保守拒绝场景仍返回 409。
- **Filter presets UI / 筛选预设界面**: the save/load/delete preset logic existed since earlier versions but had no buttons — the filter editor now has a presets bar (name input, save button, preset chips with load/delete).
  - 保存/载入/删除筛选预设的逻辑早已存在但一直没有入口按钮——筛选编辑器现在有了预设栏（命名、保存、点击载入、删除）。

### Fixed / 修复
- **Clear Library button visible on the gallery page / 清空图库按钮回到图库页面**: it was hidden inside the Import modal's collapsed "Advanced options" where nobody could find it. It now sits at the right end of the gallery toolbar — always visible, danger-styled, separated from everyday controls, with the same double-confirmation flow.
  - 此前藏在导入弹窗折叠的"高级选项"里根本找不到。现在固定显示在图库工具栏最右端——红色危险样式、与常用按钮保持距离，确认弹窗流程不变。
- **Prompts `count` honored / Prompt 生成 count 参数生效**: `/api/prompts/generate` accepted `count` (1-20) but always returned one prompt. It now returns a reproducible `prompts[]` batch (fixed seed gives seed+i per slot); the single-prompt top-level response shape is unchanged for existing callers.
  - `/api/prompts/generate` 此前接受 `count`（1-20）却永远只回一条。现在返回可复现的 `prompts[]` 批量（固定 seed 时第 i 条用 seed+i）；顶层单条响应结构保持不变。
- **WASD combo counter visible again / WASD 连击计数重新可见**: the combo kept counting after every successful sort action, but its display element was accidentally dropped in the v2.6.0 markup restructure — restored.
  - 连击其实一直在计数，但其显示元素在 v2.6.0 重构时被误删——已恢复。
- **VLM batch start no longer blocks the server / VLM 批量启动不再阻塞服务器**: counting a large filtered selection at batch start ran on the event loop; it now runs in a worker thread.
  - 批量启动时统计大筛选集的查询此前跑在事件循环上，现已移入工作线程。
- **/api/dataset/translate docs match reality / 翻译接口文档与实现一致**: the docs described request fields and per-item error semantics that never existed; rewritten to the real VLM/external provider contract (no behavior change).
  - 文档此前描述了从不存在的请求字段与逐条错误语义；已按真实的 VLM/外部翻译提供方契约重写（行为无变化）。
- **Model Manager manual-upgrade hint / 模型管理器升级提示**: a static hint now explains that downloaded models live in the old folder's `data` directory when upgrading by unzipping into a new folder — nothing is lost, copy the folder over.
  - 模型管理器新增固定提示：手动解压新版本到新文件夹后模型"全部缺失"时，模型其实都在旧文件夹的 `data` 目录里，整个复制过来即可恢复。

### Upgrading / 升级注意
- No database migration. The AI-job 409 "busy" contract is gone for the three start endpoints — third-party scripts (if any) should treat `{"status": "queued"}` as success-pending instead of retrying.
  - 不含数据库迁移。三个 AI 启动接口不再返回 409"忙碌"——如有第三方脚本，请把 `{"status": "queued"}` 当作"已受理待执行"处理，无需重试。
- **Manual upgraders / 手动升级用户**: if you unzip a new release into a NEW folder, copy the old folder's entire `data` directory into it first — it holds your library database AND all downloaded models. Skipping this makes every model show "missing" (nothing is actually lost).
  - 如果你是解压新 zip 到**新文件夹**升级：请先把旧文件夹的整个 `data` 目录复制过去——里面有你的图库数据库和所有已下载模型。不复制的话所有模型会显示"缺失"（其实什么都没丢）。

### Verified, no change needed / 验证无需修改
- The CLIP similarity ANN index roadmap item was already shipped in v3.3.2 (hnswlib top-k bypass with exact re-scoring + persisted vector cache; 50k-image exact search measured at ~13ms). The optional hnswlib accelerator stays out of the default install on purpose — wheels are not guaranteed on every platform and the exact path is already fast.
  - CLIP 相似度 ANN 索引这一路线图项目其实在 v3.3.2 已上线（hnswlib top-k 加速 + 精确重打分 + 持久化向量缓存；5 万图精确搜索实测约 13ms）。可选的 hnswlib 加速器刻意不进默认安装——并非所有平台都有预编译包，且精确路径已经够快。

## [3.4.1] - 2026-06-11

Smart Tag now honors "skip images that already have AI tags": a new default-on checkbox skips already-tagged gallery images (no tagger call, no VLM caption), shows a skipped count in progress and the completion message, and can be unchecked to re-tag. Previously the documented `skip_existing` option was accepted but never applied. Also fixes three non-functional UI surfaces: the SAM3 confidence slider, the Quick Auto Censor button, and Analytics tag clicks.

Smart Tag 现在真正支持"跳过已有 AI 标签的图片"：新增默认勾选的选项，已标记的图库图片会被直接跳过（不跑 tagger、不跑 VLM），进度与完成信息显示跳过数量；想重新打标取消勾选即可。此前文档中的 `skip_existing` 参数虽被接受但从未生效。同时修复三处"假功能"界面：SAM3 置信度滑块、一键自动打码按钮、统计面板标签点击。

### Added / 新增
- **Smart Tag skip-existing**: skips DB images whose `tagged_at` marker is set (same definition as the gallery's untagged filter — an image tagged with zero matches still counts as tagged). Applies to both selected images and filter/selection scopes; Dataset Maker local-file sources are never skipped. Progress payload gains a `skipped` count. On a tag-state lookup failure the run fails open and processes everything rather than silently dropping work.
  - **Smart Tag 跳过已标记**：以 `tagged_at` 标记判定（与图库"未标记"筛选同一定义；打过标但零命中的图也算已标记）。选中图片与筛选范围都生效；数据集制作的本地文件来源不受影响。进度新增 `skipped` 计数；查询失败时宁可全量重打也不静默丢图。

### Fixed / 修复
- **ComfyUI runtime-generated prompts**: workflows that build the positive prompt at runtime (e.g. a VLM like Qwen3-VL feeding CLIPTextEncode through ShowText) no longer extract a stale cached prompt from a previous run — queuing 5 runs in a batch used to stamp all 5 images with the prompt of an older, different image. The parser now resolves current-run literals upstream first (including DanbooruGallery selections, the actual source post of the run) and only falls back to display caches when nothing else is recoverable. Re-parse affected images via the preview window's "Re-read info" or by rescanning the folder.
  - **ComfyUI 运行时生成的提示词**：正向提示词在运行时生成的工作流（如 Qwen3-VL 经 ShowText 接入 CLIPTextEncode）不再抽到上一轮的陈旧缓存——以前一次排队 5 张会让 5 张全部带上更早另一张图的提示词。解析器现在优先回溯本轮的字面值（包括 DanbooruGallery 的本轮选图），全部不可得时才退回显示缓存。受影响的图片可用预览窗"重新读取信息"或重新扫描该文件夹刷新。
- **SAM3 confidence slider now works**: the censor editor's confidence slider was sent to the API but never consumed — every refinement ran at fixed thresholds regardless of the slider. The value now gates both the mask score and the text-prompt presence check; low-confidence refinements fall back to bounding-box censoring (counted separately in the UI). `/api/censor/refine-mask` and per-item batch overrides accept the new optional `sam3_confidence` field.
  - **SAM3 置信度滑块真正生效**：打码编辑器的置信度滑块此前虽随请求发送但后端从未使用——无论怎么调，细化都按固定阈值跑。现在滑块同时控制掩码得分与文本提示存在性两道门槛；低置信度的细化会退回边界框打码（UI 单独计数）。`/api/censor/refine-mask` 与批量逐项覆盖均支持新增的可选 `sam3_confidence` 字段。
- **Quick Auto Censor button restored**: the one-click "detect + censor the whole queue" flow had a live handler and help copy but the button itself was missing from the page, making the documented feature unreachable. The button is back in the censor sidebar's Auto Detect card; the underlying pipeline was audited end-to-end and needed no repairs.
  - **一键自动打码按钮恢复**：「检测+打码整个队列」的一键流程有完整的处理逻辑和帮助文案，但页面上的按钮本身丢失，文档承诺的功能无法触达。按钮已恢复到打码侧栏的自动检测卡片中；底层流程经逐行审计无需修复。
- **Analytics tag click no longer crashes**: clicking a tag in the Analytics panel threw a silent error against a removed DOM element — the filter was applied internally but the modal never closed, the gallery never reloaded, and the active-tag chip never appeared. Tag clicks now use the same renderer as the rest of the tag filter UI.
  - **统计面板标签点击不再崩溃**：在统计面板点击标签会对一个已被移除的 DOM 元素抛出静默错误——筛选内部已生效，但弹窗不关闭、图库不刷新、标签条也不出现。现在与其余标签筛选 UI 使用同一渲染路径。

### Upgrading / 升级注意
- No database migration. If you routinely re-tag existing images, uncheck the new "Skip images that already have AI tags" box in the Smart Tag dialog.
  - 不含数据库迁移。如果你习惯对已标记图片重新打标，请在 Smart Tag 对话框中取消勾选"跳过已有 AI 标签的图片"。

## [3.4.0] - 2026-06-10

Full-pipeline reliability release driven by a three-track audit (frontend UI/UX, backend, end-to-end pipeline). Tag categories are correct again (clothing tags no longer leak into "background", protecting LoRA caption exports), bulk tag operations and AI jobs now cover exactly the scope you filtered, long-running jobs report real errors instead of fake success and survive page reloads, and censored images refresh their thumbnails immediately.

由三路全面体检（前端 UI/UX、后端、端到端流程）驱动的可靠性大版本。标签分类恢复正确（服装类标签不再被误判为"背景"，保护 LoRA caption 导出）；批量标签操作与 AI 任务的作用范围与你筛选的完全一致；长任务出错时如实报错、刷新页面后可恢复；打码保存后缩略图立即更新。

### Fixed / 修复
- **Tag categorization regression**: common outfit/body/action tags (tank_top, pencil_skirt, winter_coat, holding_egg…) were misrouted to "background" since v3.3.3; dataset export "remove background tags" could silently strip clothing tags from LoRA training captions. Category rules reordered with a garment veto and locked by regression tests.
  - **标签分类回归**：v3.3.3 起 tank_top、pencil_skirt、winter_coat 等常见服装/动作标签被误分到"背景"，数据集导出勾选"移除背景标签"时会悄悄删掉训练 caption 里的服装标签。已重排分类规则并用回归测试锁定。
- **Bulk tag operations skipped images**: mass remove/find-replace/cleanup committed per chunk while paging the same shrinking filter — with >500 matches roughly half were silently skipped. All bulk scopes (tags, Smart Tag, VLM batch) now snapshot matching IDs before mutating.
  - **批量标签操作漏图**：批量移除/查找替换边改边翻页，超过 500 张时约一半被静默跳过。现在所有批量范围先快照 ID 再修改。
- **Sorting scope fidelity**: Auto-Separate "Copy from Gallery", WASD manual sort, and batch-move now honor collection, folder, star-rating, has-metadata, exclude-prompts/colors, and brightness/color filters — the moved set equals what the gallery shows, and the "matches gallery" indicator is truthful.
  - **整理范围保真**：自动分流"从图库复制"、WASD 手动整理与批量移动现在完整继承合集/文件夹/星级/排除词/明暗等筛选，移动范围与图库所见一致，"与图库筛选一致"指示不再误报。
- **Job reliability overhaul**: scan progress no longer freezes when polled during startup; Dataset Maker "Tag all" attaches the real progress bar; aesthetic/artist batch crashes show errors instead of success toasts; Smart Tag and VLM batches retry transient network errors, resume after F5 (cancel reachable again), and VLM batch covers the full filtered set instead of only the loaded page; completed VLM batches no longer pop open a closed preview window.
  - **任务可靠性整顿**：扫描刚启动时轮询不再卡死；数据集制作"全部打标"接上真实进度条；美学/画师批次崩溃时如实报错而非显示成功；Smart Tag 与 VLM 批次可重试瞬时网络错误、F5 后可恢复并能取消、VLM 范围覆盖全部筛选结果而非仅已加载页；VLM 完成后不再自动弹出已关闭的预览窗。
- **One AI job at a time**: gallery tagging, Smart Tag, and VLM caption batches are now mutually exclusive under one coordinator (409 with a clear bilingual message), preventing double GPU model loads and caption double-writes.
  - **同时只跑一个 AI 任务**：图库打标、Smart Tag 与 VLM 批量描述纳入同一协调器互斥（409 + 双语提示），避免 GPU 双载模型与 caption 重复写入。
- **Censored thumbnails refresh immediately**: thumbnail URLs are now versioned by file modification time, so overwriting an image in Censor Edit updates its gallery thumbnail instead of showing the uncensored cached version for up to 24 hours.
  - **打码后缩略图立即更新**：缩略图 URL 按文件修改时间加版本号，打码覆盖保存后图库立即显示已打码图，不再被浏览器缓存 24 小时。
- **Exact prompt exclusion is exact**: excluding "cat" in exact mode no longer hides "catgirl"/"scattered".
  - **精确排除词真正精确**：exact 模式排除 "cat" 不再连 "catgirl"/"scattered" 一起隐藏。
- **Desktop UX polish**: Tools menu opens at the right position on 2K/4K auto-zoom; "Teach categories" from the preview window closes it before opening Prompt Lab; "Build prompt from this image" works for older images outside the recent-200 catalog; large model downloads no longer show a false "stalled" warning after 4 minutes; Browse button no longer triple-fires; filter summaries keep their colons and missing translations were filled in.
  - **桌面体验打磨**：2K/4K 自动缩放下 Tools 菜单定位正确；预览窗内"教分类"会先关闭预览再打开 Prompt Lab；"用此图构建 Prompt"对 200 张目录之外的旧图也有效；大模型下载不再 4 分钟后误报"卡住"；浏览按钮不再一次触发多次；筛选摘要冒号与缺失的中文翻译已补齐。

### Upgrading / 升级注意
- No database migration. Existing libraries, image files, captions, model files, tags, and ratings are untouched.
  - 不含数据库迁移。既有图库、图片文件、caption、模型文件、标签与评分不受影响。
- API note: starting a tagging/Smart-Tag/VLM job while another runs now returns 409 (previously sometimes 400).
  - API 提示：AI 任务互斥冲突现在统一返回 409（此前部分场景为 400）。

## [3.3.4] - 2026-06-10

Focused Gallery/Reader usability release after v3.3.3. The preview modal now behaves like a real inspector for continuous image reading: common metadata stays visible, the content pane keeps its scroll position when switching images, and low-frequency handoff/analysis actions no longer push the prompt out of the way. This release also finishes category/purpose copy flows and Prompt Lab image-tag recipes, with 2K viewport fixes for right-side menus.

v3.3.3 后的 Gallery / Reader 可用性修复版。预览弹窗现在更像真正的连续读图 inspector：常用元数据保持可见，切换图片时正文阅读位置不会重置，低频交接/分析按钮不会再把 Prompt 挤下去。本版同时补完分类/用途型复制与 Prompt Lab 图片标签配方，并修正 2K 视口下右侧菜单定位。

### Fixed / 修复
- **Gallery preview inspector**: fixed header + independent scroll body; Copy and Tools menus replace the always-visible action rows.
  - **图库预览 inspector**：固定头部 + 独立正文滚动；Copy 与 Tools 菜单替代常驻动作按钮行。
- **Preview scroll retention**: previous/next image navigation preserves the information-pane reading position.
  - **预览切图保留阅读位置**：上一张/下一张切换会保留信息区阅读位置。
- **2K right-edge menu placement**: Gallery context menus stay near right-side images and inside the viewport; modal Tools and close controls no longer overlap.
  - **2K 右侧菜单定位**：图库右键菜单贴近右侧图片且保持在视口内；弹窗 Tools 与关闭按钮不再重叠。
- **Purpose prompt copy**: Gallery/Reader category copy can emit clean training captions, image-search keywords, and category-specific tag prompts.
  - **用途型 Prompt 复制**：Gallery / Reader 分类复制可输出干净训练 caption、搜图关键词与按分类的 tag prompt。
- **Prompt Lab image recipe**: selected gallery images can seed a categorized prompt recipe and build a cleaned prompt with quality/meta noise removed when requested.
  - **Prompt Lab 图片配方**：可用图库图片生成分类 prompt 配方，并按需去掉质量词/元信息噪音。

### Upgrading / 升级注意
- No database migration. Existing libraries, image files, captions, model files, tags, and ratings are untouched.
  - 不含数据库迁移。既有图库、图片文件、caption、模型文件、标签与评分不受影响。

## [3.3.3] - 2026-06-09

Deep UI/UX overhaul release. The app now follows the real image workflow more closely:
Gallery / Reader → Sort → Censor Edit → Similar → Dataset, with Prompt Lab and Artist ID
behind Tools. Settings & Models is now the single door for models, sound, UI scale, and
AI defaults; preview and selection surfaces expose the next pipeline step instead of
dead-ending. Backend features that already existed but had no UI are now reachable
(star ratings, per-image score/colors/artist/caption actions, update proxy/channel,
Prompt Lab data tools, VLM provider detect and local model delete). The duplicate Gallery
AI Tag and Dataset Smart Tag entry points now share one backend coordinator, guarded by
a LoRA caption golden test so existing training captions stay stable. Post-validation QA
also tightened first-run desktop readability, desktop pipeline-nav labels, Censor Edit cursor
alignment under UI scale, filtered selection scope, Queue Manager gallery-filter scope/counts,
and VLM/Color single-image wiring.
This final pass also adds category-aware tag copying from Gallery/Reader, a Prompt Lab
category board for recategorizing tags, and smarter tagger-vocab classification so every
local tagger CSV tag has a frontend category.

深度 UI/UX 大改版。应用现在更贴近真实图片工作流：图库 / 读图 → 整理 → 打码编辑 →
相似图 → 数据集，Prompt Lab 与画师识别收进 Tools。设置与模型成为统一入口，集中管理模型、
声音、界面缩放与 AI 默认值；预览弹窗和选择面板会直接给出下一步操作，不再成功后断线。
此前后端已存在但前端没有入口的能力已接上（星级评分、单图美学/颜色/画师/描述、更新代理/通道、
Prompt Lab 数据工具、VLM provider 自动识别与本地模型删除）。Gallery AI Tag 与 Dataset Smart Tag
也改由同一个后端协调器管理，并用 LoRA caption golden test 锁住既有训练 caption 输出。
发布验证后又补强了首次桌面入口可读性、桌面流程导航文字、打码编辑光标对齐、筛选选择范围、
Queue Manager 图库筛选范围与计数，以及 VLM / 单图颜色分析的前后端接线。
最终收尾还加入了 Gallery / Reader 按分类复制标签、Prompt Lab 分类面板，以及更完整的
tagger 词库分类，让本地 tagger CSV 里的 tag 都能落到前端分类。

### Added / 新增
- **Pipeline handoffs and next-step CTAs**: preview modal handoffs now send an image to Censor Edit, Similar, Dataset Maker, Collections, Prompt Helper, or Reader; scan/tag/sort success states show persistent next-step CTAs instead of transient toasts.
  - **流程交接与下一步 CTA**：预览弹窗可把图片送到打码编辑、相似图、Dataset Maker、合集、Prompt Helper 或读图；扫描 / 打标 / 整理完成后显示持续的下一步入口，不再只靠一闪而过的 toast。
- **Settings & Models home**: the old setup entry is now a Settings door with sound mute, UI scale, AI default persistence/reset, model guidance, bulk model download, and disk usage in one place.
  - **设置与模型主页**：旧的功能准备入口改成统一设置门，集中声音静音、界面缩放、AI 默认值保存/重置、模型指引、批量模型下载与磁盘占用。
- **Previously invisible backend features now have UI**: user star ratings, per-image Score / Colors / Artist / Caption actions, VLM provider auto-detect, update proxy/channel settings, Prompt Lab recategorize/delete tools, and local Ollama VLM model delete.
  - **此前没有前端入口的后端能力已接上**：用户星级评分、单图美学 / 颜色 / 画师 / 描述、VLM provider 自动识别、更新代理/通道设置、Prompt Lab 重新分类/删除工具、本地 Ollama VLM 模型删除。
- **Category-aware tag copy**: Gallery context actions and Reader copy controls can now copy all tags or only Appearance, Clothing, Pose, Scenery, Style, Quality/Meta, or Unclassified tags.
  - **按分类复制标签**：图库右键操作与 Reader 复制控件现在可复制全部标签，或只复制外观、服装、姿势、场景、风格、质量/元信息、未分类标签。
- **Prompt Lab category board**: copied/read tags can be opened in Prompt Lab for quick category review and manual recategorization.
  - **Prompt Lab 分类面板**：复制/读取到的标签可以送进 Prompt Lab，快速检查分类并手动调整。

### Changed / 变更
- **Navigation and information architecture**: core tabs are now ordered around the pipeline, while Prompt Lab and Artist ID live under Tools. Censor naming is standardized as **Censor Edit / 打码编辑**.
  - **导航与信息架构**：核心标签按流程排序，Prompt Lab 与画师识别收进 Tools。打码功能命名统一为 **Censor Edit / 打码编辑**。
- **Unified tagging coordinator**: Gallery `/api/tag/*` and Dataset `/api/smart-tag/*` now share a `TaggingPipelineService` coordinator and cannot run two heavy tagger/VLM jobs independently at the same time.
  - **统一打标协调器**：Gallery `/api/tag/*` 与 Dataset `/api/smart-tag/*` 现在共用 `TaggingPipelineService` 协调器，避免两个重型打标 / VLM 任务各自独立并发。
- **Noise reduction via progressive disclosure**: Queue Manager filters, Dataset notices, Censor shortcut hints, caption editor secondary tools, and Gallery selection export/remove actions are collapsed until needed.
  - **渐进展开降低噪音**：Queue Manager 筛选、Dataset 提示、打码快捷键、caption 编辑器次级工具、图库选择面板的导出/移除动作都默认收起，需要时再展开。

### Fixed / 修复
- **Static asset cache-busting**: frontend JS/CSS served by the backend now carries version cache-busting/no-cache behavior so upgrades do not silently reuse stale UI files.
  - **静态资源缓存失效**：后端提供的前端 JS/CSS 现在跟随版本做缓存失效 / no-cache，升级后不会静默继续用旧 UI 文件。
- **Overlapping background progress bars**: scan/tag/reconnect/file-operation progress bars now occupy distinct slots.
  - **后台进度条不再重叠**：扫描、打标、重连、文件操作进度条现在使用不同位置。
- **Selection and handoff cleanup**: bulk handoffs clear stale gallery selection where appropriate, and selection/context-menu/Dataset handoff paths now share the same helper.
  - **选择与交接清理**：批量交接后会按需清理图库旧选择，选择面板 / 右键菜单 / Dataset 交接路径共用同一 helper。
- **Post-validation QA fixes**: Censor Edit brush cursor now stays under the pointer at UI scale 130% and canvas zoom 150%; first-run desktop entry points and the main pipeline nav labels stay readable at laptop widths; filtered selection now preserves collection, folder, metadata, user-rating, and exclusion scope; Queue Manager's "Use Gallery Filters" resolves through backend selection-token/chunk and keeps its dynamic count stable; single-image Color updates flat Gallery state; and VLM settings preserve the with-tags prompt.
  - **发布验证后的 QA 修复**：打码编辑笔刷光标在 UI 130% 与画布 150% 缩放下仍贴合指针；首次进入时「导入图片 / AI 打标签」与主流程导航文字在桌面宽度保持可读；筛选选择范围保留合集、文件夹、元数据、用户星级与排除条件；Queue Manager 的「使用图库筛选」改走后端 selection-token/chunk，动态计数不会被翻译刷新重置；单图颜色分析会即时更新图库状态；VLM 设置会保存带 tags 的 prompt。
- **Tagger CSV category coverage**: WD14, PixAI, EVA02, and Oppai Oracle local CSV vocab tags now resolve to frontend categories with zero `unknown` entries; user-made tags outside known vocab can still remain `unknown`.
  - **Tagger CSV 分类覆盖**：WD14、PixAI、EVA02 与 Oppai Oracle 本地 CSV 词库现在全部能落入前端分类，`unknown` 为 0；不在已知词库里的用户自造 tag 仍可保留为 `unknown`。

### Internal / 内部
- **LoRA caption golden gate**: added deterministic tests for Smart Tag caption assembly, Gallery tag export, Dataset export, and export preview before the tagging coordinator merge.
  - **LoRA caption golden gate**：在合并打标协调器前，为 Smart Tag caption 组装、Gallery 标签导出、Dataset 导出与导出预览加入确定性测试。
- **CSV vocabulary gate**: release tests now scan local tagger `selected_tags.csv` files and fail if any known tag falls through to `unknown`.
  - **CSV 词库闸门**：发布测试会扫描本地 tagger 的 `selected_tags.csv`，任何已知 tag 落到 `unknown` 都会失败。

### Upgrading / 升级注意
- **Zero manual steps.** No database schema change ships in v3.3.3. Preferences added in this release use browser `localStorage`; existing libraries and model files are untouched.
  - **零手动操作。** v3.3.3 不包含数据库结构变更。本次新增偏好使用浏览器 `localStorage`；既有图库与模型文件不受影响。

## [3.3.2] - 2026-06-08

Sort & Cull Workbench release. The Manual Sort tab becomes a multi-mode culling hub: it keeps
the fast WASD slot-sort and adds an **A/B 擂台 King-of-Hill** showdown plus a **留/汰 Keep-Reject**
quick-cull, both with SD-aware tooling (per-image metadata chips, a differences-only compare
strip, and synchronized pixel-peep zoom) that a generic asset manager structurally can't match.
Large libraries also feel faster — bulk delete / remove run as cancelable background jobs
(export runs in the background too), thumbnails yield to active scans, and the similarity
vector cache persists across restarts; the AI runtime guard gains concurrency groundwork
(still serialized by default). Plus an adaptive UI scale for high-res
desktops and a batch of UI/UX and bug fixes. No functionality limits were added.

Sort & Cull 工作台版本。手动分拣页升级成多模式精挑中心：保留快速 WASD 槽位分拣，新增
**A/B 擂台**（擂主对挑战者）与 **留/汰 快筛** 两种模式，都带 SD 专属工具（每图元数据芯片、
只显示差异的对比条、同步像素级缩放）——这是通用素材管理器结构上做不到的。大图库也更顺：
批量删除 / 移除改为可取消的后台任务（导出也在后台运行），缩略图在扫描时自动让路，相似度
向量缓存可跨重启保留，AI 运行守卫获得并发打底（默认仍串行）。另含高分屏自适应缩放与一批 UI/UX 与 bug 修复。
未新增任何功能上限。

### Added / 新增
- **Sort & Cull Workbench (Manual Sort redesign)**: the Manual Sort tab is now a switchable hub. The existing WASD slot-sort is preserved as one mode, and the start button and keyboard map adapt to the active mode.
  - **Sort & Cull 工作台（手动分拣重构）**：手动分拣页现在是可切换的多模式中心。原有的 WASD 槽位分拣保留为其中一种模式，开始按钮与键位会随当前模式自适应。
- **A/B 擂台 King-of-Hill showdown**: a champion image stays on screen and faces the next challenger; pick the winner with ← / → (↑ skip, Z undo, Esc exit). Each fighter shows real SD metadata chips (sampler / CFG / steps / seed / checkpoint / size / aesthetic) and a champion win-streak (👑 连胜 ×N). The winner can be routed to a Collection or Favorites — non-destructive and opt-in.
  - **A/B 擂台（擂主守擂）**：擂主留在画面上迎战下一位挑战者；用 ← / → 选出胜者（↑ 跳过、Z 撤销、Esc 退出）。每位选手显示真实 SD 元数据芯片（采样器 / CFG / 步数 / 种子 / checkpoint / 尺寸 / 美学分）与擂主连胜（👑 连胜 ×N）。胜者可收入某个合集或收藏——非破坏性、需手动开启。
- **Showdown inspector (SD moat)**: a side-by-side comparator that lists only the *differing* generation params (sampler / CFG / steps / seed / scheduler / clip / denoise / model / size) and a synchronized pixel-peep zoom that scales both images to the same point.
  - **擂台检视器（SD 护城河）**：并排比对，只列出**有差异**的生成参数（采样器 / CFG / 步数 / 种子 / 调度器 / clip / denoise / 模型 / 尺寸），并提供把两张图缩放到同一位置的同步像素级放大。
- **留/汰 Keep-Reject cull mode**: a single-image fast first pass — → keep, ← reject, ↑ skip — with a live ♥ keep / ✕ reject tally and undo / redo. On finish, kept and rejected images route to your chosen destinations (none / Favorites / a collection), all by reference (no file move unless you ask).
  - **留/汰 快筛模式**：单图快速初筛——→ 留下、← 汰除、↑ 跳过——带实时 ♥ 留 / ✕ 汰 计数与撤销 / 重做。结束时留下与汰除的图片按你选择的去向归位（无 / 收藏 / 某合集），全部按引用处理（除非你要求，否则不移动文件）。
- **Adaptive interface scaling**: on large / high-resolution desktops the UI now scales up automatically (root zoom keyed to window width; viewports ≤1920 are untouched), with a manual override, so controls stay comfortably sized on 2560-wide and 4K screens.
  - **界面自适应缩放**：在大尺寸 / 高分辨率桌面上界面会自动放大（根 zoom 按窗口宽度调整；≤1920 的视口不变），并可手动覆盖，让 2560 宽与 4K 屏幕上的控件保持舒适大小。
- **Library Navigation**: a collapsible **Folders** tree in the gallery sidebar scopes the gallery to a folder *and everything beneath it* (recursive subtree) in one click; a **Library Roots** manager lists your image-source folders with per-root Rescan / Remove; and an opt-in (default OFF) idle auto-refresh quietly quick-imports the stalest root while you are idle (never tags; no-ops during any scan).
  - **资料库导航**：图库侧栏的可折叠「文件夹」树点一下即可把图库限定到某个文件夹**及其下所有子目录**（递归子树）；「图片来源（Library Roots）」管理器列出你的来源文件夹并可逐个重扫 / 移除；可选（默认关闭）的空闲自动刷新会在你空闲时悄悄对最久未更新的来源做 quick-import（绝不打标；任何扫描进行时跳过）。
- **CLIP image tools**: pick any two images for a **CLIP cosine-similarity** compare, or jump from any image to its top-K **near-duplicates / variants** in one click (read-only similarity endpoints; pairs with the cull workbench for de-duping).
  - **CLIP 图像工具**：任选两张图做 **CLIP 余弦相似度**比对，或从任意一张图一键跳到它的 top-K **近似 / 变体**图（只读相似度接口；配合精挑工作台可去重）。
- **Dataset Maker two-box caption editor + danbooru tag colors**: booru tags and the natural-language caption are now edited in two separate boxes (a VLM sentence and the tag list no longer contaminate each other on export); each image carries its own NL caption type; every booru tag is classified into a danbooru-style category and color-coded; and the hybrid VLM parser more reliably splits tag output from prose.
  - **Dataset Maker 双框 caption 编辑器 + danbooru 标签配色**：booru 标签与自然语言描述现在分两个框编辑（导出时 VLM 句子与标签列表不再互相污染）；每张图带自己的 NL 描述类型；每个 booru 标签都归类到 danbooru 风格类别并用颜色标注；混合 VLM 解析器更可靠地把标签与散文拆开。
- **VLM proxy support (http / https / socks)**: cloud captioning honours a configured proxy; SOCKS support is bundled (`socksio`) and a missing SOCKS dependency surfaces a clear error instead of crashing.
  - **VLM 代理支持（http / https / socks）**：云端打标会遵循配置的代理；已内置 SOCKS 支持（`socksio`），缺少 SOCKS 依赖时给出明确错误而非崩溃。
- **Kaloscope (Artist ID) GPU/CPU toggle + real ModelScope routing**: the experimental Kaloscope artist identifier gains the same GPU/CPU switch as the WD14 tagger (it was the only model hard-pinned to CUDA); CPU works (~2.1× slower). The "ModelScope" mirror now genuinely routes artist / Kaloscope / SAM3 downloads to modelscope.cn, and manual model placement is detected across HuggingFace-hub cache layouts, case-insensitive folder names, and git-clone directories.
  - **Kaloscope（画师识别）GPU/CPU 切换 + 真正的 ModelScope 路由**：实验性的 Kaloscope 画师识别器获得与 WD14 打标器一致的 GPU/CPU 开关（此前是唯一被硬绑 CUDA 的模型）；CPU 可用（约慢 2.1×）。「ModelScope」镜像现在会真正把画师 / Kaloscope / SAM3 下载指向 modelscope.cn，手动放置的模型即使是 HuggingFace-hub 缓存结构、大小写不同的文件夹名或 git-clone 目录也能识别。
- **Censor native pixel masks**: the auto-detector can produce true polygon pixel masks (YOLOv8-seg / SAM3) instead of only rectangles, with a **Precise / Box** toggle and a SAM3 refine entry to tighten an existing detection.
  - **打码原生像素遮罩**：自动检测可输出真正的多边形像素遮罩（YOLOv8-seg / SAM3），而非只有矩形，并提供**精确 / 方框**切换与 SAM3 精修入口，可把已有检测收紧。

### Performance / 性能
- **Adaptive GPU OOM backoff + parallel preprocess (WD14 + OppaiOracle)**: a batch that hits genuine CUDA out-of-memory now halves the GPU sub-batch (rebuilding the GPU session between steps) and retries on the GPU before any CPU fallback, and only halves for *real* OOM (a non-OOM GPU error skips straight to CPU instead of wastefully halving 64→1). Image decode / letterbox runs on a small bounded thread pool so the GPU is not starved between batches (inference stays serialized by the runtime guard).
  - **GPU 显存不足自适应回退 + 并行预处理（WD14 + OppaiOracle）**：真正撞到 CUDA 显存不足的批量会把 GPU 子批量减半（步骤间重建 GPU 会话）并先在 GPU 上重试，之后才回退 CPU；且只对**真正的** OOM 减半（非 OOM 的 GPU 错误直接走 CPU，不再无谓地把 64 砍到 1）。图像解码 / letterbox 跑在有界小线程池上，避免 GPU 在批次间空等（推理仍由运行守卫串行化）。
- **Hardware-aware Smart Tag batching + ToriiGate KV cache**: Smart Tag starts from a VRAM / model-aware booru batch size (not a fixed 64) so an 8GB-VRAM laptop GPU starts at a size that fits, and reacts to live memory pressure mid-run; the ToriiGate captioner turns its generation KV cache on (~2–4× faster) when free VRAM is comfortable and keeps it off on a tight GPU.
  - **硬件感知的 Smart Tag 批量 + ToriiGate KV 缓存**：Smart Tag 从显存 / 模型感知的 booru 批量起步（而非固定 64），让 8GB 显存的笔记本 GPU 一开始就用得下的尺寸，并在运行中响应实时内存压力；ToriiGate 描述模型在显存充裕时开启生成 KV 缓存（约 2–4× 更快），显存紧张时保持关闭。
- **Background bulk delete / remove (+ background export)**: deleting files from disk and removing from the gallery now run as cancelable background jobs with progress bars; batch tag export also runs in the background (coarse progress, not cancelable mid-run). Large selections no longer freeze the browser (file move was already a background job in v3.3.0).
  - **批量删除 / 移除改为后台任务（导出也在后台）**：从磁盘删除、从图库移除现在是可取消、带进度条的后台任务；批量标签导出也在后台运行（粗粒度进度，运行中不可取消）。大量选择不再卡死浏览器（移动文件在 v3.3.0 已是后台任务）。
- **Scan-aware thumbnail backpressure**: while a scan is running, thumbnail generation throttles to a small bounded pool so it stops competing with metadata parsing — scans feel faster. Idle throughput is unchanged.
  - **扫描感知的缩略图背压**：扫描进行时，缩略图生成会限制在一个有界小线程池，避免与元数据解析抢资源——扫描更快；空闲时吞吐不变。
- **AI runtime guard — concurrency groundwork**: the AI runtime guard gained the plumbing for fair priority ordering, per-job VRAM estimates, and an opt-in acquire timeout. This ships as foundation for future concurrency — the default is unchanged, so all AI work (tag / censor / similarity / aesthetic) still runs fully serialized and there is no new OOM risk.
  - **AI 运行守卫——并发打底**：AI 运行守卫新增了公平优先级排序、每任务显存估算与可选获取超时的底层管线。这是为后续并发预留的基础——默认行为不变，所有 AI 工作（打标 / 打码 / 相似度 / 美学）仍完全串行，因此不引入新的显存风险。
- **Persistent similarity vector cache**: the similarity vector matrix now persists to disk, so cold-start search skips re-reading every embedding from SQLite (verified identical to the streaming path). An experimental `hnswlib` ANN top-k index also ships (`SimilarityIndex.top_k_similar`, with exact re-rank) but is **not yet wired into the default paginated search** — opt-in groundwork for very large libraries (disable with `SD_SIMILARITY_DISABLE_ANN=1`).
  - **相似度向量缓存持久化**：相似度向量矩阵现在会持久化到磁盘，冷启动搜索不必再从 SQLite 重读全部 embedding（已验证与流式路径结果一致）。另附实验性 `hnswlib` ANN top-k 索引（`SimilarityIndex.top_k_similar`，带精确重排），但**尚未接入默认的分页搜索路径**——为超大图库预留的可选打底（用 `SD_SIMILARITY_DISABLE_ANN=1` 关闭）。

### Fixed / 修复
- **Filter "select all" no longer drops images**: ticking *select all* models or LoRAs (with no search) now means "no restriction", matching ratings / generators. Previously it sent the full explicit list, which silently excluded images with a NULL checkpoint or zero LoRAs, so "select all" returned fewer images than expected.
  - **筛选「全选」不再漏图**：勾选「全选」模型或 LoRA（且无搜索）现在表示「不限制」，与评级 / 生成器一致。此前它会发送完整明确列表，悄悄排除了 checkpoint 为空或没有 LoRA 的图片，导致「全选」反而比预期少。
- **Gallery total no longer flashes "-1"**: a count-skipped sentinel could briefly render as "-1 张图片"; it is now guarded.
  - **图库总数不再闪现「-1」**：计数被跳过的哨兵值曾短暂显示为「-1 张图片」，现已加保护。
- **Antivirus false positive on launch**: the launchers no longer spawn a hidden PowerShell window to open the browser (some AV, e.g. Huorong, flagged it as a trojan); opening the browser is now done in-process.
  - **启动时杀软误报**：启动器不再用隐藏的 PowerShell 窗口打开浏览器（部分杀软如火绒会误判为木马）；改为进程内打开浏览器。
- **Favorites survive a rescan**: favorites are now path-anchored, so re-scanning or re-indexing a folder no longer loses your hearts.
  - **收藏不怕重扫**：收藏现在以路径锚定，重扫 / 重新索引文件夹不再丢失你的红心。
- **Honest SAM3 batch counts + no 500-box cap**: batch SAM3 reports the real processed / detected counts instead of an optimistic number, and the previous 500-box ceiling is gone; folder scope and the metadata radios also clear correctly on reset.
  - **诚实的 SAM3 批量计数 + 取消 500 框上限**：批量 SAM3 报告真实的处理 / 检测数量而非乐观估计，且取消了此前 500 框的上限；重置时文件夹范围与元数据单选也会正确清除。
- **Localization gaps in the zh-CN UI**: 23 strings that still showed English are now localized, sub-UI modal labels (export / save options / queue manager / reconnect, etc.) are translated, and the zh-CN folder vocabulary is normalized.
  - **zh-CN 界面本地化缺口**：修正 23 处仍显示英文的字符串，翻译了子界面弹窗标签（导出 / 保存选项 / 队列管理 / 重连等），并统一了 zh-CN 的文件夹词汇。
- **Dropped-folder scan path**: dragging a folder onto the scan input now resolves its real path before scanning, with a browse fallback.
  - **拖入文件夹的扫描路径**：把文件夹拖到扫描输入框现在会先解析真实路径再扫描，并提供浏览兜底。
- **Auto-Separate preview & progress**: the preview grid fills the available pane height instead of a fixed two rows (no more large empty space), the image count is clamped to ≥0 with reset-on-error, and the move progress bar scrolls into view with an idle grace period.
  - **Auto-Separate 预览与进度**：预览网格现在填满可用面板高度，而非固定两行（不再有大片空白）；图片计数夹紧到 ≥0 并在出错时重置；移动进度条会滚动到可见处并保留空闲宽限。
- **Wasted-space empty states**: the Reader and Prompt Lab no longer reserve large empty columns before content loads; the Prompt Lab stats copy now points to AI tagging when there are no tags yet.
  - **空状态的空白浪费**：读图与 Prompt Lab 在内容加载前不再预留大片空列；Prompt Lab 统计在尚无标签时改为提示先做 AI 打标。
- **Censor editor layout**: fixed the 769–960px range where the toolbar went off-screen (it now stacks), and hid the editing chrome (toolbar + footer bars) in the empty no-image state so only the "select an image" card shows.
  - **打码编辑器布局**：修复 769–960px 区间工具栏跑出屏幕的问题（现在改为堆叠），并在无图的空状态下隐藏编辑外壳（工具栏 + 底栏），只显示「选择一张图片」卡片。
- **Workbench resume (A/B 擂台 + 留/汰)**: a paused A/B Showdown now shows a resume banner and resumes correctly instead of failing with a 409; 留/汰 keep/reject decisions made before a reload are no longer dropped (they are rebuilt from the saved session and still route at finish); the resume banner shows mode-appropriate info (comparisons / images left) for bracket and cull instead of slot-only folder text.
  - **工作台恢复（A/B 擂台 + 留/汰）**：暂停的 A/B 擂台现在会显示恢复横幅并正确续做，而不再以 409 失败；留/汰 在刷新前做出的留/汰决定不再丢失（从已保存会话重建，结束时仍会归位）；恢复横幅会按模式显示对应信息（剩余对决 / 待筛图片），而非只适用槽位分拣的文件夹文案。
- **Metadata-diff accuracy (showdown inspector)**: the differences-only strip now reads the scheduler under each generator's key (ComfyUI `scheduler` / A1111 `schedule_type` / NovelAI `noise_schedule`) instead of only ComfyUI's, normalizes sampler names so the same sampler across generators isn't flagged as different, and shows "No SD generation metadata to compare" instead of a misleading "Same generation params" when neither image carries generation params.
  - **元数据差异更准（擂台检视器）**：只显示差异的对比条现在能读取各生成器各自的调度器键（ComfyUI `scheduler` / A1111 `schedule_type` / NovelAI `noise_schedule`），不再只认 ComfyUI；对采样器名称做归一化（同一采样器跨生成器不再误报不同）；当两张图都没有生成参数时显示「没有可对比的 SD 生成参数」，而非误导性的「生成参数相同」。
- **Synchronized pixel-peep zoom**: the A/B zoom now maps to the same picture point on both images even when they have different aspect ratios (it corrects for object-fit letterboxing) instead of drifting to a different spot on each.
  - **同步像素级放大**：A/B 缩放现在即便两图长宽比不同也会对准同一画面位置（已校正 object-fit 留白），不再各自偏到不同位置。

### Internal / 内部
- **E2E coverage for the Workbench**: added Playwright coverage for the A/B Showdown flow and the Keep-Reject cull flow, plus a WASD slot-sort regression; the batch remove / delete / export smoke mocks were repointed at the new background-job `/start` + `/progress` endpoints.
  - **工作台 E2E 覆盖**：新增 A/B 擂台流程与留/汰快筛流程的 Playwright 覆盖，以及 WASD 槽位分拣回归；批量移除 / 删除 / 导出的 smoke mock 已改指向新的后台任务 `/start` + `/progress` 接口。
- **Cull decision-map regression tests**: backend tests assert the cull payload exposes the per-image keep/reject decision map (and clears it on undo) so resume routing stays correct.
  - **留/汰 决定映射回归测试**：后端测试断言 cull 负载会暴露每图留/汰决定映射（撤销时清除），确保恢复后的归位正确。

## [3.3.1] - 2026-06-03

UI/UX + visual release. Finishes the Favorites & Collections experience whose backend
shipped in v3.3.0 (you could click the heart but had no way to *see* what you favorited):
a left-sidebar Collections section, browse-by-collection, create / rename / delete, and a
right-click "Add to collection" with multi-select. Four features now connect — similarity
search and the WASD manual sort can target a collection, Prompt Lab and the gallery
cross-link, and collections thread through the gallery filters. Plus a full "Aurora Glass"
visual refresh (indigo→cyan accent). No functionality limits added.

UI/UX + 视觉版本。把 v3.3.0 只做了后端的「收藏与合集」补成完整体验（之前能点爱心却无处
查看）：左侧栏合集区、按合集浏览、新建 / 重命名 / 删除，以及右键「加入合集」（支持多选）。
四项功能打通——相似度搜索与 WASD 手动分拣可指向某个合集、Prompt Lab 与图库互相跳转、合集
贯穿图库筛选。另含完整「Aurora Glass」视觉翻新（靛→青强调色）。未新增任何功能上限。

### Added / 新增
- **Favorites & Collections UI**: the read / browse / manage half of the feature. A new left-sidebar Collections section lists Favorites plus your named collections with live counts; click one to browse only its images. Create, rename, and delete collections inline (the Favorites collection is protected from deletion). A right-click **Add to collection** menu supports multi-select batches.
  - **收藏与合集界面**：补上「查看 / 浏览 / 管理」这一半。左侧栏新增合集区，列出收藏与你的具名合集（带实时数量）；点一下即可只浏览该合集的图片。可就地新建 / 重命名 / 删除（收藏合集受删除保护）。右键「加入合集」菜单支持多选批量。

### Changed / 变更
- **"Aurora Glass" visual refresh**: a unified indigo→cyan accent over refined dark glassmorphism, replacing the previous amber / teal palette. Token-driven, so accents, gradients, borders, glows, focus rings, and hover states move together across every view.
  - **「Aurora Glass」视觉翻新**：统一的靛→青强调色配精修的深色玻璃拟态，取代旧的琥珀 / 青绿配色。完全 token 驱动，强调色、渐变、边框、光晕、聚焦环、悬停态在所有视图一起更新。
- **Features connect further**: similarity search can be scoped to a collection or Favorites; the WASD manual sort can drop images into a collection by reference (no file move) with undo / redo; Prompt Lab and the gallery cross-link (use a built prompt's terms as a gallery filter, and jump from an image back into the Lab).
  - **功能进一步打通**：相似度搜索可限定在某个合集或收藏内；WASD 手动分拣可按引用「收入合集」（不移动文件）并支持撤销 / 重做；Prompt Lab 与图库互相跳转（把构建好的 prompt 词条当图库筛选用，或从某张图跳回 Lab）。

### Fixed / 修复
- **Collection browse total count**: browsing a collection reported the whole library's image count as the gallery total. The cursor-path count now honors the collection filter, so the reported total matches what you see.
  - **合集浏览总数**：浏览合集时图库总数显示的是整库数量。游标路径的计数现在会遵守合集筛选，显示的总数与你看到的一致。

### Internal / 内部
- **Hermetic E2E artist runtime**: the model-manager E2E suite no longer resolves the LSNet artist runtime to a developer's real `models/artist/` checkout. Both runtime resolvers skip the legacy in-repo paths when `SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY=1` (set only by the test harness; production is unchanged), so local runs match clean CI.
  - **E2E 画师运行时隔离**：model-manager E2E 不再把 LSNet 画师运行时解析到开发者本机真实的 `models/artist/` 目录。两个运行时解析器在 `SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY=1` 时跳过 repo legacy 路径（仅测试环境设置，生产不变），本机结果与干净 CI 一致。

## [3.3.0] - 2026-06-02

Feature + workflow release. Three user-reported pain points fixed (file moves
now show a progress bar, new-folder scans no longer "flash back" to the previous
folder, and the crowded generator filter gained select-all / clear / invert with
shift-range). Adds Favorites & Collections, a Library Checkpoints tab, exclude-by-
prompt / exclude-by-color filters, a manual-sort cooldown, a faster similarity
search, and a tiered AI runtime scheduler. No functionality limits were added.

功能 + 工作流版本。修复三个用户反馈的痛点：移动文件重新显示进度条；指定新文件夹
扫描不再「猛回头」扫回上一个文件夹；拥挤的生成器筛选新增全选 / 清除 / 反选与
Shift 范围选取。新增收藏与合集、Library Checkpoints 分页、按 Prompt / 颜色排除的
筛选、手动分拣冷却、更快的相似度搜索，以及分层 AI 运行调度器。未新增任何功能上限。

### Added / 新增
- **Favorites & Collections**: a heart toggle on every gallery card plus named collections, exposed through a new `/api/collections` API. Membership is stored as a *reference* (no image files are copied), so toggling is instant and reversible.
  - **收藏与合集**：每张图卡上新增爱心切换，加上具名合集，经由新的 `/api/collections` API 提供。成员关系以「引用」方式保存（不复制图片文件），切换即时且可逆。
- **Library Checkpoints tab**: the Library view gained a Checkpoints facet (`GET /api/checkpoints/library`) alongside Tags and LoRAs, searchable across the full indexed library.
  - **Library Checkpoints 分页**：Library 视图在 Tags / LoRAs 之外新增 Checkpoints 分页（`GET /api/checkpoints/library`），可对整个索引库搜索。
- **Exclude by prompt / color**: gallery filters can now exclude images by prompt text and by color temperature. Active prompt chips cycle include → exclude → remove on click.
  - **按 Prompt / 颜色排除**：图库筛选新增按 prompt 文本与色温排除。已选 prompt 标签点击循环 包含 → 排除 → 移除。
- **AI runtime job snapshot**: `GET /api/system/ai-jobs` reports the live scheduler state (VRAM-exclusive vs CPU-pool jobs) for diagnostics.
  - **AI 运行任务快照**：`GET /api/system/ai-jobs` 报告实时调度状态（VRAM 独占 vs CPU 池任务）以便诊断。

### Fixed / 修复
- **File moves show a progress bar again**: moving/copying a selection runs as a background job (`/api/move/start` + `/api/move/progress`) with a cancelable progress bar, so you can tell when originals have actually been moved and it is safe to proceed.
  - **移动文件重新显示进度条**：移动 / 复制选中项改为后台任务（`/api/move/start` + `/api/move/progress`），带可取消的进度条，让你清楚知道原文件何时真正被移动、何时可以安全继续。
- **Scan no longer "flashes back" to the previous folder**: starting a scan on a new folder while a previous poll was in flight could revert progress to the old folder. A poll-generation guard now bails out stale pollers, and a second concurrent scan returns a clear "already running" toast.
  - **扫描不再「猛回头」扫回上一个文件夹**：在上一轮轮询还在进行时对新文件夹开始扫描，进度可能回退到旧文件夹。新增轮询代数守卫会丢弃过期轮询，重复扫描会返回明确的「已在运行」提示。

### Changed / 变更
- **Generator filter is manageable**: the generator / rating / checkpoint / LoRA filter groups gained Select all / Clear / Invert buttons and Shift-click range selection, so a 14-checkbox list no longer needs an autoclicker.
  - **生成器筛选更好管理**：生成器 / 评级 / checkpoint / LoRA 筛选组新增 全选 / 清除 / 反选 按钮与 Shift 点击范围选取，14 个勾选框的列表不再需要连点器。
- **Manual sort (WASD) cooldown**: an optional per-action cooldown prevents an autoclicker or held key from firing several sorts at once and scattering images.
  - **手动分拣（WASD）冷却**：可选的每动作冷却，避免连点器或长按一次触发多次分拣把图片分散乱放。
- **Censor detector default explained**: the detector picker labels **Both** as the recommended option and explains that the app auto-selects the best detector you have ready (privacy YOLO + NudeNet for the widest coverage).
  - **遮挡检测器默认说明**：检测器选择器把 **Both** 标为推荐项，并说明程序会自动选择你已就绪的最佳检测器（隐私 YOLO + NudeNet 覆盖最完整）。

### Performance / 性能
- **Faster similarity search**: repeated similarity searches reuse an in-memory, L2-normalized embedding matrix instead of re-reading and re-decoding every embedding from SQLite on each query. The streaming scan remains as an automatic fallback, so results are identical and large libraries that don't fit in memory still work. Zero new dependencies (numpy only); opt out with `SD_SIMILARITY_DISABLE_VECTOR_CACHE=1`.
  - **相似度搜索更快**：重复搜索改为复用内存中的 L2 归一化嵌入矩阵，而非每次查询都从 SQLite 重新读取并解码全部嵌入。流式扫描作为自动后备保留，结果完全一致，内存放不下的超大库仍可运行。零新增依赖（仅 numpy）；可用 `SD_SIMILARITY_DISABLE_VECTOR_CACHE=1` 关闭。
- **Tiered AI runtime scheduler**: GPU/VRAM work stays mutually exclusive, but CPU-only AI work now runs on a bounded pool instead of serializing behind GPU jobs, improving throughput when mixing tagging / censor / embedding work.
  - **分层 AI 运行调度器**：GPU/VRAM 工作仍互斥，但纯 CPU 的 AI 工作改在有界线程池上运行，不再排在 GPU 任务后面串行，混合打标 / 遮挡 / 嵌入时吞吐更好。

### Security / 安全
- **VLM endpoint scheme guard**: the captioning endpoint now rejects non-`http(s)` schemes (e.g. `file://`, `gopher://`) before connecting. Local / private / loopback endpoints (Ollama, LM Studio, llama.cpp) remain first-class and are intentionally not blocked.
  - **VLM 端点协议守卫**：captioning 端点在连接前拒绝非 `http(s)` 协议（如 `file://`、`gopher://`）。本地 / 私有 / loopback 端点（Ollama、LM Studio、llama.cpp）仍是一级支持，刻意不拦截。

### Internal / 内部
- Removed a dead GPU-confirmation branch in the tagger risk check, narrowed an over-broad `except` in the artist-model downloader, and unified the gallery grid keyboard navigation onto the shared accessibility helper (no behavior change).
  - 移除打标风险检查中的死 GPU 确认分支，收窄 artist 模型下载器中过宽的 `except`，并将图库网格键盘导航统一到共享无障碍 helper（行为不变）。

## [3.2.4] - 2026-06-02

Stability + security patch. Behavior-preserving fixes from a multi-agent code
review: heavy API routes no longer block the event loop, faster filtered
queries on large libraries, a decompression-bomb guard on dataset uploads, no
raw exceptions leaked to clients, plus a round of UI accessibility / layout /
i18n polish. No functionality limits were added.

稳定性 + 安全修补。来自多 agent 代码审查的行为保持型修正：重型 API 路由不再阻塞
event loop；大图库筛选查询更快；数据集上传新增解压炸弹防护；不再向客户端泄漏原始
异常；以及一轮 UI 无障碍 / 版面 / i18n 优化。未新增任何功能上限。

### Performance / 性能
- **Event loop no longer blocks under large libraries**: ~16 synchronous API routes (tags, sorting, colors, bulk-tag, analytics/stats) were moved off the event loop into FastAPI's threadpool, so the server stays responsive while one request does heavy SQL/CPU work.
  - **大图库下 event loop 不再卡死**：约 16 个同步 API 路由（tags / sorting / colors / 批量打标 / analytics）改到 FastAPI threadpool 执行，单一重型请求不再让整个 server 卡住。
- **Faster gallery sort/filter on large libraries**: tag-count / character-count sorts use a single `LEFT JOIN ... GROUP BY` instead of per-row correlated subqueries; exclude-tag/rating filters use `NOT EXISTS` + a new `LOWER(tag)` index; new partial indexes on `aesthetic_score` / `color_saturation`. Query results are unchanged (verified against the previous SQL).
  - **大图库排序 / 筛选更快**：tag / 角色数排序改用单次 `LEFT JOIN ... GROUP BY`；排除筛选改用 `NOT EXISTS` + 新增 `LOWER(tag)` 索引；新增 `aesthetic_score` / `color_saturation` 偏索引。查询结果不变（已对照旧 SQL 验证）。
- **Thumbnail cache**: one fewer filesystem `stat()` call per cached-thumbnail request.
  - **缩图快取**：每次命中快取少一次 `stat()` 系统呼叫。

### Security / 安全
- **Decompression-bomb guard on dataset ZIP/RAR uploads**: archives are rejected before extraction if they exceed a generous entry-count / uncompressed-size cap (a malware guard, not a dataset-size limit). Complements the obfuscation-endpoint guard added in 3.2.3.
  - **数据集 ZIP/RAR 上传解压炸弹防护**：超过宽松的档案数 / 解压体积上限的压缩档会在解压前被拒（防恶意档，非数据集大小限制）。补齐 3.2.3 已加的混淆端点防护。
- **No raw exceptions leaked to clients**: dataset / obfuscation / support-log endpoints now return a generic message and log the detail server-side; status codes unchanged.
  - **不再向客户端泄漏原始异常**：dataset / 混淆 / 支援日志端点改回传通用讯息，详细错误仅记录在 server 端；状态码不变。
- **VLM API key withheld over cleartext**: the captioning API key is no longer transmitted over a non-loopback `http://` endpoint or proxy; local loopback servers (Ollama / llama.cpp / LM Studio) are unaffected.
  - **VLM API key 不走明文**：captioning API key 不再经由非 loopback 的 `http://` 端点或代理传送；本地 loopback 服务（Ollama / llama.cpp / LM Studio）不受影响。
- Several previously-swallowed exceptions (censor mask draw, similarity model probe, dataset translator fallback, temp-file cleanup) are now logged.
  - 若干先前被吞掉的异常（censor 遮罩绘制、相似度模型探测、dataset 翻译 fallback、暂存档清理）现在会记录。

### UI / 介面
- **Modal accessibility**: the Auto-Detect and Rename dialogs now have proper `role="dialog"` / focus-trap / Esc handling routed through the shared modal helpers.
  - **弹窗无障碍**：自动侦测与重命名弹窗补上 `role="dialog"` / focus-trap / Esc，统一走共用 modal helper。
- **Gallery aspect quick-toggle**: square / landscape / portrait filtering is now a one-click toggle in the gallery header (previously only inside the filter modal).
  - **图库比例快速切换**：方形 / 横向 / 纵向筛选现在是图库标题列的一键切换（以前只在筛选弹窗里）。
- **Layout / i18n polish**: unified button heights via a `--btn-h` token, no more single-CJK-character label wrapping, a cleaner Processing-Queue button grid, and the gallery sidebar summary labels + "Any" colors default are now translatable.
  - **版面 / i18n 优化**：用 `--btn-h` token 统一按钮高度、修掉单个中文字换行、整理处理队列按钮版面、图库侧栏摘要标签与「任意」颜色预设可翻译。

## [3.2.3] - 2026-05-29

Maintenance / hardening release. No user-facing feature changes; focus is
security, dependency hygiene, internal structure, and CI quality gates.

维护 / 加固版本。无新使用者功能，重点是安全性、依赖卫生、内部结构与 CI 品质闸门。

### Security / 安全
- **idna bumped 3.13 → 3.17** to clear CVE-2026-45409 (CPU-DoS on crafted hostnames). An explicit `idna>=3.15` security floor was added to the lock inputs so it cannot regress.
  - **idna 升级 3.13 → 3.17**，清除 CVE-2026-45409（恶意主机名导致的 CPU-DoS）。lockfile 加了 `idna>=3.15` 安全下限防止回退。
- **Artist model integrity verification**: the Kaloscope checkpoint and `class_mapping.csv` are now verified against pinned SHA-256 digests before use.
  - **画师模型完整性校验**：Kaloscope checkpoint 与 `class_mapping.csv` 使用前会比对固定的 SHA-256 摘要。
- **Decompression-bomb uploads** to the obfuscation endpoint now return HTTP 413 instead of failing late.
  - **解压炸弹上传**在混淆端点会回传 HTTP 413，而非延后失败。
- Additional fixes from a full-stack (backend / frontend / pipeline) security and quality review.
  - 来自全栈（后端 / 前端 / pipeline）安全与品质审查的其他修正。

### Changed / 变更
- **`database.py` split into focused modules** (`db_core`, `db_helpers`, `db_query`, `db_schema`, `db_images_read`, `db_images_write`, `db_tags`, `db_facets`, `db_collections`). `database.py` is now a thin re-export facade (4441 → 351 lines); the public import surface is unchanged.
  - **`database.py` 拆分为多个聚焦模块**，`database.py` 变成薄薄的 re-export facade（4441 → 351 行），对外 import 介面不变。
- **ruff lint is now a blocking CI gate** (`select = F, E9`). Two real undefined-name bugs and assorted dead code/unused imports were fixed as part of adopting it.
  - **ruff lint 成为阻断式 CI 闸门**。采用时顺手修了两个真正的 undefined-name bug 与一批死码 / 未用 import。
- The release SOP is now version-controlled under `docs/RELEASE_SOP.md`.
  - 发布 SOP 现纳入版本控制（`docs/RELEASE_SOP.md`）。

## [3.2.2] - 2026-05-23

### Added / 新增
- **Linux portable bundle (Phase 1 + Phase 2)** — new `sd-image-sorter-vX.X.X-linux-portable-x86_64.tar.gz` and `sd-image-sorter-vX.X.X-linux-portable-aarch64.tar.gz` release assets that ship their own [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone) cpython 3.13. Same first-run flow as the Windows portable: extract, `chmod +x run-portable.sh`, run. Works on every modern Linux distro on either architecture, including ones whose system Python is 3.14 (where heavy AI wheels are not yet available) or where Python is missing entirely. The existing `linux.tar.gz` source-install variant stays available for advanced users who already manage a Python 3.12 / 3.13 toolchain. `run.sh` auto-detects an extracted portable bundle (`./python/bin/python3` + `./run-portable.sh`) and forwards there, so users who double-click `run.sh` on the portable archive still get the bundled-Python path. CI smoke job (`linux-portable-smoke`) builds BOTH arch tarballs on every push, then extracts the matching arch on a fresh `ubuntu-22.04` (x86_64) and `ubuntu-24.04-arm` (aarch64) runner respectively, runs `./run-portable.sh`, and probes `http://127.0.0.1:8499/` after first-run dependency install. Asset naming is `linux-portable-{x86_64,aarch64}.tar.gz`; the in-app updater picks the matching arch automatically via `LINUX_PORTABLE_ASSET_TEMPLATE` (formatted with the running machine's CPU arch).
  - **Linux 便携版打包（Phase 1 + Phase 2）**：新增 `sd-image-sorter-vX.X.X-linux-portable-x86_64.tar.gz` 跟 `sd-image-sorter-vX.X.X-linux-portable-aarch64.tar.gz` 两个发布 asset，都内置 cpython 3.13（用 [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)）。和 Windows 便携版一样：解压 → `chmod +x run-portable.sh` → 直接跑。任何现代 Linux 发行版都可用，无论是 x86_64（一般 PC、Steam Deck、x86 服务器）还是 aarch64（Raspberry Pi 4 / 5、ARM Linux 服务器、AWS Graviton、Apple Silicon 跑 Linux），包括系统 Python 已经是 3.14（重型 AI wheel 还没追上）或根本没装 Python 的情况。原有的 `linux.tar.gz` 源码版保留给会自己管 Python 3.12 / 3.13 工具链的进阶用户。`run.sh` 会自动侦测同目录是否有 `./python/bin/python3` + `./run-portable.sh`，有就直接转发，所以用户在便携版资料夹里不小心点 run.sh 还是会走 bundled Python 路径。CI smoke job（`linux-portable-smoke`）每次 push 都 build 两个架构的 tarball，分别在 ubuntu-22.04 x86_64 跟 ubuntu-24.04-arm aarch64 全新 runner 上解压、跑 `./run-portable.sh`、probe `http://127.0.0.1:8499/`。Asset 名为 `linux-portable-{x86_64,aarch64}.tar.gz`；in-app updater 会用 `LINUX_PORTABLE_ASSET_TEMPLATE` 加上运行中机器的 CPU 架构选对应 tarball。
- **Python 3.13 support alongside Python 3.12** (Linux source-install fix for users on Arch / Fedora 41+ / Ubuntu 25.04+ / Debian 13 where the default `python3` is 3.13): `backend/requirements-core.in` and `backend/requirements.in` now use `python_version` env markers so a single universal lockfile resolves correctly under both Python versions. 3.12 stays on numpy 1.26.4 + onnxruntime 1.20.1/1.25.0/1.19.2 (matches the existing SAM3/torch/scipy/opencv numpy-1 ABI). 3.13 picks numpy 2.4.6 + onnxruntime 1.26.0 (the newest cp313 wheels). `repair_torch_runtime.py` exposes `_numpy_sam3_constraint()` so the CUDA-torch repair pre-installs the correct numpy floor on each Python. README badge updated to `python-3.12 | 3.13`. Verified: 1526 / 6 skipped / 0 failed on Python 3.12.7 + numpy 1.26.4 (98s); same 1526 / 6 skipped / 0 failed on Python 3.13.13 + numpy 2.4.6 + torch 2.11.0+cpu + transformers 5.6.2 (561s, mostly cold-cache CPU torch). The original Linux user-bug — `pip install` source-building numpy 1.26.4 because of no cp313 wheel — is fixed. macOS Intel (x86_64) keeps `torch==2.2.2` / `torchvision==0.17.2` / `opencv-python==4.9.0.80` per-platform pins because PyTorch dropped Intel macOS wheels after 2.2.2 and opencv 4.11.0.86 only ships macOS 13+ wheels.
  - **Python 3.13 与 3.12 双轨支援**（Linux 源码安装的修复，针对 Arch / Fedora 41+ / Ubuntu 25.04+ / Debian 13 等系统预设 `python3` 是 3.13 的用户）：`backend/requirements-core.in` 和 `backend/requirements.in` 改用 `python_version` env marker，单一 universal lockfile 在两个 Python 下都能正确解析。3.12 维持 numpy 1.26.4 + onnxruntime 1.20.1/1.25.0/1.19.2（对齐现有 SAM3/torch/scipy/opencv 的 numpy-1 ABI），3.13 走 numpy 2.4.6 + onnxruntime 1.26.0（最新 cp313 wheel）。`repair_torch_runtime.py` 抽出 `_numpy_sam3_constraint()`，CUDA-torch 修复在每个 Python 下用对应 numpy floor 预装。README 徽章改为 `python-3.12 | 3.13`。验证结果：Python 3.12.7 + numpy 1.26.4 通过 1526/6 skipped/0 failed (98 秒)；同套测试在 Python 3.13.13 + numpy 2.4.6 + torch 2.11.0+cpu + transformers 5.6.2 也是 1526/6 skipped/0 failed (561 秒，主要是 CPU torch 冷启动)。报告里那位 Linux 用户遇到的 `pip install` 因 numpy 1.26.4 没 cp313 wheel 而硬编 mesonpy 失败的问题修好了。macOS Intel (x86_64) 维持 `torch==2.2.2` / `torchvision==0.17.2` / `opencv-python==4.9.0.80` 等旧版 pin，因为 PyTorch 在 2.2.2 之后就没有 Intel Mac wheel，opencv 4.11.0.86 也只支援 macOS 13+。
- **Smart Tag wizard inside Dataset Maker** (issue #5 partial / inspired by LoraHub's image studio): the `📦 Dataset Maker` tab now ships a `✨ Smart Tag (WD14 + VLM)` button that runs the local tagger (WD14 / OppaiOracle / Camie / PixAI) plus a VLM in one pipeline, with training-purpose-aware prompts (Style LoRA / Character LoRA / General / Concept), automatic noise-tag stripping (`masterpiece` / `score_9` / `anime` / `monochrome` / ...), trigger-word injection, and merge-vs-replace handling for existing captions. Backend exposes `POST /api/smart-tag/start`, `GET /api/smart-tag/progress`, `POST /api/smart-tag/cancel`. The training-purpose presets mirror LoraHub's `_SMART_CAPTION_PROMPT_STYLE / _CHARACTER / _GENERAL` exactly so results are comparable between the two tools. 38 new tests pin the noise-tag table, training-purpose alias map, and caption-assembly logic.
  - **Dataset Maker 智能标注向导**（issue #5 部分实现 / 灵感来自 LoraHub 图像工作台）：`📦 Dataset Maker` 标签页新增 `✨ 智能标注 (WD14 + VLM)` 按钮，一次跑完本地 tagger（WD14 / OppaiOracle / Camie / PixAI）+ VLM，按训练用途（风格 / 角色 / 通用 / 概念）自动选 prompt，自动剔除 `masterpiece` / `score_9` / `anime` / `monochrome` 等噪音 tag，自动注入 trigger word，可选替换或追加现有 caption。后端 `POST /api/smart-tag/start`、`GET /api/smart-tag/progress`、`POST /api/smart-tag/cancel` 三个接口。训练用途的 prompt 文案是逐字对齐 LoraHub 的 `_SMART_CAPTION_PROMPT_STYLE / _CHARACTER / _GENERAL`，所以两边出来的 caption 直接可比。新增 38 个测试覆盖噪音表、训练用途别名、caption 拼装逻辑。
- **OppaiOracle V1.1 ONNX tagger** (Grio43/OppaiOracle): a from-scratch ViT (~247M params, 19,294 general-only tags, 448x448 input) anime tagger added as a first-class option alongside WD14/Camie/PixAI. Auto-download (~947 MB) is wired through Model Manager and through the Tagger modal's first-run flow. Default threshold pinned to 0.7927 (the model's published P=R global threshold; produces 35-50 tags per image on real anime samples). Real-image verification on the user's reference corpus confirmed the output quality matches the model card (1girl, character names like `furina_(genshin_impact)` / `silver_wolf_(honkai:_star_rail)`, accessory tags, rating prediction). Backend route is a dedicated `OppaiOracleTagger` class because the model has TWO ONNX inputs (`pixel_values` + `padding_mask`) that the WD14 single-input runtime can't drive.
  - **OppaiOracle V1.1 ONNX 打标模型**（Grio43 训练）：和 WD14/Camie/PixAI 并列的新打标选项，从零训练的 ViT（约 2.47 亿参数，19,294 个通用标签，448x448 输入）。Model Manager 和 Tagger 弹窗都接好了 ~947 MB 自动下载。默认 threshold 0.7927（模型 README 给的 P=R 全局阈值，对真实动漫图像每张产出 35-50 个标签）。用 `L:\Pictures\AAA Reference` 实图实测，输出质量符合模型卡描述（1girl、`furina_(genshin_impact)` / `silver_wolf_(honkai:_star_rail)` 等角色识别、配饰标签、rating 预测都对）。后端是专门的 `OppaiOracleTagger` 类，因为该模型有两个 ONNX 输入（`pixel_values` + `padding_mask`），WD14 单输入运行时跑不动。
- **Dataset Maker small-gallery workspace** (issue #5 point 5): `📁 Add from Folder` button + `POST /api/dataset/folder-scan` endpoint let you add images directly from a folder without scanning them into the main library. Local items get a negative `ds_id`-derived pseudo-id, render their thumbnail from the scan response's base64 payload, and persist their captions to localStorage keyed by absolute path so re-imports restore your edits. Hard invariant: folder-scan never touches `images.db`; verified at the service and HTTP layers + an end-to-end browser run.
  - **Dataset Maker 小图库工作区**（issue #5 第 5 点）：📁「从资料夹加入」按钮 + 新 `POST /api/dataset/folder-scan` 端点，直接拖图入 Dataset Maker 不写主图库。本地项目用 `ds_id` 派生的负数伪 ID、缩图直接用 scan 回的 base64 渲染、caption 编辑写到 localStorage（按绝对路径键），重新匯入相同资料夹会自动还原。强不变式：folder-scan 绝对不动 `images.db`，service / HTTP / 浏览器端 e2e 都验证过。
- **Dataset Maker 3-tab pipeline + optional Audit modal**: a focused 3-tab nav (Import / Workbench / Export) replaces the old single-page layout. The Workbench tab houses Organize, Tag, and Caption flows; an optional Audit modal launched from the Import toolbar runs the LoRA-readiness checks. Scoped to the Dataset Maker tab only.
  - **Dataset Maker 3-tab 管线 + 可选审计模态**：聚焦的 3-tab 导航（导入 / 工作台 / 输出）取代旧版单页布局。工作台 tab 包含 Organize / Tag / Caption 流程；从导入工具列打开的可选审计模态执行 LoRA 就绪度检查。只作用在 Dataset Maker tab 内部。
- **Dataset Maker LoRA-trainer readiness audit**: optional collapsible Audit step + `POST /api/dataset/audit` endpoint. Three independent checks (aesthetic, perceptual-hash duplicate clustering, image-side dimension), all OFF by default, every threshold optional + unbounded. Result badges click-to-filter the queue; `Download report (.json)` exports the raw report. Untagged check is unconditional.
  - **Dataset Maker 训练就绪度审计**：可选折叠的「审计」步骤 + 新 `POST /api/dataset/audit` 端点。三项独立检查（美学分、phash 重复聚类、最短边像素），预设全关、每个阈值可选不设硬上限。结果徽章可点击高亮队列、可下载 .json 报告。「未标注」无条件启用。
- **Dataset Maker tag vocabulary side panel**: collapsible panel under the queue + `POST /api/dataset/vocab` endpoint. Tags aggregated into a frequency-sorted list; click cycles each tag through neutral → common → blacklist → neutral with live two-way sync to the Step B common-tags / blacklist textareas. Search filter + count display.
  - **Dataset Maker 标签词汇侧栏**：队列下方可折叠面板 + 新 `POST /api/dataset/vocab` 端点。标签按频次排序，点一次循环：中性 → 共用 → 黑名单 → 中性，与 Step B 双向即时同步。带搜索 + 计数。
- **Dataset Maker Anime LoRA defaults on first init** (ADR-2026-05-24): a fresh session pre-fills `Common tags = masterpiece, best_quality`, `underscore_to_space = ON`, `naming preset = renumber`, trigger placeholder `your_lora_trigger`. localStorage flag persists user customisation. New `🎌 Apply Anime LoRA defaults` button in Step B re-applies in one click. AI_PRINCIPLES.md §11 three-part justification met.
  - **Dataset Maker 首次进入预填 Anime LoRA 推荐预设**（ADR-2026-05-24）：新 session 自动填 `共用标签 = masterpiece, best_quality`、`底线转空格 = 开`、`命名预设 = renumber`、trigger placeholder = `your_lora_trigger`。localStorage flag 记录使用者自订；Step B 新「🎌 套用 Anime LoRA 预设」按钮可一键重套。符合 AI_PRINCIPLES.md §11 三段式。
- **Renamed-pair export discoverability chip** (issue #5 point 6): below the output folder field, a chip shows the live filename pair (`your_lora_001.png + your_lora_001.txt`) updating as you type the trigger / change the naming preset.
  - **重命名匯出可发现性 chip**（issue #5 第 6 点）：输出资料夹下方的 chip 即时显示档名对（`your_lora_001.png + your_lora_001.txt`），随 trigger / 命名预设改变同步更新。
- **Gallery selection toolbar: 'Send to Dataset Maker' replaces 'Analyze Colors'**: the standalone color analysis button on the gallery selection panel was swapped for `📦 To Dataset Maker`. Color analysis is still reachable via the Tag Images modal's Color tab.
  - **图库选择工具列：「送至 Dataset Maker」取代「色彩分析」**：色彩分析按钮换成 `📦 送至 Dataset Maker`。色彩分析仍可在打标弹窗的「色彩分析」分页里跑。
- **Tag Images modal Smart Tag hint banner**: dismissible banner inside `#tag-modal` points returning users at Dataset Maker → Smart Tag for batch LoRA workflows. localStorage flag persists dismissal.
  - **打标弹窗 Smart Tag 引导横幅**：可关闭引导横幅指向 Dataset Maker → Smart Tag。localStorage 持久记忆关闭。
- **Caption Editor virtual scroll**: unlimited images, on-demand caption loading, keyboard shortcuts (Escape/Ctrl+Enter/Arrows), queue count badge with >1000 warning
  - **Caption 编辑器虚拟滚动**：无上限图片支持、按需加载 caption、键盘快捷键、队列计数徽章
- **Per-item exclude on filters**: filter chips cycle include → exclude → remove; works on tags, generators, ratings, checkpoints, LoRAs
  - **筛选排除**：标签/生成器/分级/模型/LoRA 支持排除（红色删除线）
- **Auto-Separate inline filter chip editing**: clear individual filter dimensions without opening the modal
  - **自动分类 inline chip 编辑**：左栏每行可直接清除或添加筛选

### Fixed / 修复
- **Smart Tag VLM gate accepts local OpenAI-compatible servers without an api_key**: `_coerce_request` previously required BOTH `endpoint` AND `api_key` for any VLM, so users with a configured local Ollama / vLLM / LM Studio endpoint hit `HTTP 400 "VLM Settings has no endpoint or API key configured"` even after saving valid settings. The gate now lets local endpoints (loopback, `*.local`, `*.lan`, `*.internal`, `host.docker.internal`, RFC1918 LAN ranges) through without an api_key, and adds a Vertex AI auth path that requires `vertex_project` instead of an api_key. Cloud providers (Anthropic, public Gemini, OpenAI cloud, OpenRouter, etc.) still get caught with a clear error when api_key is missing. 9 new tests pin local-endpoint passthrough, Vertex auth, and cloud-rejection behaviour.
  - **智能标注 VLM 配置闸门接受本地 OpenAI 兼容服务无需 api_key**：`_coerce_request` 之前强制要求 `endpoint` + `api_key` 都有，本地 Ollama / vLLM / LM Studio 用户配好端点后还是会撞到 `HTTP 400 "VLM Settings has no endpoint or API key configured"`。现在本地端点（loopback / `*.local` / `*.lan` / `*.internal` / `host.docker.internal` / RFC1918 私网段）放行不需要 api_key，Vertex AI 改要 `vertex_project`，云端服务（Anthropic / 公共 Gemini / OpenAI / OpenRouter）若没填 api_key 仍按之前的明确错误拦下。新增 9 个测试覆盖本地放行、Vertex 验证、云端拒绝路径。
- **Drag-drop folders, ZIP, and RAR archives now support same-name `.txt` beside imported copies**: Beside-image export previously only worked for Gallery and folder-path scan sources; uploaded files were marked `cache_only` and forced into the folder-export branch. Drag-drop images, dropped folders, ZIP, and (new) RAR all now write the .txt next to the imported copy in the app data directory. RAR support is opt-in via the optional `rarfile` Python package + a system `unrar` binary; the upload route returns a clear bilingual error when those are missing instead of the previous hard rejection. Existing dataset-session tests updated; a new test pins the rarfile-missing failure path.
  - **拖入文件夹 / ZIP / RAR 都支持「写同名 .txt 到原图旁边」**：之前只有 Gallery 和文件夹路径扫描支持，浏览器上传的文件被标 `cache_only` 强制走文件夹导出分支。拖图、拖入文件夹、ZIP、RAR（新增）现在都会把 .txt 写到应用数据目录的导入副本旁边。RAR 是可选支持：需要 `rarfile` Python 包和系统 `unrar` 程序，没装时上传接口会返回明确双语错误而不是之前的硬拒绝。
- **`switchView` no longer flags an unconditional gallery refresh on every nav-out**: leaving the gallery used to always set `AppState.galleryNeedsRefresh = true`, which forced a full `loadImages()` API round-trip every time the user came back. After Reader save-as-new to a path outside the indexed library, the Reader's own `_markGalleryRefreshForIndexedOverwrite` correctly skipped marking, but the upstream view-switch had already flipped the flag, so the gallery still re-fetched and the `smoke.spec.ts:942` E2E contract failed. The cached `AppState.images` array survives the round-trip; coming back to the gallery now re-renders DOM via `Gallery.setImages` without a network refetch unless an explicit caller (scan completion, batch-move, save to indexed path) requested a refresh. Full smoke spec (76 chromium tests) green.
  - **`switchView` 不再每次离开图库都无条件标记需要刷新**：之前离开图库总会把 `AppState.galleryNeedsRefresh = true`，回来时强制走一次 `loadImages()`。Reader 「另存为新档案」到图库外路径时，Reader 自己的 `_markGalleryRefreshForIndexedOverwrite` 已经正确判定不该标记，但上游的 `switchView` 已经先把 flag 翻成 true，于是图库还是会重抓，`smoke.spec.ts:942` E2E 合约因此挂掉。`AppState.images` 缓存其实跨视图保留得住；现在回到图库走 `Gallery.setImages` 用缓存重渲染 DOM，只有显式调用方（扫描完成、批量移动、写到图库内路径）请求时才真正重抓。完整 smoke spec（76 个 chromium 测试）已绿。
- **batch-move catastrophic foot-gun**: `/api/batch-move` previously moved every image in the library when no filters were specified. A 3rd-party script POSTing `{"destination_folder": ..., "operation": "move", "image_ids": [a, b, c]}` had its `image_ids` silently dropped (no such field in `BatchMoveRequest`), so the worker counted 71,251 unfiltered matches and started moving them all. Now the schema requires at least one filter; an empty filter set returns 400 with a clear error pointing to which filters are accepted.
  - **批量移动灾难性陷阱**：`/api/batch-move` 原本在没有任何 filter 时会把整个图库都搬走。第三方脚本只送 `image_ids` 时 BatchMoveRequest 把 image_ids 字段直接丢弃，剩下空 filter 等于"匹配所有图片"，worker 就开始把 71,251 张图全部搬到一个文件夹。现在 schema 强制至少指定一个 filter；空 filter 直接 400，并提示哪些字段算 filter。
- **`/api/similarity/embed` ignored body image_ids**: the handler declared `image_ids: Optional[list]` without a Pydantic body model, so FastAPI treated it as a query parameter on POST and silently embedded the entire library instead of the requested subset. Wrapped in an EmbedRequest BaseModel; 7 router tests pin every body variant.
  - **`/api/similarity/embed` 忽略 body 里的 image_ids**：handler 直接用裸 Optional[list]，FastAPI 在 POST 上把它当 query 参数，body 里的 image_ids 被静默丢弃，于是不管你指定多少张，它都给整个图库做嵌入。现在用 EmbedRequest BaseModel 包好。
- **VLM tag parser rejects markdown / prose / LaTeX noise**: real Gemma / Qwen / GPT responses leak chain-of-thought into the danbooru-tags output (markdown headers `### 1. Address...`, bullet items `*   **Character Design:**`, LaTeX `$$x = ...$$`, sentence fragments). The previous parser only checked length 2 ≤ len ≤ 100 so all of those became searchable tags and silently polluted the user's library, top tags stats, and prompt-lab seeds. New shape-based filter rejects them at the parsing layer; migration 012 retroactively cleans existing pollution from the tags table.
  - **VLM 标签解析过滤 markdown / 散文 / LaTeX 噪声**：真实 Gemma / Qwen / GPT 输出会把 chain-of-thought 漏进 danbooru-tags 输出（markdown 标题 `### 1. Address...`、bullet 列表 `*   **Character Design:**`、LaTeX `$$x = ...$$`、半截句子）。旧解析器只检查长度 2 ≤ len ≤ 100，这些通通变成可搜索标签，污染图库、`/api/stats` 顶部标签和 prompt-lab。新基于形状的过滤器在解析阶段就拒掉它们；迁移 012 一次性清理已经写入的脏数据。
- **System-info endpoint cached, ~1000× faster on repeat calls**: the tagger setup modal hits `/api/system-info` repeatedly; previously each call re-spawned `nvidia-smi` + `Get-CimInstance` + torch.cuda init (~2-4 s). Now cached for 30 s with explicit `invalidate_system_info_cache()` for tests.
  - **/api/system-info 加 30 秒缓存，重复调用快约 1000 倍**：标签器设置弹窗反复打这个接口，原本每次都重新跑 `nvidia-smi`、`Get-CimInstance`、torch.cuda 初始化（约 2-4 秒）。现在加缓存，测试通过 `invalidate_system_info_cache()` 显式清缓存。
- **OSError-vs-ImportError DLL gap**: every prepare / status flow that imported torch (aesthetic, similarity, censor, optional_dependencies._needs_install) only caught `ImportError`. Windows raises `OSError` for cudnn / cuda DLL load failures, so a system with broken torch DLLs surfaced raw `[WinError 127] cudnn_cnn64_9.dll` 500 errors at the HTTP layer instead of a clean "feature unavailable" response. All affected routes / helpers now also catch `OSError`.
  - **OSError 与 ImportError 的 DLL 鸿沟**：所有 import torch 的 prepare / status 流（aesthetic、similarity、censor、optional_dependencies._needs_install）只 catch `ImportError`。Windows 在 cudnn / cuda DLL 加载失败时抛 `OSError`，结果系统 torch DLL 损坏的用户在 HTTP 层看到的是裸的 `[WinError 127] cudnn_cnn64_9.dll` 500 错误，而不是干净的"功能暂不可用"提示。受影响的 route / helper 现在一并 catch `OSError`。
- **Purge leaked pytest fixture rows from images table**: older test runs sometimes leaked their fixture rows into `data/images.db` when test isolation was less robust on Windows / WSL or when `TMPDIR` redirected to `data/tmp/`. Migration 011 detects and removes rows whose paths combine a runtime tmp prefix with an obvious pytest fixture marker. The test now also asserts up-front that the test_db fixture actually patched DATABASE_PATH so any future regression fails loudly instead of silently leaking.
  - **清理迁入图库的测试 fixture 行**：旧的测试运行有时会把 fixture 行漏进 `data/images.db`（Windows / WSL 隔离不稳，或 `TMPDIR` 指向 `data/tmp/`）。迁移 011 识别并清掉路径同时带有运行时 tmp 前缀和 pytest fixture 标记的行。测试本身现在会先检查 `test_db` fixture 真的改了 DATABASE_PATH，未来若隔离再出问题会立刻报错而非静默污染。
- **`?offset=` parameter now rejects negative values and absurd offsets**: `/api/images?offset=-1` previously silently fell back to offset=0 returning real data; now responds 400 with a clear field-name error. Upper bound 100 M caps blatant abuse.
  - **`?offset=` 拒绝负数和超大偏移量**：`/api/images?offset=-1` 原本静默当 offset=0 返回真实数据；现在响应 400 并指出是 offset 字段。上限 100M 防滥用。
- Filter modal stat grid: all 9 chips fit one row at any width
  - 筛选弹窗 9 个 chip 全部在一行
- Stale "up to 20 images" text removed from export preview
  - 移除过时的"最多 20 张"文字
- Duplicate TestExportSelectionData test class renamed (Debt-25)
  - 修复重复测试类名

### Deep bug-hunt 2026-05 — additional fixes / 深度 bug 挖掘新增修复

- **CRITICAL — caption sidecar `.txt` no longer matches image filename when filename has parens / apostrophes / commas / brackets**: `sanitize_filename` used a strict allow-list `[\w\s\.\-]` and replaced every other character with `_`. So `my (lora char).png` produced `my _lora char_.txt`, breaking exact-basename pairing that LoRA training tools require. Two-layer fix: (a) `sanitize_filename` switched from allow-list to block-list (preserves all OS-legal chars, only strips `< > : " / \ | ? *`, control chars, null byte, `..`); (b) `_allocate_output_path` derives sidecar stem from on-disk image path, not DB filename field. The same fix automatically resolves the parallel mangling in censor-edit's save-data endpoint.
  - **关键修复 — 同名 .txt 在文件名含 `()` `'` `,` `[]` 时跟原图配不起来**：`sanitize_filename` 用严格白名单 `[\w\s\.\-]`，其他字符一律换成 `_`。所以 `my (lora char).png` 产生 `my _lora char_.txt`，LoRA 训练工具按 basename 配对就配不上。两层修复：(a) `sanitize_filename` 改成黑名单（只挡 OS 不合法字符 `< > : " / \ | ? *` 和控制字符 / null byte / `..`，其他全保留）；(b) `_allocate_output_path` 直接用磁盘上图片的 basename，不再走 DB filename 字段。Censor 保存接口同样的 mangling 自动一并修好。
- **CRITICAL — Legacy DB upgrade broken on pre-v3.2.0 schemas**: any user upgrading from a pre-v3.2.0 schema hit `OperationalError: no such column: tagged_at` during `init_db()`. Three timestamp columns (`tagged_at`, `indexed_at`, `created_at`) were in `FULL_SCHEMA` but missing from the legacy backfill list `LEGACY_IMAGE_COLUMNS`. Added them so old DBs migrate cleanly.
  - **关键修复 — 旧版 DB 升级到 v3.2.x 直接报错**：从 v3.2.0 之前的 schema 升级时，`init_db()` 抛 `OperationalError: no such column: tagged_at`。三个时间戳列（`tagged_at`、`indexed_at`、`created_at`）在 `FULL_SCHEMA` 里但漏在 `LEGACY_IMAGE_COLUMNS` 补列清单。补回去后旧 DB 能干净迁移。
- **HIGH — `/api/library-health` event-loop blocking + 12 s SQL**: the route was `async def` but called synchronous SQL aggregations (~10 SUM/COUNT scans across the whole `images` table + duplicate-filename grouping + folder grouping). On a 71k-row library cold-cache calls take 4-12 seconds. The async route blocked the event loop, so 50 concurrent reads → 16 OK + 34 timeouts. Fix: switched route to `def` (offloaded to thread pool) + added 60 s TTL cache keyed by sample_limit. After: 50/50 concurrent reads succeed.
  - **HIGH — `/api/library-health` 卡死事件循环 + SQL 慢 12 秒**：路由是 `async def` 但里面调同步 SQL（71k 行做约 10 次 SUM/COUNT + 重名分组 + 文件夹分组）。冷缓存一次 4-12 秒，async 路由卡 event loop，50 个并发读 → 16 个成功 + 34 个超时。修法：路由改 `def` 让 FastAPI 丢线程池，加 60 秒 TTL 缓存（按 `sample_limit` 分桶）。修完 50/50 并发都成功。
- **HIGH — concurrent `POST /api/scan` race condition**: three simultaneous `/api/scan` POSTs all returned 200 "Scan started" but only one was actually running. The original guard required `status == 'running' AND worker_alive`, but the worker thread isn't alive until the background task picks up; second / third callers in the race window saw `worker_alive == False` and bypassed the guard. Introduced a `'starting'` transition state set inside the lock so concurrent callers see the in-flight slot before the worker is alive.
  - **HIGH — 并发 `POST /api/scan` race condition**：三个同时打 `/api/scan` 都拿到 200 「Scan started」但实际只跑了一个。旧 guard 是 `status == 'running' AND worker_alive`，但 worker thread 要等 background_task 起来才存在；race window 里第 2、3 个 caller 看到 `worker_alive == False` 就绕过 guard。修法：加一个 `'starting'` 过渡状态在锁里就 set，并发 caller 进锁就能看到 in-flight 标记。
- **HIGH — VLM tag parser accepting markdown / prose / LaTeX**: 401 garbage rows ("### 1. Address...", "*   **Character Design:**", LaTeX `$$x = ...$$`, sentence fragments) had been silently written to the user's tags table from local Gemma / Qwen / GPT outputs. New shape-based filter rejects them at parse time; migration 012 retroactively cleans existing pollution. (Already noted above; this entry calls out the row count actually cleaned from production DBs.)
  - **HIGH — VLM 标签解析器接受 markdown / 散文 / LaTeX**：本地 Gemma / Qwen / GPT 的 chain-of-thought 漏进 danbooru 标签，已经悄悄写了 401 行垃圾标签到用户图库。新形状过滤器在解析阶段就拒；迁移 012 一次性清干净。
- **HIGH — `/api/obfuscate/preview` 500 + Python BytesIO repr leak on non-image upload**: posting a zip / HTML / empty body returned `500 Internal Server Error` with the message `cannot identify image file <_io.BytesIO object at 0x000001...>` exposing internal Python repr. Now catches `UnidentifiedImageError` + `OSError` and returns a clean 400 with sanitized message.
  - **HIGH — `/api/obfuscate/preview` 收到非图片时 500 + 泄漏 Python BytesIO 对象内存地址**：传 zip / HTML / 空 body 时回 500 + `cannot identify image file <_io.BytesIO object at 0x...>`，把内部 repr 泄给客户端。现在 `UnidentifiedImageError` + `OSError` 一并 catch，回干净的 400。
- **HIGH — `/api/images/{id}` int overflow returns 500**: 24-digit numeric IDs that overflow int64 raised `UnhandledException`. Added FastAPI Path bounds `1 ≤ id ≤ 2³¹-1` so out-of-range IDs return 422 with the field name.
  - **HIGH — `/api/images/{id}` 整数溢出回 500**：24 位的超大数字 ID 让后端抛 `UnhandledException`。加上 `1 ≤ id ≤ 2³¹-1` 的 Path bound，越界值改回 422 并带字段名。
- **HIGH — 7 nav tabs visual / a11y inconsistency**: the Reader nav tab was the only tab in the 7-tab tablist without an icon (other tabs had 🖼️ 🔳 📁 🔍 🎨 🧪). 6 of 7 tabs also lacked `id` attributes, breaking deep-linking and screen-reader consistency. Added 📖 icon for Reader (mobile too) + `id="nav-tab-{view}"` for all 7 tabs.
  - **HIGH — 7 个导航 tab 视觉 / 无障碍不一致**：Reader 是 7 个 tab 里唯一没 icon 的（其他都有 🖼️ 🔳 📁 🔍 🎨 🧪）。6 个 tab 还没 `id`，导致深层链接和 screen reader 不一致。Reader 加 📖 图标（mobile 也同步），7 个 tab 都补上 `id="nav-tab-{view}"`。
- **MEDIUM — Mass-Tag-Editor modal Escape key did NOT close**: the modal opened via private `classList.add('visible')` bypassing the global `showModal` helper. As a result Escape didn't close it, focus wasn't trapped, focus wasn't restored to the trigger button on close, and the modal lacked `role="dialog"` / `aria-modal` / `aria-labelledby`. Now delegates to `window.showModal/hideModal` and has full ARIA.
  - **MEDIUM — 批量标签编辑器 modal 按 Escape 关不掉**：modal 自己 `classList.add('visible')` 绕开了全局 `showModal`，所以 Escape 关不掉、focus 不被困在 modal 里、关闭时 focus 不会回到触发按钮，modal HTML 还少了 `role="dialog"` / `aria-modal` / `aria-labelledby`。改成走 `window.showModal/hideModal`，ARIA 也补齐。
- **MEDIUM — `/api/images?generator=nai` (singular) silently returned the entire library**: FastAPI dropped the singular form as an unknown query param, so `?generator=nai` returned 71k images instead of 2,291. Added `generator` (singular) as alias for `generators` (plural) — merged + deduped. Same fix for `tag` / `rating` / `checkpoint` / `lora` (all four had the same trap).
  - **MEDIUM — `/api/images?generator=nai`（单数）静默回整个图库**：FastAPI 把单数形式当未知 query 参数丢了，所以 `?generator=nai` 回 71k 而不是 2,291 张。新增 `generator`（单数）作为 `generators`（复数）的 alias，自动合并去重。`tag` / `rating` / `checkpoint` / `lora` 同样的陷阱也一并修。
- **MEDIUM — Empty filter result mistaken for empty library**: when a user filtered their library and got 0 results, they saw the same "No images yet — Import a folder!" onboarding card meant for brand-new empty libraries. On a 71k-image library this looks like the entire collection vanished. Added a second variant: "No images match your filters" + 🧹 "Clear all filters" CTA, with full English + Chinese translations.
  - **MEDIUM — 空筛选结果被误显示成「图库是空的」**：用户筛选 0 张图时显示的是「还没有图片，导入图片文件夹」上手卡片（本来是给空图库新用户的），71k 图库的人会以为整个图库消失了。新增第二个变体：「没有符合条件的图片」+ 🧹「清除所有筛选」按钮，中英双语。
- **MEDIUM — Reader save-as `output_path` errors**: writing into `C:\Windows\System32\` returned `500 UnhandledException` (looked like server crash). Now `PermissionError` → 403, generic `OSError` → 400 with the underlying message. Empty `format=""` is now rejected at validation.
  - **MEDIUM — Reader 另存为路径错误处理**：写到 `C:\Windows\System32\` 直接 500 + `UnhandledException`（看起来像服务器崩了）。现在 `PermissionError` → 403，其他 `OSError` → 400 带具体原因。`format=""` 空字符串现在 validation 阶段就拒掉。
- **MEDIUM — `/api/tags/bulk/cleanup` `min_confidence` accepted out-of-range values**: `>1.0` silently meant "remove all tags" (destructive when `dry_run=False`); `<0` was a silent no-op. Confidence is normalized to [0.0, 1.0], so we now bound the field with Pydantic `ge=0.0, le=1.0`.
  - **MEDIUM — `/api/tags/bulk/cleanup` `min_confidence` 接受超出范围的值**：`>1.0` 静默等于「删掉所有标签」（`dry_run=False` 时直接生效）；`<0` 是静默无操作。Confidence 范围是 [0.0, 1.0]，加 `ge=0.0, le=1.0` Pydantic 约束。
- **LOW — Artist diagnostics legacy path drift**: the artist diagnostics endpoint reported `available: false` even on installs that had the legacy `models/artist/comfyui-lsnet-runtime/` path (the actual identifier resolver looked at both paths but the diagnostics endpoint only looked at the modern one).
  - **LOW — 画师识别诊断接口路径漂移**：旧版安装路径 `models/artist/comfyui-lsnet-runtime/` 下，诊断接口报 `available: false`，但实际 identifier 是好的（identifier resolver 同时查新旧两个路径，诊断只查了新的）。
- **LOW — 424 residual stress-test pollution rows**: another batch of test-run pollution cleaned via migration 013.
  - **LOW — 424 行 stress-test 污染**：迁移 013 一次性清掉 `stress-big-scan/` 等测试残留。
- **TUNING — ToriiGate VRAM threshold retuned 48 GB → 16 GB**: matches actual hardware needs of the 0.5B model.
  - **TUNING — ToriiGate 显存阈值从 48 GB 调到 16 GB**：贴合 0.5B 模型实际需要。

## [3.2.1] - 2026-05-20

### Added / 新增
- **VLM (Vision Language Model) captioning system**: a multi-provider natural-language captioning pipeline that runs alongside the existing WD14 / Camie / PixAI / ToriiGate taggers. Supports OpenAI-compatible (OpenAI, Ollama, vLLM, LMStudio, OpenRouter, Volcengine Ark), Anthropic Claude, Google Gemini (public API and Vertex AI with service-account JSON), and any local model that exposes a chat-completions endpoint. Per-image caption stored in the existing `ai_caption` column. Concurrency-controlled batch processing with retry-on-error (configurable max retries + delay), NSFW-refusal detection with relaxed-prompt fallback, mid-batch cancel that preserves completed work, token usage and success/failed counts in the progress UI.
  - **VLM 多厂商自然语言打标系统**：多家 provider 的 NL caption 流程，跟现有 WD14 / Camie / PixAI / ToriiGate 并存。支持 OpenAI 协议（OpenAI、Ollama、vLLM、LMStudio、OpenRouter、Volcengine Ark）、Anthropic Claude、Google Gemini（公共 API + Vertex AI service-account 认证）、和任何兼容 chat completions 的本地端点。NL caption 存进既有的 `ai_caption` 字段。批量处理带并发控制、自动重试（次数/间隔可配）、NSFW 拒绝时自动用宽松 prompt 重试、可中途取消保留已完成结果、进度面板显示 token 用量和成功/失败计数。
- **HTTP / HTTPS / SOCKS proxy support for VLM providers**: paste a proxy URL in VLM Settings → all VLM API calls route through it. Useful for restricted regions where OpenAI / Anthropic / Gemini are blocked. Supports separate HTTP and HTTPS proxies, or a single SOCKS5 proxy that covers both schemes.
  - **VLM provider 支持 HTTP / HTTPS / SOCKS 代理**：在 VLM 设置里填代理 URL，所有 VLM API 请求都会走这个代理。适合 OpenAI / Anthropic / Gemini 在某些地区直连不通的场景。可以分别配 HTTP 和 HTTPS 代理，或者用一个 SOCKS5 代理同时覆盖。
- **Vertex AI Gemini support**: the Gemini provider now supports Google Cloud Vertex AI in addition to the public Gemini API. Configure with project ID, region, and a service account JSON (paste content or file path). Access tokens cached for ~50 minutes. Enables enterprise / regulated deployments where the public Gemini key isn't an option.
  - **Gemini provider 支持 Vertex AI**：除了公共 Gemini API key 模式，现在也能跑 Google Cloud Vertex AI。填入 GCP project、region、service account JSON 即可（内容直接粘贴或填本地文件路径）。OAuth token 缓存约 50 分钟。适合不能用公共 Gemini key 的企业 / 合规场景。
- **VLM-as-danbooru-tagger mode**: VLM can output structured danbooru tags instead of (or in addition to) NL captions. New `output_format` setting: `nl_caption` (default), `danbooru_tags` (parsed comma-separated list, written to the tags table), or `both` (hybrid `<NL>...</NL><TAGS>...</TAGS>` format). Two new prompt presets `vlm_danbooru` and `vlm_hybrid`. Lets users with no GPU use a hosted VLM as their primary tagger, or pair WD14 with VLM-refined tags.
  - **VLM 也可以打 danbooru 标签**：除了出 NL caption，VLM 也能输出结构化 danbooru 标签。新增 `output_format` 设置：`nl_caption`（默认）、`danbooru_tags`（解析逗号分隔列表写入 tags 表）、`both`（`<NL>...</NL><TAGS>...</TAGS>` 混合格式）。新增两个 prompt preset `vlm_danbooru` 和 `vlm_hybrid`。无 GPU 用户可以用云端 VLM 当主打标，或让 VLM 来精修 WD14 的标签。
- **One-click Ollama local model deployment**: VLM Settings shows a curated list of recommended vision models (Gemma 3 4B, Gemma 4 27B + uncensored 26B Heretic, Qwen 2.5 VL 7B/32B, Qwen3-VL 8B/32B, MiniCPM-V 4.5/4.6) with size + minimum VRAM requirements + NSFW tolerance badges. Click "Download" to fetch via Ollama with live progress; "Use This" to auto-configure the endpoint. Auto-starts Ollama if installed but not running. Detects platform-specific install instructions if Ollama is missing.
  - **一键下载部署本地 VLM**：VLM 设置显示推荐的视觉模型列表（Gemma 3 4B、Gemma 4 27B + 解锁版 26B Heretic、Qwen 2.5 VL 7B/32B、Qwen3-VL 8B/32B、MiniCPM-V 4.5/4.6），每个都标注体积、最低显存、NSFW 容忍度。点 "Download" 通过 Ollama 拉取，实时进度；"Use This" 自动填好 endpoint。Ollama 已安装但没启动会自动起服务；没装会显示对应平台的安装指引。
- **Provider auto-detection from endpoint URL**: paste any endpoint URL in VLM Settings → app auto-detects whether it's Anthropic, Gemini, Vertex, or OpenAI-compatible. No more guessing which provider option to pick.
  - **从 endpoint URL 自动识别 provider**：在 VLM 设置里填任意 endpoint，app 会自动判断是 Anthropic / Gemini / Vertex / OpenAI 兼容。不用自己猜该选哪个。
- **5 prompt presets for different LoRA training styles**: built-in system prompts tuned for general LoRA training (NL caption), Anima / FLUX-style detailed NL, short single-sentence captions, character LoRA training (skips fixed character features), and NSFW-tolerant for local models.
  - **5 个 prompt preset 对应不同 LoRA 训练风格**：内建 system prompt 模板，包括通用 LoRA 训练（NL caption）、Anima / FLUX 风格详细 NL、单句短描述、角色 LoRA（跳过固定特征）、本地模型用的 NSFW 兼容版。
- **Export template engine for LoRA training**: a new template-based export system with 7 LoRA training presets (Anima Tags+NL, Anima Tags-only, Illustrious / Pony, NoobAI, FLUX, Kohya SD1.5, Custom). Anima preset auto-converts underscores to spaces (preserving `score_N`). Tag-processing pipeline: blacklist → replace → max-N → append. 14 template variables: `{trigger}`, `{tags}`, `{tags:N}`, `{tags:filtered}`, `{nl_caption}`, `{prompt}`, `{negative}`, `{rating}`, `{count}` (auto-extracts `1girl`/`2boys`/etc.), `{characters}`, `{general}`, `{quality}`, `{safety}`, `{append}`. New `nl_caption` and `prompt_nl` content modes for simple natural-language exports. Live preview API renders captions for up to 20 sample images at once.
  - **LoRA 训练专用导出模板引擎**：新的模板化导出系统，附 7 个 LoRA 训练 preset（Anima Tags+NL、Anima 纯 Tags、Illustrious / Pony、NoobAI、FLUX、Kohya SD1.5、自定义）。Anima preset 自动把下划线换成空格（保留 `score_N`）。标签处理顺序：黑名单 → 替换 → 最多 N 个 → 附加。14 个模板变量：`{trigger}`、`{tags}`、`{tags:N}`、`{tags:filtered}`、`{nl_caption}`、`{prompt}`、`{negative}`、`{rating}`、`{count}`（自动抽取 `1girl`/`2boys` 之类）、`{characters}`、`{general}`、`{quality}`、`{safety}`、`{append}`。新增 `nl_caption` 和 `prompt_nl` 两个简单的 NL-only 导出模式。Live preview API 一次最多渲染 20 张样图。
- **Color analysis during scan + color-based gallery filter & sort**: a new color analyzer extracts dominant colors (top 5 with hex + percentage), average brightness (HSV V, 0-255), color saturation, color temperature (warm / cool / neutral), brightness histogram (16 buckets), brightness skew (third moment), and brightness distribution shape (left_heavy / right_heavy / middle_heavy / edge_heavy / balanced). Stored in 7 new indexed DB columns added by migration 010. New gallery sort options: brightness, saturation, brightness_skew (asc + desc). New filter parameters: brightness_min/max, color_temperature, brightness_distribution. Colors compute on a 64x64 thumbnail (~5-15ms per image).
  - **扫图时分析图片色彩 + 图库按色彩筛选/排序**：新增色彩分析模块，抽取主色（top 5 含 hex 和占比）、平均亮度（HSV V 通道，0-255）、饱和度、色温（暖 / 冷 / 中性）、亮度直方图（16 桶）、亮度偏度（三阶矩）、亮度分布形状（左侧重 / 右侧重 / 中间重 / 两端重 / 均衡）。这些信息存进 migration 010 新增的 7 个有索引的字段。图库新增排序选项：亮度、饱和度、亮度偏度（升 + 降）。新增筛选参数：亮度区间、色温、亮度分布。色彩在 64x64 缩略图上计算，每张约 5-15 毫秒。
- **Histogram shape classification distinguishes line art from photos**: the `edge_heavy` distribution detects images with both pure-black and pure-white concentrations (line art / sketches / B&W comic style). `middle_heavy` flags typical photos / anime cels. `left_heavy` / `right_heavy` mark dark-dominant or bright-dominant scenes. New `Sort by Dark→Bright distribution` option puts dark-heavy images first.
  - **亮度分布形状分类能把线稿和照片区分开**：`edge_heavy` 识别图片同时有大量纯黑和纯白（线稿 / 素描 / 黑白漫画风格）。`middle_heavy` 对应大部分照片 / 动画。`left_heavy` / `right_heavy` 是暗调主导或亮调主导。新增 "Dark→Bright 分布" 排序，把暗调密集的图排前面。
- **`/api/colors/analyze` batch backfill endpoint**: batch-runs color analysis on existing libraries that haven't been analyzed yet. Concurrency-controlled, cancelable, with progress polling. Use `/api/colors/missing-count` to see how many images still need analysis.
  - **`/api/colors/analyze` 批量补算接口**：给已有图库批量补算色彩信息。带并发控制、可取消、可轮询进度。`/api/colors/missing-count` 看还剩多少张没分析。
- **Mass tag editor (Tag-Master inspired)**: 4 new bulk tag operations on the DB tags table — Find & Replace (rename a tag across N images, supports remove via empty replace), Bulk Add (append tags with confidence override, dedupe vs existing), Bulk Remove (delete specified tags, case-sensitive optional), Cleanup (drop tags below min confidence + dedupe by case-insensitive tag name keeping highest-confidence copy). Each operation supports `dry_run=True` to preview affected_images count and up to 5 sample before/after pairs before committing.
  - **批量标签编辑器（参考 Tag-Master 设计）**：新增 4 个批量标签操作 — 查找替换（跨 N 张图重命名标签，replace 留空表示删除）、批量添加（追加标签并指定置信度，自动去重）、批量删除（删指定标签，可选大小写敏感）、清理（删除置信度低于阈值的标签 + 按 tag 名字去重保留最高置信度）。所有操作支持 `dry_run=True` 预览，返回影响图片数和最多 5 条修改前后对比。
- **Mass Tag Editor frontend**: the nav bar gains a 🧹 button that opens a modal for the 4 bulk-tag operations above. Scope picker (current selection vs current filter), tabbed operation panels, mandatory dry-run preview, and a confirm dialog with a 2-second delayed Apply button when scope exceeds 1,000 images. Filter scope uses the `/api/images/selection-token` + `selection-chunk` flow so even a 70k-image filter loads in ~3s. Fully bilingual (en + zh-CN), zero new dependencies.
  - **批量标签编辑器前端**：导航栏新增 🧹 入口，打开上述 4 个后端接口的统一界面。带范围选择（已选 vs 当前筛选）、Tab 切换操作面板、强制 Dry-run 预览、超过 1,000 张时二次确认弹窗（Apply 按钮有 2 秒倒计时）。筛选范围通过 `/api/images/selection-token` + `selection-chunk` 分块抓 ID，7 万张图 ~3 秒搞定。完全双语，无新增依赖。
- **VLM Settings — proxy / Vertex AI / output format frontend**: the VLM Settings modal now surfaces the proxy, Vertex AI, and output-format backend features. Output Format is a segmented control (NL caption / Danbooru tags / Both) at the top. Network Proxy is a collapsed `<details>` with HTTP / HTTPS / SOCKS fields. Vertex AI is a separate collapsed `<details>` (project / region / service-account JSON) that auto-appears only when provider is Gemini. Both sections show an "active" badge when they hold non-default values.
  - **VLM 设置 — 代理 / Vertex AI / 输出格式前端**：VLM 设置弹窗现在把代理、Vertex AI、输出格式三组后端功能全接出。Output Format 是顶部三段控件；Network Proxy 是折叠区含 HTTP / HTTPS / SOCKS 三个框；Vertex AI 是另一个折叠区（GCP project / 区域 / 服务账户 JSON），仅 provider 选 Gemini 时显示。有非默认值时显示 "已启用" 徽章。
- **Color analysis backfill UX**: a banner appears above the gallery when the user picks a color-based sort and the library has images with missing color data. Shows live missing count via `/api/colors/missing-count` with a one-click "Analyze N images" button. While running, a `[🎨 N%]` chip in the nav bar opens a bottom-right toast with progress bar, current filename, and Pause / Run-hidden actions. Banner can be dismissed for 24h via localStorage.
  - **色彩补算引导**：选色彩排序但库里还有没分析过的图时，图库上方弹引导横幅。实时显示未分析数，一键启动补算。运行时导航栏出现 `[🎨 N%]` 进度芯片，点开右下角 toast 含进度条、文件名、暂停操作。横幅可关闭 24 小时。
- **`count_images_missing_color_data()` helper**: a `SELECT COUNT(*)` helper in `database.py` that replaces the "fetch full ID list then `len()`" pattern in `/api/colors/missing-count`. Constant memory regardless of library size.
  - **`count_images_missing_color_data()` 计数辅助**：用 `SELECT COUNT(*)` 替代 `/api/colors/missing-count` 里 "拉完整 ID 列表再 len()" 的写法，常数内存。

### Changed / 變更
- **Caption Editor full-screen workbench (task #33)**: the same-name `.txt` / LoRA caption export now exposes its 3-column workbench (image queue / current caption editor / shared-tag toolbox) in a dedicated near-fullscreen modal, opened via a new `Open Editor` button next to `🔄 Refresh` in the batch-export preview header. Closing the editor returns the workbench to the inline pane with all temporary edits preserved. The workbench logic, blacklist, and image-override semantics are unchanged — this is purely about giving power users editing LoRA training captions real horizontal real estate.
  - **Caption 编辑器全屏工作台（任务 #33）**：同名 `.txt` / LoRA caption 导出的 3 栏工作台（左：图片队列；中：当前 caption 编辑；右：共同标签 + 检查 + 清理）现在可以在专用的近全屏弹窗里打开，通过批量导出预览顶栏新增的 `Open Editor` 按钮。关闭弹窗后工作台回到原来的内嵌面板，临时编辑全部保留。工作台逻辑、黑名单、image override 语义都没变 —— 这次纯粹是给在改 LoRA 训练 caption 的高强度用户更宽的编辑空间。
- **Auto-Separate 3-pane workbench redesign (task #35)**: the Auto-Separate page no longer stacks Saved Configs / Filter Criteria / Destination / Preview / File Action / Run vertically on a tall single column. It now uses a left/center/right workbench: filter editor + saved configs + scope status on the left, the preview grid (the visual focus) in the center, and Destination + Move/Copy + Run CTA on the right. The shell caps to viewport so the big Run CTA stays visible at the bottom of the action pane on every common screen size. At 1080-1280px the action pane becomes a sticky bottom bar across the full shell width; below 760px the layout stacks into a single column. Every previously locked element ID is preserved verbatim, so the existing `autosep.js` keeps working without changes — saved configs, scope intent buttons (use saved / copy from gallery / resync), filter scope independence, copy-by-default radio, `confirmBeforeMove=true` default, preview list, and the underlying `/api/batch-move` flow all continue to behave identically.
  - **自动分类 3 栏工作台重设计（任务 #35）**：自动分类页不再像长表单那样把 已保存配置 / 筛选条件 / 目标文件夹 / 预览 / 文件操作 / 执行 一路竖着叠下来。现在改成左中右三栏：左侧是 已保存配置 + 筛选条件 + 同步状态；中间是预览图网格（视觉焦点）；右侧是 目标文件夹 + 移动 / 复制 + 大执行按钮。整个外壳高度被钉到 viewport，所以右侧的大「Run」按钮在常见屏幕尺寸下永远可见。1080-1280px 时右栏会改成横跨整宽的吸底操作条；低于 760px 单列堆叠。所有原来锁定的元素 ID 都原封保留，所以现有的 `autosep.js` 不需要任何改动 —— 已保存配置、3 个筛选 scope 按钮（使用已保存 / 从图库复制 / 再次复制）、自动分类筛选与图库筛选互相独立、默认复制不移动、`confirmBeforeMove=true` 默认勾、预览列表、`/api/batch-move` 后端流程全部维持原本行为。
- **VLM batch progress now reports token usage**: progress endpoint includes `tokens_used` (sum across completed requests) and `errors` list with up to 50 entries showing `image_id`, error message, and error type for debugging.
  - **VLM 批量进度增加 token 统计**：进度接口新增 `tokens_used`（已完成请求的 token 总数）和 `errors` 列表，最多 50 条，每条含 `image_id` / 错误信息 / 错误类型，方便排查。
- **`/api/images` now returns color columns**: `dominant_colors`, `avg_brightness`, `color_temperature`, `color_saturation`, `brightness_distribution` are now in gallery/list views; detail view additionally returns `brightness_histogram` and `brightness_skew`. Previously migration-010 columns existed in SQLite but were never SELECT-ed, so color sort/filter worked but display didn't. Fix in `_IMAGE_COLUMNS_*_FIELDS` constants in `database.py`.
  - **`/api/images` 现在会返回色彩字段**：5 个用户可见色彩字段加入列表视图；详情视图还多 `brightness_histogram` 和 `brightness_skew`。之前 migration 010 字段在 SQLite 存在但 SELECT 列表遗漏，修复在 `_IMAGE_COLUMNS_*_FIELDS`。
- **Tags-bulk batch-load tags** (`routers/tags_bulk.py`): the 4 bulk-tag endpoints used to call `db.get_image_tags(id)` per image (500k round-trips at max). Now one up-front `db.get_image_tags_map(image_ids)` batched 500 IDs per query. ~500× fewer SELECTs on the read side.
  - **批量标签操作改成批读**：四个端点之前逐图查标签，现在改成一次性 `get_image_tags_map` 批量读取（每 500 ID 一批），读取阶段 SELECT 降低约 500×。
- **`asyncio.get_event_loop()` → `asyncio.create_task()` / `asyncio.get_running_loop()`** in `routers/colors.py` and `routers/vlm.py`: deprecated in Python 3.10, raises in 3.12 outside a running loop.
  - **asyncio 现代化**：`routers/colors.py` 和 `routers/vlm.py` 的 `get_event_loop()` 替换为 `create_task()` / `get_running_loop()`。
- **`db.add_tags` docstring** now warns this is `DELETE + INSERT` (replace) semantics, not append. Behaviour unchanged, only documentation.
  - **`db.add_tags` docstring**：明确说明是替换语义，不是追加。

### Fixed / 修复
- **Same-name `.txt` export converts danbooru tag underscores to spaces by default for LoRA training** (`backend/services/tag_export_service.py` + `backend/services/export_template_engine.py`): the local tagger writes danbooru identifiers like `multiple_girls`, `looking_at_viewer`, `blue_hair` — which the Anima / FLUX / general anime-NL family of LoRA trainers cannot use as caption tokens. Same-name `.txt` export now normalizes those to `multiple girls`, `looking at viewer`, `blue hair` automatically for the danbooru-tag content modes (`tags`, `caption_tags`, `caption_merged`, `tags_nl`). Pony / NoobAI quality tokens that depend on the underscore — `score_5`, `score_9_up`, etc. — are preserved by an explicit `score_*` prefix list. The user's prefix / class token text and the AI caption NL text are NOT normalized (deliberate input). Pony / NoobAI / Kohya legacy users who want raw danbooru underscores can untick the new "Convert tag underscores to spaces (preserve `score_*`)" checkbox in the export modal — the choice is persisted via `localStorage`. The live preview matches what the export will write, so users see the normalized output before clicking Export. Template mode keeps its per-preset behaviour (Anima already normalized; Pony / NoobAI / FLUX / Kohya already kept underscores) and the new override only applies if the user explicitly unticks the checkbox.
  - **同名 `.txt` 导出默认把 danbooru 标签的下划线转成空格，方便 LoRA 训练**：本地 tagger 输出的是 `multiple_girls`、`looking_at_viewer`、`blue_hair` 这种 danbooru 风格的下划线写法；但 Anima / FLUX / 主流 anime-NL 系列的 LoRA 训练器其实读不进这种 token。现在同名 `.txt` 导出在 danbooru 标签模式下（`tags` / `caption_tags` / `caption_merged` / `tags_nl`）自动把它们转成 `multiple girls`、`looking at viewer`、`blue hair`。Pony / NoobAI 依赖的 `score_5`、`score_9_up` 这种品质 token 会被显式地保留（按 `score_*` 前缀名单识别）。用户填的 Class Token / 前缀和 AI 自然语言 caption 不会被改（那都是用户自己的输入）。如果你跑的是 Pony / NoobAI / 老 Kohya 工作流，想保留原汁原味的下划线，可以在导出弹窗里取消「把标签的下划线转成空格（保留 `score_*`）」勾选，选择会保存在 `localStorage`。Live preview 跟实际导出走同一份逻辑，先勾再看一眼再点 Export。Template 模式保持各 preset 自己的行为（Anima 本来就是空格、Pony / NoobAI / FLUX / Kohya 本来就保留下划线），只有当你明确取消勾选时才会强制覆盖。
- **Same-name `.txt` export no longer produces LoRA-incompatible `123.json.txt` sidecars** (`backend/services/tag_export_service.py`): when two indexed images shared a basename but had different source extensions (e.g. `123.png` and `123.json`, or `sample.jpg` and `sample.gif`), the collision-disambiguation fallback used to write the second sidecar as `{full_filename}.txt` — for example `123.json.txt` or `sample.gif.txt`. LoRA training pipelines pair captions with images by basename match, so those dual-extension sidecars were silently ignored at training time and the model never saw the captions. The allocator now uses a clean numeric suffix (`123.txt` first, `123_1.txt`, `123_2.txt`, ...) for collisions, which every LoRA trainer accepts. Existing single-image-per-basename exports are unaffected (still write `image_001.txt`). Regression coverage in `test_export_batch_keeps_lora_friendly_sidecar_for_dotted_filenames` reproduces the original `123.json.txt` failure mode and asserts the fix holds for `photo.bak.png` (which still uses `photo.bak.txt`, the LoRA-correct stem) and the `sample.jpg` / `sample.gif` collision case.
  - **同名 `.txt` 导出不再生成 LoRA 不兼容的 `123.json.txt`**：之前如果图库里有 basename 相同但副档名不同的两张图（例如 `123.png` 和 `123.json`，或 `sample.jpg` 和 `sample.gif`），第二张图的 sidecar fallback 会用 `{完整原文件名}.txt` 命名 —— 实际写出 `123.json.txt` / `sample.gif.txt` 这样的双副档名文件。LoRA 训练脚本以 basename 配对 caption 和图片，这种文件训练时会被静默忽略，模型根本看不到 caption。现在第二张图改用纯数字后缀（首张 `123.txt`，冲突的 `123_1.txt`、`123_2.txt`），任何 LoRA 训练器都能识别。单 basename 单图的常规导出不受影响（依然写 `image_001.txt`）。
- **PyPI + CUDA PyTorch downloads both auto-pick the fastest mirror**: the launcher (`run.bat` / `run.sh`) now probes Tsinghua TUNA, Aliyun, USTC, and the official PyPI host with a stdlib-only probe BEFORE `pip install -r requirements.txt`, then passes `--index-url <fastest> --extra-index-url https://pypi.org/simple` to every pip call. The same probe runs again for the CUDA torch wheel reinstall in `repair_torch_runtime.py`, choosing between SJTU and the official PyTorch host. Both probes hit the real PEP 503 path (`<base>/pip/` and `<base>/cu128/torch/`) so portal-page mirrors that 200 on `/` but 404 on the actual index are detected at probe time. The httpx-based selector caches its answer in `data/state/mirror_cache.json` for 30 minutes; the launcher's pre-install probe is stdlib-only (no httpx dep, since httpx is being installed by the very call we are accelerating). Power users can force a specific mirror with `SD_IMAGE_SORTER_PYPI_MIRROR=tuna|aliyun|ustc|official|<url>` and `SD_IMAGE_SORTER_TORCH_CUDA_MIRROR=sjtu|official|<url>`. Before this fix `_resolve_pypi_fallback_index()` already referenced a `mirror_selector` module that had never been committed — every call silently fell back to `pypi.org/simple` and the CUDA torch wheel was never routed through any mirror selection at all. On a Chinese broadband line that means the previously slow ~1.5 GB `requirements.txt` install (10–25 minutes on Fastly) plus the 2.5 GB CUDA torch wheel (30–60 minutes) now both fall to minutes via Tuna / SJTU.
  - **PyPI 和 CUDA PyTorch 下载都自动选最快镜像**：启动脚本（`run.bat` / `run.sh`）在 `pip install -r requirements.txt` **之前**用纯 stdlib 探测清华 TUNA、阿里云、中科大、官方 PyPI 源，挑最快的传给每个 pip 调用 `--index-url <fastest> --extra-index-url https://pypi.org/simple`。CUDA torch wheel 在 `repair_torch_runtime.py` 里再探一次，在 SJTU 和官方 PyTorch 源之间挑。两个 probe 都打真正的 PEP 503 路径（`<base>/pip/` 和 `<base>/cu128/torch/`），所以"`/` 返回 200 但实际 index 404"的门户页假镜像在探测阶段就会被识破。httpx 版的选择器把结果缓存到 `data/state/mirror_cache.json` 保 30 分钟；启动脚本里那一步是 stdlib-only（不能用 httpx，因为 httpx 正是它要装的东西）。可用 `SD_IMAGE_SORTER_PYPI_MIRROR=tuna|aliyun|ustc|official|<url>` 和 `SD_IMAGE_SORTER_TORCH_CUDA_MIRROR=sjtu|official|<url>` 强制指定。修复前 `_resolve_pypi_fallback_index()` 已经引用了一个从未提交过的 `mirror_selector` 模块 —— 每次调用都静默回退到 `pypi.org/simple`，而 CUDA torch wheel 主路径压根没接入任何镜像选择。对中国宽带用户来说，原来慢的 ~1.5 GB `requirements.txt`（Fastly 上 10–25 分钟）加上 2.5 GB CUDA torch wheel（30–60 分钟），现在通过 Tuna / SJTU 都能降到几分钟。
- **Thumbnail cache temp-path collision** (`thumbnail_cache.py`): two writers in the same process+thread that both finished in the same `time.time_ns()` window could collide on the `.tmp` path. Path now combines PID + TID + nanosecond + process-local counter + 8 hex chars of OS randomness. Verified by the previously-failing regression test `test_thumbnail_cache_temp_paths_are_unique_for_same_cache_key`.
  - **缩略图缓存临时路径冲突**：同进程同线程两个写入者落在同一 `time.time_ns()` 窗口会撞到相同 `.tmp` 路径。现在路径组合 PID + TID + 纳秒戳 + 单调计数 + 8 个随机十六进制字符。
- **Windows browser no longer opens before server is ready** — launcher now probes the port in a background PowerShell process and only opens the browser once the server responds (up to 15 s timeout). Eliminates the `ERR_CONNECTION_REFUSED` page on first launch.
  - **Windows 浏览器不再在 server 就绪前打开** —— 启动器现在用后台 PowerShell 探测端口，server 响应后才开浏览器（最多等 15 秒）。
- **macOS source-clone no longer rejected by `run.sh`** — the Darwin check now only fires inside release tarballs (detected via `update/package-manifest.json`). Users cloning from source on macOS can run `./run.sh` directly.
  - **macOS 从源码 clone 不再被 `run.sh` 拒绝** —— Darwin 检查现在只在 release tarball 内触发。
- **Onboarding tour auto-starts on true first-run** — when the gallery has never loaded images (fresh install), the interactive guided tour starts automatically. A "Tour" button in the Guide modal lets users restart it anytime.
  - **首次启动自动开始引导导览** —— 空图库时自动启动互动导览；Guide 弹窗里有「Tour」按钮可随时重启。
- **Model download polling has a 4-minute timeout** — if the backend silently stalls, the UI shows a warning and re-enables the Prepare button instead of spinning forever.
  - **模型下载轮询有 4 分钟超时** —— 后端静默卡住时 UI 会提示并恢复按钮。
- **Cancel button for in-progress model downloads** — users can abort a download without closing the modal or refreshing the page.
  - **模型下载可取消** —— 下载中出现 Cancel 按钮，不用关弹窗或刷新页面。
- **Feature Setup button pulses on first visit** — an orange ring animation draws attention to the setup entry point until the user clicks it once.
  - **Feature Setup 按钮首次访问有脉冲动画** —— 橘色光环提示新用户注意。
- **Feature availability notice now lists Color Analysis, LoRA Export, and VLM captioning** — the "Ready" and "Needs Prepare" cards in Feature Setup are more complete.
  - **功能可用性说明补全** —— 现在列出色彩分析、LoRA 导出、VLM 打标。

### Notes / 注意事項
- Vertex AI requires the `google-auth` Python package; the app shows a helpful error message if it's missing. Run `pip install google-auth` to enable.
  - Vertex AI 需要 `google-auth` 包，没装会有提示。`pip install google-auth` 装上即可。
- SOCKS proxy requires `httpx[socks]` extra. The provider falls back to direct connection with a log warning if not installed.
  - SOCKS 代理需要 `httpx[socks]`，没装的话会自动降级直连并记录警告。
- Color analysis is opt-in for existing libraries; run via `/api/colors/analyze` or use the new backfill banner to backfill. New scans automatically populate color data.
  - 色彩分析对老图库是按需执行；用 `/api/colors/analyze` 或新的补算引导横幅来补算。新扫的图会自动填好。
- All 1,112 backend tests pass after the changes.
  - 改完后 1,112 个后端测试全部通过。
- The Mass Tag Editor confirm-dialog threshold (1,000 images) is the trip-wire for the 2-second delayed Apply button. Below that, the operation runs immediately on click.
  - Mass Tag Editor 二次确认阈值是 1,000 张（超过才会出现倒计时 Apply 按钮），少于该数量直接执行。

### UX Polish (pre-release sweep) / 体验抛光（发版前最后一轮）
- **i18n stays fresh after upgrade without `Ctrl+Shift+R`**: every `<script>` and `<link>` tag served by `GET /` now gets a `?v=APP_VERSION` cache-bust query, so a normal browser F5 after upgrading the backend pulls the new `lang/*.js` instead of silently re-using the cached old language pack. The Help modal also gains a "🔄 Refresh translations / 🔄 重新载入界面文字" button that re-fetches the language packs in place — gallery filters, scan progress, selection state, and `localStorage` survive the swap.
  - **升级后 i18n 不再需要硬刷**：服务端在每个 `<script>` 和 `<link>` 上自动加 `?v=APP_VERSION`，浏览器普通 F5 就能拉到新版 `lang/*.js`，不会再静默用旧语言包。Help 弹窗也加了「🔄 重新载入界面文字」按钮，原地重抓语言包，**图库筛选 / 扫描进度 / 选择 / `localStorage` 都会保留**，不会丢资料。
- **Help "?" reachable at every viewport**: at ≤768px the desktop nav row is hidden by CSS; we now expose a `mobile-btn-help` inside the hamburger overlay that opens the same Guide modal. Verified by Playwright on 1920 / 1366 / 1024 / 800 / 768 / 600 / 480.
  - **❓ 在任何视口都能找到**：768px 以下桌面导航被 CSS 隐藏，现在 hamburger 菜单里加了 `mobile-btn-help`，进同一个 Guide 弹窗。Playwright 实际点过 7 种视口都没问题。
- **Scan modal feels less cramped in zh-CN**: the import modal moves from `modal-small` (400px) to `modal-medium` (500px), giving folder path input + Browse button visible breathing room.
  - **扫描弹窗中文版不再挤**：导入图片对话框从 400px 改到 500px，路径输入框和 Browse 按钮都有空间了。
- **Tagger modal split into 3 tabs**: the single "AI Auto Tagging" panel now has Local Tagger / Natural Language / Aesthetic Score tabs. The model dropdown is filtered per tab (Local: WD14 / Camie / PixAI / Custom; Natural Language: ToriiGate + VLM API; Aesthetic: dedicated panel). VLM mode banner and ToriiGate setup card live inside Natural Language. Aesthetic gets its own Score / Set up CTA.
  - **打标弹窗拆成 3 个 tab**：原来一锅烩的"AI 自动打标"现在分本地打标 / 自然语言 / 美学评分三个标签页。每个 tab 自动过滤模型下拉（本地：WD14 / Camie / PixAI / Custom；自然语言：ToriiGate + VLM API；美学：独立面板）。VLM banner、ToriiGate 安装卡片归到自然语言；美学评分独立有自己的 Score / 安装 CTA。
- **"Set up Aesthetic / ToriiGate" deep-links into Setup**: the Setup CTA in the new Tagger tabs closes the tagger modal, opens Model Manager, scrolls the matching card into view, and pulses a 2-second highlight so you know exactly where to click.
  - **「美学 / ToriiGate 安装」直接跳到 Setup**：Tagger 里的安装按钮会自动关掉打标弹窗、打开 Model Manager、滚到对应模型卡片、做 2 秒的高亮闪烁，让你不用自己找。
- **Model Manager card buttons readable in zh-CN at 1366×768**: prepare/repair buttons go from `btn-small` (32px tall) to default `btn` (40px tall, 132px wide minimum) so "立即准备" / "重新检查" never get clipped. Bulk download button gets the same treatment.
  - **Model Manager 卡片按钮中文版可读**：「立即准备」「重新检查」不再被截断 — 按钮从 32px 改到 40px 高、最少 132px 宽。批量下载按钮同样处理。
- **Export modal becomes the unified per-image preview/edit hub**: the batch-export modal's preview pane is now visible for **every** content mode (was template-only before), and gains two new output destinations alongside sidecar files: "📋 Copy combined to clipboard" and "⬇️ Download single file". Per-image edit applies to the combined paths too — your text overrides survive into the clipboard / download blob.
  - **导出弹窗变成统一的逐图预览/编辑中心**：原来只有 template 模式才显示的 preview 区，现在所有内容格式都显示。新增两个输出目的地：「📋 合并复制到剪贴板」「⬇️ 下载成单一文件」，跟原来的 sidecar 一起放在 segmented control 里。逐图编辑对合并路径同样生效，你的文字覆盖会原样写进剪贴板 / 下载文件。
- **Gallery auto-refreshes after tagging / VLM completion**: tagging done path dispatches a `taggingCompleted` event, VLM batch completion now dispatches `vlmBatchCompleted` and also calls `loadImages()` + `loadStats()` directly, so freshly tagged images surface their new tag chips and counts without you having to switch tabs.
  - **打标 / VLM 完成后图库自动刷新**：打标完成除了原本的 `loadImages` 还会派发 `taggingCompleted` 事件；VLM 批量完成会派发 `vlmBatchCompleted` 并直接调 `loadImages()` + `loadStats()`，刚打完的图标签和计数会自动出现在图库，不用切 tab 再切回来。

## [3.2.0] - 2026-05-16

### Added / 新增
- **Detect more generators**: Fooocus, sd-webui-reForge, Easy Diffusion, InvokeAI (v3 `invokeai_metadata`, v3 `invokeai_graph`, v2 `sd-metadata`, legacy `Dream`), SwarmUI / StableSwarmUI (`sui_image_params`), Draw Things (XMP `exif:UserComment`), Gemini / nano-banana (Software/Make/Description regex + C2PA byte-scan), and OpenAI gpt-image / ChatGPT / DALL-E (Software/Make/Description regex + C2PA byte-scan). All show their actual generator name in the gallery now instead of "Unknown" or generic "Others".
  - **识别更多 generator**：Fooocus、sd-webui-reForge、Easy Diffusion、InvokeAI（v3 `invokeai_metadata`、v3 `invokeai_graph`、v2 `sd-metadata`、旧版 `Dream`）、SwarmUI / StableSwarmUI（`sui_image_params`）、Draw Things（XMP `exif:UserComment`）、Gemini / nano-banana（Software/Make/Description 关键字 + C2PA 字节扫描）、OpenAI gpt-image / ChatGPT / DALL-E（Software/Make/Description 关键字 + C2PA 字节扫描）。这些图现在在图库里都会显示真实的生成器名字，不再统一归到"Unknown"或笼统的"Others"。
- **C2PA Content Credentials byte-scan**: when a Gemini or gpt-image image has its EXIF tags stripped by hosting platforms (Twitter / Discord / Pixiv re-encode), the parser now scans the first 512 KiB of the file for a C2PA / JUMBF manifest anchor and the provider's `claim_generator_info`. Anchor-required guard prevents false positives where an SD prompt happens to mention "openai-style" or "imagen-style".
  - **C2PA Content Credentials 字节扫描**：图片被平台重新转存导致 EXIF 被清掉时，本版本会去文件前 512 KiB 找 C2PA / JUMBF manifest 锚点和 `claim_generator_info`，从而仍然识别出 Gemini 和 gpt-image。需要锚点 + 厂商关键字同时命中才算数，避免提示词中提到 "openai" 类似词被误判。
- **Filter Criteria modal expanded**: the filter modal now lists all 14 generators (ComfyUI / NovelAI / WebUI / Forge / reForge / Fooocus / InvokeAI / SwarmUI / Easy Diffusion / Draw Things / Gemini / gpt-image / Unknown / Others) so users can isolate exactly the generator they want. The top-level gallery tab bar stays compact at 5 primary tabs + 1 "Others" bucket.
  - **筛选条件弹窗扩充**：筛选弹窗现在列出全部 14 个 generator，可以精确单选某一个。最上方的图库分类列保持紧凑，仍然是 5 个主分类 + 1 个 "其他" 合集。
- **"Others" tab bundles uncommon generators**: clicking the "Others" gallery tab queries the union of `others / fooocus / reforge / easy-diffusion / invokeai / swarmui / drawthings / gemini / gpt-image`, and the badge count sums them.
  - **"其他" 分类合并罕见 generator**：点击 "其他" 分类会一次性显示罕见 generator 全部，徽标数字也是合计。
- **"Download all recommended models" button in Feature Setup**: one click to fetch every recommended model in one go (default WD14 swinv2 / NudeNet / CLIP / Aesthetic / Artist ID / SAM 3). Confirmation dialog shows total disk space needed, per-model size, which models will be downloaded vs already ready, and which models are intentionally skipped (Wenaka Privacy YOLO and ToriiGate). Downloads run sequentially with progress; the dialog can be closed to leave the download running in the background.
  - **功能准备新增 "一键下载推荐模型"**：一次性下载所有推荐模型（默认 WD14 swinv2、NudeNet、CLIP、美学评分、画师识别、SAM 3）。确认窗会显示所需磁盘空间总量、每个模型体积、哪些会下载、哪些已就绪，以及为什么跳过 Wenaka Privacy YOLO 和 ToriiGate。多个模型按顺序下载，可关掉窗口让它在后台继续。
- **Closed-source AI provider notice in image-detail modal**: when the user opens a Gemini or gpt-image image, an inline note now explains that the source was identified via Content Credentials / EXIF metadata and that the in-pixel invisible watermark (SynthID for Gemini, OpenAI's pixel signal for gpt-image) is NOT yet checked by the app. Tracked as a TODO for a future opt-in detector.
  - **图片详情弹窗对闭源 AI 厂商图片增加提示**：打开 Gemini 或 gpt-image 图片时会显示一行提示，说明本图通过 Content Credentials / EXIF 元数据识别，App 暂时还没检测像素层的隐形水印（Gemini 的 SynthID、OpenAI 的内嵌信号）。已记入 TODO，未来作为可选功能加入。
- **Batch Tag Export: "Save next to each image" mode**: a new segmented control above "Output Folder" in the Batch Tag Export modal lets the user choose between writing every sidecar into one shared folder (legacy flat output) or writing each `.txt` / `.json` into the same directory as its source image. The new mode preserves subfolder structure for libraries spread across many directories and matches the convention of per-folder training tools that look for `foo.png` + `foo.txt` side by side. New API field `output_mode: "folder" | "beside_image"`. UI defaults to `beside_image`; the `output_folder` field is greyed out and skipped when that mode is selected. Rows whose source folder no longer exists are reported as per-row errors instead of crashing the whole export.
  - **批量打标导出新增 "存到每张图所在的资料夹" 模式**：批量打标导出弹窗里"输出文件夹"上方新增一组单选，可选"存到每张图所在的资料夹"或"统一存到一个资料夹"。新模式会把每个 `.txt` / `.json` 写到对应来源图的同一个资料夹，保留子资料夹结构，适合图库分散在多个子资料夹或依赖 `foo.png` + `foo.txt` 同位的训练工具。API 新增 `output_mode: "folder" | "beside_image"` 字段，UI 默认 `beside_image`，此时输出文件夹输入框会自动禁用。来源资料夹已不存在的图片会按行报错，不会让整批导出崩掉。

### Changed / 變更
- **Setup moved to the nav bar**: the global "Setup" button now lives in the top nav bar, reachable from any view (Reader, Censor, Sorting, Library Health) instead of being hidden in the gallery toolbar.
  - **Setup 移到主導航**：全域用的 "Setup" 按鈕現在在最上方導航列，從任何頁面都可以打開，不再藏在 Gallery 工具列裡。
- **Clear Gallery moved into the gallery toolbar**: the destructive "Clear Gallery" action moved out of the global nav bar and into the gallery toolbar, where its scope is visible.
  - **Clear Gallery 移到 Gallery 工具列**：破壞性的 "Clear Gallery" 按鈕從全域導航列移到 Gallery 自己的工具列，在 UI 上明確只影響當前 gallery。
- **Setup added to the mobile menu**: a "Setup" entry was added to the mobile navigation panel.
  - **手機選單新增 Setup 入口**：手機版選單也能直接打開 Setup。
- **Generator filter and tab list now expose "Others"**: gallery generator tabs, the filter modal, and gallery counts now include an "Others" category alongside ComfyUI / NovelAI / WebUI / Forge / Unknown.
  - **Generator 分類與篩選新增 "Others"**：分類列、篩選彈窗、計數都多了 "Others" 一欄。
- **Auto-Separate and Manual Sort default to copy (not move)**: the file-action mode for both batch sorting flows now defaults to "Copy and keep originals" out of the box. The Move radio is still available and the user's last choice is persisted to localStorage, so power users only flip once. This is the safer default for first-time users who might click Start before reading the radio labels and end up moving thousands of files into the wrong folders. Locked by `docs/AI_PRINCIPLES.md` Principle #11 + a regression test that asserts the radio markup and JS fallbacks resolve to copy.
  - **Auto-Separate 与 Manual Sort 默认改为复制而不是移动**：两套批量分类流程的文件操作默认值都改成"复制并保留原图"。移动选项仍在，用户上次的选择会保存到 localStorage，重度用户切换一次就行。新默认对第一次使用、还没看清单选按钮就点 Start 的用户更安全，避免一不小心把成千上万的文件搬到错的资料夹。`docs/AI_PRINCIPLES.md` Principle #11 加上回归测试一起锁住该默认值。
- **Default scan brings back the count-first pass**: scan progress now walks the folder once for a precise total, then runs the import + metadata pipeline with a real `current/total` denominator. The phase order is `counting → counted → importing` and every import heartbeat shows `processed=14800/48062` (instead of `processed=14800/?` like before). On a local SSD the count walk is ~1–2 seconds for 50 K files, which is dwarfed by the metadata-parse phase that follows. Callers that legitimately need to skip the count walk (e.g. multi-million-file network shares) can opt out with `precise_total=False`.
  - **默认扫描恢复"先点数再导入"两阶段**：扫描进度现在会先走一遍资料夹算出精确总数，再跑导入和 metadata 流程，进度条和心跳都会显示真正的 `current/total`。阶段顺序是 `counting → counted → importing`，每次心跳显示 `processed=14800/48062` 而不是原本的 `processed=14800/?`。本地 SSD 上 5 万张图的点数大约 1–2 秒，相比后面的 metadata 解析微不足道。确实需要跳过点数（例如几百万张图的网路硬碟）的调用方可以传 `precise_total=False` 选择 streaming 模式。

### Fixed / 修复
- **JPEG / WEBP / GIF files saved with `.png` extension now import (no more "Invalid PNG signature" errors)**: real-world libraries are full of JPEGs renamed to `.png` by Civitai, Discord, browsers, and content-management tools. Browsers and Windows Explorer render them fine because they sniff format from content magic bytes, but the parser was strict-trusting the extension and rejecting the file with `Invalid PNG signature` — flagging hundreds of perfectly valid images as "unreadable" and hiding them from the gallery. Fixed by falling through to Pillow's content-sniff path whenever the PNG fast-path's magic-byte check fails. Pillow detects format from content regardless of file extension, so a `.png`-extension JPEG is parsed as JPEG. Genuine PNG corruption (truncated chunks, bad IEND, etc.) still surfaces as a parse failure — the fallback only kicks in for the magic-bytes-don't-match-extension case.
  - **`.png` 后缀但实际是 JPEG / WEBP / GIF 的图片不再被报为不可读**：Civitai、Discord、浏览器等工具经常会在上传/下载时把 JPEG 改成 `.png` 后缀。浏览器和 Windows 资源管理器能渲染是因为它们看魔术字节嗅探格式，但 metadata 解析器以前完全相信扩展名，看到首 8 字节不是 PNG 签名就直接报 `Invalid PNG signature`，导致成百上千张正常的图被当成"损坏"藏起来。修复方法：PNG 快速路径校验失败时回退到 Pillow 的内容嗅探路径。Pillow 完全按内容识别格式，所以 `.png` 后缀的 JPEG 会被当 JPEG 正常解析。真正的 PNG 损坏（chunk 截断、IEND 错误等）仍会按错误处理——回退只针对"魔术字节与扩展名不一致"这一种情况。
- **Dataset Audit panel layout fixed**: the score ring inside the Dataset Audit (Setup → Dataset Audit) was using `display: grid; place-items: center` plus per-child `margin-top` overrides (8 px on the score, 52 px on the label) that pushed the "Health Score" label completely outside the inner dark donut hole and onto the conic-gradient ring, where it was nearly unreadable. The "Read-only library audit" eyebrow heading was also styled as a tiny uppercase label that visually disappeared under the subtitle, and the hero baseline-aligned the subtitle with the Refresh button — pushing the eyebrow off the top of the modal-content scroll area on common viewport heights. All three issues fixed: the score ring now uses a proper flex column with sane font sizes; the eyebrow is rendered as the actual section heading (1.05 rem bold); and opening the audit details now scrolls the section's top into view so the eyebrow is always visible.
  - **资料集体检面板排版修复**：体检里的分数环之前用 `display: grid; place-items: center` 加上每个子元素的 `margin-top`（数字 8px、标签 52px）硬撑，结果 "健康评分" 标签整个被挤出内圈深色甜甜圈，落在外层渐变圈上几乎看不清。"只读图库体检" 这行 eyebrow 还被做成很小的大写标签，视觉上完全藏在副标题下面；hero 区还用 baseline 对齐让副标题和"重新体检"按钮排在同一基准线，把 eyebrow 推到 modal 滚动区上方裁掉。三处都修了：分数环改用正常的 flex 纵向排列加合理字号；eyebrow 改成真正的小标题（1.05rem 粗体）；展开体检时会自动把整段顶端滚到可见位置，eyebrow 一定看得到。
- **Fresh Windows portable: SAM3 now survives a flaky CUDA-index download (release blocker)**: when `repair_torch_runtime.py` ran on first SAM3 prepare and the cu126 wheel download hit an `IncompleteRead` or DNS hiccup mid-transfer, the fallback loop tried cu124 / cu121 in order. Each fallback used `--extra-index-url https://pypi.org/simple` plus a plain `torch==X.Y.Z` requirement, which let pip silently satisfy the requirement from PyPI's CPU torch wheel instead of the cu-specific index. The install reported success, but `torch.version.cuda` was empty and SAM3 refused to load with the confusing message "this app's Python has CPU-only PyTorch; SAM3 needs a CUDA-enabled Torch build". The user had no clear path forward. Fixed by (1) pinning the explicit `+cuXXX` local-version label on the torch and torchvision requirements so PyPI's no-suffix CPU wheel cannot match, (2) dropping `--extra-index-url` from the cu-index pip call so the cu-specific index is the only source, and (3) installing the `numpy<2.0` constraint in a separate up-front pip call from PyPI (numpy never lives on download.pytorch.org). A new regression test `test_cuda_install_pins_local_version_label_so_pypi_cannot_satisfy` locks the behaviour.
  - **Windows 便携版首次启动 SAM3 在 CUDA 索引网路抖动下也能装好（发布阻断 bug）**：第一次准备 SAM3 时如果 cu126 wheel 下载中途断流（`IncompleteRead`、DNS 解析失败等），原本的回退会接着试 cu124 / cu121，每次重试都带 `--extra-index-url https://pypi.org/simple` + 普通 `torch==X.Y.Z`，结果 pip 在 cu 索引断流时悄悄从 PyPI 拉到 CPU 版 torch wheel，安装报成功但 `torch.version.cuda` 是空的，SAM3 报 "Python 是 CPU-only PyTorch，SAM3 需要 CUDA 版" 让人摸不着头脑。修复方法：(1) 给 torch / torchvision 加上明确的 `+cuXXX` local version 标签，PyPI 上没后缀的 CPU wheel 没法对上；(2) 从 cu 索引那次 pip 调用中移除 `--extra-index-url`，让 cu 专属索引成为唯一来源；(3) 把 `numpy<2.0` 这个约束改成单独一次 pip 调用从 PyPI 装，因为 numpy 从来不放在 download.pytorch.org。新增回归测试 `test_cuda_install_pins_local_version_label_so_pypi_cannot_satisfy` 锁住新行为。
- **Fresh Windows portable: ONNX Runtime now installs on first launch (release blocker)**: when `repair_onnxruntime.py` ran on a freshly-extracted Windows portable with NO onnxruntime variant installed at all (`onnxruntime`, `onnxruntime-gpu`, `onnxruntime-directml` all missing), it would report "No repair needed" and exit cleanly. The next WD14 / NudeNet / CLIP model download then failed with `No module named 'onnxruntime'`. The repair function only handled the cases where at least one variant was already present (CPU+GPU coexisting, CPU-only-with-GPU-detected, both GPU runtimes installed, mismatched vendor). The empty-state case fell through every branch. Fixed by adding Step 0: when nothing is installed, install the runtime that matches the detected GPU vendor (NVIDIA → `onnxruntime-gpu[cuda,cudnn]`, AMD/Intel → `onnxruntime-directml`, no vendor detected → CPU `onnxruntime`). Two new regression tests (`test_repair_installs_runtime_when_nothing_present_with_nvidia_vendor` and `test_repair_falls_back_to_cpu_runtime_when_nothing_present_and_no_gpu_detected`) lock the behaviour.
  - **Windows 便携版首次启动 ONNX Runtime 自动安装（发布阻断 bug）**：刚解压的 Windows 便携版本来一个 onnxruntime 变体都没装的情况下，`repair_onnxruntime.py` 会报 "No repair needed" 然后正常退出。之后第一次下载 WD14 / NudeNet / CLIP 模型就会因为 `No module named 'onnxruntime'` 失败。修复方法是给 repair 函数加 Step 0：当所有 onnxruntime 变体都不存在时，按检测到的 GPU 厂商安装匹配版本（NVIDIA→`onnxruntime-gpu[cuda,cudnn]`、AMD/Intel→`onnxruntime-directml`、未检测到 GPU→CPU 版 `onnxruntime`）。新增两个回归测试锁住新行为。
- **Real Fooocus output now classifies as Fooocus, not NAI**: upstream lllyasviel/Fooocus writes its `Comment` PNG chunk with lowercase `prompt` + `negative_prompt` keys plus Fooocus-specific siblings (`base_model`, `performance`, `metadata_scheme`). Earlier drafts of the parser saw the lowercase `prompt` key and classified the image as NovelAI. The parser now disambiguates Fooocus vs NovelAI by looking for sibling keys (or `negative_prompt` without `uc`) before claiming the block.
  - **真实 Fooocus 输出现在归到 Fooocus，不再归到 NAI**：上游 lllyasviel/Fooocus 写到 PNG `Comment` 里的 JSON 用的是小写 `prompt` + `negative_prompt`，加上 Fooocus 自己的 sibling key（`base_model`、`performance`、`metadata_scheme`）。之前看到小写 `prompt` 就直接当 NovelAI 处理。现在 parser 会先用 sibling key（或 `negative_prompt` 但没有 `uc`）来区分 Fooocus 与 NovelAI。
- **Chinese UI: navbar emoji icons no longer clipped**: a global text-truncation rule (`overflow:hidden; text-overflow:ellipsis`) on `.nav-actions .btn span:last-child` was clipping the emoji glyph (the ONLY child) on icon-only buttons (🧰 Setup / 📚 Library / 🌐 Language / ⬆️ Update / ❓ Help) by ~24px. Fixed by scoping the rule to `:not(.btn-icon-only)` and adding an explicit unclipped rule for `.btn-icon-only span[aria-hidden]`. Visual symptom was most obvious in Chinese because longer translations made the navbar layout tighter.
  - **中文 UI：导航栏 emoji 图标不再被裁切**：导航栏图标按钮（🧰 Setup / 📚 Library / 🌐 Language / ⬆️ Update / ❓ Help）的 emoji 图标因为一条全局的文本省略规则（`overflow:hidden; text-overflow:ellipsis`）被裁掉了大约 24px。修复方式是把规则限制在非 icon-only 按钮上。中文模式下因为标签更长、布局更紧，问题最明显。
- **Filter modal generator labels alignment**: the order-sensitive `_setCheckboxTexts` translation list was binding 6 keys to the 14 new checkboxes, causing reForge / Fooocus to render as "其他" / "未知" in zh-CN. List updated to match the new HTML order.
  - **筛选弹窗 generator 标签错位**：旧的 `_setCheckboxTexts` 只传了 6 个翻译 key 给新加的 14 个 checkbox，导致 reForge / Fooocus 在简中模式下显示成 "其他" / "未知"。已按新 HTML 顺序补齐 14 个 key。
- **Metadata parser no longer silently buckets recognizable images into "Unknown"**: PNG / JPEG / WebP rows that carry a real prompt, negative prompt, checkpoint, or LoRA list but whose generator string is not ComfyUI / NovelAI / WebUI / Forge are now classified as `others` instead of `unknown`.
  - **Metadata parser 不再把有 metadata 的圖默默歸成 "Unknown"**：有真實 prompt / negative prompt / checkpoint / LoRA、但 generator 字串不在已知列表內的 PNG / JPEG / WebP 現在歸到 `others`，不再混進 `unknown`。

### Notes / 備註
- No database migration. Drop-in replacement for v3.1.6.
  - 不需要資料庫遷移，可以直接替換 v3.1.6。
- Existing rows whose generator is `unknown` keep that value; new scans, reparses, and Reader / clipboard uploads use the new generator labels when applicable.
  - 升級後 `unknown` 的舊 row 保留原值；新掃描、reparse、Reader / 剪貼簿上傳的新圖在合適情況下會用上新的 generator 標籤。
- Detection uses metadata only — see the in-app notice on Gemini / gpt-image images for the limitation. Tracked as `Debt-23` in `docs/TECHNICAL_DEBT_NOTES.md` (pixel-level SynthID detection deferred to opt-in feature).
  - 识别只读元数据 —— Gemini / gpt-image 图片的弹窗里有提示。已记录在 `docs/TECHNICAL_DEBT_NOTES.md` 的 `Debt-23`（像素级 SynthID 检测延后做为可选功能）。

## [3.1.6] - 2026-05-13

### Fixed / 修复
- **Tagger threshold race condition**: concurrent tagging requests no longer corrupt each other's confidence thresholds.
  - **标签器阈值竞态条件**：并发标签请求不再互相覆盖置信度阈值。
- **Graceful shutdown on update**: update apply now uses SIGINT instead of os._exit(0), allowing proper cleanup of DB connections and pending writes.
  - **更新时优雅关闭**：更新应用现在使用 SIGINT 而非 os._exit(0)，确保数据库连接和待写入数据正确清理。
- **Similarity progress race**: embedding progress dict is now updated under its lock, preventing partial reads.
  - **相似度进度竞态**：嵌入进度字典现在在锁内更新，防止读取到不完整状态。
- **Censor resize listener leak**: resize handler is now debounced (150ms) and removed when leaving censor view.
  - **打码编辑器 resize 泄漏**：resize 处理器现在有 150ms 防抖，离开打码视图时移除。
- **JPEG prompt metadata scanning**: `.jpg` / `.jpeg` images are now parsed for SD metadata in EXIF `UserComment` and APP1 XMP, including UTF-16 `UNICODE` UserComment blocks. Existing JPEG rows parsed by older parser versions will reparse on normal folder scan.
  - **JPEG 提示词元数据扫描**：现在会从 `.jpg` / `.jpeg` 的 EXIF `UserComment` 和 APP1 XMP 里解析 SD 元数据，包括 UTF-16 `UNICODE` UserComment。旧解析版本扫过的 JPEG 行会在普通文件夹扫描时自动重扫。
- **Broader bounded metadata harvesting**: TIFF/TIF images, GIF comments, WebP XMP chunks, and small same-name `.txt` / `.json` / `.xmp` sidecars can now feed Gallery metadata when embedded fields are missing. Sidecars are size-capped and fallback-only to avoid slowing normal scans.
  - **更广但有边界的元数据收集**：TIFF/TIF、GIF comment、WebP XMP chunk，以及小体积同名 `.txt` / `.json` / `.xmp` sidecar 现在可以在图片内嵌字段缺失时补充 Gallery 元数据。Sidecar 有大小限制且只作兜底，避免拖慢普通扫描。

### Improved / 优化
- **Pagination performance**: COUNT query automatically skipped on cursor-paginated pages (saves 200-500ms per page on large libraries).
  - **翻页性能**：游标翻页时自动跳过 COUNT 查询（大图库每页节省 200-500ms）。
- **Query efficiency**: removed unnecessary SELECT DISTINCT on non-JOIN queries (10-30% faster for simple filters).
  - **查询效率**：非 JOIN 查询移除不必要的 SELECT DISTINCT（简单过滤快 10-30%）。
- **Generator facet cache**: get_all_generators() now cached with 60s TTL (saves 10-50ms per gallery load).
  - **生成器缓存**：get_all_generators() 现在有 60 秒 TTL 缓存（每次加载图库节省 10-50ms）。
- **Prompt Lab memory**: image picker no longer loads entire library into memory; uses server-side search with 200-image initial page.
  - **Prompt Lab 内存**：图片选择器不再将整个图库加载到内存；使用服务端搜索，初始只加载 200 张。
- **WD14 GPU runtime repair**: Windows portable startup and WD14 Prepare / Recheck now run ONNX Runtime repair before tagger code loads. Supported NVIDIA hardware is repaired to `onnxruntime-gpu==1.21.0` plus CUDA/cuDNN runtime DLLs; AMD/Intel hardware is repaired to `onnxruntime-directml==1.21.0`; CPU-only or undetected hardware keeps the small CPU runtime. Repair also downgrades incompatible newer installs, force-reinstalls the pinned runtime when the `onnxruntime` import surface is corrupt, and uses no-deps/pip-safe locked constraints so first launch does not reinstall GPU runtime twice or drift shared pins such as NumPy.
  - **WD14 GPU 运行库修复**：Windows 便携版启动、WD14 Prepare / Recheck 现在都会在 tagger 代码加载前运行 ONNX Runtime 修复。检测到 NVIDIA 时修复到 `onnxruntime-gpu==1.21.0` 并补 CUDA/cuDNN runtime DLL；检测到 AMD/Intel 时修复到 `onnxruntime-directml==1.21.0`；纯 CPU 或未可靠检测到 GPU 时保留轻量 CPU runtime。修复也会把不兼容的新版本降回发布 pin，在 `onnxruntime` 导入表面损坏时强制重装发布 pin，并用 no-deps/锁定 constraints 避免首启重复重装 GPU runtime 或把 NumPy 等共享依赖漂到未锁版本。

## [3.1.5] - 2026-05-12

### Changed / 改进
- **Prompt Lab fixed tags**: Generate / Randomize now supports fixed beginning and ending tags with automatic duplicate removal. Presets save and restore these fields, and the UI explains the behavior in beginner-readable copy.
  - **Prompt Lab 固定标签**：生成 / 随机生成现在支持固定加开头和固定加结尾，并自动去重。Preset 会保存 / 恢复这些字段，界面文案也改成新手能直接看懂。
- **Export scope clarity**: Combined Export and same-name `.txt` export now explicitly say they only affect the currently selected Gallery images, so users can add training caption prefixes/blacklists to just one selected batch.
  - **导出范围更清楚**：Combined Export 和同名 `.txt` 导出现在明确说明只影响当前在图库里选中的图片，方便只给某一批训练 caption 加前缀 / 黑名单。
- **Censor model clarity**: Auto Censor now shows the actual local YOLO file being used near the detector selector, instead of hiding it inside the advanced picker only.
  - **打码模型更清楚**：自动打码现在会在检测器选择器附近显示实际使用的本地 YOLO 文件，不再只藏在高级选择器里。
- **Optional AI dependency predictability**: Feature Setup optional Python installs now prefer the exact versions already pinned in `backend/requirements.txt` when preparing feature groups, reducing surprise resolver drift.
  - **可选 AI 依赖更可预测**：Feature Setup 准备可选功能时，会优先使用 `backend/requirements.txt` 里已经锁定的精确版本，减少 pip 临场解析漂移。
- **Security lock refresh**: `urllib3` is pinned to `2.7.0` in the full/dev runtime locks to clear the current pip-audit CVE report.
  - **安全锁定更新**：full/dev runtime lock 中的 `urllib3` 已升到 `2.7.0`，解决当前 pip-audit 报告的 CVE。

### Fixed / 修复
- **Portable launcher runtime check**: The portable launcher dependency probe now checks only startup-critical packages (`fastapi`, `PIL`, `numpy`, `onnxruntime`). Optional heavy AI packages no longer force repeated `pip install` on every startup.
  - **Portable 启动依赖检查**：便携版启动器现在只检查启动必需包（`fastapi`、`PIL`、`numpy`、`onnxruntime`）。可选重型 AI 包不会再导致每次启动都重跑 `pip install`。


## [3.1.4] - 2026-05-10

### Fixed / 修复
- **Artist ID / Kaloscope availability**: `triton` is no longer a hard blocker for Artist ID on Windows. The health check now treats triton as informational instead of blocking `available=True`. Feature Setup Prepare now also installs `triton-windows` (Windows) or `triton` (Linux) as a best-effort soft dependency — if the install fails, core Artist ID still works with the PyTorch fallback.
  - **画师识别 / Kaloscope 可用性**：`triton` 不再是 Windows 上画师识别的硬性阻断条件。健康检查现在把 triton 视为信息提示而非阻止 `available=True`。Feature Setup 的 Prepare 现在也会尝试安装 `triton-windows`（Windows）或 `triton`（Linux）作为 best-effort 软依赖——如果安装失败，核心画师识别仍然可以通过 PyTorch fallback 正常工作。
- **Prompt filter duplicate bug**: Clicking a prompt suggestion in the filter modal now normalizes underscores to spaces before checking for duplicates, matching the existing Enter-key handler behavior. Previously, clicking a suggestion could add a duplicate prompt if the existing filter used spaces while the suggestion used underscores (or vice versa).
  - **Prompt 过滤器重复 bug**：在过滤器弹窗中点击 prompt 建议现在会在检查重复前将下划线正规化为空格，与已有的回车键处理逻辑一致。之前点击建议可能会添加重复的 prompt（如果现有过滤器用空格而建议用下划线，或反之）。

## [3.1.3] - 2026-05-09

### Fixed / 修复
- Large folder scans are now safer for 80k+ metadata-heavy libraries: metadata parsing uses bounded process workers by default, timed-out metadata reads are skipped instead of freezing the whole scan, expected corrupt-image metadata failures stay out of normal console noise, and scan progress exposes stalled-state diagnostics with support log access. This does not mean every filesystem wait can be killed; network/cloud drives, antivirus, SQLite/disk I/O, or OS directory enumeration can still be slow, but the UI now tells users what is happening and how to collect support information.
  - 大图库扫描现在对 8 万+ 带 metadata 的图片更安全：metadata 解析默认走有上限的进程 worker，单图 metadata 超时会跳过而不是拖死整个扫描，常见坏图 metadata 错误不会刷爆普通终端，并且扫描进度会暴露卡住诊断和支持日志入口。这不代表所有文件系统等待都能被强杀；网络盘/云盘、杀毒软件、SQLite/磁盘 I/O、系统枚举目录仍可能很慢，但 UI 会明确告诉用户当前情况和如何收集支持信息。
- Metadata storage compaction now covers old and new write paths: scans, reparses, copied images, direct DB upserts, and favorites/collection snapshots are normalized to compact `_compact` / `_parsed` payloads instead of re-copying legacy raw EXIF/XMP/ComfyUI workflow blobs back into `images.db`. Migration 009 also catches raw-only metadata rows that an already-run v8 migration could have missed.
  - metadata 存储瘦身现在覆盖旧库和新写入口：扫描、重新解析、复制图片、直接 DB upsert、收藏/collection 快照都会统一写入 compact 的 `_compact` / `_parsed`，不会把旧 raw EXIF/XMP/ComfyUI workflow 大块数据重新塞回 `images.db`；新增迁移 009 会补压已经跑过 v8 但漏掉的 raw-only metadata 行。
- Feature Setup now keeps first launch lightweight: the default launcher installs only core dependencies, heavy AI Python packages move behind Prepare, system Python is protected from accidental optional installs, and old full-AI installs can schedule a next-start lightweight runtime rebuild without deleting `data/`, `images.db`, settings, caches, or downloaded models.
  - Feature Setup 现在让首次启动保持轻量：默认启动器只装核心依赖，重型 AI Python 包改为按需 Prepare，system Python 默认不会被误装 optional 包；旧的 full-AI 安装可以安排下次启动重建轻量运行环境，而且不会删除 `data/`、`images.db`、设置、缓存或已下载模型。
- Thumbnail cache now has a default 500 MB cap, can be disabled with a `0` limit, and explains the disk-vs-CPU/IO trade-off in Disk Usage.
  - 缩略图缓存现在默认上限为 500 MB，可用 `0` 关闭持久缓存，并在 Disk Usage 里明确说明省空间与重建缩略图 CPU/IO 开销之间的取舍。
- Feature Setup / Disk Usage no longer advertises externally redirected temp/cache/thumbnail paths as one-click safe cleanup targets. The cleanup list is app-owned `data/` cache only, symlinked safe-cache roots are refused, symlink targets are not counted as reclaimable bytes, and external package/model/runtime cache locations remain visible as informational/preserved rows.
  - Feature Setup / 磁盘占用不再把被环境变量重定向到外部的临时/缓存/缩略图路径显示成“一键安全清理”。可清理列表只包含 app 自己 `data/` 下的缓存，symlink 形式的可清理根目录会被拒绝，symlink 指向的外部目标不会被算成可回收空间，外部包/模型/运行时缓存会作为信息展示/保留。
- Feature Setup / Disk Usage asks for a second confirmation before cleaning any selected cache whose size could not be fully scanned, and the manual setup guide keeps keyboard focus inside the dialog.
  - Feature Setup / 磁盘占用现在会在清理大小未完整扫描的缓存前二次确认，并且手动设置引导弹窗会把键盘焦点留在弹窗内。
- ToriiGate optional setup now requires a Transformers version new enough for the Qwen3.5 classes it imports, and Linux full-AI launcher installs no longer repeat because of a temporary filtered requirements hash.
  - ToriiGate optional setup 现在要求足够新的 Transformers 版本来匹配实际导入的 Qwen3.5 类；Linux full-AI 启动器也不会再因为临时过滤后的 requirements hash 反复安装。
- Thumbnail cache writes are now atomic (write-then-rename), preventing corrupt partial thumbnails when concurrent requests or crashes overlap.
  - 缩略图缓存写入现在是原子操作（先写临时文件再 rename），避免并发请求或崩溃导致半写损坏的缩略图。
- Stale `.tmp` files left in the thumbnail cache by interrupted writes are now cleaned up automatically during periodic cache maintenance.
  - 被中断写入遗留在缩略图缓存里的 `.tmp` 文件现在会在定期缓存维护时自动清理。
- Artist ID optional dependency group now declares the same Transformers version floor as SAM3 and ToriiGate, preventing version drift across feature groups.
  - Artist ID 的 optional dependency group 现在和 SAM3、ToriiGate 声明相同的 Transformers 最低版本，防止 feature group 之间版本漂移。
- File-rename collision loops in sidecar export and image move/copy operations now have a safety cap, preventing theoretical infinite loops when a destination folder contains an extreme number of identically-named files.
  - sidecar 导出和图片 move/copy 的文件名冲突重试循环现在有安全上限，防止目标目录中存在极端数量同名文件时的理论死循环。

### Release Notes / 发布注意
- Existing users who still see large Python runtime usage should open **Feature Setup → Disk Usage → Python runtime environment → Rebuild lightweight runtime on next start**, then close and restart the app.
  - 旧用户如果 Python runtime 占用仍然很大，请进入 **Feature Setup → Disk Usage → Python 运行环境 → 下次启动重建轻量运行环境**，然后关闭并重启 app。
- The first launch after upgrading an old metadata-heavy `images.db` may spend time compacting metadata and running `VACUUM`; very large databases need temporary free disk space while SQLite rewrites the file.
  - 旧的大 metadata `images.db` 升级后首次启动可能会花时间压缩 metadata 并执行 `VACUUM`；超大数据库在 SQLite 重写文件时需要临时空闲磁盘空间。
- Lower thumbnail cache limits save disk, but large-gallery scrolling may regenerate thumbnails more often and use more CPU / disk I/O.
  - 缩略图缓存上限调低会省磁盘，但大图库滚动时可能更频繁重建缩略图，占用更多 CPU / 磁盘 IO。

### Validation / 验证
- Added regression coverage for scan diagnostics contracts, metadata compaction write paths and migrations, Disk Usage cleanup safety, runtime rebuild, optional dependency install guards, and release packaging launcher behavior.
  - 新增回归覆盖扫描诊断契约、metadata compact 写入口和迁移、Disk Usage 清理安全、runtime rebuild、optional dependency 安装保护，以及发布包启动器行为。

## [3.1.2] - 2026-05-08

### Added / 新增
- Added `update.bat` as an external rescue updater so users can check, download, verify, and apply updates even when the web UI cannot open.
  - 新增 `update.bat` 外部救援更新入口：即使网页进不去，也能检查、下载、校验并应用更新。
- Added `fix.bat` as a rare diagnostics/repair tool for runtime packages, port diagnostics, and startup readiness snapshots. It does not start the app and is not the normal port fallback path.
  - 新增 `fix.bat` 作为少数情况下使用的诊断/修复工具，用于 runtime 包修复、端口诊断和启动就绪快照；它不会启动 app，也不是普通端口兜底入口。

### Fixed / 修复
- Facet search now searches the full indexed library before applying display limits, so typing partial terms like `blue` can find lower-frequency tags such as `nagisa_(blue_archive)` instead of only searching the first preloaded slice.
  - Facet 搜索现在会先查完整索引库，再应用显示数量限制；输入 `blue` 这类局部词时，可以找到低频标签（例如 `nagisa_(blue_archive)`），不再只搜前端预载的前几百/一千项。
- Manual Sort now starts from a JSON request body instead of packing large tag/checkpoint/LoRA/prompt scopes into the URL, while keeping the legacy query-string API compatible. Large filter scopes no longer fail because of arbitrary query-length limits.
  - Manual Sort 现在通过 JSON 请求体启动，不再把大量 tag / checkpoint / LoRA / prompt 筛选条件塞进 URL，同时保留旧 query-string API 兼容；大型筛选范围不会再因为随意的查询字符串长度限制失败。
- Custom ONNX tagging now treats explicit local model and metadata paths as hard user contracts: missing files fail loudly, profile-specific metadata is validated, and user-supplied ONNX files are never deleted or replaced by the built-in model repair/download path.
  - Custom ONNX 标注现在把用户显式填写的本地模型和 metadata 路径当成硬契约：文件不存在会明确失败，metadata 会按 profile 校验，并且绝不会删除或替换用户提供的本地 ONNX。
- Custom Local Model now supports explicit WD14-compatible, PixAI, and Camie ONNX profiles while rejecting ToriiGate as a fake Custom ONNX path because ToriiGate uses the separate VLM/PyTorch backend.
  - Custom Local Model 现在支持明确选择 WD14-compatible、PixAI、Camie ONNX profile；ToriiGate 会被拒绝伪装成 Custom ONNX，因为它走的是独立 VLM/PyTorch 后端。
- Windows launchers now preflight the localhost port before opening the browser. If the default `8487` is refused by a Windows reserved/excluded TCP range, the launcher automatically uses the next safe localhost port and starts the backend on that same port; explicit `SD_IMAGE_SORTER_PORT` values still fail loudly instead of being silently changed.
  - Windows 启动器现在会先检查 localhost 端口再打开浏览器。如果默认 `8487` 被 Windows 保留/排除端口段拒绝，会自动改用下一个安全的本机端口，并让后端绑定同一个端口；用户显式设置的 `SD_IMAGE_SORTER_PORT` 仍然会明确报错，不会偷偷改掉。
- The selected launcher port is now written back into the backend environment before startup so runtime diagnostics and browser URL agree with the actual bind port.
  - 启动器选出的端口现在会写回后端环境，确保运行时诊断、浏览器 URL 和实际绑定端口一致。
- Artist identification single-image requests now run model loading/inference off the FastAPI event loop, so a slow Kaloscope load no longer freezes unrelated UI/API requests.
  - 画师识别的单图请求现在会在线程池中执行模型加载/推理；Kaloscope 加载很慢时，不再冻结其它 UI/API 请求。
- Tagging cancel issued before the worker process is spawned is no longer silently swallowed: `cancel_tagging` now finalizes the `cancelled` state and invalidates the pending run id so the queued background task aborts when it finally executes, instead of clobbering progress back to `running` and starting an unkillable batch.
  - 标记任务在 worker 子进程起来之前就被取消时，不再被静默吃掉：`cancel_tagging` 现在会在锁内直接落地「已取消」状态并废弃排队中的 run id，让 FastAPI 后台任务真正执行时主动放弃，而不是把进度回写成 `running` 并启动一个无法取消的批次。
- The rescue updater (`update.bat` / `backend/update_cli.py`) now probes the configured localhost port and refuses to apply an update while another SD Image Sorter instance is still running. Without this guard, the in-process apply + relaunch would race the existing window for the same port and leave the user with two instances on different ports. `--force` overrides the guard when the existing window is hung.
  - 救援更新器（`update.bat` / `backend/update_cli.py`）现在会先探测配置的本机端口，如果还有 SD Image Sorter 实例在运行就拒绝直接覆盖；不加这层守护，就会出现 in-process apply + relaunch 和旧窗口抢同一个端口、最终两个实例占两个端口的情况。`--force` 可在旧窗口卡死时强制覆盖。
- PixAI tagger now applies sigmoid to ONNX logits before thresholding, matching the v3.1.1 fix that landed for Camie. Without this, runtime logs showed ~940 of ~9000 scores per image discarded as out-of-range and the threshold compared against meaningless confidence values; the v3.1.1 fix accidentally only patched Camie's config.
  - PixAI tagger 现在会在比对阈值前对 ONNX logits 套用 sigmoid，对齐 v3.1.1 给 Camie 的修复。之前 v3.1.1 漏改 PixAI 的 config，导致每张图运行日志会丢掉 ~940/9000 分数为越界、并用毫无意义的 confidence 跟阈值比较。

### Validation / 验证
- Added regression coverage for Custom ONNX profile selection, explicit path failures, metadata validation, user-file safety, artist request threadpool dispatch, and deterministic E2E tag/artist persistence without live WD14 or Kaloscope loads.
  - 新增 Custom ONNX profile 选择、显式路径失败、metadata 校验、用户文件安全、画师识别线程池派发，以及不依赖在线 WD14 或 Kaloscope 加载的确定性 E2E 标签/画师持久化覆盖。
- Added launcher port-selection, rescue updater, external PID-free update application, and release packaging regression coverage so portable builds keep `run` self-healing plus `fix.bat` / `update.bat`.
  - 新增启动端口选择、救援更新器、外部无 PID 更新应用和发布打包回归测试，确保 portable 包保留 `run` 自愈以及 `fix.bat` / `update.bat`。
- Added regression coverage for the tagging cancel-vs-spawn race: cancellations issued before the worker process spawns finalize cleanly and invalidate the pending background task instead of being clobbered back into a running state.
  - 新增标记取消与 worker 启动竞态的回归测试：worker 子进程起来之前按下取消能落地「已取消」并废弃排队中的后台任务，不会被回写成 running。
- Added regression coverage for the rescue updater's running-instance guard, covering the abort path with a clear error message, the `--force` bypass for hung windows, and the read-only `--check-only` exemption.
  - 新增救援更新器「实例运行中」守护的回归测试，覆盖中止路径并提示明确错误信息、`--force` 在旧窗口卡死时的绕过，以及只读 `--check-only` 不受守护影响。
- Added a contract regression test asserting both PixAI and Camie declare `output_activation=sigmoid`, so future v3.x changes cannot silently drop the activation again.
  - 新增 PixAI / Camie 的 `output_activation=sigmoid` 契约回归测试，未来 v3.x 修改不会再悄悄漏掉激活函数。

## [3.1.1] - 2026-05-08

### Fixed / 修复
- Fixed Custom ONNX tagger layout detection so WD14-compatible NCHW models (`[B,3,H,W]`) no longer crash with width/channel shape errors.
  - 修复 Custom ONNX tagger 的输入布局判断，WD14 兼容的 NCHW 模型（`[B,3,H,W]`）不会再因为宽度/通道维度反了而崩。
- Fixed Camie tagger score handling by applying sigmoid to logits before threshold filtering.
  - 修复 Camie tagger 分数语义：先把 logits 过 sigmoid，再按阈值过滤。
- Hardened tag filtering so NaN/Inf/out-of-range model scores are rejected instead of becoming random-looking tags.
  - 加固标签过滤：NaN / Inf / 越界分数直接丢弃，不再变成看起来随机的标签。
- Fixed PixAI fallback rating/category handling so it only uses tags that already passed the configured thresholds.
  - 修复 PixAI fallback rating / 分类逻辑，只使用已经通过阈值的标签。
- Clarified Custom model UX/docs: Custom is for WD14-compatible ONNX only; Camie, PixAI, and ToriiGate must use their built-in entries.
  - 明确 Custom 模型的边界：Custom 只支持 WD14 兼容 ONNX；Camie、PixAI、ToriiGate 必须走内建模型选项。

### Security / 安全
- Updated `python-multipart` to `0.0.27` in backend runtime/dev lockfiles.
  - 后端 runtime/dev lockfile 将 `python-multipart` 升到 `0.0.27`。

### Validation / 验证
- Added regression coverage for strict thresholds, invalid-score rejection, Camie sigmoid confidence, Custom NCHW ONNX input layout, PixAI thresholded fallback, and ToriiGate long-caption output handling.
  - 新增回归测试覆盖严格阈值、非法分数拒绝、Camie sigmoid 置信度、Custom NCHW ONNX 输入布局、PixAI 阈值 fallback，以及 ToriiGate 长 caption 输出处理。

## [3.1.0] - 2026-05-04

### About This Release / 关于这一版
v3.1.0 was driven by real user feedback and a focused tech-debt pass. Almost every fix below either resolves a concrete issue reported by users running the portable build on real hardware, or pays down accumulated complexity that was making the app harder to use and harder to ship safely. **A huge thank you to everyone who shared logs, screenshots, and step-by-step reproductions — this release exists because of you.**

v3.1.0 完全由真实用户反馈和一轮聚焦的技术债务清理推动。下面几乎每一项修复，要么是来自用户在真机上跑 portable 包时报告的具体问题，要么是在偿还过去积累下来的复杂度——那些让 app 越来越难用、越来越难安全发版的东西。**衷心感谢每一位分享日志、截图、复现步骤的用户——这一版完全是因为你们才存在的。**

### Added / 新增
- Reader is no longer just for viewing. Users can now edit prompt, negative prompt, seed, sampler, steps, CFG, size, model, and LoRA fields, then save the result as a new image directly from the app.
  - Reader 不再只是看图。现在可以直接在 app 里编辑 prompt、负面 prompt、seed、采样器、步数、CFG、尺寸、模型、LoRA 等字段，改完直接另存成新图。
- Reader save now lets users choose the output format (`png` / `webp` / `jpg`) and save location more directly, including images that were uploaded through the browser.
  - Reader 保存时可以选输出格式（`png` / `webp` / `jpg`）和保存位置，浏览器上传进来的图也能存。
- Folder scan now becomes usable earlier: the library can appear first, while the remaining images and metadata continue loading in the background. (commit `d818029`, `5f38955`)
  - 资料夹扫描更早可用：图库会先显示出来，剩下的图片和 metadata 继续在后台加载，不用傻等。
- **Reconnect-missing flow** for libraries whose images were moved or renamed. The app can now match missing rows against new locations and re-link them without re-importing. (commit `d818029`)
  - **重新连接遗失文件流程**：图库里的图被移动或改名后，新的「重连」流程可以扫描新位置并重新对上，不用整个重新导入。
- **Disk Usage panel** in Feature Setup modal — see how much space `tmp` / `pip_cache` / `thumbnails` / `cache` take up, with safe-cleanup checkboxes. Read-only sizes for protected directories (`models`, `hf_cache`, `torch_runtime`, `favorites`, `config`) so users never accidentally wipe model data. Backed by a strict whitelist + path-containment service. (commit `d3178ea`)
  - **Feature Setup 模态框里新增「磁盘占用」面板**：看 tmp / pip 缓存 / 缩略图 / 通用缓存各占多少、勾选可安全清理；模型、HF 缓存、Torch runtime、favorites、设置等只读显示，避免误删模型数据。后端走严格白名单 + 路径包含检查。
- **Auto-Separate cooperative cancellation** — batch move/copy can be cancelled mid-flight and stops cleanly instead of running to completion. (commit `667212c`)
  - **自动分类批量移动/复制可中途取消**，按下取消会立刻停下来，不会硬跑完。
- **Aesthetic + artist filters wired through the sorting backend**, so they actually compose with the rest of the gallery filter pipeline instead of living off to the side. (commits `5426926`, `23651f7`)
  - **美学分数与画师筛选接入后端排序通道**，可以和图库其他筛选条件正常叠用，不再是孤岛。
- **Larger libraries supported.** identify-batch / obfuscation per-request ceilings raised from 10,000 to 50,000 (so users with >10k images can run a single pass), with a 5,000,000 backend ceiling for `image_ids`. (commits `0d059fe`, `cdac6e2`)
  - **支持更大的图库**：identify-batch / obfuscation 单次上限从 1 万提到 5 万（17k 图库的用户可以一次跑完），后端 `image_ids` 总上限拉到 500 万。
- **SAM3 Pro Segmentation** is available as an experimental option in the censor editor, alongside the existing Wenaka / NudeNet privacy detectors. (commits `c85f38a`, `452629e`, `95305d6`)
  - **SAM3 Pro 文字 prompt 分割（实验性）**，跟原本的 Wenaka / NudeNet 隐私检测器并存。
- **Privacy YOLO setup guidance dialog** — Civitai login wall now produces a structured 409 with manual fallback steps instead of a silent failure. (commit `6b82134`)
  - **Privacy YOLO 设置引导对话框**：Civitai 登录墙改成结构化 409 响应 + 手动下载步骤指引，不会再静默失败。
- WD14 tagger picker now lists Camie and PixAI tagger options alongside the default WD14/EVA02 set. (model registry update + credit doc)
  - WD14 tagger 选单新增 Camie、PixAI 选项，跟原本的 WD14/EVA02 并列。
- Lazy-human / lazy-release QA harnesses for repeatable manual-style smoke runs (developer tooling, no user-visible UI). (commits `a3f82a5`, `ed5944b`)
  - 增加 lazy-human / lazy-release 自动化 QA 跑测脚本（开发者工具，没有用户可见 UI）。

### Changed / 变更
- **SAM3 backend switched from `sam3==0.1.3` to `transformers.Sam3Model`.** The original Meta `sam3` PyPI package is no longer maintained; we now load checkpoints via `Sam3Model.from_pretrained(directory)` which expects a directory layout (`config.json` + `model.safetensors` + tokenizer files). ModelScope downloads deliver the correct shape automatically. (commit `c85f38a`)
  - **SAM3 后端从 `sam3==0.1.3` 套件换到 `transformers.Sam3Model`**：Meta 那个 PyPI 套件已经停更，新方案用 `Sam3Model.from_pretrained(目录)`，需要目录结构（`config.json` + `model.safetensors` + tokenizer 档）。从 ModelScope 下载的就是正确格式。
- Bundled portable Python embed bumped from 3.11.9 to 3.12.8 to match `requirements.txt`'s `python_requires`. (commit `5624f9a`)
  - Portable 内建 Python embed 从 3.11.9 升到 3.12.8，对齐 `requirements.txt` 的 python_requires。
- Service layer extracted **domain exceptions** (`ServiceError`, `ImageFileNotFoundError`) from raw `HTTPException`, so router-vs-service responsibilities are clean. (commit `5624f9a`)
  - Service 层从 raw `HTTPException` 抽出 **domain exceptions**（`ServiceError`、`ImageFileNotFoundError`），router 与 service 的职责分开。

### Fixed / 修复
- Reader overwrite is now safer and less annoying. If the user saves to the same path, the app asks first instead of failing once before asking. (commit `0e6faf9`)
  - Reader 覆盖保存更顺：保存到同一路径时会先问你要不要覆盖，而不是先报错一次再问。
- Reader confirmation text no longer gets overwritten while the dialog is open.
  - Reader 确认对话框开着的时候，文字不会再被动态覆写。
- Desktop navigation no longer hides the Reader tab too aggressively on normal desktop screens.
  - 桌面端导航不会再在正常桌面尺寸下把 Reader 页签藏起来。
- WSL / Linux runs now handle old Windows drive paths (`L:\...`) properly, so affected libraries no longer lose thumbnails just because the backend is running in WSL.
  - 在 WSL / Linux 跑后端时，旧的 Windows 路径（`L:\...`）也能正常处理，受影响的图库缩略图不会再因此消失。
- Scan progress is clearer during large imports. Users now see that the app is still importing in the background instead of feeling like the scan froze. (commit `5f38955`)
  - 大型扫描进度更清楚：后台还在继续导入时，画面会明确告诉你「还在跑」，不会再像卡死。
- JPG / WebP warnings now explain the metadata limitations honestly instead of implying they behave like PNG.
  - JPG / WebP 的提示会诚实告诉你 metadata 限制，不会再让人以为它们和 PNG 一样能塞所有信息。
- **Critical correctness fixes in core flows** (commit `fa93a23`):
  - Clear gallery no longer throws `ReferenceError: _scanProgressTimer is not defined` — Clear DB button works again. Scan/tag/aesthetic progress now probed in parallel via `Promise.allSettled`.
  - Auto move with copy no longer freezes at 0% for minutes — the up-front pixel-decode pass moved into the per-image loop, so progress shows up on the first iteration. Truncated-PNG protection preserved.
  - Tagging "Collecting image list", batch tag export, and delete-selected switched from per-id `db.get_image_by_id` loops to batched `db.get_images_by_ids` / `db.get_image_tags_map` (already chunks at 500 ids).
  - Similarity progress no longer gets stuck on `step="embedding"` after a crash — surfaces `step="error"` with the failure message; cancellation writes `step="cancelled"` instead of the success message.
  - Manual sort undo: file-op failures now return HTTP 500 with the session state rolled back (history/redo_stack restored).
  - **核心流程关键正确性修复**：Clear gallery 不再 ReferenceError，自动移动复制不再 0% 卡死，批量打标/导出/删除走批量 DB 查询，相似度进度死锁时正确报错并支持取消，手动分类撤销失败时回滚 session 状态。
- Aesthetic scores no longer become invisible after stop. Sort-by was being forced back to `newest` when the predictor went unavailable, hiding existing scored images behind unscored recent imports. (commit `0d059fe`)
  - 美学分数不再因为「停止」就消失。之前预测器不可用时前端会强制把排序拉回 newest，把已经打分的图盖在没打分的新图后面。
- 4 user-reported portable-testing bugs fixed: large-library 10k ceiling, aesthetic visibility, missing-folder rename UX, and a Bug 4 surface fix. (commit `0d059fe`)
  - 真机 portable 测试发现的 4 个用户回报 bug 全部修好（大图库上限、美学可见性、目录改名 UX、Bug 4 表层）。
- Embedded Python sibling import resolution + `nvidia-smi` CUDA-version parser fix. The launcher's CUDA detection no longer misreads driver version as CUDA version, and the embedded interpreter can find sibling backend modules during repair. (commit `17fd80a`)
  - 内嵌 Python 兄弟模块导入修复，加 `nvidia-smi` 解析 CUDA 版本不再误读成驱动版本。Launcher 修复脚本能正确找到 backend 模块，CUDA 选择更准。
- Aesthetic background task errors now surface to the UI; `ImageFileNotFoundError` raised by the service layer correctly maps to HTTP 404 instead of generic 500. (commit `dbeffc7`)
  - 美学后台任务错误会上抛到前端；`ImageFileNotFoundError` 走 404 而不是 500。
- Heavy AI runtime no longer crashes the server on certain edge cases (timing-related model loading guards). (commit `14a2800`)
  - 重型 AI 模块加载时序导致的 server 崩溃修复。
- Kaloscope artist runtime no longer hits `UnboundLocalError` on missing modules — explicit raise with diagnostic message instead. (commit `89389c9`)
  - Kaloscope 画师识别 runtime 缺模块时不再 `UnboundLocalError`，改成明确抛出诊断错误。
- Tag import writes unified into a single transactional path; Reader overwrite now refreshes derived state correctly. (commit `0e6faf9`)
  - 标签导入写入统一为一条事务路径；Reader 覆盖保存正确刷新派生状态。
- **Pagination cursor stability** — opaque cursors no longer break across edits/deletes during a paginated session. (commit `0e3d470`)
  - **分页 cursor 稳定性修复**：不透明 cursor 在编辑/删除时不会再失效。
- Cross-platform runtime dependency lock fixed — Linux / Windows / macOS all resolve to the correct PyTorch / ONNX / opencv variants. (commit `d5fa92c`)
  - 跨平台 runtime 依赖 lock 修好：Linux / Windows / macOS 都能正确解析到对应的 PyTorch / ONNX / OpenCV 版本。
- Selection token + migration review bugs (selection state desync after page changes; migration safety checks). (commit `4eec3e0`)
  - 选取 token 与 migration review 多个 bug 修好（页面变化时选取状态不同步、migration 安全检查）。
- Gallery batch actions + manual sort resume guard fixed. (commit `ba06d08`)
  - 图库批量操作 + 手动分类恢复 session 守卫修复。
- Smoke-test UX regressions (release-package smoke blockers). (commits `26bd20b`, `8607219`)
  - Release smoke 测试的 UX 回归与发布阻塞问题修好。
- Filter contracts + runtime invariants hardened — filter store mutations go through proper commits instead of side-channel writes. (commit `5426926`)
  - 筛选 contract 与 runtime invariant 收紧：筛选 store 变更走正规 commit，不允许 side-channel 写入。
- **6 verified tech-debt streams** (commit `5624f9a`): styles.css `:root` block corruption (modal-color rules misplaced), `censor-v2.css` hardcoded `60px` → `var(--nav-height)`, broken `aria-labelledby="nav-tab-gallery"` reference, 19 duplicate `promptlab.*` keys in zh-CN.js + 21 in en.js removed, dead `RedoStack` from manual-sort.js, `finishSorting()` raw `fetch` → API layer, minimap thumbnail capped at 1000 images (OOM cap), 64 MB PNG-chunk size limit + 64 MB zlib-decompression limit in metadata_parser, `aesthetic_service` DB connection unified to `get_db()` context manager.
  - **6 条已验证的技术债流**：CSS root 块错位、硬编码导航高度、aria-labelledby 引用错、200 个重复 i18n 键移除、dead code 清理、原生 fetch 换成 API 层、minimap 1000 图上限防 OOM、metadata parser 64 MB PNG/zlib 限制、aesthetic 服务统一 DB context manager。
- Service lifecycle hardening — clean shutdown paths and release-time safety checks. (commit `48793ff`)
  - Service 生命周期收紧：明确的关闭路径与发布期安全检查。
- SAM3 Pro censor no longer paints a giant box over the whole image when a prompt isn't actually present. A presence-probability gate plus a max-mask-area cap rejects the whole-body false-positive collapse. Concepts that genuinely *are* present (breasts, nipples, buttocks) keep working and recover small detections that the old score-only threshold accidentally filtered out. (commit `d800da4`)
  - SAM3 Pro 打码不再在 prompt 实际不存在时画整张图框。新的 presence-probability 门控加 mask 最大面积上限挡掉全身框误判，真的存在的概念（breasts、nipples、buttocks）继续正常工作，旧的纯分数阈值误过滤掉的小区域救回来了。
- SAM3 launcher / build robustness: tokenizer vocab provisioned from `open_clip` on first SAM3 load, `torch.load weights_only=False` forced during build, dead SAM3 runtime patch + orphan similarity helpers removed. (commits `452629e`, `95305d6`, `d6d1add`)
  - SAM3 启动 / 打包鲁棒性：第一次加载从 `open_clip` 取 tokenizer 词表、build 时强制 `torch.load weights_only=False`、清理 SAM3 runtime 死代码与相似度孤儿函数。
- SAM3 popup close handling fixed — modal can be dismissed cleanly. (commit `6b82134`)
  - SAM3 弹窗关闭逻辑修复，可以正常退出。
- Windows first launch no longer misreads a freshly installed CUDA PyTorch wheel through the old already-imported CPU `torch` module. Adds `--no-deps` to the CUDA torch reinstall to kill the multi-GB transitive cascade noise. (commits `0ef4fe1`, `17fd80a`)
  - Windows 第一次启动不再透过已经 import 进 process 的旧 CPU `torch` 看刚装好的 CUDA wheel；CUDA torch 重装加 `--no-deps`，避免几 GB 的 transitive 依赖瀑布噪音。
- Artist (Kaloscope) generic `torch.load` fallback now passes `weights_only=False` so the load actually succeeds. (commit `d921c5a`)
  - 画师识别（Kaloscope）的 `torch.load` 通用回退路径补 `weights_only=False`，加载真的能成功。
- Lockfile hash now normalizes line endings before computing sha256 — a stamp written on Windows (CRLF) now validates on Linux CI (LF), so lock-freshness checks are stable across platforms. (commit `4f806c7`)
  - Lockfile 哈希在算 sha256 前先 normalize 换行符——Windows（CRLF）写的 stamp 在 Linux CI（LF）也验得过，跨平台 lockfile freshness 检查不再误报 stale。

### Security / 安全
- File-protocol model downloads (`file://` URLs) are now refused unless the explicit test-only env var `SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS=1` is set. Closes a small attack surface where a misconfigured `SD_IMAGE_SORTER_*_URL` could redirect to a local path. (commit `0a563af`)
  - `file://` 协议的模型下载默认全部拒绝，除非显式设置测试用 env var `SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS=1`。封住一个 misconfigured `SD_IMAGE_SORTER_*_URL` 可能指向本地路径的小攻击面。

### Documentation / 文档
- README now states realistic first-launch disk-space and network-traffic budgets, including CUDA runtimes, pip cache, and on-demand AI model sizes.
  - README 现在写出真实的首次启动磁盘空间和网络流量预算，包含 CUDA runtime、pip cache、按需下载的 AI 模型大小。
- Special thanks / credits expanded with all currently-used model and tool authors: Camie, PixAI, ToriiGate, NudeNet, SAM3, ModelScope (heathcliff01), LAION aesthetic predictor, OpenCLIP, 大番茄 / 小番茄 obfuscation. Self-references removed.
  - 鸣谢 / 致谢表更新，把当前用到的所有模型和工具作者都列出来：Camie、PixAI、ToriiGate、NudeNet、SAM3、ModelScope（heathcliff01）、LAION 美学预测器、OpenCLIP、大番茄 / 小番茄 obfuscation。移除了自我引用。
- New `docs/AI_PRINCIPLES.md` and `docs/TECHNICAL_DEBT_NOTES.md` capturing AI-assisted development governance and the ongoing tech-debt log.
  - 新增 `docs/AI_PRINCIPLES.md` 与 `docs/TECHNICAL_DEBT_NOTES.md`，记录 AI 协作开发治理与持续追踪的技术债。

### Known Limitations / 已知限制
- **SAM3 Pro Segmentation is experimental.** The text-prompted detection path is significantly weaker than its ComfyUI counterpart (which uses box-prompted refinement). Recall on anime/SD images is low and bounding boxes are often coarse. **Recommended workflow: keep NudeNet (default) or Wenaka YOLOv8 for primary censoring.** SAM3 is best treated as an opt-in experiment until a future release lands a hybrid NudeNet→SAM3 refine pipeline.
  - **SAM3 Pro 文字 prompt 分割是实验性的。** 它的文字 prompt 路线明显比 ComfyUI 上的 box-prompt refine 用法弱：在动漫/SD 图上的召回率低、bounding box 也常常粗糙。**建议工作流：主打码请继续用 NudeNet（默认）或 Wenaka YOLOv8，把 SAM3 当成需要时再开的实验功能。** 我们下个版本会做 NudeNet→SAM3 的混合 refine 流程，到时候 SAM3 才会真正发挥价值。

### Validation / 验证
- 749+ backend pytest, 0 failures (pre-`5624f9a` measurement); the v3.1.0 release commit also passes the full `python scripts/run_ci.py` pipeline (lockfile freshness, security audit, frontend JS syntax, backend pytest, Playwright E2E) on Linux + Windows.
  - 后端 pytest 749+ 项全过零失败；v3.1.0 发布 commit 在 Linux + Windows 双平台 `python scripts/run_ci.py` 全套（lockfile / security / frontend JS / backend pytest / Playwright E2E）通过。
- Reader save / overwrite flow passed real browser validation end-to-end.
  - Reader 保存 / 覆盖流程在真实浏览器里走完整 E2E 通过。
- Scan + metadata regression suite passed after the v3.1.0 scan-experience updates.
  - 扫描与 metadata regression 套件在 v3.1.0 扫描体验更新后通过。
- SAM3 presence-gate verified on real anime/SD test images (no whole-body false positives on absent prompts; small-region recall preserved).
  - SAM3 presence-gate 在真实动漫/SD 测试图上验证：prompt 不存在时不会出全身框、原本会被误过滤的小区域保留下来了。
- Reconnect-missing flow verified on a library where files were renamed/moved out-of-band.
  - 重连流程在真实「文件被改名/移动」的图库上验证可用。

## [3.0.6] - 2026-04-20

### Fixed
- ComfyUI prompt extraction now follows `SamplerCustomAdvanced → CFGGuider` chains, `JoinStringMulti` nodes, and capital-`S` `String` nodes.
- Aesthetic scoring no longer freezes the system at ~1000 images. Added periodic `torch.cuda.empty_cache()` + `gc.collect()`, explicit PIL image closing, and batched commits.
- Disabled LoRAs (`on: false`) in rgthree Power Lora Loader are now excluded from the LoRA list and filter.
- Censor save as JPG/WebP now preserves SD metadata by converting PNG text chunks to EXIF UserComment. Parser also reads ComfyUI JSON back from EXIF UserComment in JPEG/WebP files.
- Gallery empty state no longer shows a duplicate camera-icon message alongside the styled card.
- Artist ID progress bar no longer stuck on "Starting..." — removed blocking overlay and fixed `data-i18n` attribute that kept overwriting dynamic progress text.
- Artist confidence threshold value no longer disappears after language refresh.
- Manual Sort now shows a confirmation dialog before starting a sort session.

### Added
- LoRA weights (`strength_model` / `strength_clip`) are now extracted and displayed next to each LoRA name in the image detail modal.
- VAE and CLIP/Text Encoder models are now extracted from ComfyUI workflows and shown in the Model Assets section.
- Version strings synced to `3.0.6`.

## [3.0.5] - 2026-04-20

### Fixed
- Removed the stale "launch-time GPU confirmation" product semantics from the tagger flow. The UI and E2E suite now match the real behaviour: automatic hardware clamps stay active without a separate confirmation modal.
- Tightened the Censor workspace sidebar sizing so the queue header and Queue Manager button stay readable without squeezing the canvas workspace.
- Folder scan now performs a real two-pass streaming walk: one cheap count pass for truthful progress totals, then a second processing pass without materializing the full file list in memory.
- Synced release-facing version strings to `3.0.5` across the API metadata, README download links, and the model-download User-Agent.
- Playwright startup paths now fall back across Windows and POSIX virtualenv layouts instead of hardcoding one platform-specific Python path.

## [3.0.4] - 2026-04-19

### Fixed
- Reader clipboard capture now tells the truth: clipboard images may lose SD PNG metadata in the browser, the button arms the `Ctrl+V` capture flow instead of relying on `navigator.clipboard.read()`, and metadata-lost clipboard results no longer silently look like successful parses.
- `POST /api/models/prepare` for `censor-legacy` now returns a structured `409 Conflict` auth-wall response instead of a generic `500`. The payload includes `error`, `type`, `message`, `manual_steps`, and `provider`, and the model manager renders the result as a warning instead of a server crash.
- `POST /api/models/prepare` for `censor-legacy` now also returns a structured non-500 `ModelPreparationFailed` response when Civitai serves a bad archive or extraction fails, instead of leaking `BadZipFile` / generic server-crash semantics.
- Folder scan now performs a real image decode verification, so corrupt and truncated files are reported as errors, named in scan progress, and kept out of manual sort / tagging / similarity flows.
- Single-image move now re-validates file readability, so truncated images are rejected instead of being treated as successful moves just because the file still exists.
- Similarity embedding progress now reports `skipped`, `unreadable`, and `failed` separately, including recent filenames / image ids instead of a vague `1 failed`, and similarity search / duplicate results now exclude rows already marked unreadable.

## [3.0.3] - 2026-04-18

### Fixed
- `run-portable.bat`, `run.bat`, and `run.sh` now honour `SD_IMAGE_SORTER_PORT` when printing the "Open browser" URL and when auto-opening the browser. Previously the launchers hardcoded `http://localhost:8487`, so users who overrode the port were silently routed to the wrong URL while the server bound the correct one.
- `/api/models/prepare` for `censor-legacy` no longer 500s on fresh installs. Two fixes: (1) Civitai metadata + archive requests now use a realistic browser `User-Agent` header (the old default `Python-urllib/x.y` was rejected with HTTP 403), target the new `civitai.red` domain, and fall back to a pinned direct-download URL when the API path misbehaves. (2) Civitai additionally gates NSFW model downloads behind account login; unauthenticated requests get an HTML sign-in page instead of the zip, which used to surface as a cryptic `BadZipFile`. The backend now detects the sign-in page (Content-Type `text/html` or invalid zip) and raises a clear manual-download guide pointing at the Civitai page and the local `models/yolo/` directory. The app cannot bypass Civitai's auth wall — this is a Civitai policy change.
- `/api/artists/diagnostics` now reports `available:true` when the HuggingFace / ModelScope fallback has already loaded a working artist model at runtime, matching the behaviour of `/api/artists/identify`. Adds `runtime_loaded`, `runtime_backend`, and `runtime_error` fields so the UI can distinguish "Kaloscope files missing but fallback loaded" from "nothing loaded".

### Added
- ToriiGate first-use now emits an explicit `~5 GB from HuggingFace` progress message before the model download starts, so users on slow or metered connections are not surprised by a silent multi-gigabyte fetch. Subsequent runs show a short "Loading ToriiGate on GPU/CPU" message instead.

## [3.0.2] - 2026-04-18

### Fixed
- NVIDIA VRAM total is no longer clamped at 4095 MB on Windows when `torch.cuda` is unavailable. `hardware_monitor.py` now overlays `nvidia-smi --query-gpu` results on top of WMI's 32-bit `AdapterRAM` readout.
- Dual-NVIDIA rigs match each card to its own VRAM by device name instead of by enumeration index, so WMI PnP order and nvidia-smi NVML order disagreeing no longer swaps VRAM between cards.
- Tagger batch-size recommendation now reflects actual VRAM (e.g., RTX 3090 picks batch size 32 instead of 8).

### Added
- Regression tests in `backend/tests/test_hardware_monitor.py` covering the WMI cap override, the degraded fallback when nvidia-smi is unavailable, dual-NVIDIA name-match ordering, and the guarantee that Intel/AMD devices never receive nvidia-smi overlays.

## [2.1.0] - 2026-04-04

### Added
- Local model readiness reporting in the launcher and browser UI
- Portable release packaging script with core-model, artist-runtime, and split large-model assets
- User-facing release and model setup guides
- Artist diagnostics endpoint and Similar CLIP status endpoint

### Changed
- Default artist backend switched from `cafe_style` to `Kaloscope2.0`
- Censor Edit now auto-selects the recommended Wenaka privacy model when it exists locally
- Legacy YOLO support now distinguishes privacy-part models from general compatibility models
- README and third-party model policy rewritten around the real verified model pipeline

### Fixed
- Kaloscope runtime path now works with `comfyui-lsnet` / `lsnet-test` layouts
- Local CLIP model path is preferred correctly for similarity search
- NudeNet box normalization is corrected for frontend/backend integration
- General YOLO `.onnx` / `.pt` compatibility is validated instead of assuming Wenaka-only outputs

## [2.0.0] - 2024-03-XX

### Added
- **Favorites Workflow**: New favorites gallery with copy-to-favorites functionality
- **Upgraded Gallery Preview**: Improved image preview with keyboard navigation
- **SAM3 Mask Refinement**: Pixel-precise segmentation for censoring
- **CLIP Similarity Search**: Find similar images and detect duplicates
- **Prompt Lab**: Intelligent prompt generation with tag categorization
- **Artist Identification**: Experimental artist/style classification (LSNet-based)
- **Thumbnail Cache**: Persistent disk-based thumbnail cache with WebP compression
- **Service Layer Refactoring**: Dependency injection pattern for all routers
- **Path Validation Security**: Comprehensive directory traversal prevention

### Changed
- Refactored all routers to use service layer pattern
- Improved metadata parser to handle more ComfyUI workflow variations
- Enhanced thumbnail generation with configurable sizes
- Updated UI with glassmorphism design improvements

### Fixed
- SQL injection prevention in all database queries
- Path traversal vulnerabilities in file operations
- Memory leaks in AI model loading
- Race conditions in background tasks

### Security
- Added `utils/path_validation.py` for comprehensive path security
- Parameterized all SQL queries
- Added input validation at API layer

## [1.5.0] - 2024-02-XX

### Added
- YOLOv8 detection for NSFW content
- NudeNet integration for body part detection
- Manual sort session with WASD keyboard controls
- Auto-separate feature for batch image organization
- WebP metadata extraction support

### Changed
- Improved ComfyUI workflow parsing
- Enhanced tag import/export functionality

### Fixed
- Unicode handling in prompts
- Memory usage with large image libraries
- Database locking issues

## [1.4.0] - 2024-01-XX

### Added
- WD14 tagger integration (ONNX Runtime)
- Multiple tagger model support (EVA02, ViT, Swin, ConvNeXt)
- Tag confidence filtering
- Batch tagging with progress tracking

### Changed
- Migrated to ONNX Runtime for AI models
- Improved database schema with indexes

## [1.3.0] - 2023-12-XX

### Added
- Forge generator detection
- NovelAI metadata parsing
- ComfyUI workflow extraction
- WebUI/A1111 parameter parsing

### Changed
- Unified metadata parser architecture

## [1.2.0] - 2023-11-XX

### Added
- Gallery view with generator tabs
- Advanced filtering (generator, tags, dimensions)
- Image detail modal with metadata display

### Changed
- Redesigned frontend with glassmorphism theme

## [1.1.0] - 2023-10-XX

### Added
- SQLite database for image metadata
- Folder scanning with metadata extraction
- Basic image grid view

### Changed
- Initial FastAPI backend structure

## [1.0.0] - 2023-09-XX

### Added
- Initial release
- Basic image serving
- Simple HTML frontend
