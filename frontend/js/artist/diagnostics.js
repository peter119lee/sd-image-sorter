/**
 * artist/diagnostics.js — artist-ident.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/artist-ident.js
 * pre-cut lines 362-495 (of 1,171): syncSelectionActionState,
 * refreshAvailabilityState and loadDiagnostics (the runtime-health
 * banner + availability gating for the identify buttons). Classic
 * non-strict script: joins the ONE unsealed window.ArtistIdent object
 * declared in artist/core.js, which loads FIRST; artist/boot.js runs
 * the DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    syncSelectionActionState() {
        const identifySelectedBtn = document.getElementById('btn-identify-selected');
        if (!identifySelectedBtn) return;

        const hasSelection = this._getExplicitGallerySelectionIds().length > 0;
        const isAvailable = this.diagnostics ? this.diagnostics.available !== false : true;
        const disabled = this.isIdentifying || !hasSelection || !isAvailable;

        identifySelectedBtn.disabled = disabled;
        identifySelectedBtn.setAttribute('aria-disabled', String(disabled));

        if (this.isIdentifying) {
            identifySelectedBtn.dataset.dynamicTitle = 'true';
            identifySelectedBtn.title = this.tText(
                'Artist identification is already running',
                '画师识别已经在运行中'
            );
        } else if (!isAvailable) {
            identifySelectedBtn.dataset.dynamicTitle = 'true';
            identifySelectedBtn.title = this.tText(
                'Finish the setup in the status card above before identifying images.',
                '请先按上方状态卡完成准备，再开始识别。'
            );
        } else if (!hasSelection) {
            identifySelectedBtn.dataset.dynamicTitle = 'true';
            identifySelectedBtn.title = this.tText(
                'Select images in Gallery first',
                '请先在图库里选中图片'
            );
        } else {
            delete identifySelectedBtn.dataset.dynamicTitle;
            identifySelectedBtn.removeAttribute('title');
        }
    },

    refreshAvailabilityState() {
        const isAvailable = this.diagnostics ? this.diagnostics.available !== false : true;
        const identifyAllBtn = document.getElementById('btn-identify-all');
        const clearDataBtn = document.getElementById('btn-clear-artist-data');
        const controls = document.querySelector('#view-artist .artist-controls');

        controls?.classList.toggle('is-disabled', !isAvailable);

        if (identifyAllBtn) {
            const disabled = this.isIdentifying || !isAvailable;
            identifyAllBtn.disabled = disabled;
            identifyAllBtn.setAttribute('aria-disabled', String(disabled));
            if (!isAvailable) {
                identifyAllBtn.dataset.dynamicTitle = 'true';
                identifyAllBtn.title = this.tText(
                    'Finish the setup in the status card above before identifying images.',
                    '请先按上方状态卡完成准备，再开始识别。'
                );
            } else if (!this.isIdentifying) {
                delete identifyAllBtn.dataset.dynamicTitle;
                identifyAllBtn.removeAttribute('title');
            }
        }

        if (clearDataBtn) {
            clearDataBtn.disabled = this.isIdentifying;
            clearDataBtn.setAttribute('aria-disabled', String(this.isIdentifying));
            if (this.isIdentifying) {
                clearDataBtn.dataset.dynamicTitle = 'true';
                clearDataBtn.title = this.tText(
                    'Wait for identification to finish before clearing predictions.',
                    '请等待识别任务结束，再清空预测结果。'
                );
            } else if (clearDataBtn.dataset.dynamicTitle === 'true') {
                delete clearDataBtn.dataset.dynamicTitle;
                clearDataBtn.removeAttribute('title');
            }
        }

        this.syncSelectionActionState();
    },

    async loadDiagnostics() {
        const banner = document.getElementById('artist-model-health');
        if (!banner) return;

        try {
            const result = await window.App.API.get('/api/artists/diagnostics');
            this.diagnostics = result;

            const classes = ['model-health-banner', 'is-visible'];
            if (!result.available) {
                classes.push('model-health-banner-warning');
            }

            const title = result.available
                ? this.tText('Style Finder is ready', '画师识别已就绪')
                : this.tText('Style Finder needs setup first', '画师识别还需要先完成准备');
            const summary = result.available
                ? this.tText(
                    'You can start identification now, then review the strongest matches in the center panel.',
                    '现在可以开始识别，然后在中间结果区查看最强匹配。'
                )
                : this.tText(
                    'Finish the missing runtime or model setup first. Do not start a full library run before it is ready.',
                    '先补齐缺少的运行环境或模型，再回来开始识别。不要在没准备好时直接跑整库。'
                );
            const detailItems = [];
            if (result.message) detailItems.push(this.localizeDiagnosticsMessage(result.message));
            if (result.missing_dependencies?.length) {
                detailItems.push(`${this.tText('Missing dependencies', '缺少依赖')}: ${result.missing_dependencies.join(', ')}`);
            }
            if (result.runtime_note) detailItems.push(this.localizeDiagnosticsMessage(result.runtime_note));
            if (result.runtime_path) detailItems.push(`${this.tText('Runtime path', '运行时路径')}: ${result.runtime_path}`);
            if (result.checkpoint_path) detailItems.push(`${this.tText('Checkpoint path', '检查点路径')}: ${result.checkpoint_path}`);
            banner.className = classes.join(' ');
            // ENTRY-06: shared "needs setup -> open Model Manager" affordance,
            // reusing the global data-action="open-model-guidance" handler.
            const setupBtnHtml = result.available ? '' : `
                <button type="button" class="btn btn-secondary btn-small model-health-setup-btn" data-action="open-model-guidance">
                    <svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg> ${this._escapeHtml(this.tText('Open Setup / Download', '打开设置 / 下载模型'))}
                </button>
            `;
            banner.innerHTML = `
                <div class="model-health-copy">
                    <span class="model-health-title">${this._escapeHtml(title)}</span>
                    <span>${this._escapeHtml(summary)}</span>
                    ${detailItems.length ? `
                        <details class="model-health-details">
                            <summary>${this._escapeHtml(this.tText('Technical details', '技术细节'))}</summary>
                            <ul>${detailItems.map((item) => `<li>${this._escapeHtml(item)}</li>`).join('')}</ul>
                        </details>
                    ` : ''}
                    ${setupBtnHtml}
                </div>
            `;
            this.refreshAvailabilityState();
        } catch (e) {
            banner.className = 'model-health-banner is-visible model-health-banner-warning';
            banner.innerHTML = `
                <div class="model-health-copy">
                    <span class="model-health-title">${this._escapeHtml(this.tText('Style Finder needs setup first', '画师识别还需要先完成准备'))}</span>
                    <span>${this._escapeHtml(this.tText('Artist runtime status could not be loaded.', '画师识别运行状态无法加载。'))}</span>
                    <button type="button" class="btn btn-secondary btn-small model-health-setup-btn" data-action="open-model-guidance">
                        <svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg> ${this._escapeHtml(this.tText('Open Setup / Download', '打开设置 / 下载模型'))}
                    </button>
                </div>
            `;
            this.diagnostics = { available: false };
            this.refreshAvailabilityState();
        }
    },

});
