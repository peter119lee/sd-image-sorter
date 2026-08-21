/**
 * guide/copy.js — guide.js decomposition (pure data; loads FIRST).
 * Extracted VERBATIM from frontend/js/guide.js pre-split lines 1-8 +
 * 10-553 (of 1,085): the original file docblock, the IIFE's
 * 'use strict' (kept as a file-level directive here and ADDED to the
 * other family files — the smart-tag/image-reader precedent; the
 * original file was strict throughout), and the three closure-private
 * immutable data blocks: GUIDE_COPY (bilingual per-tab guide copy,
 * 9 tabs each), TAB_SHORTCUTS (per-tab keyboard-shortcut rows) and
 * TAB_ANCHORS (inline ❔ button selectors; gallery/autosep/manual
 * intentionally null). Classic script: the three top-level consts land
 * in the one shared global lexical environment (smart-tag/autosep/
 * censor precedent), so guide/engine.js reads them via the scope
 * chain and they still do NOT become window own-properties (pinned by
 * tests/e2e/specs/guide-pins.spec.ts). Only the IIFE frame lines
 * (pre-split 9 and 1,085) were dropped; no identifiers were renamed
 * (tree-wide collision census: GUIDE_COPY / TAB_SHORTCUTS /
 * TAB_ANCHORS / Guide are declared nowhere else). Everything below is
 * byte-identical to the pre-split file.
 */
