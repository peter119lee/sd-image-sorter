/**
 * app/stuck-job-reset.js — the shared recovery control for a job stranded in
 * `cancelling`.
 *
 * `cancel` publishes `cancelling` and leaves the terminal write to the worker,
 * so a worker that dies first strands the status there permanently. The poller
 * never reaches a terminal state, the progress panel never collapses, and every
 * later run of that feature is refused with HTTP 409 until the app restarts.
 *
 * Four endpoints clear exactly that state — /api/move/reset,
 * /api/batch-move/reset, /api/images/delete-selected/reset and
 * /api/images/remove-selected/reset — with one contract: 409 while the job is
 * genuinely running, 200 `status:"reset"` when something was cleared, and 200
 * `status:"idle"` when there was nothing to clear.
 *
 * Deliberately never auto-called. An automatic reset would hide a real problem
 * and could race a worker that is still finishing; the 409 exists precisely to
 * refuse that race, and a control the user chose to press is the only way to
 * tell "the worker is gone" from "the worker is slow".
 */

// How long a job may sit in `cancelling` before the manual reset is offered.
// The cooperative cancel is only observed at the worker's next image/chunk
// boundary, so a live worker needs room to get there; 15s is far past that and
// still far short of the app restart this replaces.
const STUCK_JOB_STALL_MS = 15000;

/** Per-panel state for the stall detector. */
function createStuckJobWatcher() {
    return { cancellingSince: 0 };
}

/**
 * Feed each polled status in. Returns true once the job has been sitting in
 * `cancelling` longer than the stall window.
 *
 * Only `cancelling` is treated as strandable: every other non-terminal status
 * either advances on its own or is already handled by the poller's terminal
 * branches.
 */
function isStuckJobStalled(watcher, status, now = Date.now()) {
    if (!watcher) return false;
    if (status !== 'cancelling') {
        watcher.cancellingSince = 0;
        return false;
    }
    if (!watcher.cancellingSince) {
        watcher.cancellingSince = now;
        return false;
    }
    return now - watcher.cancellingSince >= STUCK_JOB_STALL_MS;
}

/**
 * POST the reset and classify the answer.
 *
 * `reset`   — the stranded state was cleared, the feature is usable again.
 * `idle`    — there was nothing to clear (also a usable outcome).
 * `running` — 409. The refusal is the endpoint working correctly, so callers
 *             must report it as "still running", never as a failure.
 * `failed`  — anything else; a real error worth showing as one.
 */
async function requestStuckJobReset(endpoint) {
    try {
        const payload = await window.App.API.post(endpoint, {});
        return { outcome: payload?.status === 'reset' ? 'reset' : 'idle', payload };
    } catch (error) {
        if (Number(error?.apiStatus) === 409) return { outcome: 'running', error };
        return { outcome: 'failed', error };
    }
}

/** The label every reset control shows, in the active language. */
function stuckJobResetLabel() {
    return appT('job.resetStuck', 'Reset stuck job');
}

/**
 * Show or hide a panel's reset button, keeping its label localized.
 *
 * The button must carry a `[hidden]` CSS guard of its own: the `hidden`
 * attribute is a specificity 0-0-1 UA rule that any class-level `display`
 * beats, which is how controls in this app have silently failed to hide before.
 */
function setStuckJobResetVisible(button, visible) {
    if (!button) return;
    if (visible && button.hidden) {
        button.textContent = stuckJobResetLabel();
        button.hidden = false;
    } else if (!visible && !button.hidden) {
        button.hidden = true;
    }
}

/**
 * Wire a reset button once. `onCleared` runs only when the job really was
 * cleared (or was already idle), so the caller collapses its panel exactly when
 * the feature is usable again.
 */
function bindStuckJobResetButton(button, { endpoint, onCleared }) {
    if (!button || button.dataset.stuckResetBound === '1') return;
    button.dataset.stuckResetBound = '1';
    button.type = 'button';
    button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        try {
            const { outcome, error } = await requestStuckJobReset(endpoint);
            if (outcome === 'reset' || outcome === 'idle') {
                if (typeof onCleared === 'function') onCleared(outcome);
                window.App.showToast(
                    appT('job.resetStuckDone', 'Cleared the stuck job. You can start again.'),
                    'success'
                );
                return;
            }
            if (outcome === 'running') {
                // 409 means the reset was correctly refused, not that anything
                // broke — say so instead of showing a failure.
                window.App.showToast(
                    appT('job.resetStuckStillRunning', 'That job is still running. Wait for it to finish, then try again.'),
                    'info'
                );
                return;
            }
            window.App.showToast(
                formatUserError(error, appT('job.resetStuckFailed', 'Could not reset the stuck job')),
                'error'
            );
        } finally {
            button.disabled = false;
            button.textContent = stuckJobResetLabel();
        }
    });
}
