## v3.5.0-beta.6 — 元数据 prompt 读取 / Metadata Prompt Recovery

ComfyUI/WebUI/NovelAI stealth WebP 嵌图 prompt 可读。旧库需重扫。Embedded prompts including stealth WebP recover; re-scan to refresh.

---

## Fixed / 修复

- **ComfyUI UI workflow and hybrid geninfo / ComfyUI UI workflow 与 hybrid 参数**: parse the UI `workflow` when the API `prompt` chunk is missing or is only an upscale subgraph. Keep `generator=comfyui` when a graph is present. A full A1111 `parameters` trailer is executed text or a Reader edit; a truncated decoy does not replace a fuller encoder prompt.
  - 没有 API `prompt`、或只有放大 subgraph 时，从 UI `workflow` 取字。有 graph 时 generator 保持 comfyui。完整 `Steps`/`Sampler` trailer 当 executed text 或 Reader 编辑。

- **Off-PNG carriers / 非 PNG 载体**: EXIF/WebP `Workflow:` / `Prompt:` prefixes (including NUL-separated fragments) and UTF-16 UserComment feed the same parse path.
  - EXIF/WebP 的 `Workflow:` / `Prompt:`（含 NUL 分段）与 UTF-16 UserComment 走同一条解析路径。

- **InvokeAI not stolen as ComfyUI / InvokeAI 不再被当成 ComfyUI**: `invokeai_graph` uses a nodes object and is no longer promoted as a ComfyUI UI workflow.
  - `invokeai_graph` 的 nodes 是 object，不再被提升成 ComfyUI UI workflow。

- **Natural-language and Flux encoder keys / 自然语言与 Flux 编码器字段**: graph position decides `t5xxl` / `text_g` / `text_l` / `populated_text`. Harvest accepts NL without commas and rejects bus titles, paths, and sampler names.
  - 编码器字段以图位置为准。harvest 接受无逗号自然语言，拒绝总线标题、路径、sampler 名。

- **NovelAI Diffusion V5 / NovelAI Diffusion V5**: official V5 images still use the V4 Comment/`v4_prompt` carriers. Character prompts now come from `characterPrompts` and `caption.char_captions` (22 slots). Source names like `nai-diffusion-5-full` are kept.
  - 官方 V5 仍写 V4 Comment。角色提示词从 `characterPrompts` / `char_captions` 读取；`nai-diffusion-5-*` 会记入模型名。
- **NovelAI stealth WebP / NovelAI stealth WebP**: lossless RGBA/RGB WebP uses the same signed LSB decoder as PNG (`stealth_pngcomp` and RGB variants). Pixiv-saved NovelAI WebP with no EXIF is readable. Default scan re-parses stored rows (parser version 10).
  - 无损 WebP 的 alpha/RGB LSB 与 PNG 同一套解码。没有 EXIF 的 NovelAI WebP 现在读得到。默认扫描会重解析已存列。
- **Gallery uses scanned metadata / 扫完图库就能用新 metadata**: search, prompt filters, and the gallery detail modal use stored character slots and checkpoints after Scan. Slot text is not written into the base prompt.
  - 扫描后搜索、筛选和详情弹窗能用到角色槽与 checkpoint。角色槽不会写进正向 prompt。
- **ComfyUI settings-only parameters / 只有 Model hash 的 ComfyUI PNG**: recover checkpoint, keep prompt empty, keep `generator=comfyui`.
  - 只有设定行的 ComfyUI PNG 记下 checkpoint，不把 Clip skip 当 prompt。
- **Reader character slots / Reader 角色槽**: NovelAI V4/V5 character cards in Image Reader, matching the gallery modal.
  - Reader 也显示 NovelAI 角色提示词卡片。
- **Dataset review keys / Dataset 审阅快捷键**: `X` drops without confirm, `Z` undoes, `A`/`D` step. Tag colors stay.
  - 工作台 `X` 剔除、`Z` 撤销、`A`/`D` 翻图。
