/**
 * SD Image Sorter - Onboarding Tour
 * Interactive guided tour for new users
 */

const OnboardingTour = (function() {
    'use strict';

    const STORAGE_KEY = 'sd-image-sorter-onboarding-completed';
    const DISMISSED_KEY = 'sd-image-sorter-onboarding-dismissed-version';

    // Current tour version - increment when adding new features
    // v3 (v3.4.3): navigation step reflects direct advanced tabs with More fallback.
    const TOUR_VERSION = 2;

    // Tour step definitions (bilingual)
    const TOUR_STEPS_EN = [
        {
            id: 'welcome',
            title: 'Welcome to SD Image Sorter',
            content: `<p>This tool helps you organize, tag, and manage your Stable Diffusion generated images.</p>
                <ul>
                    <li>Scan folders for images with metadata</li>
                    <li>AI-powered tagging with WD14</li>
                    <li>Auto-separate by filters</li>
                    <li>Manual keyboard sorting (WASD)</li>
                    <li>Canvas-based censor editing</li>
                </ul>`,
            target: null,
            position: 'center'
        },
        {
            id: 'navigation-tabs',
            title: 'Navigation Tabs',
            content: `<p>The tabs read left-to-right as your workflow:</p>
                <ul>
                    <li><strong>Gallery</strong> - browse, filter and tag your images</li>
                    <li><strong>Read Image</strong> - inspect full metadata + privacy tools</li>
                    <li><strong>Organize</strong> - auto-separate by filter, or WASD manual sort</li>
                    <li><strong>Censor Edit</strong> - canvas-based censoring</li>
                    <li><strong>Find Similar</strong> - CLIP visual similarity &amp; duplicates</li>
                    <li><strong>Dataset</strong> - build a LoRA training set (caption + export)</li>
                </ul>
                <p><strong>Prompt Helper</strong> and <strong>Style Finder</strong> appear directly when there is room, or under <strong>More ▾</strong> on narrower screens.</p>`,
            target: '.nav-tabs',
            position: 'bottom'
        },
        {
            id: 'scan-folder',
            title: 'Scan Your Images',
            content: `<p>Click <strong>Import Images</strong> to load images from a folder.</p>
                <ul>
                    <li>Detect the generator (ComfyUI, NAI, WebUI, Forge)</li>
                    <li>Extract prompts, checkpoints, and LoRAs</li>
                    <li>Store metadata in a local database</li>
                </ul>`,
            target: '#btn-scan',
            position: 'bottom'
        },
        {
            id: 'setup',
            title: 'Settings & Models',
            content: `<p>Click the <strong>settings icon</strong> to manage app settings and AI models.</p>
                <ul>
                    <li>WD14 tagger for auto-tagging</li>
                    <li>CLIP for similar image search</li>
                    <li>NudeNet / YOLO for censor detection</li>
                </ul>
                <p>Models download on first use. Some need a restart after install.</p>`,
            target: '#btn-open-model-manager',
            position: 'bottom'
        },
        {
            id: 'complete',
            title: 'You\'re All Set!',
            content: `<p>The fastest way to start:</p>
                <p><button type="button" class="btn btn-primary" data-onboarding-action="quickstart"><svg class="icon" aria-hidden="true"><use href="#i-zap"/></svg> Quick Start — choose a folder to scan</button></p>
                <p class="helper-text">This opens the import dialog. After scanning, click <strong><svg class="icon" aria-hidden="true"><use href="#i-tag"/></svg> Tag</strong> to auto-tag with <strong>wd-swinv2</strong> — the balanced, recommended model, already selected for you.</p>
                <p>Or click anywhere outside this dialog to explore on your own.</p>`,
            target: null,
            position: 'center'
        }
    ];

    const TOUR_STEPS_ZH = [
        {
            id: 'welcome',
            title: '欢迎使用 SD Image Sorter',
            content: `<p>这个工具帮你整理、打标、管理 Stable Diffusion 生成的图片。</p>
                <ul>
                    <li>扫描文件夹，自动读取 SD 元数据</li>
                    <li>WD14 AI 自动打标</li>
                    <li>按筛选条件自动分类</li>
                    <li>WASD 键盘快速手动排序</li>
                    <li>画布式打码编辑</li>
                </ul>`,
            target: null,
            position: 'center'
        },
        {
            id: 'navigation-tabs',
            title: '导航标签',
            content: `<p>标签从左到右就是你的工作流程：</p>
                <ul>
                    <li><strong>图片库</strong> - 浏览、筛选、打标</li>
                    <li><strong>读图</strong> - 查看完整元数据 + 隐私工具</li>
                    <li><strong>整理图片</strong> - 按筛选自动分类，或 WASD 手动排序</li>
                    <li><strong>打码编辑</strong> - 画布式打码</li>
                    <li><strong>找相似</strong> - CLIP 视觉相似 &amp; 查重</li>
                    <li><strong>数据集</strong> - 制作 LoRA 训练集（caption + 导出）</li>
                </ul>
                <p>更多工具（提示词助手、画风识别）在 <strong>更多 ▾</strong> 菜单里。</p>`,
            target: '.nav-tabs',
            position: 'bottom'
        },
        {
            id: 'scan-folder',
            title: '扫描图片',
            content: `<p>点击 <strong>导入图片</strong> 选择一个图片文件夹。</p>
                <ul>
                    <li>自动识别生成器（ComfyUI、NAI、WebUI、Forge）</li>
                    <li>提取 prompt、checkpoint、LoRA 信息</li>
                    <li>元数据存入本地数据库</li>
                </ul>`,
            target: '#btn-scan',
            position: 'bottom'
        },
        {
            id: 'setup',
            title: '设置与模型',
            content: `<p>点击右上角 <strong><svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg> 设置图标</strong> 管理应用设置和 AI 模型。</p>
                <ul>
                    <li>WD14 打标模型</li>
                    <li>CLIP 相似图搜索模型</li>
                    <li>NudeNet / YOLO 打码检测模型</li>
                </ul>
                <p>模型首次使用时下载。部分功能安装后需要重启。</p>`,
            target: '#btn-open-model-manager',
            position: 'bottom'
        },
        {
            id: 'complete',
            title: '准备就绪！',
            content: `<p>最快的上手方式：</p>
                <p><button type="button" class="btn btn-primary" data-onboarding-action="quickstart"><svg class="icon" aria-hidden="true"><use href="#i-zap"/></svg> 快速开始 — 选择要扫描的文件夹</button></p>
                <p class="helper-text">这会打开导入对话框。扫描完后点 <strong><svg class="icon" aria-hidden="true"><use href="#i-tag"/></svg> 打标</strong>，即可用 <strong>wd-swinv2</strong>（已为你选好的均衡推荐模型）自动打标。</p>
                <p>或点击对话框外任意位置，自行探索。</p>`,
            target: null,
            position: 'center'
        }
    ];

    function _getSteps() {
        return window.I18n?.getLang?.() === 'zh-CN' ? TOUR_STEPS_ZH : TOUR_STEPS_EN;
    }

    // State
    let currentStepIndex = 0;
    let isActive = false;
    let overlayEl = null;
    let tooltipEl = null;
    let progressEl = null;
    let originalOverflow = '';

    function cleanupResidualTourUi() {
        document.querySelectorAll('.onboarding-overlay, .onboarding-tooltip').forEach((node) => {
            node.remove();
        });
        document.body.style.overflow = '';
    }

    /**
     * Check if onboarding has been completed
     * @returns {boolean}
     */
    function isCompleted() {
        const completed = localStorage.getItem(STORAGE_KEY);
        if (completed) {
            try {
                const data = JSON.parse(completed);
                return data.version >= TOUR_VERSION && data.completed === true;
            } catch (e) {
                return false;
            }
        }
        return false;
    }

    /**
     * Check if current version was dismissed
     * @returns {boolean}
     */
    function wasDismissed() {
        const dismissed = localStorage.getItem(DISMISSED_KEY);
        return dismissed && parseInt(dismissed, 10) >= TOUR_VERSION;
    }

    /**
     * Mark onboarding as completed
     */
    function markCompleted() {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            version: TOUR_VERSION,
            completed: true,
            completedAt: new Date().toISOString()
        }));
    }

    /**
     * Mark current version as dismissed
     */
    function markDismissed() {
        localStorage.setItem(DISMISSED_KEY, TOUR_VERSION.toString());
    }

    /**
     * Reset onboarding state (for testing or manual restart)
     */
    function resetState() {
        localStorage.removeItem(STORAGE_KEY);
        localStorage.removeItem(DISMISSED_KEY);
    }

    /**
     * Create the overlay element
     * @returns {HTMLElement}
     */
    function createOverlay() {
        const overlay = document.createElement('div');
        overlay.className = 'onboarding-overlay';
        overlay.innerHTML = `
            <div class="onboarding-highlight-container">
                <div class="onboarding-highlight"></div>
            </div>
        `;
        return overlay;
    }

    /**
     * Create the tooltip element
     * @returns {HTMLElement}
     */
    function createTooltip() {
        const isZh = window.I18n?.getLang?.() === 'zh-CN';
        const tooltip = document.createElement('div');
        tooltip.className = 'onboarding-tooltip';
        tooltip.setAttribute('role', 'dialog');
        tooltip.setAttribute('aria-labelledby', 'onboarding-title');
        tooltip.innerHTML = `
            <div class="onboarding-header">
                <h3 id="onboarding-title" class="onboarding-title"></h3>
                <button class="onboarding-lang" aria-label="Switch language" title="${isZh ? 'Switch to English' : '切换到中文'}"><svg class="icon" aria-hidden="true"><use href="#i-globe"/></svg></button>
                <button class="onboarding-skip" aria-label="${isZh ? '跳过引导' : 'Skip tour'}">
                    <span>${isZh ? '跳过' : 'Skip'}</span>
                </button>
            </div>
            <div class="onboarding-content"></div>
            <div class="onboarding-footer">
                <div class="onboarding-progress"></div>
                <div class="onboarding-actions">
                    <button class="btn btn-ghost onboarding-back" disabled>
                        <span>${isZh ? '上一步' : 'Back'}</span>
                    </button>
                    <button class="btn btn-primary onboarding-next">
                        <span>${isZh ? '下一步' : 'Next'}</span>
                    </button>
                </div>
            </div>
        `;
        return tooltip;
    }

    /**
     * Update progress indicators
     */
    function updateProgress() {
        const progressContainer = tooltipEl.querySelector('.onboarding-progress');
        progressContainer.innerHTML = '';

        _getSteps().forEach((step, index) => {
            const dot = document.createElement('span');
            dot.className = 'onboarding-progress-dot';
            if (index < currentStepIndex) {
                dot.classList.add('completed');
            } else if (index === currentStepIndex) {
                dot.classList.add('active');
            }
            dot.setAttribute('aria-label', `Step ${index + 1}`);
            progressContainer.appendChild(dot);
        });
    }

    /**
     * Position the tooltip relative to the target element
     * @param {HTMLElement} targetEl - Target element to highlight
     * @param {string} position - Preferred position (top, bottom, left, right, center)
     */
    function positionTooltip(targetEl, position) {
        const highlight = overlayEl.querySelector('.onboarding-highlight');

        if (!targetEl || position === 'center') {
            // Center mode - hide highlight, center tooltip
            highlight.style.display = 'none';
            tooltipEl.classList.add('onboarding-center');
            tooltipEl.style.top = '50%';
            tooltipEl.style.left = '50%';
            tooltipEl.style.transform = 'translate(-50%, -50%)';
            return;
        }

        highlight.style.display = 'block';
        tooltipEl.classList.remove('onboarding-center');

        // Get target position
        const targetRect = targetEl.getBoundingClientRect();
        const tooltipRect = tooltipEl.getBoundingClientRect();

        // Position highlight around target
        const padding = 8;
        window.PopupPosition?.setFixedRect(highlight, {
            top: targetRect.top - padding,
            left: targetRect.left - padding,
            width: targetRect.width + padding * 2,
            height: targetRect.height + padding * 2,
        });

        // Position tooltip
        const gap = 16;
        let top, left;

        switch (position) {
            case 'top':
                top = targetRect.top - tooltipRect.height - gap;
                left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
                break;
            case 'bottom':
                top = targetRect.bottom + gap;
                left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
                break;
            case 'left':
                top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
                left = targetRect.left - tooltipRect.width - gap;
                break;
            case 'right':
                top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
                left = targetRect.right + gap;
                break;
            default:
                top = targetRect.bottom + gap;
                left = targetRect.left;
        }

        // Keep tooltip within viewport
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        if (left < 20) left = 20;
        if (left + tooltipRect.width > viewportWidth - 20) {
            left = viewportWidth - tooltipRect.width - 20;
        }
        if (top < 80) top = 80; // Below nav bar
        if (top + tooltipRect.height > viewportHeight - 20) {
            top = viewportHeight - tooltipRect.height - 20;
        }

        window.PopupPosition?.setFixedRect(tooltipEl, { top, left });
        tooltipEl.style.transform = 'none';

        // Scroll target into view if needed
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    /**
     * Show a specific step
     * @param {number} index - Step index
     */
    function showStep(index) {
        if (index < 0 || index >= _getSteps().length) return;

        currentStepIndex = index;
        const step = _getSteps()[index];

        // Update tooltip content
        const titleEl = tooltipEl.querySelector('.onboarding-title');
        const contentEl = tooltipEl.querySelector('.onboarding-content');

        titleEl.textContent = step.title;
        contentEl.innerHTML = step.content;

        // MODELS-01: the final step offers a "Quick Start" CTA that does the
        // choosing for a new user — finish the tour and open the Import/Scan
        // dialog so the only decision left is picking a folder. Tagging then
        // defaults to the recommended wd-swinv2 model, so no model choice is
        // forced on a beginner.
        const quickStartBtn = contentEl.querySelector('[data-onboarding-action="quickstart"]');
        if (quickStartBtn) {
            quickStartBtn.addEventListener('click', (e) => {
                e.preventDefault();
                complete();
                // Let the overlay tear down before opening the scan modal.
                setTimeout(() => {
                    document.getElementById('btn-scan')?.click();
                }, 150);
            });
        }

        // Update buttons
        const backBtn = tooltipEl.querySelector('.onboarding-back');
        const nextBtn = tooltipEl.querySelector('.onboarding-next');

        backBtn.disabled = index === 0;
        const isZh = window.I18n?.getLang?.() === 'zh-CN';
        nextBtn.querySelector('span').textContent = index === _getSteps().length - 1 ? (isZh ? '完成' : 'Finish') : (isZh ? '下一步' : 'Next');

        // Update progress
        updateProgress();

        // Find target element and position
        let targetEl = null;
        if (step.target) {
            targetEl = document.querySelector(step.target);
        }

        // Small delay to allow DOM updates
        requestAnimationFrame(() => {
            positionTooltip(targetEl, step.position);
        });
    }

    /**
     * Go to next step
     */
    function nextStep() {
        if (currentStepIndex < _getSteps().length - 1) {
            showStep(currentStepIndex + 1);
        } else {
            complete();
        }
    }

    /**
     * Go to previous step
     */
    function prevStep() {
        if (currentStepIndex > 0) {
            showStep(currentStepIndex - 1);
        }
    }

    /**
     * Start the tour
     */
    function start() {
        if (isActive) return;

        isActive = true;
        currentStepIndex = 0;

        // Do NOT block body scroll — it makes the page unresponsive if
        // cleanup fails for any reason.

        // Create and append elements
        overlayEl = createOverlay();
        tooltipEl = createTooltip();

        document.body.appendChild(overlayEl);
        document.body.appendChild(tooltipEl);

        // Add event listeners
        const skipBtn = tooltipEl.querySelector('.onboarding-skip');
        const backBtn = tooltipEl.querySelector('.onboarding-back');
        const nextBtn = tooltipEl.querySelector('.onboarding-next');

        skipBtn.addEventListener('click', skip);
        backBtn.addEventListener('click', prevStep);
        nextBtn.addEventListener('click', nextStep);

        // Language toggle: switch language and restart tour with new language
        const langBtn = tooltipEl.querySelector('.onboarding-lang');
        if (langBtn) {
            langBtn.addEventListener('click', () => {
                const newLang = window.I18n?.getLang?.() === 'zh-CN' ? 'en' : 'zh-CN';
                if (window.I18n?.setLang) window.I18n.setLang(newLang);
                end();
                // Restart with new language
                setTimeout(() => start(), 100);
            });
        }

        // Allow clicking the overlay backdrop to dismiss the tour
        overlayEl.addEventListener('click', (e) => {
            if (e.target !== overlayEl && !e.target.classList.contains('onboarding-highlight-container')) return;
            overlayEl.style.pointerEvents = 'none';
            const target = document.elementFromPoint(e.clientX, e.clientY);
            overlayEl.style.pointerEvents = '';
            const navTarget = target?.closest?.('.nav-tab, .mobile-nav-item');
            skip();
            if (navTarget) navTarget.click();
        });

        // Keyboard navigation
        document.addEventListener('keydown', handleKeydown);

        // Show first step
        showStep(0);

        // Announce to screen readers
        tooltipEl.setAttribute('aria-live', 'polite');
    }

    /**
     * Handle keyboard navigation
     * @param {KeyboardEvent} e
     */
    function handleKeydown(e) {
        if (!isActive) return;

        switch (e.key) {
            case 'ArrowRight':
            case 'Enter':
                e.preventDefault();
                nextStep();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                prevStep();
                break;
            case 'Escape':
                e.preventDefault();
                skip();
                break;
        }
    }

    /**
     * Skip the tour
     */
    function skip() {
        markDismissed();
        end();
    }

    /**
     * Complete the tour
     */
    function complete() {
        markCompleted();
        end();
    }

    /**
     * End the tour (cleanup)
     */
    function end() {
        isActive = false;

        // Remove event listeners
        document.removeEventListener('keydown', handleKeydown);

        // Remove elements immediately — no animation delay that could leave
        // a blocking overlay if something goes wrong.
        if (overlayEl && overlayEl.parentNode) {
            overlayEl.parentNode.removeChild(overlayEl);
        }
        if (tooltipEl && tooltipEl.parentNode) {
            tooltipEl.parentNode.removeChild(tooltipEl);
        }

        overlayEl = null;
        tooltipEl = null;

        // Restore body scroll (safety — in case old code set it)
        document.body.style.overflow = '';
    }

    /**
     * Initialize — clean up any residual tour UI from a previous session.
     *
     * QA P3-4 (2026-07-11): the auto-start path is formally retired. It had
     * been dead for a while (the auto-start flag shipped disabled, and the
     * Aurora entry overlay covers the gallery on first run anyway); first-run
     * guidance is carried by the empty-state cards and the ❓ context guide.
     * The tour itself stays available via the guide modal's 🎓 Tour button
     * (OnboardingTour.start()).
     */
    function init() {
        cleanupResidualTourUi();
    }

    // Public API
    return {
        init,
        start,
        skip,
        complete,
        resetState,
        isCompleted,
        wasDismissed
    };
})();

// Export to window for backward compatibility
window.OnboardingTour = OnboardingTour;

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', OnboardingTour.init);
} else {
    OnboardingTour.init();
}
