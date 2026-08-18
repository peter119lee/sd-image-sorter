(function () {
    'use strict';

    var state = {
        initialized: false,
        loading: false,
        loaded: false,
        data: null,
        reparse: {
            running: false,
            jobId: null,
            pollTimer: null
        }
    };

    var REPARSE_POLL_MS = 1200;

    // Every key db_facets.ISSUE_VOCABULARY publishes in issue_counts, in the
    // order the panel draws them. A published key with no bar here still feeds
    // actionable_count and still earns a recommendation card, so its number
    // reaches the page with nothing to trace it to; a key listed here that the
    // payload no longer publishes reads a permanent zero. Both happened at once
    // when missing_prompt / missing_checkpoint / unknown_generator moved to
    // statistics, so the two lists are now pinned to each other by
    // test_every_health_issue_bar_is_a_number_the_payload_actually_publishes.
    var ISSUE_KEYS = [
        'unreadable',
        'metadata_error',
        'metadata_pending',
        'missing_text',
        'sd_missing_checkpoint',
        'unattributed_sd_metadata',
        'missing_dimensions',
        'missing_file_size',
        'untagged',
        'missing_embedding',
        'missing_aesthetic'
    ];

    // Optional enrichment coverage rather than defects: the vocabulary declares
    // both as reported-only, so they stay on screen at zero to say the coverage
    // is complete instead of vanishing like a fixed problem.
    var COVERAGE_KEYS = ['missing_embedding', 'missing_aesthetic'];

    function $(selector) {
        return document.querySelector(selector);
    }

    function t(key, fallback, params) {
        var translated = window.I18n && typeof window.I18n.t === 'function'
            ? window.I18n.t(key, params)
            : key;
        return translated && translated !== key ? translated : (fallback || key);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatNumber(value) {
        var number = Number(value || 0);
        return Number.isFinite(number) ? number.toLocaleString() : '0';
    }

    function formatPercent(value) {
        var number = Number(value || 0);
        if (!Number.isFinite(number)) return '0%';
        return number.toFixed(number % 1 === 0 ? 0 : 1) + '%';
    }

    function formatSize(bytes) {
        if (window.App && typeof window.App.formatSize === 'function') {
            return window.App.formatSize(Number(bytes || 0));
        }
        var size = Number(bytes || 0);
        if (size < 1024) return size + ' B';
        if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
        if (size < 1024 * 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB';
        return (size / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
    }

    function setText(selector, value) {
        var element = $(selector);
        if (element) element.textContent = value;
    }

    function showEmpty(container, key, fallback) {
        if (!container) return;
        container.innerHTML = '<div class="health-empty">' + escapeHtml(t(key, fallback)) + '</div>';
    }

    function recommendationText(item) {
        var count = formatNumber(item && item.count);
        var key = item && item.kind;
        // One entry per recommendation kind db_facets can publish. A kind that
        // is missing here loses its count as well as its wording, because the
        // generic fallback interpolates nothing.
        var fallbackMap = {
            metadata_pending: 'Wait for metadata import to finish before trusting generator counts.',
            reparse_or_reconnect: 'Re-parse or reconnect unreadable records before moving files.',
            missing_text: 'Run Recover Missing Text on the images that have neither a prompt nor a caption.',
            sd_missing_checkpoint: 'Re-scan the generated folders so the model name is read back out of the files.',
            unattributed_sd_metadata: 'Re-parse the images that record generation data against no generator.',
            incomplete_scan_record: 'Re-scan the folder to restore file sizes and dimensions.',
            untagged: 'Run AI tagging to unlock safer filtering, sorting, and search.',
            duplicate_filenames: 'Duplicate filenames are risky for cache, tag export, and flat archive folders.'
        };
        return t('health.recommendation.' + key, fallbackMap[key] || 'Review this library signal.', { count: count });
    }

    function issueLabel(key) {
        var fallbackMap = {
            unreadable: 'Unreadable / missing files',
            metadata_error: 'Metadata parse errors',
            metadata_pending: 'Metadata still pending',
            missing_text: 'No prompt and no caption',
            sd_missing_checkpoint: 'Generated images missing checkpoint',
            unattributed_sd_metadata: 'Generation data with no generator',
            missing_dimensions: 'Missing dimensions',
            missing_file_size: 'Missing file size',
            untagged: 'Not AI-tagged',
            missing_embedding: 'No similarity embedding',
            missing_aesthetic: 'No aesthetic score'
        };
        return t('health.issue.' + key, fallbackMap[key] || key);
    }

    // Mirrors db_helpers.UNATTRIBUTED_GENERATORS: the ids metadata_parser records
    // when no SD tool claimed the image. A checkpoint is only expected of a row
    // some generator did claim, so this list is what decides whether an empty
    // checkpoint column is a gap or simply the truth about a downloaded picture.
    var UNATTRIBUTED_GENERATORS = ['unknown', 'others'];

    // Mirrors db_helpers.NO_GENERATOR_RECORDED_SQL's value set, which is narrower
    // than the negation of the list above: 'others' is a finished verdict
    // ("metadata was found, nothing recognised it"), while '' — NULL and blank
    // legacy rows, reached through TRIM(COALESCE(...)) — and 'unknown' mean no
    // attribution was ever recorded.
    var NO_GENERATOR_RECORDED = ['', 'unknown'];

    // Mirrors db_facets.SAMPLE_REASON_LADDER: the order the backend ranks a
    // listed row's defects in. Walking the same order is what makes the reason
    // column say why the row is on the list, instead of naming the first thing
    // that happens to look empty. Both directions were wrong here: a caption-only
    // row was reported as "Missing prompt" though issue_counts.missing_text
    // excludes it, and any row without a checkpoint was reported as "Missing
    // checkpoint" though sd_missing_checkpoint only counts rows a generator
    // claimed. Pinned by
    // test_the_attention_list_only_names_defects_the_audit_itself_counts.
    var SAMPLE_REASON_LADDER = [
        'unreadable',
        'metadata_error',
        'metadata_pending',
        'missing_text',
        'sd_missing_checkpoint',
        'unattributed_sd_metadata',
        'missing_dimensions',
        'untagged'
    ];

    // Rank 0 is absent on purpose: an unreadable row carries its own read_error,
    // which says more than any label could.
    var REASON_LABELS = {
        metadata_error: ['health.reason.metadataError', 'Metadata error'],
        metadata_pending: ['health.reason.metadataPending', 'Metadata pending'],
        missing_text: ['health.reason.missingText', 'No prompt and no caption'],
        sd_missing_checkpoint: ['health.reason.missingCheckpoint', 'Missing checkpoint'],
        unattributed_sd_metadata: [
            'health.reason.unattributedSdMetadata',
            'Generation data with no generator'
        ],
        missing_dimensions: ['health.reason.missingDimensions', 'Missing dimensions'],
        untagged: ['health.reason.untagged', 'Not tagged']
    };

    function isBlank(value) {
        return value == null || !String(value).trim();
    }

    function generatorId(sample) {
        return String(sample.generator == null ? '' : sample.generator).trim().toLowerCase();
    }

    // One predicate per rank, over the columns issue_samples actually ships.
    var REASON_TESTS = {
        // read_error stands in for is_readable = 0, which the payload does not
        // carry; mark_image_unreadable writes both together.
        unreadable: function (sample) {
            return !isBlank(sample.read_error);
        },
        metadata_error: function (sample) {
            return String(sample.metadata_status || '').trim().toLowerCase() === 'error';
        },
        metadata_pending: function (sample) {
            return String(sample.metadata_status || '').trim().toLowerCase() === 'pending';
        },
        missing_text: function (sample) {
            return isBlank(sample.prompt) && isBlank(sample.sidecar_caption);
        },
        sd_missing_checkpoint: function (sample) {
            return isBlank(sample.checkpoint_normalized)
                && !isBlank(sample.generator)
                && UNATTRIBUTED_GENERATORS.indexOf(generatorId(sample)) === -1;
        },
        // The payload ships two of the four columns HAS_SD_METADATA_SQL reads, so
        // a row whose only generation data is a negative prompt or a LoRA list
        // falls through to the honest fallback rather than being renamed.
        unattributed_sd_metadata: function (sample) {
            return NO_GENERATOR_RECORDED.indexOf(generatorId(sample)) !== -1
                && (!isBlank(sample.prompt) || !isBlank(sample.checkpoint_normalized));
        },
        missing_dimensions: function (sample) {
            return !sample.width || !sample.height;
        },
        untagged: function (sample) {
            return !sample.tagged_at;
        }
    };

    function sampleReason(sample) {
        if (!sample) return '';
        for (var index = 0; index < SAMPLE_REASON_LADDER.length; index++) {
            var key = SAMPLE_REASON_LADDER[index];
            if (!REASON_TESTS[key](sample)) continue;
            if (key === 'unreadable') return String(sample.read_error);
            return t(REASON_LABELS[key][0], REASON_LABELS[key][1]);
        }
        // The backend lists a row only when one of these ranks matches it, so
        // reaching here means the evidence is in a column issue_samples withholds.
        // Naming a defect anyway is the bug this ladder exists to stop.
        return t('health.reason.unnamed', 'Reason not available');
    }

    function renderStatus(data) {
        var summary = data.summary || {};
        var score = Number(summary.quality_score || 0);
        var ring = $('#health-score-ring');
        if (ring) ring.style.setProperty('--score', String(Math.max(0, Math.min(100, score))));
        setText('#health-score-value', Number.isFinite(score) ? score.toFixed(0) : '—');

        var titleKey = 'health.statusGoodTitle';
        var detailKey = 'health.statusGoodDetail';
        if ((summary.total_images || 0) <= 0) {
            titleKey = 'health.statusEmptyTitle';
            detailKey = 'health.statusEmptyDetail';
        } else if (score < 60) {
            titleKey = 'health.statusRiskTitle';
            detailKey = 'health.statusRiskDetail';
        } else if (score < 82) {
            titleKey = 'health.statusWatchTitle';
            detailKey = 'health.statusWatchDetail';
        }
        setText('#health-status-title', t(titleKey));
        setText('#health-status-detail', t(detailKey));
    }

    function renderKpis(data) {
        var summary = data.summary || {};
        setText('#health-total-images', formatNumber(summary.total_images));
        setText('#health-metadata-ready', formatPercent(summary.metadata_ready_percent));
        setText('#health-tag-coverage', formatPercent(summary.tagged_percent));
        setText('#health-actionable', formatNumber(summary.actionable_count));
    }

    function renderIssues(data) {
        var list = $('#health-issue-list');
        if (!list) return;
        var counts = data.issue_counts || {};
        var rows = ISSUE_KEYS.map(function (key) {
            return { key: key, count: Number(counts[key] || 0) };
        }).filter(function (item) {
            return item.count > 0 || COVERAGE_KEYS.indexOf(item.key) !== -1;
        });

        if (!rows.length) {
            showEmpty(list, 'health.noIssues', 'No quality issues found.');
            return;
        }

        var max = Math.max.apply(null, rows.map(function (item) { return item.count; }).concat([1]));
        list.innerHTML = rows.map(function (item) {
            var width = Math.max(4, Math.round((item.count / max) * 100));
            return '<div class="health-issue-row">'
                + '<div class="health-issue-meta"><span>' + escapeHtml(issueLabel(item.key)) + '</span><strong>' + formatNumber(item.count) + '</strong></div>'
                + '<div class="health-issue-bar"><span style="width:' + width + '%"></span></div>'
                + '</div>';
        }).join('');
    }

    function renderRecommendations(data) {
        var container = $('#health-recommendations');
        if (!container) return;
        var recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
        if (!recommendations.length) {
            showEmpty(container, 'health.noRecommendations', 'Nothing urgent. Keep importing and tagging normally.');
            return;
        }
        container.innerHTML = recommendations.map(function (item) {
            var severity = item.severity || 'info';
            return '<article class="health-recommendation ' + escapeHtml(severity) + '">'
                + '<span class="health-rec-dot"></span>'
                + '<p>' + escapeHtml(recommendationText(item)) + '</p>'
                + '</article>';
        }).join('');
    }

    function renderDuplicates(data) {
        var duplicateData = data.duplicate_filenames || {};
        var samples = Array.isArray(duplicateData.samples) ? duplicateData.samples : [];
        setText('#health-duplicate-summary', t('health.duplicateSummary', '{groups} groups • {images} images', {
            groups: formatNumber(duplicateData.groups || 0),
            images: formatNumber(duplicateData.images || 0)
        }));

        var container = $('#health-duplicate-list');
        if (!container) return;
        if (!samples.length) {
            showEmpty(container, 'health.noDuplicates', 'No duplicate filenames detected.');
            return;
        }
        container.innerHTML = samples.map(function (item) {
            return '<div class="health-row">'
                + '<span class="health-row-main">' + escapeHtml(item.filename || t('common.unknown', 'Unknown')) + '</span>'
                + '<span>' + formatNumber(item.count) + '×</span>'
                + '<span>' + formatSize(item.total_size) + '</span>'
                + '</div>';
        }).join('');
    }

    function renderFolders(data) {
        var container = $('#health-folder-list');
        if (!container) return;
        var folders = Array.isArray(data.top_folders) ? data.top_folders : [];
        if (!folders.length) {
            showEmpty(container, 'health.noFolders', 'No folder data yet.');
            return;
        }
        container.innerHTML = folders.map(function (item) {
            var folder = item.folder || t('health.rootFolder', 'Root / unknown folder');
            var issueText = t('health.folderIssues', '{missing} missing prompts • {untagged} untagged', {
                missing: formatNumber(item.missing_prompt || 0),
                untagged: formatNumber(item.untagged || 0)
            });
            return '<div class="health-row health-folder-row">'
                + '<span class="health-row-main" title="' + escapeHtml(folder) + '">' + escapeHtml(folder) + '</span>'
                + '<span>' + formatNumber(item.count) + '</span>'
                + '<span>' + formatSize(item.total_size) + '</span>'
                + '<small>' + escapeHtml(issueText) + '</small>'
                + '</div>';
        }).join('');
    }

    function renderSamples(data) {
        var container = $('#health-sample-list');
        if (!container) return;
        var samples = Array.isArray(data.issue_samples) ? data.issue_samples : [];
        if (!samples.length) {
            showEmpty(container, 'health.noSamples', 'No attention samples right now.');
            return;
        }
        container.innerHTML = samples.map(function (item) {
            var dimensions = item.width && item.height ? item.width + '×' + item.height : '—';
            return '<div class="health-row health-sample-row">'
                + '<span class="health-row-main" title="' + escapeHtml(item.path || '') + '">#' + escapeHtml(item.id) + ' ' + escapeHtml(item.filename || '') + '</span>'
                + '<span>' + escapeHtml(item.generator || 'unknown') + '</span>'
                + '<span>' + escapeHtml(dimensions) + '</span>'
                + '<span>' + escapeHtml(sampleReason(item)) + '</span>'
                + '</div>';
        }).join('');
    }

    function render(data) {
        state.data = data;
        renderStatus(data);
        renderKpis(data);
        renderIssues(data);
        renderRecommendations(data);
        renderDuplicates(data);
        renderFolders(data);
        renderSamples(data);
    }

    function setLoading(isLoading) {
        state.loading = isLoading;
        var button = $('#btn-health-refresh');
        if (button) {
            button.disabled = isLoading;
            button.classList.toggle('is-loading', isLoading);
        }
        if (isLoading && !state.loaded) {
            setText('#health-status-title', t('health.loadingTitle', 'Checking your library...'));
            setText('#health-status-detail', t('health.loadingDetail', 'This is read-only. No files will be moved, deleted, or rewritten.'));
        }
    }

    async function refresh() {
        if (state.loading) return;
        setLoading(true);
        try {
            var api = window.App && window.App.API;
            var data = api && typeof api.get === 'function'
                ? await api.get('/api/library-health?sample_limit=8')
                : await (window.apiFetch || fetch)('/api/library-health?sample_limit=8').then(function (response) {
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return response.json();
                });
            state.loaded = true;
            render(data || {});
            updateReparseVisibility();
        } catch (error) {
            setText('#health-status-title', t('health.failedTitle', 'Could not load library health'));
            setText('#health-status-detail', t('health.failedDetail', 'The audit endpoint failed. Try again after the current scan finishes.'));
            if (window.App && typeof window.App.showToast === 'function') {
                window.App.showToast(t('health.failedToast', 'Failed to load library health'), 'error');
            }
        } finally {
            setLoading(false);
        }
    }

    // ------------------------------------------------------------------
    // Metadata L3: re-parse missing-prompt images (raw envelopes + files)
    // ------------------------------------------------------------------

    function apiGet(url) {
        var api = window.App && window.App.API;
        if (api && typeof api.get === 'function') return api.get(url);
        return (window.apiFetch || fetch)(url).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        });
    }

    function toast(message, kind) {
        if (window.App && typeof window.App.showToast === 'function') {
            window.App.showToast(message, kind || 'info');
        }
    }

    function setReparseButton(running, progressText) {
        var button = $('#btn-metadata-reparse');
        var label = $('#metadata-reparse-label');
        if (!button) return;
        button.disabled = running;
        button.classList.toggle('is-loading', running);
        if (label) {
            label.textContent = running
                ? (progressText || t('health.reparseRunning', 'Recovering…'))
                : t('health.reparse', 'Recover Missing Text');
        }
    }

    // missing_prompt stays high forever for images that were never generated by
    // Stable Diffusion, so gating on it left a permanent button that recovers
    // nothing. missing_text ("no prompt AND no sidecar caption") is the counter
    // this job actually drives to zero.
    function updateReparseVisibility() {
        var button = $('#btn-metadata-reparse');
        if (!button) return;
        apiGet('/api/metadata/health').then(function (health) {
            var missing = health && health.totals ? Number(health.totals.missing_text || 0) : 0;
            button.hidden = !(missing > 0 || state.reparse.running);
            if (!state.reparse.running && missing > 0) {
                button.title = t('health.reparseTitle',
                    'Retry {count} images that have neither a prompt nor a caption (uses stored raw metadata, then the files and their .txt/.json sidecars).',
                    { count: formatNumber(missing) });
            }
        }).catch(function () {
            button.hidden = !state.reparse.running;
        });
    }

    function stopReparsePolling() {
        if (state.reparse.pollTimer) {
            clearTimeout(state.reparse.pollTimer);
            state.reparse.pollTimer = null;
        }
    }

    function finishReparse(job) {
        stopReparsePolling();
        state.reparse.running = false;
        state.reparse.jobId = null;
        setReparseButton(false);
        var result = (job && job.result) || {};
        var recovered = Number(result.recovered || 0);
        // Sidecar text lands in images.sidecar_caption, never in `prompt`, so a
        // run that recovers thousands of captions and no prompt is still a
        // success — reporting only `recovered` called it "0 recovered".
        var captions = Number(result.captions_recovered || 0);
        var foundSomething = recovered > 0 || captions > 0;
        var stillMissing = Number(result.still_missing || 0) + Number(result.missing_source || 0);
        if (job && job.status === 'done') {
            toast(t('health.reparseDone', 'Recovery finished: {recovered} prompts and {captions} sidecar captions recovered; {still} images still have no SD prompt.', {
                recovered: formatNumber(recovered),
                captions: formatNumber(captions),
                still: formatNumber(stillMissing)
            }), foundSomething ? 'success' : 'info');
        } else if (job && job.status === 'cancelled') {
            toast(t('health.reparseCancelled', 'Text recovery cancelled.'), 'info');
        } else {
            toast(t('health.reparseFailed', 'Text recovery failed.'), 'error');
        }
        refresh();
        updateReparseVisibility();
        // Recovered text changes gallery rows; let an open gallery refetch.
        if (foundSomething && window.App && typeof window.App.loadImages === 'function') {
            try { window.App.loadImages(); } catch (_e) { /* gallery view may be closed */ }
        }
    }

    function pollReparseJob() {
        if (!state.reparse.jobId) return;
        apiGet('/api/bulk-jobs/' + encodeURIComponent(state.reparse.jobId)).then(function (job) {
            if (!job || !job.status) throw new Error('no job');
            if (job.status === 'queued' || job.status === 'running') {
                var total = Number(job.total || 0);
                var processed = Number(job.processed || 0);
                setReparseButton(true, t('health.reparseRunningCount', 'Recovering… {processed}/{total}', {
                    processed: formatNumber(processed),
                    total: formatNumber(total)
                }));
                state.reparse.pollTimer = setTimeout(pollReparseJob, REPARSE_POLL_MS);
                return;
            }
            finishReparse(job);
        }).catch(function () {
            finishReparse(null);
        });
    }

    function startReparse() {
        if (state.reparse.running) return;
        state.reparse.running = true;
        setReparseButton(true);
        var api = window.App && window.App.API;
        var request = api && typeof api.post === 'function'
            ? api.post('/api/metadata/reparse', { scope: 'missing_prompt' })
            : (window.apiFetch || fetch)('/api/metadata/reparse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope: 'missing_prompt' })
            }).then(function (response) {
                if (response.status === 409) { var err = new Error('busy'); err.busy = true; throw err; }
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            });
        Promise.resolve(request).then(function (data) {
            if (!data || !data.job_id) throw new Error('no job id');
            state.reparse.jobId = data.job_id;
            pollReparseJob();
        }).catch(function (error) {
            state.reparse.running = false;
            setReparseButton(false);
            var isBusy = !!(error && (error.busy || error.apiStatus === 409 || /409/.test(String(error && error.message))));
            toast(isBusy
                ? t('health.reparseBusy', 'A text recovery run is already in progress.')
                : t('health.reparseFailed', 'Text recovery failed.'), isBusy ? 'info' : 'error');
        });
    }

    function reattachRunningReparse() {
        apiGet('/api/metadata/reparse-status').then(function (status) {
            if (status && status.active && status.job_id) {
                state.reparse.running = true;
                state.reparse.jobId = status.job_id;
                setReparseButton(true);
                var button = $('#btn-metadata-reparse');
                if (button) button.hidden = false;
                pollReparseJob();
            }
        }).catch(function () { /* endpoint unavailable — keep the button hidden */ });
    }

    function bind() {
        var refreshButton = $('#btn-health-refresh');
        if (refreshButton && refreshButton.dataset.healthBound !== '1') {
            refreshButton.dataset.healthBound = '1';
            refreshButton.addEventListener('click', refresh);
        }
        var reparseButton = $('#btn-metadata-reparse');
        if (reparseButton && reparseButton.dataset.healthBound !== '1') {
            reparseButton.dataset.healthBound = '1';
            reparseButton.addEventListener('click', startReparse);
        }
        // Keep the audit hero (eyebrow + subtitle) on screen the moment
        // the user expands the Dataset Audit details. Without this, the
        // <details> element opens at its current scroll position and the
        // first ~50px of the audit-hero — including the "Read-only
        // library audit" eyebrow — sits above the modal-content scroll
        // top, looking like the layout is broken.
        var auditSection = $('#audit-section');
        if (auditSection && auditSection.dataset.healthScrollBound !== '1') {
            auditSection.dataset.healthScrollBound = '1';
            auditSection.addEventListener('toggle', function () {
                if (!auditSection.open) return;
                try {
                    auditSection.scrollIntoView({ block: 'start', behavior: 'smooth' });
                } catch (_e) {
                    auditSection.scrollIntoView();
                }
            });
        }
    }

    function init() {
        if (!$('#audit-section') && !$('#health-score-ring')) return;
        bind();
        if (!state.loaded) refresh();
        if (!state.initialized) reattachRunningReparse();
        state.initialized = true;
    }

    document.addEventListener('languageChanged', function () {
        var auditOpen = (function () {
            var el = $('#audit-section');
            return el && el.tagName === 'DETAILS' ? el.open : false;
        })();
        if (state.data && auditOpen) render(state.data);
    });

    window.LibraryHealth = {
        init: init,
        refresh: refresh,
        render: render
    };
})();