- **Lucida auto-mask batch / Lucida 批量遮罩**: Dataset Maker queues Lucida/rembg masks for the whole set and skips images that already have a mask.
  - 整批自动遮罩可续跑，已有遮罩会跳过。

---

## Upgrading / 升级注意

- A normal Scan now re-parses previously imported images (parser version 10). Empty-prompt reparse is not required for this beta.
  - 普通扫描就会重解析已导入图片（parser version 10）。这次不必只靠「空 prompt 再解析」。

- No database migration is required. Existing images, tags, projects, models, settings, and the `data/` folder remain outside updater-managed application files. Back up `data/` before beta updates as usual.
  - 本次测试版不需要新的数据库迁移。现有图片、标签、项目、模型、设置与 `data/` 仍不属于更新器管理的应用文件；测试版更新前仍建议备份 `data/`。

- Beta 5 and older supported installations can update through Check Update.
  - Beta 5 及更早的受支持安装可通过「检查更新」升级。

---

## Validation / 验证

Backend pytest 6617 collected, green. Desktop E2E 737 passed / 21 failed on first sharded run; parser Reader-edit, off-gallery scan, and checkpoint-chip tests were fixed. Remaining failures are Gallery chrome/mock pins, not the metadata slices. / 后端 pytest 6617 条通过。桌面 E2E 首次分片 737 通过 / 21 失败；Reader 编辑、库外扫描、checkpoint chip 已修。其余为图库顶栏/mock 钉，不是这次 metadata 切片。

---

## ⬇️ Which file should I download? / 我该下载哪一个？

**Windows → `sd-image-sorter-v3.5.0-beta.6-windows-portable.zip`** — extract, run `run-portable.bat`.

**Linux portable x86_64 → `sd-image-sorter-v3.5.0-beta.6-linux-portable-x86_64.tar.gz`** — extract, run `./run-portable.sh`.

**Linux portable aarch64 → `sd-image-sorter-v3.5.0-beta.6-linux-portable-aarch64.tar.gz`** — for ARM Linux, Raspberry Pi 5, and Graviton.

**Linux source install → `sd-image-sorter-v3.5.0-beta.6-linux.tar.gz`** — for systems with Python 3.12+.

**Do NOT download / 不要下载：**
- `sd-image-sorter-v3.5.0-beta.6-app-patch.zip` — in-app updater only / 仅供应用内更新器
- `sd-image-sorter-v3.5.0-beta.6-release-manifest.json` — updater metadata / 更新器元数据

---

## Checksums

| Asset | SHA-256 |
|---|---|
| `sd-image-sorter-v3.5.0-beta.6-windows-portable.zip` | `e32328b59f16fc0e49ab857182d975cbd95f5300e22611230beb8779e552cba4` |
| `sd-image-sorter-v3.5.0-beta.6-app-patch.zip` | `857b47a09b887cfb16afd0839ee053cca605bc2518c455327b12ce76159245cf` |
| `sd-image-sorter-v3.5.0-beta.6-linux.tar.gz` | `c30733ac726508ddc95654af7f85782045a35b8470a7c271428634ed247b42f3` |
| `sd-image-sorter-v3.5.0-beta.6-linux-portable-x86_64.tar.gz` | `5352e716f9e7a7de66ad17454d5442073ffe597621ff442621b9c4a804316ae8` |
| `sd-image-sorter-v3.5.0-beta.6-linux-portable-aarch64.tar.gz` | `513380c038c76a593fe503aee651699dbf503d8e11942b7618a18d0998fb991c` |
| `sd-image-sorter-v3.5.0-beta.6-release-manifest.json` | `7ef705827039ccec65dd74e64bec0352fec998dd82428250018ba614b45573d2` |

The manifest contains the five archive checksums; its own checksum is recorded above. / manifest 内含五个归档校验和，其自身校验和记录于上表。
