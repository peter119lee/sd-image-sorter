/**
 * SD Image Sorter — Color backfill UX (v3.2.1+)
 *
 * Surfaces the lazy-backfill workflow for color analysis:
 *   1. A banner appears above the gallery when the user picks a color sort and
 *      the library still has images missing avg_brightness / temperature.
 *   2. A nav chip appears while a backfill job is running, showing live %.
 *   3. The chip opens a corner toast with progress detail + pause / hide actions.
 *
 * The module is intentionally self-contained: it owns its own polling loop and
 * does not modify app.js. The only required integration point is the existing
 * `#gallery-sort` select element.
 */
(function () {
    "use strict";

    const COLOR_SORT_KEYS = new Set(["brightness", "saturation", "brightness_skew"]);
    const POLL_INTERVAL_MS = 1500;
    const BANNER_DISMISS_TTL_MS = 24 * 60 * 60 * 1000;   // remember dismiss for 1 day
    const BANNER_DISMISS_KEY = "color-backfill-banner-dismissed-at";

    const ColorBackfill = {
        pollTimer: null,
        lastProgress: null,
        bannerVisible: false,
        wasRunning: false,         // tracks running->idle transition for "Done" UX
        doneAutoCloseTimer: null,  // auto-close toast/chip 5s after completion
        doneState: false,          // when true, chip/toast show completion banner

        t(en, zh) {
            return window.I18n?.getLang?.() === "zh-CN" ? zh : en;
        },

        async init() {
            this.bindEvents();
            // Check on load — backend may already be running a job from a
            // previous session, in which case we want the chip visible immediately.
            await this.refreshProgress();
        },

        bindEvents() {
            document.getElementById("gallery-sort")?.addEventListener("change", (e) => {
                this.maybeShowBanner(e.target.value);
            });
            document.getElementById("btn-color-backfill-start")?.addEventListener("click", () => this.openColorAnalysis());
            document.getElementById("btn-color-backfill-dismiss")?.addEventListener("click", () => this.dismissBanner());
            document.getElementById("nav-color-progress")?.addEventListener("click", () => this.openToast());
            document.getElementById("btn-color-progress-close")?.addEventListener("click", () => this.closeToast());
            document.getElementById("btn-color-progress-hide")?.addEventListener("click", () => this.closeToast());
            document.getElementById("btn-color-progress-pause")?.addEventListener("click", () => this.cancelAnalysis());
            // v3.2.1 task #26: filter modal inline banner.
            document.getElementById("btn-filter-color-backfill-start")?.addEventListener("click", () => this.openColorAnalysis());
            // Refresh the filter banner whenever the filter modal opens.
            window.addEventListener("filterModalOpened", () => this.refreshFilterBanner());
        },

        // ---- Banner -------------------------------------------------------

        async maybeShowBanner(sortKey) {
            if (!COLOR_SORT_KEYS.has(String(sortKey))) {
                this.hideBanner();
                return;
            }
            // Respect dismiss-for-a-day decision unless a job is actively running.
            const dismissed = parseInt(localStorage.getItem(BANNER_DISMISS_KEY) || "0", 10);
            if (dismissed && Date.now() - dismissed < BANNER_DISMISS_TTL_MS) {
                return;
            }
            const missing = await this._fetchMissingCount();
            if (missing <= 0) {
                this.hideBanner();
                return;
            }
            this.showBanner(missing);
        },

        showBanner(missingCount) {
            const banner = document.getElementById("color-backfill-banner");
            if (!banner) return;
            const detail = document.getElementById("color-backfill-banner-detail");
            if (detail) {
                detail.textContent = this.t(
                    `${missingCount.toLocaleString()} images haven't been analyzed yet. Sort and filter results will be incomplete until backfill finishes.`,
                    `还有 ${missingCount.toLocaleString()} 张图未分析。排序和筛选会不完整，直到补算完成。`,
                );
            }
            const startBtn = document.getElementById("btn-color-backfill-start");
            if (startBtn) {
                const label = window.I18n?.t?.("color.openAnalysisTabWithCount", { count: missingCount.toLocaleString() });
                startBtn.textContent = label && label !== "color.openAnalysisTabWithCount"
                    ? label
                    : this.t(
                        `Open Color Analysis (${missingCount.toLocaleString()})`,
                        `打开色彩分析（${missingCount.toLocaleString()}）`,
                    );
            }
            banner.hidden = false;
            this.bannerVisible = true;
        },

        hideBanner() {
            const banner = document.getElementById("color-backfill-banner");
            if (banner) banner.hidden = true;
            this.bannerVisible = false;
        },

        dismissBanner() {
            localStorage.setItem(BANNER_DISMISS_KEY, String(Date.now()));
            this.hideBanner();
        },

        // ---- Filter modal inline banner (task #26) ----------------------

        async refreshFilterBanner() {
            const banner = document.getElementById("filter-color-backfill-banner");
            if (!banner) return;
            const titleEl = document.getElementById("filter-color-backfill-title");
            let missing = 0;
            try {
                missing = await this._fetchMissingCount();
            } catch (_e) {
                missing = 0;
            }
            if (missing <= 0) {
                banner.hidden = true;
                return;
            }
            if (titleEl) {
                const tmpl = window.I18n?.t?.("filter.colorBackfillMissing")
                    || (window.I18n?.getLang?.() === "zh-CN"
                        ? `色彩筛选还需分析 {count} 张图。`
                        : `Color filters need analysis on {count} more images.`);
                titleEl.textContent = tmpl.replace("{count}", missing.toLocaleString());
            }
            banner.hidden = false;
        },

        // ---- Start / cancel ----------------------------------------------

        openColorAnalysis() {
            this.hideBanner();
            if (typeof window.App?.openColorAnalysis === "function") {
                window.App.openColorAnalysis();
                return;
            }
            document.getElementById("btn-tag")?.click();
            setTimeout(() => window.V321Integration?.setTaggerTab?.("color"), 0);
        },

        async startAnalysis() {
            this.hideBanner();
            try {
                const resp = await (window.apiFetch || fetch)("/api/colors/analyze", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    // Cap one call at 50k images (backend Field max_length).
                    // For libraries beyond that, the user can click Analyze
                    // again after the first batch finishes — backend
                    // get_images_missing_color_data() returns the next slice.
                    body: JSON.stringify({ limit: 50000 }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    this._toast(err.detail || resp.statusText, "error");
                    return;
                }
                const data = await resp.json();
                // Nothing queued (everything already analyzed) — say so and
                // skip the progress toast, which would sit at "0/0 0%" forever.
                if (!Number(data.total)) {
                    this._toast(
                        this.t("All images already have color analysis.", "所有图片都已完成色彩分析。"),
                        "info",
                    );
                    return;
                }
                this._toast(
                    this.t(
                        `Color analysis started — ${data.total.toLocaleString()} images queued.`,
                        `已开始补算 ${data.total.toLocaleString()} 张图的色彩。`,
                    ),
                    "info",
                );
                this.openToast();
                this.startPolling();
            } catch (e) {
                this._toast(String(e.message || e), "error");
            }
        },

        async cancelAnalysis() {
            try {
                await (window.apiFetch || fetch)("/api/colors/cancel", { method: "POST" });
            } catch (_) { /* ignore */ }
        },

        // ---- Polling + chip / toast --------------------------------------

        startPolling() {
            if (this.pollTimer) return;
            this.pollTimer = setInterval(() => this.refreshProgress(), POLL_INTERVAL_MS);
        },

        // v3.2.1 task #34: external entry point used when another flow (e.g.
        // the "Analyze Colors" common action) kicks off analysis directly.
        // Forces wasRunning=true so the next idle tick fires the Done banner,
        // surfaces the toast immediately, and triggers a fresh poll.
        notifyAnalysisStarted() {
            this.wasRunning = true;
            this.openToast();
            this.refreshProgress();
            this.startPolling();
        },

        stopPolling() {
            if (this.pollTimer) {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
            }
        },

        async refreshProgress() {
            let data;
            try {
                const resp = await (window.apiFetch || fetch)("/api/colors/progress");
                if (!resp.ok) return;
                data = await resp.json();
            } catch (_) {
                return;
            }
            // Detect running->idle transition for "Done" UX banner.
            // Only fire when the user actually saw a run in this session
            // (wasRunning=true), not on initial-load idle state.
            const justFinished = this.wasRunning && !data.running;
            this.wasRunning = Boolean(data.running);

            this.lastProgress = data;

            if (justFinished && data.completed > 0) {
                this._showDoneBanner(data);
                try { window.dispatchEvent(new CustomEvent("colorAnalysisCompleted")); } catch (_e) {}
            } else if (justFinished && Number(data.failed || 0) > 0) {
                // Every image failed — say so instead of ending silently.
                this._toast(
                    this.t(
                        `Color analysis finished, but all ${Number(data.failed).toLocaleString()} image(s) failed. Check the backend log for details.`,
                        `色彩分析结束，但 ${Number(data.failed).toLocaleString()} 张图全部失败。详情请查看后端日志。`,
                    ),
                    "error",
                );
            }

            this._renderChip(data);
            this._renderToast(data);

            // Stale-toast guard: idle with nothing processed reads as a stuck
            // "0/0 0%" job — close it (v3.5.0 audit).
            if (!data.running && !this.doneState && !(Number(data.total) > 0)) {
                this.closeToast();
            }

            if (data.running) {
                this.startPolling();
            } else {
                this.stopPolling();
            }
        },

        _showDoneBanner(data) {
            // Switch chip + toast into "done" mode for 5 seconds, then auto-hide.
            this.doneState = true;
            if (this.doneAutoCloseTimer) {
                clearTimeout(this.doneAutoCloseTimer);
            }
            this.doneAutoCloseTimer = setTimeout(() => {
                this.doneState = false;
                this.doneAutoCloseTimer = null;
                this.closeToast();
                const chip = document.getElementById("nav-color-progress");
                if (chip) chip.hidden = true;
            }, 5000);
            // Surface a global toast too — visible even when the chip toast is closed.
            this._toast(
                this.t(
                    `Color analysis done — ${data.completed.toLocaleString()} images analyzed.`,
                    `色彩分析完成 — 已分析 ${data.completed.toLocaleString()} 张。`,
                ),
                "success",
            );
        },

        _renderChip(data) {
            const chip = document.getElementById("nav-color-progress");
            const label = document.getElementById("nav-color-progress-label");
            if (!chip || !label) return;
            // Done mode: keep chip visible with a checkmark for 5s after completion.
            if (this.doneState) {
                chip.hidden = false;
                label.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-check\"/></svg>";
                chip.setAttribute("aria-label", this.t(
                    `Color analysis complete — ${data.completed.toLocaleString()} analyzed`,
                    `色彩分析完成 — 已分析 ${data.completed.toLocaleString()}`,
                ));
                return;
            }
            if (!data.running) {
                chip.hidden = true;
                return;
            }
            const pct = data.total > 0 ? Math.floor((data.completed / data.total) * 100) : 0;
            chip.hidden = false;
            label.textContent = `${pct}%`;
            chip.setAttribute("aria-label", this.t(
                `Color analysis ${pct}% — ${data.completed.toLocaleString()} of ${data.total.toLocaleString()}`,
                `色彩分析 ${pct}% — ${data.completed.toLocaleString()} / ${data.total.toLocaleString()}`,
            ));
        },

        _renderToast(data) {
            const toast = document.getElementById("color-progress-toast");
            if (!toast || toast.hidden) return;
            const fill = document.getElementById("color-progress-toast-fill");
            const count = document.getElementById("color-progress-toast-count");
            const pctEl = document.getElementById("color-progress-toast-percent");
            const currentEl = document.getElementById("color-progress-toast-current");
            const pauseBtn = document.getElementById("btn-color-progress-pause");

            // Done mode: show 100% and a clear completion message.
            const showingDone = this.doneState;
            const pct = showingDone
                ? 100
                : (data.total > 0 ? Math.floor((data.completed / data.total) * 100) : 0);
            if (fill) {
                fill.style.width = `${pct}%`;
                fill.parentElement?.setAttribute("aria-valuenow", String(pct));
            }
            if (count) count.textContent = `${data.completed.toLocaleString()} / ${data.total.toLocaleString()}`;
            if (pctEl) pctEl.textContent = `${pct}%`;
            if (currentEl) {
                if (showingDone) {
                    currentEl.textContent = this.t(
                        `Done — ${data.completed.toLocaleString()} images analyzed`,
                        `已完成 — 共分析 ${data.completed.toLocaleString()} 张`,
                    );
                } else {
                    currentEl.textContent = data.current_image
                        ? this.t(`Current: ${data.current_image}`, `当前：${data.current_image}`)
                        : "";
                }
            }
            if (pauseBtn) {
                if (data.cancel_requested) {
                    pauseBtn.disabled = true;
                    pauseBtn.textContent = this.t("Cancelling...", "正在取消...");
                } else if (showingDone || !data.running) {
                    pauseBtn.disabled = true;
                    pauseBtn.textContent = data.failed > 0
                        ? this.t(`Done with ${data.failed} error(s)`, `完成 (${data.failed} 错误)`)
                        : this.t("Done", "完成");
                } else {
                    pauseBtn.disabled = false;
                    pauseBtn.textContent = this.t("Pause", "暂停");
                }
            }
        },

        openToast() {
            const toast = document.getElementById("color-progress-toast");
            if (!toast) return;
            toast.hidden = false;
            if (this.lastProgress) this._renderToast(this.lastProgress);
        },

        closeToast() {
            const toast = document.getElementById("color-progress-toast");
            if (toast) toast.hidden = true;
        },

        // ---- Helpers ------------------------------------------------------

        async _fetchMissingCount() {
            try {
                const resp = await (window.apiFetch || fetch)("/api/colors/missing-count");
                if (!resp.ok) return 0;
                const data = await resp.json();
                return Number(data.missing) || 0;
            } catch (_) {
                return 0;
            }
        },

        _toast(message, level) {
            // Prefer the existing global toast if available; fall back to console.
            if (window.showToast) {
                window.showToast(message, level || "info");
                return;
            }
            if (level === "error" && typeof window.alert === "function") {
                window.alert(message);
            }
        },
    };

    window.ColorBackfill = ColorBackfill;
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => ColorBackfill.init());
    } else {
        ColorBackfill.init();
    }
})();
