/**
 * SD Image Sorter - Audio Manager
 * Handles sound effects for the manual sort mode
 * Uses Web Audio API for low-latency playback
 */

class AudioManagerClass {
    constructor() {
        this.ctx = null;
        this.sounds = {};
        this.enabled = true;
        this.volume = 0.5;
        this.initialized = false;
    }

    async init() {
        if (this.initialized) return;

        try {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();

            // Generate synth sounds programmatically
            this.sounds = {
                move: this.createMoveSound.bind(this),
                skip: this.createSkipSound.bind(this),
                combo: this.createComboSound.bind(this),
                undo: this.createUndoSound.bind(this),
                start: this.createStartSound.bind(this),
                finish: this.createFinishSound.bind(this)
            };

            this.initialized = true;
        } catch (e) {
            console.warn('Web Audio API not available');
        }
    }

    async play(soundName, variant = null) {
        if (!this.enabled) return;

        await this.init();

        if (this.ctx.state === 'suspended') {
            await this.ctx.resume();
        }

        const soundFn = this.sounds[soundName];
        if (soundFn) {
            soundFn(variant);
        }
    }

    // ============== Sound Generators ==============

    createMoveSound(direction) {
        const now = this.ctx.currentTime;

        // Oscillator for the main tone
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        // Different pitch based on direction
        const pitches = { w: 880, a: 660, s: 440, d: 770 };
        osc.frequency.value = pitches[direction] || 660;
        osc.type = 'sine';

        gain.gain.setValueAtTime(this.volume * 0.3, now);
        gain.gain.exponentialDecayTo = 0.01;
        gain.gain.setTargetAtTime(0.01, now + 0.05, 0.1);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start(now);
        osc.stop(now + 0.2);

        // Add a higher harmonic
        const osc2 = this.ctx.createOscillator();
        const gain2 = this.ctx.createGain();

        osc2.frequency.value = (pitches[direction] || 660) * 1.5;
        osc2.type = 'sine';

        gain2.gain.setValueAtTime(this.volume * 0.1, now);
        gain2.gain.setTargetAtTime(0.01, now + 0.03, 0.05);

        osc2.connect(gain2);
        gain2.connect(this.ctx.destination);

        osc2.start(now);
        osc2.stop(now + 0.15);
    }

    createSkipSound() {
        const now = this.ctx.currentTime;

        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.frequency.value = 300;
        osc.frequency.setTargetAtTime(200, now, 0.1);
        osc.type = 'triangle';

        gain.gain.setValueAtTime(this.volume * 0.2, now);
        gain.gain.setTargetAtTime(0.01, now + 0.1, 0.1);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start(now);
        osc.stop(now + 0.25);
    }

    createComboSound() {
        const now = this.ctx.currentTime;

        // Arpeggio effect
        const notes = [523.25, 659.25, 783.99, 1046.50]; // C5, E5, G5, C6

        notes.forEach((freq, i) => {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();

            osc.frequency.value = freq;
            osc.type = 'sine';

            const startTime = now + i * 0.05;
            gain.gain.setValueAtTime(0, startTime);
            gain.gain.linearRampToValueAtTime(this.volume * 0.2, startTime + 0.02);
            gain.gain.setTargetAtTime(0.01, startTime + 0.1, 0.1);

            osc.connect(gain);
            gain.connect(this.ctx.destination);

            osc.start(startTime);
            osc.stop(startTime + 0.3);
        });
    }

    createUndoSound() {
        const now = this.ctx.currentTime;

        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        // Descending pitch
        osc.frequency.value = 600;
        osc.frequency.setTargetAtTime(300, now, 0.15);
        osc.type = 'sawtooth';

        // Filter for warmer sound
        const filter = this.ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 2000;

        gain.gain.setValueAtTime(this.volume * 0.15, now);
        gain.gain.setTargetAtTime(0.01, now + 0.15, 0.1);

        osc.connect(filter);
        filter.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start(now);
        osc.stop(now + 0.3);
    }

    createStartSound() {
        const now = this.ctx.currentTime;

        // Rising sweep
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.frequency.value = 200;
        osc.frequency.exponentialRampToValueAtTime(800, now + 0.3);
        osc.type = 'sawtooth';

        const filter = this.ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 1500;
        filter.frequency.exponentialRampToValueAtTime(5000, now + 0.3);

        gain.gain.setValueAtTime(this.volume * 0.2, now);
        gain.gain.setTargetAtTime(0.01, now + 0.25, 0.1);

        osc.connect(filter);
        filter.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start(now);
        osc.stop(now + 0.5);
    }

    createFinishSound() {
        const now = this.ctx.currentTime;

        // Victory fanfare
        const notes = [
            { freq: 523.25, time: 0 },      // C5
            { freq: 659.25, time: 0.1 },    // E5
            { freq: 783.99, time: 0.2 },    // G5
            { freq: 1046.50, time: 0.35 }   // C6
        ];

        notes.forEach(note => {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();

            osc.frequency.value = note.freq;
            osc.type = 'sine';

            const startTime = now + note.time;
            gain.gain.setValueAtTime(0, startTime);
            gain.gain.linearRampToValueAtTime(this.volume * 0.25, startTime + 0.02);
            gain.gain.setTargetAtTime(0.01, startTime + 0.2, 0.15);

            osc.connect(gain);
            gain.connect(this.ctx.destination);

            osc.start(startTime);
            osc.stop(startTime + 0.5);
        });
    }

    // ============== Controls ==============

    setVolume(vol) {
        this.volume = Math.max(0, Math.min(1, vol));
    }

    toggle() {
        this.enabled = !this.enabled;
        return this.enabled;
    }

    enable() {
        this.enabled = true;
    }

    disable() {
        this.enabled = false;
    }
}

// Create singleton instance
window.AudioManager = new AudioManagerClass();

// Initialize on first user interaction
document.addEventListener('click', () => {
    window.AudioManager.init();
}, { once: true });

document.addEventListener('keydown', () => {
    window.AudioManager.init();
}, { once: true });
