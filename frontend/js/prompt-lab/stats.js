/**
 * prompt-lab/stats.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 1873-2048 (of 2,485):
 * loadStats (/api/prompts/stats cards: top/high tags, checkpoints, leaders,
 * scored images, recipes) and the load-more sync/expand helpers.
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    async loadStats() {
        try {
            const statsQuery = new URLSearchParams({
                tag_limit: String(Math.max(this.statsVisibleCounts.topTags, 100)),
                high_tag_limit: String(Math.max(this.statsVisibleCounts.highTags, 100)),
                checkpoint_limit: String(Math.max(this.statsVisibleCounts.checkpoints, 30)),
                leader_limit: String(Math.max(this.statsVisibleCounts.bestCheckpoints, 24)),
                recipe_limit: String(Math.max(this.statsVisibleCounts.recipes, 24)),
                scored_limit: String(Math.max(this.statsVisibleCounts.scoredImages, 24)),
            });
            const stats = await window.App.API.get(`/api/prompts/stats?${statsQuery.toString()}`);
            this.lastStats = stats;
            document.getElementById('pl-total-images').textContent = stats.total_images || 0;
            document.getElementById('pl-scored-images').textContent = stats.scored_images || 0;
            document.getElementById('pl-avg-prompt-len').textContent = stats.prompt_length?.avg || 0;
            this._renderCaptionStat(stats.caption_length);

            const topTagsEl = document.getElementById('pl-top-tags');
            if (topTagsEl && stats.top_tags) {
                const visible = stats.top_tags.slice(0, this.statsVisibleCounts.topTags);
                const maxCount = stats.top_tags[0]?.count || 1;
                topTagsEl.innerHTML = visible.length
                    ? visible.map(t =>
                        `<div class="promptlab-tag-item">
                            <span class="tag-name">${escapeHtml(t.tag)}</span>
                            <div class="tag-bar"><div class="tag-bar-fill" style="width:${(t.count / maxCount * 100).toFixed(0)}%"></div></div>
                            <span class="tag-count">${t.pct}%</span>
                            <div class="promptlab-inline-actions">
                                <button class="btn btn-ghost btn-small" data-action="gallery-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.filterGallery', 'Filter Gallery')}</button>
                                <button class="btn btn-ghost btn-small" data-action="random-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.useInRandom', 'Use in Random')}</button>
                                <button class="btn btn-secondary btn-small" data-action="build-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.addToBuild', 'Add to Build')}</button>
                            </div>
                        </div>`
                    ).join('')
                    : this._renderStatsEmpty(this._t('promptlab.noTopTagsYet', 'Import more images to see your strongest recurring tags here.'));
            }

            const highEl = document.getElementById('pl-high-tags');
            if (highEl && stats.high_aesthetic_tags) {
                const maxH = stats.high_aesthetic_tags[0]?.count || 1;
                const visible = stats.high_aesthetic_tags.slice(0, this.statsVisibleCounts.highTags);
                highEl.innerHTML = stats.high_aesthetic_tags.length
                    ? visible.map(t =>
                        `<div class="promptlab-tag-item">
                            <span class="tag-name">${escapeHtml(t.tag)}</span>
                            <div class="tag-bar"><div class="tag-bar-fill" style="width:${(t.count / maxH * 100).toFixed(0)}%;background:#4A9D69;"></div></div>
                            <span class="tag-count">${t.count}</span>
                            <div class="promptlab-inline-actions">
                                <button class="btn btn-ghost btn-small" data-action="gallery-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.filterGallery', 'Filter Gallery')}</button>
                                <button class="btn btn-ghost btn-small" data-action="random-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.useInRandom', 'Use in Random')}</button>
                                <button class="btn btn-secondary btn-small" data-action="build-tag" data-tag="${escapeHtml(t.tag)}">${this._t('promptlab.addToBuild', 'Add to Build')}</button>
                            </div>
                        </div>`
                    ).join('')
                    : this._renderStatsEmpty(this._t('promptlab.noScoredImagesYet', 'No scored images yet'));
            }

            const cpEl = document.getElementById('pl-top-checkpoints');
            if (cpEl && stats.top_checkpoints) {
                const visible = stats.top_checkpoints.slice(0, this.statsVisibleCounts.checkpoints);
                cpEl.innerHTML = visible.length
                    ? visible.map(c => {
                        const name = c.name.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || c.name;
                        return `<div class="promptlab-tag-item"><span class="tag-name"><svg class="icon" aria-hidden="true"><use href="#i-cpu"/></svg> ${escapeHtml(name)}</span><span class="tag-count">${c.count}</span></div>`;
                    }).join('')
                    : this._renderCheckpointEmpty(stats, 'top_checkpoints_empty_reason');
            }

            const bestCheckpointEl = document.getElementById('pl-best-checkpoints');
            if (bestCheckpointEl) {
                const leaders = stats.checkpoint_score_leaders || [];
                bestCheckpointEl.innerHTML = leaders.length
                    ? leaders.slice(0, this.statsVisibleCounts.bestCheckpoints).map((entry) => {
                        const cleanName = entry.name.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || entry.name;
                        const metaText = entry.avg_score != null
                            ? `★ ${Number(entry.avg_score || 0).toFixed(2)} · ${entry.count} images`
                            : `${entry.count} images`;
                        const matchingRecipe = (stats.checkpoint_recipes || []).find(recipe => recipe.name === entry.name);
                        const recipeTags = Array.isArray(matchingRecipe?.tags) ? matchingRecipe.tags : [];
                        const recipePreview = recipeTags.slice(0, 8);
                        return `<div class="promptlab-action-card">
                            <div class="promptlab-action-title"><svg class="icon" aria-hidden="true"><use href="#i-cpu"/></svg> ${escapeHtml(cleanName)}</div>
                            <div class="promptlab-action-meta">${metaText}${recipePreview.length ? `<br>${escapeHtml(recipePreview.join(', '))}` : ''}</div>
                            <div class="promptlab-action-buttons">
                                <button class="btn btn-ghost btn-small" data-action="gallery" data-checkpoint="${escapeHtml(entry.name)}">${this._t('promptlab.filterGallery', 'Filter Gallery')}</button>
                                <button class="btn btn-secondary btn-small" data-action="random" data-checkpoint="${escapeHtml(entry.name)}" data-tags="${escapeHtml(recipeTags.join('|'))}">${this._t('promptlab.useInRandom', 'Use in Random')}</button>
                                <button class="btn btn-primary btn-small" data-action="build" data-checkpoint="${escapeHtml(entry.name)}" data-tags="${escapeHtml(recipeTags.join('|'))}">${this._t('promptlab.sendRecipeToBuild', 'Send to Build')}</button>
                            </div>
                        </div>`;
                    }).join('')
                    : this._renderCheckpointEmpty(stats, 'checkpoint_score_leaders_empty_reason');
            }

            const topScoredEl = document.getElementById('pl-top-scored-images');
            if (topScoredEl) {
                const examples = stats.top_scored_images || [];
                topScoredEl.innerHTML = examples.length
                    ? examples.slice(0, this.statsVisibleCounts.scoredImages).map((entry) => {
                        const cleanCheckpoint = entry.checkpoint
                            ? entry.checkpoint.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || entry.checkpoint
                            : '';
                        const promptPreview = escapeHtml(String(entry.prompt || '').slice(0, 120) || '');
                        return `<div class="promptlab-action-card promptlab-action-card-image">
                            <div class="promptlab-action-thumb">
                                <img src="${escapeHtml(this._getImageThumbUrl(entry.id, 320))}" alt="${escapeHtml(entry.filename || '')}" loading="lazy">
                            </div>
                            <div class="promptlab-action-main">
                                <div class="promptlab-action-title">${escapeHtml(entry.filename)} · <svg class="icon" aria-hidden="true"><use href="#i-star"/></svg> ${Number(entry.aesthetic_score || 0).toFixed(2)}</div>
                                <div class="promptlab-action-meta">${cleanCheckpoint ? `🧠 ${escapeHtml(cleanCheckpoint)}<br>` : ''}${promptPreview}</div>
                                <div class="promptlab-action-buttons">
                                    <button class="btn btn-primary btn-small" data-action="build" data-image-id="${entry.id}">${this._t('promptlab.openInBuild', 'Open in Build')}</button>
                                    <button class="btn btn-ghost btn-small" data-action="reader" data-image-id="${entry.id}" data-filename="${escapeHtml(entry.filename || '')}">${this._t('promptlab.openInReader', 'Open in Reader')}</button>
                                    <button class="btn btn-ghost btn-small" data-action="preview" data-image-id="${entry.id}">${this._t('promptlab.previewImage', 'Preview Image')}</button>
                                </div>
                            </div>
                        </div>`;
                    }).join('')
                    : this._renderStatsEmpty(this._t('promptlab.noScoredExamples', 'No scored examples yet'));
            }

            const recipeEl = document.getElementById('pl-recipe-suggestions');
            if (recipeEl) {
                const recipes = stats.checkpoint_recipes || [];
                recipeEl.innerHTML = recipes.length
                    ? recipes.slice(0, this.statsVisibleCounts.recipes).map((entry) => {
                        const cleanName = entry.name.replace(/\\/g, '/').split('/').pop()?.replace(/\.(safetensors|ckpt)$/i, '') || entry.name;
                        const tags = Array.isArray(entry.tags) ? entry.tags : [];
                        const tagPreview = tags.slice(0, 8);
                        const metaText = entry.avg_score != null
                            ? `★ ${Number(entry.avg_score || 0).toFixed(2)} · ${entry.count} images`
                            : `${entry.count} images`;
                        return `<div class="promptlab-action-card">
                            <div class="promptlab-action-title"><svg class="icon" aria-hidden="true"><use href="#i-flask"/></svg> ${escapeHtml(cleanName)}</div>
                            <div class="promptlab-action-meta">${metaText}<br>${escapeHtml(tagPreview.join(', '))}</div>
                            <div class="promptlab-action-buttons">
                                <button class="btn btn-secondary btn-small" data-action="gallery" data-checkpoint="${escapeHtml(entry.name)}" data-tags="${escapeHtml(tags.join('|'))}">${this._t('promptlab.tryRecipe', 'Try in Gallery')}</button>
                                <button class="btn btn-secondary btn-small" data-action="random" data-checkpoint="${escapeHtml(entry.name)}" data-tags="${escapeHtml(tags.join('|'))}">${this._t('promptlab.useInRandom', 'Use in Random')}</button>
                                <button class="btn btn-primary btn-small" data-action="build" data-checkpoint="${escapeHtml(entry.name)}" data-tags="${escapeHtml(tags.join('|'))}">${this._t('promptlab.sendRecipeToBuild', 'Send to Build')}</button>
                            </div>
                        </div>`;
                    }).join('')
                    : this._renderCheckpointEmpty(stats, 'checkpoint_recipes_empty_reason');
            }

            this._syncStatsLoadMore('pl-top-tags-more', stats.top_tags_total ?? stats.top_tags?.length ?? 0, this.statsVisibleCounts.topTags);
            this._syncStatsLoadMore('pl-high-tags-more', stats.high_aesthetic_tags_total ?? stats.high_aesthetic_tags?.length ?? 0, this.statsVisibleCounts.highTags);
            this._syncStatsLoadMore('pl-top-checkpoints-more', stats.top_checkpoints_total ?? stats.top_checkpoints?.length ?? 0, this.statsVisibleCounts.checkpoints);
            this._syncStatsLoadMore('pl-best-checkpoints-more', stats.checkpoint_score_leaders_total ?? (stats.checkpoint_score_leaders || []).length, this.statsVisibleCounts.bestCheckpoints);
            this._syncStatsLoadMore('pl-top-scored-images-more', stats.top_scored_images_total ?? (stats.top_scored_images || []).length, this.statsVisibleCounts.scoredImages);
            this._syncStatsLoadMore('pl-recipe-suggestions-more', stats.checkpoint_recipes_total ?? (stats.checkpoint_recipes || []).length, this.statsVisibleCounts.recipes);
        } catch (e) {
            (window.Logger?.error || console.error)('Failed to load prompt stats:', e);
            // Surface the failure instead of leaving a silently stale/empty
            // panel: inline note in the primary stats column + a toast.
            const failMsg = this._t('promptlab.statsLoadFailed', 'Could not load prompt stats. Please try again.');
            const topTagsEl = document.getElementById('pl-top-tags');
            if (topTagsEl) {
                topTagsEl.innerHTML = this._renderStatsEmpty(failMsg);
            }
            const toast = window.App?.showToast;
            if (typeof toast === 'function') {
                toast(typeof formatUserError === 'function' ? formatUserError(e, failMsg) : failMsg, 'error');
            }
        }
    },

    /**
     * The plain fact behind an empty checkpoint panel, from the reason the
     * backend measured. Returns null for a reason this build does not know, so
     * the caller can fall back rather than invent one.
     */
    _checkpointEmptyFact(reason, coverage) {
        switch (reason) {
            case 'no_checkpoint_metadata':
                return this._t('promptlab.emptyNoCheckpointMetadata',
                    'No image in this library records which checkpoint made it.');
            case 'checkpoint_metadata_only_on_missing_files':
                return this._t('promptlab.emptyCheckpointOnlyMissingFiles',
                    'The only images recording a checkpoint are missing from disk, so they are left out here.',
                    { count: Number(coverage.images_with_checkpoint_any || 0) });
            case 'no_scored_images':
                return this._t('promptlab.emptyNoScoredImages',
                    'Checkpoints are ranked by aesthetic score, and nothing here has been scored yet.');
            case 'not_enough_scored_images_per_checkpoint':
                return this._t('promptlab.emptyNotEnoughScoredPerCheckpoint',
                    'No checkpoint has reached the minimum number of scored images yet.',
                    {
                        min: Number(coverage.min_scored_images_per_checkpoint || 0),
                        scored: Number(coverage.scored_usable_images || 0),
                    });
            default:
                return null;
        }
    },

    /**
     * An empty checkpoint panel: the fact first and alone, then an offer only
     * when the backend established that one exists.
     *
     * `checkpoint_empty_action` is a separate answer to a separate question, and
     * a null there is a finding, not a gap: the same reason can arrive with the
     * scan offer (the user's generations are simply not indexed here) or without
     * it (they ARE indexed and recorded no model name, so scanning again is a
     * long operation that cannot change the answer). Inventing an offer for the
     * second case is the mistake the old single "import more prompt metadata"
     * line made for every case.
     */
    _renderCheckpointEmpty(stats, reasonField) {
        const coverage = stats?.checkpoint_coverage || {};
        const known = this._checkpointEmptyFact(stats?.[reasonField] || null, coverage);
        const fact = known || this._t('promptlab.noCheckpointDataYet', 'No checkpoint data to show yet.');
        // The offer rides on a fact we could state. Attaching it to the vague
        // fallback would suggest the scan addresses something we did not
        // establish, which is the same overreach in a quieter form.
        const offer = known && stats?.checkpoint_empty_action === 'scan_generated_images_folder'
            ? this._t('promptlab.emptyScanGeneratedImages',
                'If you do have a folder of your own generations, scanning it — or adding it as a separate library — is what fills these panels.')
            : '';
        return `<div class="promptlab-empty-note">
            <span class="promptlab-empty-fact">${escapeHtml(fact)}</span>
            ${offer ? `<span class="promptlab-empty-offer">${escapeHtml(offer)}</span>` : ''}
        </div>`;
    },

    /**
     * The caption statistic. `sample: 0` with the column present means no
     * caption has been recorded yet — not that none exists: the sidecars sit
     * next to the images and a rescan reads them, which is a real remedy that
     * happens to live outside the database. Printing 0 as the headline average
     * would state a measurement that was never taken, so the number is withheld
     * and the note carries the state instead.
     */
    _renderCaptionStat(caption) {
        const numberEl = document.getElementById('pl-avg-caption-len');
        const noteEl = document.getElementById('pl-avg-caption-note');
        if (!numberEl || !noteEl) return;
        const sample = Number(caption?.sample || 0);
        if (caption?.available === true && sample > 0) {
            numberEl.textContent = String(caption.avg ?? 0);
            noteEl.textContent = this._t('promptlab.captionFromSidecars',
                'from {sample} images with a .txt sidecar', { sample });
            return;
        }
        numberEl.textContent = '—';
        noteEl.textContent = caption?.available === true
            ? this._t('promptlab.captionNoneYet',
                'No captions recorded yet. A rescan reads the .txt files sitting next to your images.')
            : this._t('promptlab.captionNotTracked', 'This library has not recorded captions yet.');
    },

    _syncStatsLoadMore(buttonId, totalCount, visibleCount) {
        const button = document.getElementById(buttonId);
        if (!button) return;
        button.style.display = totalCount > visibleCount ? 'inline-flex' : 'none';
    },

    _expandStatsSection(key, step) {
        this.statsVisibleCounts[key] = (this.statsVisibleCounts[key] || 0) + step;
        this.loadStats();
    },

});
