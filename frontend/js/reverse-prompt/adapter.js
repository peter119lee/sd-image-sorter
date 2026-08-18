/**
 * The mode adapter: one `await` for three modes that do not share a transport.
 *
 * All three modes the owner asked for already exist in the backend, but not
 * behind one response shape — and they cannot be made to share one:
 *
 *   tagger    POST /api/tag/single       synchronous, tags in the response body
 *   vlm       POST /api/smart-tag/start  a job: start, poll, then read results
 *   grounded  POST /api/smart-tag/start  the same job, both phases, tags handed
 *                                        to the vision model with the image
 *
 * Flattening the job into a synchronous backend route would mean holding a
 * request open across a queued lease for the shared inference runtime, and
 * nothing else in the app wants that. So the seam is here, on the client:
 * `runMode(mode, path)` resolves the same record either way and the caller
 * cannot tell which transport ran.
 *
 * The mode table is two booleans plus one switch, because that is exactly what
 * Smart Tag already exposes. `vlm_grounding` is the third mode: it is what
 * sends the tag list alongside the image so the vision model writes about what
 * the tagger actually found. It is the backend default and the page's default.
 *
 * Path-sourced items are yielded with `image_id = 0`, and the pipeline only
 * writes back for `image_id > 0` — so a run started from here cannot create or
 * touch a library row. `POST /api/tag/single` answers `stored: false` for the
 * same reason.
 */
'use strict';

Object.assign(window.ReversePrompt, {
    MODES: Object.freeze({
        grounded: { enable_wd14: true, enable_vlm: true, vlm_grounding: true },
        tagger: { enable_wd14: true, enable_vlm: false },
        vlm: { enable_wd14: false, enable_vlm: true },
    }),

    // Every status a Smart Tag job can hold except the two it is still alive
    // in ('queued' while the shared runtime is taken, 'running'), plus 'idle',
    // which is what /progress answers for a job it no longer knows.
    TERMINAL_JOB_STATUSES: Object.freeze(['completed', 'warning', 'failed', 'cancelled', 'idle']),

    JOB_POLL_MS: 900,

    /**
     * Run one mode over one file and resolve a single record shape:
     *
     *   { mode, path, prompt, tags, model, elapsedMs }
     *
     * `options.onProgress(snapshot)` is called for the polled modes only; a
     * caller that ignores it still gets identical behaviour, which is the
     * point of the adapter.
     */
    async runMode(mode, path, options = {}) {
        const flags = this.MODES[mode];
        if (!flags) throw new Error(`Unknown reverse-prompt mode: ${mode}`);
        const target = String(path || '').trim();
        if (!target) throw new Error('No image has been loaded yet.');

        const started = Date.now();
        const record = flags.enable_vlm
            ? await this._runAsJob(mode, target, flags, options)
            : await this._runSynchronously(mode, target, options);
        return {
            mode,
            path: target,
            prompt: String(record.prompt || '').trim(),
            tags: record.tags || [],
            model: String(record.model || ''),
            elapsedMs: Number.isFinite(record.elapsedMs) ? record.elapsedMs : Date.now() - started,
        };
    },

    /**
     * Tagger-only: one request, tags in the body, nothing stored.
     *
     * There is no job to cancel here, so Cancel is honoured by aborting the
     * request itself: the point of pressing it is that the guess must not
     * arrive, and a button that lets the abandoned answer land anyway is worse
     * than no button. The backend call is synchronous and finishes regardless
     * — nothing is written either way, since `/api/tag/single` answers
     * `stored: false`.
     */
    async _runSynchronously(mode, path, _options) {
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        this.state.abort = controller;
        if (this.state.cancelRequested) controller?.abort();
        const body = await window.App.API.post('/api/tag/single', {
            image_path: path,
            tagger_model: '',
        }, { signal: controller?.signal });
        const tags = Array.isArray(body?.all_tags) && body.all_tags.length
            ? body.all_tags
            : (body?.tags || []);
        return {
            prompt: this.tagsToPromptText(tags),
            tags,
            model: body?.model || '',
            elapsedMs: body?.elapsed_ms,
        };
    },

    /** The two vision-model modes: start the job, poll it, read its result. */
    async _runAsJob(mode, path, flags, options) {
        const payload = {
            image_paths: [path],
            enable_wd14: !!flags.enable_wd14,
            enable_vlm: !!flags.enable_vlm,
            // Nothing here is a training set: keep the caption verbatim rather
            // than merging it with library state that does not exist.
            merge_strategy: 'replace',
            skip_existing: false,
            training_purpose: 'general',
        };
        if (flags.vlm_grounding) payload.vlm_grounding = true;
        const profile = window.TargetModel?.captionProfileFor?.(this.currentTarget());
        if (profile) payload.caption_profile = profile;

        const snapshot = await window.App.API.post('/api/smart-tag/start', payload);
        const jobId = String(snapshot?.job_id || '');
        if (!jobId) throw new Error('The run started without a job id, so its result cannot be read.');
        this.state.jobId = jobId;

        const finished = await this._pollJob(jobId, snapshot, options);
        const page = await window.App.API.get(
            `/api/smart-tag/results?job_id=${encodeURIComponent(jobId)}&offset=0&limit=1`
        );
        const row = (page?.results || [])[0];
        const caption = String(row?.caption || '').trim();
        if (!caption) {
            // A run that produced no text is not a run that produced an empty
            // prompt. Reporting the second would be a false success.
            //
            // `message` must NOT be consulted here. On a terminal job that did
            // not fail, `jobs.py:_completion_message` sets it to the progress
            // summary — literally "Done. 1 ok, 0 failed." — and this branch is
            // reached exactly by a job that succeeded on paper: a per-image
            // provider error is swallowed into an empty caption by
            // `caption_phase.py` while the image is still counted as
            // succeeded. The reason, when the backend kept one, is in
            // `errors[]`. `message` still leads in `_pollJob`'s
            // failed/cancelled branch, where it really is the reason.
            const reason = String((finished?.errors || [])[0]?.error || '').trim();
            throw new Error(reason || 'This image produced no prompt.');
        }
        return {
            prompt: caption,
            tags: this.promptTextToTags(row?.booru_text),
            model: String(finished?.settings?.tagger_model || ''),
        };
    },

    async _pollJob(jobId, initialSnapshot, options) {
        let snapshot = initialSnapshot;
        const report = typeof options.onProgress === 'function' ? options.onProgress : null;
        if (report) report(snapshot);

        while (!this.TERMINAL_JOB_STATUSES.includes(String(snapshot?.status || ''))) {
            if (this.state.cancelRequested) {
                await window.App.API.post('/api/smart-tag/cancel', {}).catch(() => null);
                throw new Error('Cancelled.');
            }
            await new Promise((resolve) => setTimeout(resolve, this.JOB_POLL_MS));
            snapshot = await window.App.API.get(
                `/api/smart-tag/progress?job_id=${encodeURIComponent(jobId)}`
            );
            if (report) report(snapshot);
        }

        const status = String(snapshot?.status || '');
        if (status === 'failed' || status === 'cancelled') {
            const reason = String(snapshot?.message || '').trim()
                || String((snapshot?.errors || [])[0]?.error || '').trim();
            throw new Error(reason || `The run ended as ${status}.`);
        }
        return snapshot;
    },
});