/**
 * SD Image Sorter - Contextual Guide System
 *
 * Provides a detailed per-tab guide in English and Simplified Chinese.
 * The content is embedded here so the guide keeps working even when the
 * main translation packs are incomplete.
 */

    'use strict';

    const GUIDE_COPY = {
        en: {
            button: 'Guide',
            subtitle: 'What this tab does and how to use it',
            close: 'Close',
            closeAria: 'Close guide',
            tour: '🎓 Restart tour',
            tourTitle: 'Restart the onboarding tour from the beginning',
            refreshI18n: '🔄 Refresh translations',
            refreshI18nTitle: 'Re-fetch lang/*.js without losing your scan, filters, or selection',
            refreshI18nDone: 'Translations refreshed without losing your data.',
            refreshI18nFailed: 'Could not refresh translations. Try a normal F5 instead.',
            sections: {
                purpose: 'What This Tab Is For',
                steps: 'How To Use It',
                features: 'Main Functions',
                tips: 'Practical Tips',
            },
            tabs: {
                gallery: {
                    icon: '🖼️',
                    title: 'Gallery',
                    purpose: [
                        'Browse every scanned image in one place, inspect prompts and metadata, and narrow the list with filters before you move or edit anything.',
                    ],
                    steps: [
                        'Start with "Import Images" to scan a folder into the library.',
                        'Use generator tabs, filters, and sort controls to narrow the gallery to the images you actually want.',
                        'Open an image to inspect its prompt, tags, LoRAs, parameters, and prompt format conversions.',
                        'Turn on "Select Images" when you want batch export, censor editing, or other multi-image actions.',
                    ],
                    features: [
                        'Multiple view modes for dense browsing, larger previews, or waterfall layout.',
                        'Filter summaries so you always know why the current gallery looks the way it does.',
                        'Prompt and metadata copy buttons in the image preview modal.',
                        'Quick access to random image, tags library, and selection tools.',
                    ],
                    tips: [
                        'Use the filter editor when the summary starts getting too dense to read.',
                        'If the prompt format toggle is available in the preview modal, compare the converted format before copying it into another tool.',
                    ],
                },
                reader: {
                    icon: '📖',
                    title: 'Read Image',
                    purpose: [
                        'Inspect one image in depth — including images that are NOT in your library — read its full SD metadata, edit generation info, and use the Privacy / obfuscation tools.',
                    ],
                    steps: [
                        'Open from the Gallery preview (Metadata / Info), or drag-and-drop / paste any image file here.',
                        'Read the parsed prompt, negative, model, sampler, and other generation parameters.',
                        'Use "Edit Metadata" to adjust fields, then "Save New Image" to write a copy.',
                        'Switch to "Privacy Tools" to encode (protect) or decode (restore) an image.',
                    ],
                    features: [
                        'Reads external images via drag-drop, file picker, or clipboard paste.',
                        'Full metadata editor with a save-as-new-image action.',
                        'Privacy / obfuscation workbench (encode, decode, batch).',
                    ],
                    tips: [
                        'The Gallery preview modal already covers quick metadata viewing — come here when you need to edit, read an out-of-library file, or use Privacy Tools.',
                    ],
                },
                dataset: {
                    icon: '📦',
                    title: 'Dataset',
                    purpose: [
                        'Turn a set of images into a LoRA-training-ready dataset: import, caption (booru tags + natural language), tidy captions, then export images and matching .txt files to one folder.',
                    ],
                    steps: [
                        'Import: add images from the Gallery selection or a folder path (step 1).',
                        'Workbench: review each image, edit its booru tags and natural-language caption, and use Smart Tag (WD14 + VLM) or Auto-tag to fill them in (step 2).',
                        'Export: choose the caption format and output folder, then export image + .txt pairs (step 3).',
                    ],
                    features: [
                        'Two-box caption editor: booru tags (with colored danbooru groups) plus a natural-language caption.',
                        'Smart Tag bundles WD14 tagging and VLM captioning with an optional trigger word.',
                        'Mass tag editor for bulk find/replace, add, remove, and cleanup across the whole set.',
                    ],
                    tips: [
                        'Set a trigger word before exporting if you are training a specific character or style.',
                        'Use the tag-confidence panel to drop low-confidence tags before export.',
                    ],
                },
                autosep: {
                    icon: '📁',
                    title: 'Auto-Separate',
                    purpose: [
                        'Move every image that matches the Auto-Separate filters into a destination folder in one batch operation, without touching the Gallery or Manual Sort filter state.',
                    ],
                    steps: [
                        'Open the filter editor here and build the exact Auto-Separate rule set you want.',
                        'Save a config when this move pattern is something you will reuse later.',
                        'Enter a destination folder and run "Preview Results" first.',
                        'Review the preview count, sample images, and destination before you move anything.',
                    ],
                    features: [
                        'Keeps its own filter state, so this tab no longer pollutes Gallery or Manual Sort.',
                        'Saved configs remember both filters and destination presets for repeated cleanup jobs.',
                        'Preview list shows examples before files are moved.',
                        'Supports generator, rating, tags, prompts, checkpoints, LoRAs, dimensions, and aesthetic score filters.',
                    ],
                    tips: [
                        'Preview before every large move. It is still the fastest way to catch a bad filter combination.',
                        'Keep one config per repeated workflow, such as "high-score NAI portraits" or "webui leftovers to archive".',
                    ],
                },
                manual: {
                    icon: '🎮',
                    title: 'Manual Sort',
                    purpose: [
                        'Review images with Slot sort (WASD), A/B Showdown, or Keep / Reject while keeping a dedicated Manual Sort filter state.',
                    ],
                    steps: [
                        'Choose Slot sort (WASD), A/B Showdown, or Keep / Reject before starting.',
                        'For Slot sort, configure the folders for W, A, S, and D.',
                        'For A/B Showdown, choose where the winner goes; for Keep / Reject, choose both destination collections.',
                        'Use the Manual Sort filters when you only want to review one slice of the library.',
                        'Start the session and use the visible shortcut card for the selected mode.',
                        'Watch the minimap and progress cards to track what is processed, skipped, or still pending.',
                    ],
                    features: [
                        'Slot sort provides four-way folder mapping for fast keep/delete/best/needs-work workflows.',
                        'A/B Showdown compares pairs until one winner remains without moving the source files.',
                        'Keep / Reject records one decision per image and routes both outcomes to collections.',
                        'Undo and redo support so a single bad key press does not ruin the session.',
                        'Resume banner for unfinished sessions.',
                        'Status strip, minimap, and shortcut card for high-speed operation.',
                    ],
                    tips: [
                        'Keep folder names and meanings consistent between sessions. Speed comes from muscle memory.',
                        'If you work in Chinese, the translated labels change, but the keyboard mapping does not.',
                    ],
                },
                censor: {
                    icon: '🔳',
                    title: 'Censor Edit',
                    purpose: [
                        'Apply mosaic, blur, bars, or manual painting to one or many selected images.',
                    ],
                    steps: [
                        'Send images here from Gallery selection when you know which files need edits.',
                        'Choose a tool, adjust brush settings, and edit the current image in the center canvas.',
                        'Use auto-detection when you want the model to propose sensitive regions first.',
                        'Save all processed images only after checking your output format and metadata options.',
                    ],
                    features: [
                        'Queue view for batch work across multiple images.',
                        'Brush, pen, eraser, clone, zoom, reset, and detection settings.',
                        'Batch rename and output format controls before final save.',
                    ],
                    tips: [
                        'If the sidebar feels dense on smaller screens, finish one image at a time instead of trying to open every control at once.',
                        'For share-safe output, strip metadata unless you explicitly need to preserve it.',
                    ],
                },
                similar: {
                    icon: '🔍',
                    title: 'Similar Images',
                    purpose: [
                        'Find visually similar images or near-duplicates using embeddings.',
                    ],
                    steps: [
                        'Generate embeddings first if this is your first run or your library changed significantly.',
                        'Search by image ID when you already know the source image you want to compare.',
                        'Upload an image when the search source is outside the current library.',
                        'Use the duplicates panel to review very high-similarity pairs before deleting anything.',
                    ],
                    features: [
                        'Separate search and duplicate workflows in one tab.',
                        'Threshold control so you can tune how strict duplicate detection should be.',
                        'Useful for curation after a large generation session.',
                    ],
                    tips: [
                        'High thresholds are safer for duplicate cleanup. Lower thresholds are better for inspiration clustering.',
                        'Embeddings are model-generated features, so visually close images may still need a human check before deletion.',
                    ],
                },
                promptlab: {
                    icon: '🧪',
                    title: 'Prompt Helper',
                    purpose: [
                        'Turn your own library data into usable prompt drafts, recipes, comparisons, and reusable Random/Build starting points.',
                    ],
                    steps: [
                        'Start in Stats when you want to learn what tags, checkpoints, and examples are actually working in your library.',
                        'Use Compare when you want to extract common prompt structure from two images.',
                        'Use Build when you want a direct draft from an example image, recipe, or comparison result.',
                        'Use Random when you want the category builder, but seeded with useful data from your own library.',
                    ],
                    features: [
                        'Stats can jump straight into Gallery, Build, Reader, and Random.',
                        'Compare can build a draft from common prompt tokens instead of only showing a diff.',
                        'Build supports loading from images, scored examples, and recipe suggestions.',
                        'Random can now absorb top tags and recipe insights from your own library.',
                    ],
                    tips: [
                        'Treat Stats as a decision surface, not just a dashboard. If a card has an action button, use it.',
                        'Use Build for exact drafts and Random for variation. Switching between them is now part of the intended workflow.',
                    ],
                },
                artist: {
                    icon: '🎨',
                    title: 'Artist ID',
                    purpose: [
                        'Estimate likely artist or style labels across the image library and then drill back into Gallery with that filter applied.',
                    ],
                    steps: [
                        'Choose the model source and confidence threshold first.',
                        'Run identification for all images or only the selected set.',
                        'Review the aggregated artist cards and switch between grid and list if needed.',
                        'Open an artist and use the gallery filter shortcut to inspect those images in context.',
                    ],
                    features: [
                        'Batch identification with progress tracking.',
                        'Stats cards for total images, confident matches, unconfirmed candidates, no-match, and artist count.',
                        'Direct bridge back into Gallery filtering.',
                    ],
                    tips: [
                        'Treat this as an assistive classifier, not a guaranteed attribution tool.',
                        'Raise the threshold if too many weak guesses are cluttering the result list. The slider can only tighten; it cannot assert a name below the 0.20 high tier.',
                    ],
                },
            },
        },
        'zh-CN': {
            button: '指南',
            subtitle: '这个标签页能做什么，以及应该怎么用',
            close: '关闭',
            closeAria: '关闭指南',
            tour: '🎓 重新开始引导',
            tourTitle: '从头重新开始新手引导',
            refreshI18n: '🔄 重新载入界面文字',
            refreshI18nTitle: '重新拉取 lang/*.js，不会清空扫描结果、筛选或选择',
            refreshI18nDone: '界面文字已刷新，资料没有被清空。',
            refreshI18nFailed: '刷新失败，请直接按 F5 重新整理整页。',
            sections: {
                purpose: '这个标签页的用途',
                steps: '使用步骤',
                features: '主要功能',
                tips: '实用建议',
            },
            tabs: {
                gallery: {
                    icon: '🖼️',
                    title: '图库',
                    purpose: [
                        '在一个地方浏览所有已扫描图片，查看提示词与元数据，并先用筛选器缩小范围，再决定移动、导出或编辑。',
                    ],
                    steps: [
                        '先用“扫描文件夹”把图片导入数据库。',
                        '使用生成器标签、筛选器与排序，先把图库收敛到真正想处理的图片。',
                        '打开单张图片，查看提示词、标签、LoRA、参数，以及不同提示词格式的转换结果。',
                        '需要批量导出、送去打码编辑或执行其他批量操作时，再打开“选择图片”。',
                    ],
                    features: [
                        '支持多种浏览模式，适合密集浏览、大图预览或瀑布流查看。',
                        '筛选摘要会明确告诉你当前结果是如何被筛出来的。',
                        '图片预览弹窗里可以直接复制提示词、参数与其他元数据。',
                        '可快速访问随机图片、标签库和批量选择工具。',
                    ],
                    tips: [
                        '当筛选条件越来越多时，直接打开筛选编辑器比只看摘要更清楚。',
                        '如果预览弹窗里能切换提示词格式，复制前先确认转换后的格式是否真的是你要用的版本。',
                    ],
                },
                reader: {
                    icon: '📖',
                    title: '读图',
                    purpose: [
                        '深入查看单张图片（包括不在图库里的图片），读取完整 SD 元数据、编辑生成信息，并使用隐私 / 混淆工具。',
                    ],
                    steps: [
                        '从图库预览（元数据 / 信息）打开，或把任意图片拖入 / 粘贴到这里。',
                        '查看解析出的 prompt、负面、模型、sampler 等生成参数。',
                        '用「编辑元数据」修改字段，再用「另存为新图片」写出一份副本。',
                        '切到「隐私工具」可对图片进行编码（保护）或解码（还原）。',
                    ],
                    features: [
                        '支持拖放、文件选择、剪贴板粘贴读取外部图片。',
                        '完整的元数据编辑器，可另存为新图片。',
                        '隐私 / 混淆工作台（编码、解码、批量）。',
                    ],
                    tips: [
                        '快速看元数据用图库预览弹窗即可；需要编辑、读取库外图片或用隐私工具时再来这里。',
                    ],
                },
                dataset: {
                    icon: '📦',
                    title: '数据集',
                    purpose: [
                        '把一批图片做成可直接训练 LoRA 的数据集：导入、打标（booru 标签 + 自然语言描述）、整理 caption，再把图片和对应的 .txt 一起导出到同一个文件夹。',
                    ],
                    steps: [
                        '导入：从图库选择或文件夹路径添加图片（第 1 步）。',
                        '工作台：逐张查看图片，编辑 booru 标签和自然语言 caption，用 Smart Tag（WD14 + VLM）或自动打标来填充（第 2 步）。',
                        '导出：选择 caption 格式和输出文件夹，导出 图片 + .txt 成对文件（第 3 步）。',
                    ],
                    features: [
                        '双框 caption 编辑器：booru 标签（带 danbooru 分组配色）+ 自然语言描述。',
                        'Smart Tag 把 WD14 打标和 VLM 描述打包，可选触发词（trigger word）。',
                        '批量标签编辑器，可对整组做查找替换、增删、清理。',
                    ],
                    tips: [
                        '训练特定角色或画风时，导出前先设好 trigger word。',
                        '用标签置信度面板在导出前剔除低置信度标签。',
                    ],
                },
                autosep: {
                    icon: '📁',
                    title: '自动分类',
                    purpose: [
                        '把所有符合自动分类筛选条件的图片一次性移动到目标文件夹，而且不会再污染图库或手动排序的筛选状态。',
                    ],
                    steps: [
                        '先在这里打开筛选器，建立这次自动分类真正要用的规则。',
                        '如果这是会重复使用的移动规则，先把它保存成配置。',
                        '填写目标文件夹后，先运行“预览结果”。',
                        '确认数量、样例图片和目标路径都正确，再执行真正的移动。',
                    ],
                    features: [
                        '现在有独立筛选状态，不需要担心自动分类把图库筛选弄乱。',
                        '已保存配置会同时记住筛选条件和目标路径。',
                        '正式移动前会先展示样例图片与统计数量。',
                        '支持生成器、分级、标签、提示词、模型、尺寸和美学分数筛选。',
                    ],
                    tips: [
                        '每次大批量移动前都先预览，这仍然是发现误筛最快的方法。',
                        '把重复工作保存成配置，比如“高分 NAI 立绘”或“WebUI 待归档”，后面会省很多时间。',
                    ],
                },
                manual: {
                    icon: '🎮',
                    title: '手动排序',
                    purpose: [
                        '使用槽位整理（WASD）、A/B 擂台或留 / 汰来审核图片，同时保留独立的手动排序筛选状态。',
                    ],
                    steps: [
                        '开始前先选择槽位整理（WASD）、A/B 擂台或留 / 汰。',
                        '使用槽位整理时，设置 W、A、S、D 对应的文件夹。',
                        '使用 A/B 擂台时设置胜者合集；使用留 / 汰时设置两个结果合集。',
                        '需要的话，先用手动排序筛选器缩小工作集，只处理这一轮真正想看的图片。',
                        '开始后按照当前模式显示的快捷键卡操作。',
                        '结合缩略图小地图和进度统计，随时掌握哪些已经处理、哪些还没处理。',
                    ],
                    features: [
                        '槽位整理提供四方向文件夹映射，适合保留、删除、精选、待复查等高速流程。',
                        'A/B 擂台逐对比较，直到选出唯一胜者，同时不会移动源文件。',
                        '留 / 汰逐张记录决定，并把两种结果分别送入指定合集。',
                        '支持撤销和重做，避免一次误按毁掉整轮排序。',
                        '检测到未完成会话时可直接恢复。',
                        '状态栏、小地图与快捷键卡片适合长时间高频操作。',
                    ],
                    tips: [
                        '尽量保持每个方向的语义一致，这样速度会明显提升。',
                        '即使切换成中文，键盘映射仍然不变，真正决定效率的是肌肉记忆。',
                    ],
                },
                censor: {
                    icon: '🔳',
                    title: '打码编辑',
                    purpose: [
                        '对一张或多张图片应用马赛克、模糊、黑条或手动涂抹。',
                    ],
                    steps: [
                        '先从图库把要处理的图片送进这个标签页。',
                        '选择工具，调整画笔与样式，然后在中间画布上处理当前图片。',
                        '想让模型先帮你标出区域时，可以先执行自动检测。',
                        '全部检查完成后，再根据输出格式和元数据设置进行保存。',
                    ],
                    features: [
                        '支持批量队列，不需要一张一张重新打开。',
                        '提供画笔、钢笔、橡皮、克隆、缩放、重置和检测设置。',
                        '保存前可批量重命名并决定输出格式。',
                    ],
                    tips: [
                        '小分辨率或较窄屏幕下，优先专注当前图片，不要同时展开过多设置区。',
                        '如果是对外分享，除非确实需要，否则建议去掉元数据。',
                    ],
                },
                similar: {
                    icon: '🔍',
                    title: '相似图片',
                    purpose: [
                        '用嵌入向量查找视觉上相似的图片，或找出接近重复的图。',
                    ],
                    steps: [
                        '第一次使用或图库变化很大时，先生成嵌入向量。',
                        '已知源图片时，可直接按图片 ID 搜索。',
                        '源图片不在当前图库时，可上传图片进行搜索。',
                        '在重复图面板中，结合高相似度阈值人工确认后再删除。',
                    ],
                    features: [
                        '搜索和查重分成两个独立流程，逻辑更清楚。',
                        '可调阈值，便于控制重复图检测的严格程度。',
                        '特别适合大批量出图后的清理与归档。',
                    ],
                    tips: [
                        '高阈值更适合安全查重，低阈值更适合找风格接近的图。',
                        '向量结果只是辅助，真正删除前最好仍然做一次人工确认。',
                    ],
                },
                promptlab: {
                    icon: '🧪',
                    title: '提示词助手',
                    purpose: [
                        '把你自己的图库数据转成可直接使用的提示词草稿、配方、对比结果，以及可复用的 Random / Build 起手模板。',
                    ],
                    steps: [
                        '想先看规律时，从 Stats 开始，看看你的标签、模型和高分样例到底在做什么。',
                        '想抽出两张图的共同核心时，用 Compare。',
                        '想做精确草稿时，用 Build，从图片、配方或 Compare 结果直接起步。',
                        '想做变化版本时，用 Random，并把 Stats 里的洞察直接塞进去。',
                    ],
                    features: [
                        'Stats 里的卡片现在可以直接送去 Gallery、Build、Reader 和 Random。',
                        'Compare 不只是看 diff，还能直接拿共同 token 建草稿。',
                        'Build 可以直接吃图片、高分样例和配方建议。',
                        'Random 可以直接吸收你图库里的高价值标签和配方。',
                    ],
                    tips: [
                        '把 Stats 当成决策面板，而不是统计墙。只要卡片上有动作按钮，就说明这块数据可以直接拿来做下一步。',
                        'Build 适合精确草稿，Random 适合做变化版本，这两个模式现在本来就应该来回切换使用。',
                    ],
                },
                artist: {
                    icon: '🎨',
                    title: '画师识别',
                    purpose: [
                        '对图库中的图片做画师/风格预测，再一键回到图库按该结果继续筛图。',
                    ],
                    steps: [
                        '先选择模型来源和置信度阈值。',
                        '再决定是识别全部图片，还是只识别当前选中图片。',
                        '查看汇总结果卡片，必要时在网格和列表视图间切换。',
                        '点进某个画师后，可直接把这个结果应用到图库筛选里。',
                    ],
                    features: [
                        '支持批量识别，并带进度反馈。',
                        '顶部统计卡会显示总图片数、高置信匹配、未确认候选、没有匹配，以及画师数。',
                        '结果可直接回流到图库继续处理。',
                    ],
                    tips: [
                        '这更适合作为辅助分类，而不是严格的作者归属工具。',
                        '如果弱匹配太多，可以提高阈值来减少噪声。滑条只能收紧，无法把低于 0.20 高档的猜测当成识别结果。',
                    ],
                },
            },
        },
    };

    const TAB_SHORTCUTS = {
        en: {
            sectionTitle: 'Keyboard Shortcuts',
            global: [
                { key: '?', desc: 'Open keyboard shortcuts panel' },
                { key: 'Esc', desc: 'Close modal or cancel action' },
            ],
            gallery: [
                { key: 'G / L / W', desc: 'Switch to Grid / Large / Waterfall view' },
                { key: 'F', desc: 'Open filter editor' },
                { key: 'R', desc: 'Show a random image' },
                { key: 'S', desc: 'Toggle selection mode' },
                { key: '← →', desc: 'Navigate images in preview' },
                { key: 'Esc', desc: 'Clear selection' },
                { key: 'Del', desc: 'Remove selected from gallery' },
            ],
            autosep: [],
            manual: [
                { key: 'W / A / S / D', desc: 'Send image to one of four folders' },
                { key: 'Space', desc: 'Skip current image' },
                { key: 'Z', desc: 'Undo last move' },
                { key: 'Y', desc: 'Redo' },
                { key: '↑ ↓ ← →', desc: 'Same as W / S / A / D' },
            ],
            censor: [
                { key: 'B', desc: 'Brush tool' },
                { key: 'P', desc: 'Pen tool' },
                { key: 'E', desc: 'Eraser tool' },
                { key: 'G', desc: 'Clone mode' },
                { key: '[ / ]', desc: 'Decrease / increase brush size' },
                { key: 'H', desc: 'Toggle show changes' },
                { key: 'D', desc: 'Run detection' },
                { key: 'Ctrl+Z', desc: 'Undo' },
                { key: 'Ctrl+Shift+Z', desc: 'Redo' },
            ],
            similar: [],
            promptlab: [],
            artist: [],
        },
        'zh-CN': {
            sectionTitle: '键盘快捷键',
            global: [
                { key: '?', desc: '打开快捷键面板' },
                { key: 'Esc', desc: '关闭弹窗或取消操作' },
            ],
            gallery: [
                { key: 'G / L / W', desc: '切换 网格 / 大图 / 瀑布流 视图' },
                { key: 'F', desc: '打开筛选编辑器' },
                { key: 'R', desc: '随机显示一张图' },
                { key: 'S', desc: '切换选择模式' },
                { key: '← →', desc: '在预览中翻页' },
                { key: 'Esc', desc: '清除选择' },
                { key: 'Del', desc: '从图库移除已选图片' },
            ],
            autosep: [],
            manual: [
                { key: 'W / A / S / D', desc: '把图片送到四个文件夹之一' },
                { key: 'Space', desc: '跳过当前图片' },
                { key: 'Z', desc: '撤销上次移动' },
                { key: 'Y', desc: '重做' },
                { key: '↑ ↓ ← →', desc: '功能同 W / S / A / D' },
            ],
            censor: [
                { key: 'B', desc: '画笔工具' },
                { key: 'P', desc: '钢笔工具' },
                { key: 'E', desc: '橡皮擦' },
                { key: 'G', desc: '克隆模式' },
                { key: '[ / ]', desc: '缩小 / 放大画笔' },
                { key: 'H', desc: '切换显示修改' },
                { key: 'D', desc: '运行检测' },
                { key: 'Ctrl+Z', desc: '撤销' },
                { key: 'Ctrl+Shift+Z', desc: '重做' },
            ],
            similar: [],
            promptlab: [],
            artist: [],
        },
    };

    const TAB_ANCHORS = {
        // gallery: nav-bar #btn-help already covers gallery view (avoid duplicate ❓/❔ buttons)
        gallery: null,
        autosep: null,
        manual: null,
        censor: '#view-censor .censor-toolbar-v2',
        similar: '#view-similar .similar-header',
        promptlab: '#view-promptlab .promptlab-builder-header',
        artist: '#view-artist .results-header',
    };
