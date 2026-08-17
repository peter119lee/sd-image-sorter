/**
 * Shared tag-category copy menu.
 */
(function () {
    const CATEGORY_GROUPS = [
        { id: 'appearance', labelKey: 'tagCategory.appearance', fallback: 'Appearance', icon: '◌', categories: ['character', 'body', 'expression'], saveCategory: 'body' },
        { id: 'clothing', labelKey: 'tagCategory.clothing', fallback: 'Clothing', icon: '▣', categories: ['outfit'], saveCategory: 'outfit' },
        { id: 'pose', labelKey: 'tagCategory.pose', fallback: 'Pose', icon: '↕', categories: ['pose', 'action', 'angle'], saveCategory: 'pose' },
        { id: 'scenery', labelKey: 'tagCategory.scenery', fallback: 'Scenery', icon: '△', categories: ['background'], saveCategory: 'background' },
        { id: 'style', labelKey: 'tagCategory.style', fallback: 'Style', icon: '✦', categories: ['style', 'artist'], saveCategory: 'style' },
        { id: 'qualityMeta', labelKey: 'tagCategory.qualityMeta', fallback: 'Quality / Meta', icon: '#', categories: ['quality', 'meta', 'rating'], saveCategory: 'quality' },
        { id: 'unclassified', labelKey: 'tagCategory.unclassified', fallback: 'Unclassified', icon: '?', categories: ['unknown'], saveCategory: 'unknown' },
    ];

    const CORE_BOARD_GROUPS = CATEGORY_GROUPS.filter((group) => (
        ['appearance', 'clothing', 'pose', 'scenery', 'unclassified'].includes(group.id)
    ));
    const KNOWN_CATEGORIES = new Set(CATEGORY_GROUPS.flatMap((group) => group.categories));
    const CATEGORY_ALIASES = {
        anatomy: 'body',
        camera: 'angle',
        clothes: 'outfit',
        clothing: 'outfit',
        composition: 'meta',
        copyright: 'character',
        environment: 'background',
        face: 'expression',
        general: 'unknown',
        medium: 'style',
        object: 'background',
        prop: 'background',
        subject: 'character',
        view: 'angle',
    };

    const PURPOSE_PRESETS = [
        { id: 'characterPrompt', labelKey: 'tagCategory.characterPrompt', fallback: 'Character / Appearance', icon: '◎', groupIds: ['appearance'] },
        { id: 'outfitPrompt', labelKey: 'tagCategory.outfitPrompt', fallback: 'Outfit prompt', icon: '▣', groupIds: ['clothing'] },
        { id: 'poseScenePrompt', labelKey: 'tagCategory.poseScenePrompt', fallback: 'Pose + Scene prompt', icon: '↕', groupIds: ['pose', 'scenery'] },
        { id: 'stylePrompt', labelKey: 'tagCategory.stylePrompt', fallback: 'Style prompt', icon: '✦', groupIds: ['style'] },
        { id: 'trainingCaption', labelKey: 'tagCategory.trainingCaption', fallback: 'Clean training caption', icon: 'TXT', groupIds: ['appearance', 'clothing', 'pose', 'scenery', 'style'] },
        { id: 'noQualityPrompt', labelKey: 'tagCategory.noQualityPrompt', fallback: 'Prompt without quality/meta', icon: '-#', groupIds: ['appearance', 'clothing', 'pose', 'scenery', 'style', 'unclassified'] },
    ];

    const FIND_GROUP_IDS = ['appearance', 'clothing', 'pose', 'scenery', 'style'];

    function t(key, fallback, params) {
        const value = window.I18n?.t?.(key, params);
        return value && value !== key ? value : fallback;
    }

    function escapeHtml(value) {
        if (window.escapeHtml) return window.escapeHtml(value);
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function normalizeTagValue(value) {
        return String(value ?? '')
            .trim()
            .replace(/^["']|["']$/g, '')
            .replace(/\s+/g, ' ');
    }

    function normalizePromptTagPreservingLoraDirectives(value) {
        const text = String(value ?? '')
            .trim()
            .replace(/^["']|["']$/g, '');
        const parts = [];
        const pattern = /<lora:[^<>]+>/gi;
        let cursor = 0;
        let match = pattern.exec(text);

        while (match) {
            parts.push(text.slice(cursor, match.index).replace(/\s+/g, ' '));
            parts.push(match[0]);
            cursor = match.index + match[0].length;
            match = pattern.exec(text);
        }

        parts.push(text.slice(cursor).replace(/\s+/g, ' '));
        return parts.join('');
    }

    function normalizeCategoryName(value) {
        const clean = normalizeTagValue(value || 'unknown').toLowerCase() || 'unknown';
        if (KNOWN_CATEGORIES.has(clean)) return clean;
        return CATEGORY_ALIASES[clean] || 'unknown';
    }

    function splitPromptTags(value) {
        const text = String(value || '');
        if (!text.trim()) return [];
        const parts = [];
        let current = '';
        let roundDepth = 0;
        let squareDepth = 0;
        let curlyDepth = 0;
        let angleDepth = 0;

        for (const char of text) {
            if (char === '(') roundDepth += 1;
            else if (char === ')' && roundDepth > 0) roundDepth -= 1;
            else if (char === '[') squareDepth += 1;
            else if (char === ']' && squareDepth > 0) squareDepth -= 1;
            else if (char === '{') curlyDepth += 1;
            else if (char === '}' && curlyDepth > 0) curlyDepth -= 1;
            else if (char === '<') angleDepth += 1;
            else if (char === '>' && angleDepth > 0) angleDepth -= 1;

            if (char === ',' && roundDepth === 0 && squareDepth === 0 && curlyDepth === 0 && angleDepth === 0) {
                const token = normalizePromptTagPreservingLoraDirectives(current);
                if (token) parts.push(token);
                current = '';
                continue;
            }
            current += char;
        }

        const last = normalizePromptTagPreservingLoraDirectives(current);
        if (last) parts.push(last);
        return parts;
    }

    function parsePromptTags(value) {
        return dedupeTags(splitPromptTags(value));
    }

    function getLoraDirectives(value) {
        return String(value || '').match(/<lora:[^<>]+>/gi) || [];
    }

    function stripLoraDirectives(tag) {
        return normalizeTagValue(String(tag).replace(/<lora:[^<>]+>/gi, ' '));
    }

    function getPromptCategoryTags(tags) {
        return dedupeTags(dedupeTags(tags).map(stripLoraDirectives).filter(Boolean));
    }

    function replaceUnderscoresOutsideLoraDirectives(tag) {
        return String(tag).replace(/<lora:[^<>]+>|_/gi, (match) => match === '_' ? ' ' : match);
    }

    function dedupeTags(tags) {
        const seen = new Set();
        const result = [];
        (tags || []).forEach((tag) => {
            const value = normalizeTagValue(typeof tag === 'object' ? (tag.tag || tag.name || tag.text) : tag);
            if (!value) return;
            const key = value.toLowerCase();
            if (seen.has(key)) return;
            seen.add(key);
            result.push(value);
        });
        return result;
    }

    function dedupePromptTagsPreservingLoraDirectives(tags) {
        const seen = new Set();
        const result = [];
        (tags || []).forEach((tag) => {
            const value = normalizePromptTagPreservingLoraDirectives(tag);
            if (!value) return;
            if (/<lora:[^<>]+>/i.test(value)) {
                result.push(value);
                return;
            }
            const key = value.toLowerCase();
            if (seen.has(key)) return;
            seen.add(key);
            result.push(value);
        });
        return result;
    }

    function parsePromptTagsPreservingLoraDirectives(value) {
        return dedupePromptTagsPreservingLoraDirectives(splitPromptTags(value));
    }

    async function getTagsFromSource(source = {}) {
        const directTags = dedupeTags(source.tags || []);
        if (directTags.length > 0) return directTags;

        const imageId = Number(source.imageId || source.image?.id);
        if (Number.isFinite(imageId) && imageId > 0 && window.App?.API?.getImage) {
            try {
                const result = await window.App.API.getImage(imageId);
                const fetchedTags = dedupeTags(result?.tags || result?.image?.tags || []);
                if (fetchedTags.length > 0) return fetchedTags;
                const promptTags = getPromptCategoryTags(parsePromptTags(result?.image?.prompt || source.image?.prompt || source.prompt || ''));
                if (promptTags.length > 0) return promptTags;
            } catch (_error) {
                // Fall through to prompt parsing.
            }
        }

        return getPromptCategoryTags(parsePromptTags(source.prompt || source.image?.prompt || ''));
    }

    async function classifyTags(tags) {
        const cleanTags = dedupeTags(tags);
        const byCategory = {};
        const tagCategory = {};
        CATEGORY_GROUPS.forEach((group) => {
            group.categories.forEach((category) => {
                byCategory[category] = [];
            });
        });

        if (cleanTags.length === 0) {
            return { tags: [], byCategory, tagCategory };
        }

        try {
            const result = await window.App?.API?.post?.('/api/prompts/categorize', cleanTags);
            (result?.results || []).forEach((item) => {
                const tag = normalizeTagValue(item?.tag);
                const category = normalizeCategoryName(item?.category);
                if (!tag) return;
                if (!byCategory[category]) byCategory[category] = [];
                byCategory[category].push(tag);
                tagCategory[tag.toLowerCase()] = category;
            });
        } catch (_error) {
            cleanTags.forEach((tag) => {
                byCategory.unknown = byCategory.unknown || [];
                byCategory.unknown.push(tag);
                tagCategory[tag.toLowerCase()] = 'unknown';
            });
        }

        cleanTags.forEach((tag) => {
            const key = tag.toLowerCase();
            if (tagCategory[key]) return;
            byCategory.unknown = byCategory.unknown || [];
            byCategory.unknown.push(tag);
            tagCategory[key] = 'unknown';
        });

        return { tags: cleanTags, byCategory, tagCategory };
    }

    function tagsForGroup(classified, group) {
        return dedupeTags(group.categories.flatMap((category) => classified.byCategory[category] || []));
    }

    function groupById(groupId) {
        return CATEGORY_GROUPS.find((group) => group.id === groupId) || null;
    }

    function tagsForGroupIds(classified, groupIds = []) {
        return dedupeTags((groupIds || []).flatMap((groupId) => {
            const group = groupById(groupId);
            return group ? tagsForGroup(classified, group) : [];
        }));
    }

    function buildPurposePrompt(classified, presetOrId) {
        const preset = typeof presetOrId === 'string'
            ? PURPOSE_PRESETS.find((item) => item.id === presetOrId)
            : presetOrId;
        if (!preset) return [];
        return tagsForGroupIds(classified, preset.groupIds);
    }

    function cleanPromptTags(tags, options = {}) {
        const cleaned = dedupeTags(tags).map((tag) => {
            const value = normalizeTagValue(tag);
            return options.spaces ? replaceUnderscoresOutsideLoraDirectives(value) : value;
        });
        return dedupeTags(cleaned);
    }

    function cleanPromptTagsPreservingLoraDirectives(tags, options = {}) {
        const cleaned = dedupePromptTagsPreservingLoraDirectives(tags).map((tag) => {
            const value = normalizePromptTagPreservingLoraDirectives(tag);
            return options.spaces ? replaceUnderscoresOutsideLoraDirectives(value) : value;
        });
        return dedupePromptTagsPreservingLoraDirectives(cleaned);
    }

    function groupForCategory(category) {
        const clean = String(category || 'unknown').toLowerCase();
        return CORE_BOARD_GROUPS.find((group) => group.categories.includes(clean)) || CORE_BOARD_GROUPS.find((group) => group.id === 'unclassified');
    }

    function copyTags(tags, message) {
        const text = dedupeTags(tags).join(', ');
        if (!text) {
            window.App?.showToast?.(t('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
            return Promise.resolve(false);
        }
        if (typeof window.App?.copyTextToClipboard === 'function') {
            return window.App.copyTextToClipboard(text, message);
        }
        return navigator.clipboard.writeText(text).then(() => {
            window.App?.showToast?.(message, 'success');
            return true;
        });
    }

    function findGalleryByTags(tags, label) {
        const cleanTags = dedupeTags(tags).slice(0, 24);
        if (cleanTags.length === 0) {
            window.App?.showToast?.(t('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
            return false;
        }
        if (typeof window.App?.applyTagFiltersFromExternal !== 'function') {
            window.App?.showToast?.(t('tagCategory.findUnavailable', 'Gallery filtering is not ready yet.'), 'error');
            return false;
        }
        return window.App.applyTagFiltersFromExternal(cleanTags, {
            replaceTags: true,
            tagMode: 'and',
            label,
        });
    }

    function appendSectionTitle(body, label) {
        const title = document.createElement('div');
        title.className = 'tag-category-copy-section-title';
        title.textContent = label;
        body.appendChild(title);
    }

    function removeMenu() {
        document.querySelector('.tag-category-copy-menu')?.remove();
    }

    function clampMenuToViewport(menu, left, top) {
        window.PopupPosition?.place(menu, {
            x: left,
            y: top,
            placement: 'point',
        });
    }

    function positionMenu(menu, options = {}) {
        const anchor = options.anchor || null;
        let left = Number(options.x);
        let top = Number(options.y);

        if ((!Number.isFinite(left) || !Number.isFinite(top)) && anchor?.getBoundingClientRect) {
            const rect = anchor.getBoundingClientRect();
            left = rect.left;
            top = rect.bottom + 6;
        }

        clampMenuToViewport(menu, left, top);
    }

    function buildMenuShell(options = {}) {
        removeMenu();
        const menu = document.createElement('div');
        menu.className = 'tag-category-copy-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = `
            <div class="tag-category-copy-title">${escapeHtml(options.title || t('tagCategory.copyOptions', 'Copy Options'))}</div>
            <div class="tag-category-copy-body">
                <div class="tag-category-copy-loading">${escapeHtml(t('common.loading', 'Loading...'))}</div>
            </div>
        `;
        document.body.appendChild(menu);
        positionMenu(menu, options);

        const closeOnOutside = (event) => {
            if (!menu.contains(event.target)) {
                removeMenu();
                document.removeEventListener('click', closeOnOutside);
                document.removeEventListener('keydown', closeOnEscape);
            }
        };
        const closeOnEscape = (event) => {
            if (event.key === 'Escape') {
                removeMenu();
                document.removeEventListener('click', closeOnOutside);
                document.removeEventListener('keydown', closeOnEscape);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeOnOutside);
            document.addEventListener('keydown', closeOnEscape);
        }, 0);

        return menu;
    }

    async function showMenu(options = {}) {
        const menu = buildMenuShell(options);
        const body = menu.querySelector('.tag-category-copy-body');
        const tags = await getTagsFromSource(options.source || options);
        const classified = await classifyTags(tags);

        if (!menu.isConnected) return null;

        const allCount = classified.tags.length;
        body.innerHTML = '';

        const allButton = document.createElement('button');
        allButton.type = 'button';
        allButton.className = 'tag-category-copy-item';
        allButton.innerHTML = `
            <span class="tag-category-copy-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-tag"/></svg></span>
            <span class="tag-category-copy-label">${escapeHtml(t('tagCategory.allTags', 'All Tags'))}</span>
            <span class="tag-category-copy-count">${allCount}</span>
        `;
        allButton.addEventListener('click', () => {
            copyTags(classified.tags, t('tagCategory.allCopied', 'Tags copied'));
            removeMenu();
        });
        body.appendChild(allButton);

        appendSectionTitle(body, t('tagCategory.purposePrompts', 'Purpose prompts'));
        PURPOSE_PRESETS.forEach((preset) => {
            const presetTags = buildPurposePrompt(classified, preset);
            const label = t(preset.labelKey, preset.fallback);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'tag-category-copy-item tag-category-copy-purpose';
            button.dataset.purpose = preset.id;
            button.innerHTML = `
                <span class="tag-category-copy-icon" aria-hidden="true">${escapeHtml(preset.icon)}</span>
                <span class="tag-category-copy-label">${escapeHtml(label)}</span>
                <span class="tag-category-copy-count">${presetTags.length}</span>
            `;
            button.addEventListener('click', () => {
                copyTags(
                    preset.id === 'trainingCaption' ? cleanPromptTags(presetTags, { spaces: false }) : presetTags,
                    t('tagCategory.purposeCopied', 'Copied {purpose}', { purpose: label }).replace('{purpose}', label)
                );
                removeMenu();
            });
            body.appendChild(button);
        });

        appendSectionTitle(body, t('tagCategory.copyByCategory', 'Copy by category'));
        CATEGORY_GROUPS.forEach((group) => {
            const groupTags = tagsForGroup(classified, group);
            const label = t(group.labelKey, group.fallback);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'tag-category-copy-item';
            button.dataset.group = group.id;
            button.innerHTML = `
                <span class="tag-category-copy-icon" aria-hidden="true">${escapeHtml(group.icon)}</span>
                <span class="tag-category-copy-label">${escapeHtml(label)}</span>
                <span class="tag-category-copy-count">${groupTags.length}</span>
            `;
            button.addEventListener('click', () => {
                copyTags(
                    groupTags,
                    t('tagCategory.groupCopied', 'Copied {category} tags', { category: label }).replace('{category}', label)
                );
                removeMenu();
            });
            body.appendChild(button);
        });

        appendSectionTitle(body, t('tagCategory.findSimilarByCategory', 'Find images by category'));
        FIND_GROUP_IDS.forEach((groupId) => {
            const group = groupById(groupId);
            if (!group) return;
            const groupTags = tagsForGroup(classified, group);
            const label = t(group.labelKey, group.fallback);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'tag-category-copy-item tag-category-copy-find';
            button.dataset.group = group.id;
            button.innerHTML = `
                <span class="tag-category-copy-icon" aria-hidden="true">⌕</span>
                <span class="tag-category-copy-label">${escapeHtml(label)}</span>
                <span class="tag-category-copy-count">${groupTags.length}</span>
            `;
            button.addEventListener('click', () => {
                findGalleryByTags(groupTags, label);
                removeMenu();
            });
            body.appendChild(button);
        });

        if (options.showTeach !== false && typeof window.PromptLab?.openCategoryBoard === 'function') {
            const separator = document.createElement('div');
            separator.className = 'tag-category-copy-separator';
            body.appendChild(separator);

            const teach = document.createElement('button');
            teach.type = 'button';
            teach.className = 'tag-category-copy-item';
            teach.innerHTML = `
                <span class="tag-category-copy-icon" aria-hidden="true">↔</span>
                <span class="tag-category-copy-label">${escapeHtml(t('tagCategory.teach', 'Teach categories'))}</span>
                <span class="tag-category-copy-count">${allCount}</span>
            `;
            teach.addEventListener('click', () => {
                if (allCount === 0) {
                    window.App?.showToast?.(t('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
                    return;
                }
                // View-switching handoffs close the image modal first (see
                // gallery.js _handleModalHandoff) — otherwise the fixed
                // z-index 3000 modal keeps covering the category board.
                const imageModal = document.getElementById('image-modal');
                if (imageModal?.classList.contains('visible')) {
                    const closeModal = window.App?.closeModal || window.closeModal;
                    closeModal?.('image-modal');
                }
                window.App?.switchView?.('promptlab');
                window.PromptLab.openCategoryBoard(classified.tags);
                removeMenu();
            });
            body.appendChild(teach);
        }

        positionMenu(menu, options);
        return { tags, classified };
    }

    window.TagCategoryCopy = {
        CATEGORY_GROUPS,
        CORE_BOARD_GROUPS,
        PURPOSE_PRESETS,
        buildPurposePrompt,
        classifyTags,
        cleanPromptTags,
        cleanPromptTagsPreservingLoraDirectives,
        copyTags,
        findGalleryByTags,
        getPromptCategoryTags,
        getTagsFromSource,
        getLoraDirectives,
        groupForCategory,
        parsePromptTags,
        parsePromptTagsPreservingLoraDirectives,
        replaceUnderscoresOutsideLoraDirectives,
        showMenu,
        tagsForGroup,
        tagsForGroupIds,
    };
}());
