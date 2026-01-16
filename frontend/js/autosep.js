/**
 * SD Image Sorter - Auto-Separate Module
 * Handles batch filtering and moving of images
 */

const AutoSepState = {
    matchCount: 0
};

// ============== Initialization ==============

function initAutoSeparate() {
    // Use direct selectors to avoid timing issues with window.App
    const $ = (sel) => document.querySelector(sel);

    // Edit Filters button - opens unified filter modal
    const filterBtn = $('#btn-autosep-filters');
    if (filterBtn) {
        filterBtn.addEventListener('click', () => {
            if (window.App && window.App.openFilterModal) {
                window.App.openFilterModal();
            } else {
                console.error('openFilterModal not available');
            }
        });
    }

    // Preview button
    const previewBtn = $('#btn-preview-autosep');
    if (previewBtn) {
        previewBtn.addEventListener('click', updateAutoSepPreview);
    }

    // Execute button
    const executeBtn = $('#btn-execute-autosep');
    if (executeBtn) {
        executeBtn.addEventListener('click', executeAutoSeparate);
    }

    // Browse button for destination folder
    const browseBtn = $('#btn-browse-destination');
    if (browseBtn) {
        browseBtn.addEventListener('click', () => {
            const input = $('#autosep-destination');
            // Browser can't access filesystem directly, prompt user for path
            const currentPath = input ? input.value : '';
            const path = prompt('Enter destination folder path:\n\nExample: D:\\sorted\\my-folder', currentPath);
            if (path !== null && input) {
                input.value = path;
            }
        });
    }
}

// ============== Update Summary Display ==============

function updateAutoSepSummary() {
    const { $, AppState } = window.App;
    const f = AppState.filters;

    const allGens = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
    const allRatings = ['general', 'sensitive', 'questionable', 'explicit'];

    // Generators
    const genEl = $('#autosep-summary-generators');
    if (genEl) {
        genEl.textContent =
            f.generators?.length === allGens.length ? 'All' :
                !f.generators?.length ? 'None' :
                    f.generators.length > 2 ? `${f.generators.length} selected` : f.generators.join(', ');
    }

    // Tags
    const tagEl = $('#autosep-summary-tags');
    if (tagEl) {
        tagEl.textContent =
            !f.tags?.length ? 'None' :
                f.tags.length > 3 ? `${f.tags.length} tags` : f.tags.join(', ');
    }

    // Ratings
    const ratingEl = $('#autosep-summary-ratings');
    if (ratingEl) {
        ratingEl.textContent =
            f.ratings?.length === allRatings.length ? 'All' :
                !f.ratings?.length ? 'None' :
                    f.ratings.join(', ');
    }

    // Checkpoints
    const cpEl = $('#autosep-summary-checkpoints');
    if (cpEl) {
        cpEl.textContent =
            !f.checkpoints?.length ? 'None' :
                f.checkpoints.length > 2 ? `${f.checkpoints.length} selected` : f.checkpoints.join(', ');
    }

    // Loras
    const loraEl = $('#autosep-summary-loras');
    if (loraEl) {
        loraEl.textContent =
            !f.loras?.length ? 'None' :
                f.loras.length > 2 ? `${f.loras.length} selected` : f.loras.join(', ');
    }

    // Prompts
    const promptEl = $('#autosep-summary-prompts');
    if (promptEl) {
        promptEl.textContent =
            !f.prompts?.length ? 'None' :
                f.prompts.length > 2 ? `${f.prompts.length} prompts` : f.prompts.join(', ');
    }

    // Dimensions
    const dimEl = $('#autosep-summary-dimensions');
    if (dimEl) {
        const hasDimFilter = f.minWidth || f.maxWidth || f.minHeight || f.maxHeight || f.aspectRatio;
        if (!hasDimFilter) {
            dimEl.textContent = 'Any';
        } else {
            const parts = [];
            if (f.minWidth || f.maxWidth) parts.push(`W: ${f.minWidth || 0}-${f.maxWidth || '∞'}`);
            if (f.minHeight || f.maxHeight) parts.push(`H: ${f.minHeight || 0}-${f.maxHeight || '∞'}`);
            if (f.aspectRatio) parts.push(f.aspectRatio);
            dimEl.textContent = parts.join(', ') || 'Custom';
        }
    }
}

// ============== Preview ==============

async function updateAutoSepPreview() {
    const { $, API, AppState } = window.App;

    // Update summary display
    updateAutoSepSummary();

    const f = AppState.filters;

    // Check if any meaningful filters are set
    const hasFilters =
        (f.generators?.length > 0 && f.generators.length < 5) ||
        (f.tags?.length > 0) ||
        (f.ratings?.length > 0 && f.ratings.length < 4) ||
        (f.checkpoints?.length > 0) ||
        (f.loras?.length > 0) ||
        (f.prompts?.length > 0) ||
        f.minWidth || f.maxWidth || f.minHeight || f.maxHeight || f.aspectRatio;

    if (!hasFilters) {
        $('#autosep-preview .stat-number').textContent = '0';
        AutoSepState.matchCount = 0;
        return;
    }

    try {
        // Pass all filter types
        const result = await API.getImages({
            generators: f.generators?.length > 0 ? f.generators : null,
            tags: f.tags?.length > 0 ? f.tags : null,
            ratings: f.ratings?.length < 4 ? f.ratings : null,
            checkpoints: f.checkpoints?.length > 0 ? f.checkpoints : null,
            loras: f.loras?.length > 0 ? f.loras : null,
            prompts: f.prompts?.length > 0 ? f.prompts : null,
            minWidth: f.minWidth,
            maxWidth: f.maxWidth,
            minHeight: f.minHeight,
            maxHeight: f.maxHeight,
            aspectRatio: f.aspectRatio,
            limit: 10000
        });

        AutoSepState.matchCount = result.count;
        $('#autosep-preview .stat-number').textContent = result.count;

    } catch (error) {
        console.error('Failed to preview:', error);
    }
}

// ============== Execute ==============

async function executeAutoSeparate() {
    const { $, API, showToast, AppState } = window.App;

    const destination = $('#autosep-destination').value.trim();

    if (!destination) {
        showToast('Please enter a destination folder', 'error');
        return;
    }

    if (AutoSepState.matchCount === 0) {
        showToast('No images match the current filters', 'error');
        return;
    }

    const f = AppState.filters;

    try {
        // Build dimensions object
        const dimensions = {
            minWidth: f.minWidth,
            maxWidth: f.maxWidth,
            minHeight: f.minHeight,
            maxHeight: f.maxHeight,
            aspectRatio: f.aspectRatio
        };

        // Pass all filter types including prompts and dimensions
        const result = await API.batchMove(
            f.generators.length > 0 ? f.generators : null,
            f.tags.length > 0 ? f.tags : null,
            f.ratings.length < 4 ? f.ratings : null,
            destination,
            f.checkpoints?.length > 0 ? f.checkpoints : null,
            f.loras?.length > 0 ? f.loras : null,
            f.prompts?.length > 0 ? f.prompts : null,
            dimensions
        );

        showToast(`Moved ${result.count} images to ${destination}`, 'success');

        // Reset preview
        AutoSepState.matchCount = 0;
        $('#autosep-preview .stat-number').textContent = '0';

        // Refresh gallery if on that view
        if (window.loadImages) {
            window.loadImages();
        }

    } catch (error) {
        showToast('Failed to move images: ' + error.message, 'error');
    }
}

// ============== Initialize ==============

document.addEventListener('DOMContentLoaded', () => {
    initAutoSeparate();
});

// Export for use by app.js filter modal
window.updateAutoSepSummary = updateAutoSepSummary;
