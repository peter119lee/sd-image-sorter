/**
 * SD Image Sorter - Auto-Separate Module
 * Handles batch filtering and moving of images
 */

const AutoSepState = {
    matchCount: 0
};

// ============== Initialization ==============

function initAutoSeparate() {
    const { $, API, showToast } = window.App;

    // Edit Filters button - opens unified filter modal
    // Note: Call window.App.openFilterModal directly (not destructured) to ensure 
    // it's available at click time, not init time
    $('#btn-autosep-filters')?.addEventListener('click', () => {
        if (window.App.openFilterModal) {
            window.App.openFilterModal();
        } else {
            console.error('openFilterModal not available');
        }
    });

    // Preview button
    $('#btn-preview-autosep')?.addEventListener('click', updateAutoSepPreview);

    // Execute button
    $('#btn-execute-autosep')?.addEventListener('click', executeAutoSeparate);
}

// ============== Update Summary Display ==============

function updateAutoSepSummary() {
    const { $, AppState } = window.App;
    const f = AppState.filters;

    const allGens = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
    const allRatings = ['general', 'sensitive', 'questionable', 'explicit'];

    $('#autosep-summary-generators').textContent =
        f.generators.length === allGens.length ? 'All' :
            f.generators.length === 0 ? 'None' :
                f.generators.length > 2 ? `${f.generators.length} selected` : f.generators.join(', ');

    $('#autosep-summary-tags').textContent =
        f.tags.length === 0 ? 'None' :
            f.tags.length > 3 ? `${f.tags.length} tags` : f.tags.join(', ');

    $('#autosep-summary-ratings').textContent =
        f.ratings.length === allRatings.length ? 'All' :
            f.ratings.length === 0 ? 'None' :
                f.ratings.join(', ');
}

// ============== Preview ==============

async function updateAutoSepPreview() {
    const { $, API, AppState } = window.App;

    // Update summary display
    updateAutoSepSummary();

    const f = AppState.filters;

    if (f.generators.length === 0 && f.tags.length === 0 && f.ratings.length === 4) {
        $('#autosep-preview .stat-number').textContent = '0';
        AutoSepState.matchCount = 0;
        return;
    }

    try {
        const result = await API.getImages({
            generators: f.generators.length > 0 ? f.generators : null,
            tags: f.tags.length > 0 ? f.tags : null,
            ratings: f.ratings.length < 4 ? f.ratings : null,
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
        const result = await API.batchMove(
            f.generators.length > 0 ? f.generators : null,
            f.tags.length > 0 ? f.tags : null,
            destination,
            f.ratings.length < 4 ? f.ratings : null
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
