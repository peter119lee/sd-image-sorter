/**
 * prompt-lab/build.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 2106-2472 (of 2,485):
 * populateBuildSelector + ensureBuildSourceOption (out-of-catalog handoff seam),
 * build drafts/recipes, the build category workbench (_useCheckedBuildCategories,
 * _copyBuildTrainingCaption, _cleanBuildPrompt), the random/gallery bridges
 * (_openRandomFromTokens/_filterGalleryByTags), and loadBuildSource.
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    populateBuildSelector() {
        const images = this._getPromptLabImages();
        const options = images.map(img =>
            `<option value="${img.id}">${escapeHtml(img.filename)}${img.aesthetic_score != null ? ' <svg class="icon" aria-hidden="true"><use href="#i-star"/></svg>' + img.aesthetic_score.toFixed(1) : ''}</option>`
        ).join('');
        const el = document.getElementById('pl-build-source');
        if (el) {
            const previousValue = el.value;
            el.innerHTML = `<option value="">${this._t('promptlab.selectTemplate', 'Select an image as template...')}</option>` + options;
            // innerHTML resets the selection; keep the current template alive
            // across the async catalog rebuild (including out-of-catalog
            // handoff options inserted by ensureBuildSourceOption).
            if (previousValue) {
                this.ensureBuildSourceOption(previousValue);
                el.value = previousValue;
            }
        }
        this._renderImagePreviewCard('pl-build-preview', el?.value || '', 'promptlab.buildPreviewEmpty', 'Choose a template image to see it here before loading the prompt.');
        if (!this.imageCatalogLoaded) {
            this._ensureImageCatalog().then(() => this.populateBuildSelector()).catch(() => {});
        }
    },

    // The Build template <select> only lists the newest-200 catalog, but
    // gallery/modal/similar handoffs can reference any library image. Insert
    // a one-off option so `select.value = id` does not silently reset to ''
    // (which hides the Build editor instead of loading the image).
    ensureBuildSourceOption(imageId, label = '') {
        const select = document.getElementById('pl-build-source');
        const numericId = Number(imageId);
        if (!select || !Number.isFinite(numericId) || numericId <= 0) return false;
        const value = String(numericId);
        if (Array.from(select.options).some((option) => option.value === value)) return true;
        const option = document.createElement('option');
        option.value = value;
        const record = this._getImageRecord(numericId);
        option.textContent = label
            || record?.filename
            || this._t('promptlab.imageOptionFallback', 'Image #{id}', { id: value }).replace('{id}', value);
        select.appendChild(option);
        return true;
    },

    _openBuildFromImageId(imageId) {
        window.App?.openPromptBuildFromImage?.(imageId);
    },

    _openBuildDraft(tokens) {
        const buildTab = document.querySelector('.promptlab-tab[data-mode="build"]');
        buildTab?.click();
        const editor = document.getElementById('pl-build-editor');
        const promptArea = document.getElementById('pl-build-prompt');
        const negativeArea = document.getElementById('pl-build-negative');
        const infoEl = document.getElementById('pl-build-info');
        if (editor) editor.style.display = '';
        if (promptArea) promptArea.value = (tokens || []).join(', ');
        if (negativeArea) negativeArea.value = '';
        if (infoEl) infoEl.textContent = this._t('promptlab.commonDraftLoaded', 'Loaded common prompt tokens into Build');
    },

    _openBuildRecipe(checkpoint, tokens) {
        const buildTab = document.querySelector('.promptlab-tab[data-mode="build"]');
        buildTab?.click();
        const editor = document.getElementById('pl-build-editor');
        const promptArea = document.getElementById('pl-build-prompt');
        const negativeArea = document.getElementById('pl-build-negative');
        const infoEl = document.getElementById('pl-build-info');
        if (editor) editor.style.display = '';
        if (promptArea) promptArea.value = (tokens || []).join(', ');
        if (negativeArea) negativeArea.value = '';
        if (infoEl) {
            infoEl.textContent = [
                checkpoint ? `🧠 ${checkpoint.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || checkpoint}` : '',
                this._t('promptlab.recipeLoaded', 'Loaded from recipe suggestion'),
            ].filter(Boolean).join(' · ');
        }
    },

    _appendBuildDraftTokens(tokens) {
        const buildTab = document.querySelector('.promptlab-tab[data-mode="build"]');
        buildTab?.click();
        const editor = document.getElementById('pl-build-editor');
        const promptArea = document.getElementById('pl-build-prompt');
        const infoEl = document.getElementById('pl-build-info');
        if (editor) editor.style.display = '';
        if (!promptArea) return;

        const currentTokens = String(promptArea.value || '')
            .split(',')
            .map((token) => token.trim())
            .filter(Boolean);
        const merged = [...new Set([...currentTokens, ...(tokens || []).filter(Boolean)])];
        promptArea.value = merged.join(', ');
        if (infoEl) infoEl.textContent = this._t('promptlab.tagDraftLoaded', 'Added selected data insight to Build');
    },

    _defaultBuildGroupIds() {
        return ['appearance', 'clothing', 'pose', 'scenery', 'style'];
    },

    _getBuildGroupLabel(group) {
        return this._t(group.labelKey, group.fallback);
    },

    async _prepareBuildCategoryWorkbench(imageId, image, tags = []) {
        const workbench = document.getElementById('pl-build-category-workbench');
        const copy = window.TagCategoryCopy;
        if (!workbench || !copy?.getTagsFromSource || !copy?.classifyTags) {
            this.buildCategoryState = null;
            return;
        }

        const sourceTags = await copy.getTagsFromSource({
            imageId,
            image,
            tags,
            prompt: image?.prompt || '',
        });
        const classified = await copy.classifyTags(sourceTags);
        const checked = new Set(this._defaultBuildGroupIds());
        this.buildCategoryState = { imageId: Number(imageId), classified, checked };
        this._renderBuildCategoryWorkbench();
    },

    _renderBuildCategoryWorkbench() {
        const workbench = document.getElementById('pl-build-category-workbench');
        const groupsContainer = document.getElementById('pl-build-category-groups');
        const countEl = document.getElementById('pl-build-category-count');
        const copy = window.TagCategoryCopy;
        if (!workbench || !groupsContainer || !copy?.CATEGORY_GROUPS || !this.buildCategoryState?.classified) {
            if (workbench) workbench.hidden = true;
            if (groupsContainer) groupsContainer.innerHTML = '';
            return;
        }

        const { classified, checked } = this.buildCategoryState;
        const groups = copy.CATEGORY_GROUPS
            .map((group) => ({ group, tags: copy.tagsForGroup(classified, group) }))
            .filter(({ tags }) => tags.length > 0);
        if (countEl) countEl.textContent = String(classified.tags?.length || 0);
        if (!groups.length) {
            workbench.hidden = true;
            groupsContainer.innerHTML = '';
            return;
        }

        workbench.hidden = false;
        groupsContainer.innerHTML = groups.map(({ group, tags }) => {
            const label = this._getBuildGroupLabel(group);
            const encodedGroup = this._safeDataValue(group.id);
            const isChecked = checked.has(group.id);
            return `
                <section class="promptlab-build-category-group" data-group="${encodedGroup}">
                    <div class="promptlab-build-category-group-head">
                        <label class="promptlab-build-category-toggle">
                            <input type="checkbox" data-build-category-check="${encodedGroup}" ${isChecked ? 'checked' : ''}>
                            <span>${this._escapeValue(label)}</span>
                            <span class="tag-category-copy-count">${tags.length}</span>
                        </label>
                        <span class="promptlab-build-category-mini-actions">
                            <button class="btn btn-ghost btn-small" type="button" data-build-category-copy="${encodedGroup}">${this._escapeValue(this._t('reader.copy', 'Copy'))}</button>
                            <button class="btn btn-ghost btn-small" type="button" data-build-category-find="${encodedGroup}">${this._escapeValue(this._t('tagCategory.find', 'Find'))}</button>
                        </span>
                    </div>
                    <div class="promptlab-build-category-chip-list">
                        ${tags.length ? tags.map((tag) => `<span class="promptlab-build-category-chip">${this._escapeValue(tag)}</span>`).join('') : `<span class="promptlab-build-category-empty">${this._escapeValue(this._t('promptlab.categoryBoardDropHere', 'Drop tags here'))}</span>`}
                    </div>
                </section>
            `;
        }).join('');

        groupsContainer.querySelectorAll('[data-build-category-check]').forEach((input) => {
            input.addEventListener('change', () => {
                const groupId = this._decodeDataValue(input.dataset.buildCategoryCheck);
                if (input.checked) checked.add(groupId);
                else checked.delete(groupId);
            });
        });
        groupsContainer.querySelectorAll('[data-build-category-copy]').forEach((button) => {
            button.addEventListener('click', () => {
                const groupId = this._decodeDataValue(button.dataset.buildCategoryCopy);
                const item = groups.find(({ group }) => group.id === groupId);
                if (!item) return;
                copy.copyTags(item.tags, this._t('tagCategory.groupCopied', 'Copied {category} tags', { category: this._getBuildGroupLabel(item.group) }).replace('{category}', this._getBuildGroupLabel(item.group)));
            });
        });
        groupsContainer.querySelectorAll('[data-build-category-find]').forEach((button) => {
            button.addEventListener('click', () => {
                const groupId = this._decodeDataValue(button.dataset.buildCategoryFind);
                const item = groups.find(({ group }) => group.id === groupId);
                if (!item) return;
                copy.findGalleryByTags(item.tags, this._getBuildGroupLabel(item.group));
            });
        });
    },

    _getBuildTagsForGroupIds(groupIds) {
        const copy = window.TagCategoryCopy;
        const classified = this.buildCategoryState?.classified;
        if (!copy?.tagsForGroupIds || !classified) return [];
        return copy.tagsForGroupIds(classified, groupIds);
    },

    _useCheckedBuildCategories() {
        const checked = Array.from(this.buildCategoryState?.checked || []);
        const groupIds = checked.length ? checked : this._defaultBuildGroupIds();
        const tags = this._getBuildTagsForGroupIds(groupIds);
        if (!tags.length) {
            window.App?.showToast?.(this._t('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
            return;
        }
        const promptArea = document.getElementById('pl-build-prompt');
        if (promptArea) promptArea.value = tags.join(', ');
        window.App?.showToast?.(this._t('promptlab.checkedCategoriesApplied', 'Applied checked categories to Build'), 'success');
    },

    _copyBuildTrainingCaption() {
        const tags = this._getBuildTagsForGroupIds(this._defaultBuildGroupIds());
        if (!tags.length) {
            window.App?.showToast?.(this._t('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
            return;
        }
        window.TagCategoryCopy?.copyTags?.(tags, this._t('tagCategory.trainingCaptionCopied', 'Training caption copied'));
    },

    async _cleanBuildPrompt(options = {}) {
        const promptArea = document.getElementById('pl-build-prompt');
        if (!promptArea) return;
        const copy = window.TagCategoryCopy;
        const loraDirectives = copy?.getLoraDirectives?.(promptArea.value) || [];
        const rawTags = copy?.parsePromptTagsPreservingLoraDirectives?.(promptArea.value) || this._parsePromptTags(promptArea.value);
        if (!rawTags.length) {
            window.App?.showToast?.(this._t('promptlab.addTagBeforeGenerate', 'Add at least one tag or apply a tag set before generating'), 'warning');
            return;
        }

        let nextTags = copy?.cleanPromptTagsPreservingLoraDirectives?.(
            rawTags,
            { spaces: Boolean(options.spaces) },
        ) || this._mergePromptTags(rawTags);
        if ((options.dropQuality || options.reorder) && copy?.classifyTags && copy?.tagsForGroupIds) {
            const promptCategoryTags = copy.getPromptCategoryTags?.(nextTags) || nextTags;
            const classified = await copy.classifyTags(promptCategoryTags);
            const orderedGroups = options.dropQuality
                ? [...this._defaultBuildGroupIds(), 'unclassified']
                : [...this._defaultBuildGroupIds(), 'qualityMeta', 'unclassified'];
            const orderedTags = copy.tagsForGroupIds(classified, orderedGroups);
            const orderedKeys = new Set(orderedTags.map((tag) => String(tag).toLowerCase()));
            const leftovers = options.dropQuality
                ? []
                : classified.tags.filter((tag) => !orderedKeys.has(String(tag).toLowerCase()));
            nextTags = [...orderedTags, ...leftovers, ...loraDirectives];
        }
        if (options.spaces) {
            nextTags = nextTags.map(copy.replaceUnderscoresOutsideLoraDirectives);
        }

        promptArea.value = nextTags.join(', ');
        window.App?.showToast?.(this._t('promptlab.promptCleaned', 'Prompt cleaned'), 'success');
    },

    _findCategoryForToken(token) {
        const normalized = String(token || '').trim().toLowerCase();
        if (!normalized) return null;

        for (const [category, tags] of Object.entries(this.categories || {})) {
            const match = (tags || []).find((tag) => String(tag || '').trim().toLowerCase() === normalized);
            if (match) {
                return { category, tag: match };
            }
        }

        return null;
    },

    async _openRandomFromTokens(tokens, checkpoint = '') {
        const randomTab = document.querySelector('.promptlab-tab[data-mode="random"]');
        randomTab?.click();

        const assigned = [];
        const unmatched = [];
        for (const token of tokens || []) {
            const match = this._findCategoryForToken(token);
            if (!match) {
                unmatched.push(token);
                continue;
            }
            if (!this.slots[match.category]) {
                this.slots[match.category] = [];
            }
            if (!this.slots[match.category].includes(match.tag)) {
                this.slots[match.category] = [...this.slots[match.category], match.tag];
                assigned.push(match.tag);
            }
        }

        this.invalidateGeneratedPrompt();
        this.renderCategoryBrowser();
        this.renderSlotBuilder();

        if (assigned.length) {
            await this.generate();
        }

        const checkpointLabel = checkpoint
            ? (checkpoint.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || checkpoint)
            : '';
        const info = [
            checkpointLabel ? `🧠 ${checkpointLabel}` : '',
            this._t('promptlab.randomDraftLoaded', 'Loaded data insight into Random'),
            assigned.length ? `${assigned.length}` : '',
        ].filter(Boolean).join(' · ');

        const outputEl = document.getElementById('promptlab-output');
        if (outputEl && info) {
            outputEl.title = info;
        }

        if (assigned.length && unmatched.length) {
            window.App?.showToast?.(
                this._t('promptlab.randomDraftLoadedPartial', 'Loaded {assigned} tags into Random. {unmatched} did not match known categories.', {
                    assigned: assigned.length,
                    unmatched: unmatched.length,
                })
                    .replace('{assigned}', assigned.length)
                    .replace('{unmatched}', unmatched.length),
                'info'
            );
            return;
        }

        if (assigned.length) {
            window.App?.showToast?.(
                this._t('promptlab.randomDraftLoadedFull', 'Loaded {count} tags into Random and generated a draft.', { count: assigned.length })
                    .replace('{count}', assigned.length),
                'success'
            );
            return;
        }

        window.App?.showToast?.(this._t('promptlab.randomDraftNoMatch', 'Could not map these insights into the Random categories yet.'), 'warning');
    },

    _filterGalleryByTags(tags) {
        window.App?.applyTagFiltersFromExternal?.(tags, { replaceTags: false, tagMode: 'and' });
    },

    async loadBuildSource(imageId) {
        const editor = document.getElementById('pl-build-editor');
        this._renderImagePreviewCard('pl-build-preview', imageId, 'promptlab.buildPreviewEmpty', 'Choose a template image to see it here before loading the prompt.');
        if (!imageId) {
            if (editor) editor.style.display = 'none';
            this.buildCategoryState = null;
            this._renderBuildCategoryWorkbench();
            return;
        }
        try {
            const result = await window.App.API.get(`/api/images/${imageId}`);
            const img = result.image;
            if (editor) editor.style.display = '';
            document.getElementById('pl-build-prompt').value = img.prompt || '';
            document.getElementById('pl-build-negative').value = img.negative_prompt || '';
            const info = [];
            if (img.checkpoint) info.push(`🧠 ${img.checkpoint.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '')}`);
            if (img.width && img.height) info.push(`📐 ${img.width}×${img.height}`);
            if (img.aesthetic_score != null) info.push(`★ ${Number(img.aesthetic_score).toFixed(1)}`);
            document.getElementById('pl-build-info').textContent = info.join(' · ') || '';
            await this._prepareBuildCategoryWorkbench(imageId, img, result.tags || img.tags || []);
        } catch (e) {
            this.buildCategoryState = null;
            this._renderBuildCategoryWorkbench();
            window.App?.showToast?.(this._t('promptlab.loadImageFailed', 'Failed to load image'), 'error');
        }
    },
});
