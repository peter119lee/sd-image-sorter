## v3.5.0-beta.5 — 少重开 + TIPO v2.1 / Fewer Restarts + TIPO v2.1

多数套件安装后可立刻继续下载模型，真要重开时有一键重启。TIPO 升到 v2.1；图库顶栏不再乱跑。

Most installs continue in the same click. One-button restart when needed. TIPO v2.1 and calmer Gallery chrome.

---

## Fixed / 修复

- **Fewer restarts after Prepare / 安装后少重开**: optional Python packages that import in this process continue to model-weight download in the same click. A restart is required only when a loaded module was replaced, Windows locked a DLL, or import works only in a clean interpreter. Model Center then offers Restart now and continue; remaining downloads resume after relaunch.
  - 能在当前进程 import 的套件会立刻继续下载权重。只有覆盖已加载模块、DLL 被锁、或干净进程才能 import 时才要重开，并提供「立即重启并继续」。

- **TIPO v2.1 with a public CPU wheel / TIPO v2.1 与官方 CPU wheel**: Reverse Prompt and Dataset Maker default to TIPO-v2.1 (~1.1 GB) with a lighter 200M-ft choice (~210 MB). Prepare installs `llama-cpp-python` from the first-party CPU extra-index with `--only-binary`; it will not compile an sdist. Covered hosts: Windows x86_64, Linux x86_64 / aarch64, macOS Apple Silicon.
  - 反推和数据集默认 v2.1，可改选较轻的 200M-ft。模型中心「准备」只装官方 CPU wheel，禁止源码编译。

- **CJK tag aliases and character series / 中日文别名与角色作品**: autocomplete resolves CJK queries (长发 → `long_hair`) from the bundled MIT StoryAura table. Accepting a character tag such as `hatsune_miku` also writes its series (`vocaloid`) when known.
  - 标签补全开箱可用中日文别名；选中角色标签时若知道作品会一并写入。

- **Gallery chrome stays put / 图库顶栏不再乱跑**: Import and AI Tag stay on Gallery; status chips stay icon-sized; generator counts, sort width, and mode switches no longer jump when selected. Entry cover-mode buttons keep their slots.
  - 「导入 / AI 打标」留在图库；状态芯片不再撑开按钮；生成器数字、排序宽度、模式开关选中不再跳位。

---

## Upgrading / 升级注意

- No database migration is required for this beta. Existing images, tags, projects, models, settings, and the `data/` folder remain outside updater-managed application files. Back up `data/` before beta updates as usual.
  - 本次测试版不需要新的数据库迁移。现有图片、标签、项目、模型、设置与 `data/` 仍不属于更新器管理的应用文件；测试版更新前仍建议备份 `data/`。

- Beta 4 and older supported installations can update through Check Update. If a Prepare step says a restart is required, use Restart now and continue instead of closing the window by hand.
  - Beta 4 及更早的受支持安装可通过「检查更新」升级。若「准备」提示需要重启，请用「立即重启并继续」，不必自己关窗。

- TIPO v2 is no longer offered separately (same RAM class as v2.1). Existing v2 weights still work until you Prepare v2.1.
  - 不再单独提供 TIPO v2（与 v2.1 内存档位相同）。已有的 v2 权重在你准备 v2.1 之前仍可用。

---

## Validation / 验证

Owner skipped a full local CI rerun for this beta. Focused proven-restart and restart-API pytest pins were already green on this worktree before packaging. / 本次测试版按所有者指示跳过完整本地 CI。打包前，证明制重启与重启 API 的聚焦 pytest 已在此工作区通过。

---

## ⬇️ Which file should I download? / 我该下载哪一个？

**Windows → `sd-image-sorter-v3.5.0-beta.5-windows-portable.zip`** — extract, run `run-portable.bat`.

**Linux portable x86_64 → `sd-image-sorter-v3.5.0-beta.5-linux-portable-x86_64.tar.gz`** — extract, run `./run-portable.sh`.

**Linux portable aarch64 → `sd-image-sorter-v3.5.0-beta.5-linux-portable-aarch64.tar.gz`** — for ARM Linux, Raspberry Pi 5, and Graviton.

**Linux source install → `sd-image-sorter-v3.5.0-beta.5-linux.tar.gz`** — for systems with Python 3.12+.

**Do NOT download / 不要下载：**
- `sd-image-sorter-v3.5.0-beta.5-app-patch.zip` — in-app updater only / 仅供应用内更新器
- `sd-image-sorter-v3.5.0-beta.5-release-manifest.json` — updater metadata / 更新器元数据

---

## Checksums

| Asset | SHA-256 |
|---|---|
| `sd-image-sorter-v3.5.0-beta.5-windows-portable.zip` | `9bb8b4fb8ea9edb1461c06dd1c81699f1586b89fa769677c41f2b9f188a7d7a2` |
| `sd-image-sorter-v3.5.0-beta.5-app-patch.zip` | `f9e0d0fb691a1f113b90fb7a936589cbd36bcafd4a502439fd05dc0c56f996ce` |
| `sd-image-sorter-v3.5.0-beta.5-linux.tar.gz` | `b1482ad8e5b54d65952de715a16a0529d110daca9266511537eddc9906f11af9` |
| `sd-image-sorter-v3.5.0-beta.5-linux-portable-x86_64.tar.gz` | `4c60b169b52810b3b4e3a51bc1c9d1956278fa463a198b38552678eca9276f43` |
| `sd-image-sorter-v3.5.0-beta.5-linux-portable-aarch64.tar.gz` | `7ae741af68db02a4d0f74d0fa2dd0dac5bb4850099d8c6599e9fed78354350f4` |
| `sd-image-sorter-v3.5.0-beta.5-release-manifest.json` | `d1820e067dec1864c36e000c8ca29f963c0a270339273a1f77eb4f549592b5ce` |

The manifest contains the five archive checksums; its own checksum is recorded above. / manifest 内含五个归档校验和，其自身校验和记录于上表。

