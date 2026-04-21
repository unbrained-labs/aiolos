// beep.js — tiny old-computer click sounds on interaction.
//
// Defaults to OFF. A small toggle in the bottom-right persists the choice via
// localStorage. When on, a short square-wave ping fires on click / keyboard
// Enter on links and buttons. Lazily creates an AudioContext on first use
// because browsers block audio until a user gesture.

(() => {
  const KEY = "claude-setup:beep";
  let enabled = localStorage.getItem(KEY) === "on";
  let ctx = null;

  const ensureCtx = () => {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { ctx = null; }
    }
    if (ctx && ctx.state === "suspended") ctx.resume();
    return ctx;
  };

  const beep = (freq = 880, duration = 0.035, type = "square", gain = 0.04) => {
    const ac = ensureCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const osc = ac.createOscillator();
    const amp = ac.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, now);
    // Square wave with a fast attack + decay = that little chirp from a
    // 1990s beige PC speaker. Low gain so it stays unobtrusive.
    amp.gain.setValueAtTime(0, now);
    amp.gain.linearRampToValueAtTime(gain, now + 0.003);
    amp.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    osc.connect(amp).connect(ac.destination);
    osc.start(now);
    osc.stop(now + duration + 0.02);
  };

  // Two slightly different tones — linky things vs interactive buttons.
  const beepLink = () => beep(880, 0.035, "square", 0.05);
  const beepBtn  = () => beep(660, 0.05,  "square", 0.05);

  // ── Toggle widget ─────────────────────────────────────────────────────
  const toggle = document.createElement("button");
  toggle.className = "sound-toggle";
  toggle.type = "button";
  toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
  toggle.title = "Toggle UI beeps";
  toggle.textContent = "SOUND";
  toggle.addEventListener("click", () => {
    enabled = !enabled;
    localStorage.setItem(KEY, enabled ? "on" : "off");
    toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
    if (enabled) beepBtn();          // confirm with a ping
  });
  document.addEventListener("DOMContentLoaded", () => {
    document.body.appendChild(toggle);
  });

  // ── Click handler (event delegation) ──────────────────────────────────
  document.addEventListener("click", (e) => {
    if (!enabled) return;
    const t = e.target.closest("a, button, summary, [role=button]");
    if (!t) return;
    if (t.classList.contains("sound-toggle")) return; // handled above
    if (t.tagName === "A") beepLink();
    else beepBtn();
  }, true);
})();
