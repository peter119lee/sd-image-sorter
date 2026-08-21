/**
 * The shared AI runtime, made visible.
 *
 * WD14 tagging, censor detection, similarity embedding, aesthetic scoring and
 * artist identification all take one lease from `ai_runtime_guard`, and the
 * Gallery's AI Tag job takes it from a spawned CHILD PROCESS. Two consequences
 * were invisible in the UI:
 *
 *   - `GET /api/system/ai-jobs` has published the live lease registry since
 *     v3.3.0 (label, tier, elapsed seconds, and the `stuck` flag the guard
 *     itself computes) and nothing read it, so "why is this slow" or "is
 *     something already running" had no answer on screen. The badge is that
 *     answer, and it shows only while something holds the runtime.
 *
 *   - A refused request comes back 409 with a structured `blocker`, and the
 *     frontend used only the raw sentence. That lost the one distinction that
 *     changes what the user should DO: a `process`-scope holder is the Gallery
 *     tag job in another process, cancelled from the Gallery's tagging bar,
 *     while a `thread`-scope holder is a job inside this server, which frees
 *     itself and is cancelled where it was started. `stale_lock_holder_gone` is
 *     neither - the owner is verifiably dead, so waiting is not advice and a
 *     restart is.
 *
 * Nothing here reports a capability or a queue position it cannot see: with no
 * blocker in the payload it says another process holds the runtime and names
 * nobody.
 */
(function () {
    'use strict';

    const ENDPOINT = '/api/system/ai-jobs';
    // Idle polling is slow because nothing is happening; while a lease is held
    // the elapsed counter is the point, so it ticks.
    const IDLE_POLL_MS = 6000;
    const BUSY_POLL_MS = 1500;

    const STATE = {
        timer: null,
        inFlight: false,
        snapshot: null,
    };

    function t(key, fallback, params) {
        const translated = window.I18n?.t?.(key, params);
        if (translated && translated !== key) return translated;
        let text = fallback;
        for (const [name, value] of Object.entries(params || {})) {
            text = text.split(`{${name}}`).join(String(value));
        }
        return text;
    }

    function seconds(value) {
        const n = Number(value);
        if (!Number.isFinite(n) || n < 0) return null;
        if (n < 90) return t('aiBusy.seconds', '{n}s', { n: Math.round(n) });
        return t('aiBusy.minutes', '{n}m', { n: Math.round(n / 60) });
    }

    function chip() {
        return document.getElementById('nav-ai-busy');
    }

    function render(snapshot) {
        const element = chip();
        if (!element) return;
        const jobs = Array.isArray(snapshot?.jobs) ? snapshot.jobs : [];
        if (jobs.length === 0) {
            element.hidden = true;
            element.classList.remove('is-stuck');
            return;
        }
        // The snapshot is longest-running first, which is also the job most
        // likely to be the one in the user's way.
        const lead = jobs[0];
        const elapsed = seconds(lead.elapsed_seconds);
        const label = document.getElementById('nav-ai-busy-label');
        const stuck = jobs.some((job) => job.stuck === true);
        if (label) {
            if (stuck) label.textContent = '!';
            else if (elapsed) label.textContent = elapsed;
            else label.textContent = '';
        }

        const lines = jobs.map((job) => {
            const each = seconds(job.elapsed_seconds);
            return each
                ? t('aiBusy.tooltipJob', '{label} — running {elapsed}', { label: job.label, elapsed: each })
                : String(job.label || '');
        });
        lines.unshift(t(
            'aiBusy.tooltipHead',
            'Using the AI runtime right now:',
            {}
        ));
        if (stuck) {
            lines.push(t(
                'aiBusy.tooltipStuck',
                'This has run long enough that the app treats it as stuck. If nothing is progressing, restart the app.',
                {}
            ));
        }
        element.title = lines.join('\n');
        element.setAttribute('aria-label', lines.join(' '));
        element.classList.toggle('is-stuck', stuck);
        element.hidden = false;
    }

    function schedule() {
        if (STATE.timer) clearTimeout(STATE.timer);
        const active = Array.isArray(STATE.snapshot?.jobs) && STATE.snapshot.jobs.length > 0;
        STATE.timer = setTimeout(poll, active ? BUSY_POLL_MS : IDLE_POLL_MS);
    }

    async function poll() {
        if (STATE.inFlight) return;
        STATE.inFlight = true;
        try {
            const response = await fetch(ENDPOINT, { headers: { Accept: 'application/json' } });
            if (response.ok) {
                STATE.snapshot = await response.json();
                render(STATE.snapshot);
            }
        } catch (_error) {
            // A status badge must never become the reason something looks
            // broken; an unreachable snapshot just leaves the last state.
        } finally {
            STATE.inFlight = false;
            schedule();
        }
    }

    async function refresh() {
        if (STATE.timer) clearTimeout(STATE.timer);
        STATE.inFlight = false;
        await poll();
    }

    function showDetails() {
        const jobs = Array.isArray(STATE.snapshot?.jobs) ? STATE.snapshot.jobs : [];
        if (jobs.length === 0) {
            window.App?.showToast?.(t('aiBusy.toastIdle', 'The AI runtime is free.', {}), 'info');
            return;
        }
        const element = chip();
        window.App?.showToast?.(
            (element?.title || '').replace(/\n/g, ' '),
            jobs.some((job) => job.stuck === true) ? 'warning' : 'info'
        );
    }

    // ---- 409 refusals ---------------------------------------------------

    function isBusyError(error) {
        const data = error && typeof error === 'object' ? error.apiData : null;
        return !!(data && data.type === 'AiRuntimeBusyError');
    }

    /** One or two sentences: who holds the runtime, then the only thing that is
     *  true about reaching it. */
    function explain(data) {
        const payload = data || {};
        const blocker = payload.blocker || null;
        const label = blocker?.label || null;
        const elapsed = seconds(blocker?.elapsed_seconds);

        if (payload.reason === 'stale_lock_holder_gone') {
            return label
                ? t(
                    'aiBusy.staleNamed',
                    'The AI runtime is still locked, but {label}, which claimed it, is no longer running. Restart the app to clear the lock.',
                    { label }
                )
                : t(
                    'aiBusy.stale',
                    'The AI runtime is still locked by a job that is no longer running. Restart the app to clear the lock.',
                    {}
                );
        }

        if (!label) {
            return t(
                'aiBusy.busyUnnamed',
                'Another process is using the AI runtime, and it did not say what it is. Only one AI job can run at a time, so try again once it finishes.',
                {}
            );
        }

        const fact = elapsed
            ? t('aiBusy.busyFact', '{label} is using the AI runtime (running {elapsed}).', { label, elapsed })
            : t('aiBusy.busyFactNoTime', '{label} is using the AI runtime.', { label });

        const remedy = blocker.scope === 'process'
            ? t(
                'aiBusy.busyProcess',
                'It runs as a separate process, so stop it from the tagging bar in the Gallery, or wait for it.',
                {}
            )
            : t(
                'aiBusy.busyThread',
                'It runs inside the app and releases the runtime when it finishes; you can also cancel it where you started it.',
                {}
            );
        return `${fact} ${remedy}`;
    }

    function init() {
        const element = chip();
        element?.addEventListener('click', showDetails);
        // A refusal is fresher evidence than any poll, so read the registry
        // again the moment one arrives instead of leaving a stale idle badge.
        document.addEventListener('ai-runtime-busy', () => { refresh(); });
        document.addEventListener('languageChanged', () => {
            if (STATE.snapshot) render(STATE.snapshot);
        });
        poll();
    }

    window.AiBusy = { refresh, explain, isBusyError, showDetails };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
