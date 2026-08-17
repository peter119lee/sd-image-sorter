/**
 * SD Image Sorter - Audio Manager
 *
 * The synthetic sound-effect set (per-direction move chords, skip blips,
 * combo arpeggios, undo sweeps, start sweeps, victory fanfares) is gone.
 * All that remains is ONE very short, very quiet confirmation blip for the
 * Manual Sort WASD keypress, and it is OFF unless the user explicitly turns
 * it on. Every other legacy sound name is accepted and silently ignored so
 * existing call sites keep working untouched.
 */

const SORT_AUDIO_STORAGE_KEY = 'sort-audio-enabled';

// A neutral, non-melodic pip. Deliberately not pitch-mapped per direction:
// four different pitches turned WASD sorting into a tune.
const BLIP_FREQUENCY = 660;
// Peak amplitude at volume 1.0; the default volume of 0.5 lands at 0.04.
const BLIP_PEAK_GAIN = 0.08;
// Short ramps on both ends — a bare start/stop at full amplitude clicks.
const BLIP_ATTACK_SECONDS = 0.004;
const BLIP_DURATION_SECONDS = 0.03;
const BLIP_RELEASE_PAD_SECONDS = 0.005;

class AudioManagerClass {
    constructor() {
        this.ctx = null;
        this.enabled = readStoredSortAudioPreference();
        this.volume = 0.5;
        this.initialized = false;
        this.contextUnavailable = false;
    }

    // Kept for API compatibility. Constructing an AudioContext is the one
    // real cost here, so a muted user must never reach _ensureContext().
    async init() {
        if (!this.enabled) return;
        this._ensureContext();
    }

    async play(soundName, variant = null) {
        // `variant` (the old w/a/s/d direction) is accepted and ignored.
        if (soundName !== 'move' || !this.enabled) return;

        try {
            const ctx = this._ensureContext();
            if (!ctx) return;
            if (ctx.state === 'suspended') {
                await ctx.resume();
            }
            this._playBlip(ctx);
        } catch (_) {
            // A confirmation sound must never break the sort action itself.
        }
    }

    _ensureContext() {
        if (this.ctx || this.contextUnavailable) return this.ctx;

        try {
            const Ctor = window.AudioContext || window.webkitAudioContext;
            if (!Ctor) throw new Error('Web Audio API unavailable');
            this.ctx = new Ctor();
            this.initialized = true;
        } catch (_) {
            this.contextUnavailable = true;
            this.ctx = null;
        }
        return this.ctx;
    }

    _playBlip(ctx) {
        const now = ctx.currentTime;
        const peak = Math.max(0, Math.min(1, this.volume)) * BLIP_PEAK_GAIN;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = 'sine';
        osc.frequency.value = BLIP_FREQUENCY;

        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(peak, now + BLIP_ATTACK_SECONDS);
        gain.gain.linearRampToValueAtTime(0, now + BLIP_DURATION_SECONDS);

        osc.connect(gain);
        gain.connect(ctx.destination);

        // One blip per keypress adds up over a long session, so release the
        // graph instead of leaving gain nodes wired to the destination.
        osc.onended = () => {
            try {
                osc.disconnect();
                gain.disconnect();
            } catch (_) { /* already torn down */ }
        };

        osc.start(now);
        osc.stop(now + BLIP_DURATION_SECONDS + BLIP_RELEASE_PAD_SECONDS);
    }

    // ============== Controls ==============

    setVolume(vol) {
        const numeric = Number(vol);
        if (!Number.isFinite(numeric)) return;
        this.volume = Math.max(0, Math.min(1, numeric));
    }

    toggle() {
        if (this.enabled) {
            this.disable();
        } else {
            this.enable();
        }
        return this.enabled;
    }

    enable() {
        this.enabled = true;
        persistSortAudioPreference('true');
    }

    disable() {
        this.enabled = false;
        persistSortAudioPreference('false');
    }
}

// Default OFF: only an explicit stored 'true' enables the blip, so a user who
// never touched the setting hears nothing and pays no Web Audio cost.
function readStoredSortAudioPreference() {
    try {
        return localStorage.getItem(SORT_AUDIO_STORAGE_KEY) === 'true';
    } catch (_) {
        return false;
    }
}

function persistSortAudioPreference(value) {
    try {
        localStorage.setItem(SORT_AUDIO_STORAGE_KEY, value);
    } catch (_) {
        // Private mode / quota: keep the in-memory state for this session.
    }
}

// Create singleton instance
window.AudioManager = new AudioManagerClass();
