/* ═══════════════════════════════════════════════════════════════
   Sullybase Local LLM Chat v2.3.0 — settings.js
   ═══════════════════════════════════════════════════════════════ */

const API = {
  settings:     ()  => fetch("/api/settings").then(r => r.json()),
  settingsSave: (d) => fetch("/api/settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(d),
  }).then(r => r.json()),
  models: ()        => fetch("/api/models").then(r => r.json()),
};

const DEFAULTS = {
  ollama_url: "http://localhost:11434",
  model: "",
  custom_instructions: "",
};

const $ = id => document.getElementById(id);
const els = {
  ollamaUrl:     $("inp-ollama-url"),
  defaultModel:  $("inp-default-model"),
  customInstr:   $("inp-custom-instructions"),
  ollamaStatus:  $("ollama-status"),
  charCount:     $("char-count"),
  saveIndicator: $("settings-save-indicator"),
  footerMsg:     $("settings-footer-msg"),
  btnSave:       $("btn-save"),
  btnReset:      $("btn-reset"),
};

let loaded = { ...DEFAULTS };
let dirty  = false;

async function init() {
  await loadSettings();
  attachListeners();
  checkOllamaUrl(els.ollamaUrl.value);
}

async function loadSettings() {
  try {
    const s = await API.settings();
    loaded = {
      ollama_url:          s.ollama_url || DEFAULTS.ollama_url,
      model:                s.model || "",
      custom_instructions:  s.custom_instructions || "",
    };
  } catch (_) {
    loaded = { ...DEFAULTS };
    setFooterMsg("Could not load settings — showing defaults.", "err");
  }
  applyToForm(loaded);
  updateCharCount();
}

function applyToForm(s) {
  els.ollamaUrl.value    = s.ollama_url || "";
  els.defaultModel.value = s.model || "";
  els.customInstr.value  = s.custom_instructions || "";
}

function updateCharCount() {
  const n = els.customInstr.value.length;
  els.charCount.textContent = `${n.toLocaleString()} character${n !== 1 ? "s" : ""}`;
  els.charCount.classList.toggle("char-count-warn", n > 4000);
}

function markDirty() {
  dirty = true;
  setFooterMsg("Unsaved changes");
}

function setFooterMsg(msg, cls) {
  els.footerMsg.textContent = msg || "";
  els.footerMsg.className = cls || "";
}

// ── Ollama URL reachability check ─────────────────────────────────────────
let urlCheckDebounce = null;

function checkOllamaUrl(url) {
  clearTimeout(urlCheckDebounce);
  if (!url.trim()) {
    setStatus("", "");
    return;
  }
  setStatus("Checking…", "pending");
  urlCheckDebounce = setTimeout(async () => {
    try {
      // Use the existing models endpoint as a reachability probe against
      // whatever URL is currently saved server-side; we only fully verify
      // a *new* URL once it's saved, since the backend client owns the
      // live connection. Until then, give a best-effort local hint.
      const looksValid = /^https?:\/\/.+/i.test(url.trim());
      if (!looksValid) {
        setStatus("✗ Must start with http:// or https://", "err");
        return;
      }
      setStatus("Will connect to this URL on save", "");
    } catch (_) {
      setStatus("", "");
    }
  }, 250);
}

function setStatus(msg, cls) {
  els.ollamaStatus.textContent = msg;
  els.ollamaStatus.className = "field-status" + (cls ? " " + cls : "");
}

// ── Save / reset ───────────────────────────────────────────────────────────
async function saveSettings() {
  const customInstructions = els.customInstr.value;
  if (customInstructions.length > 8000) {
    setFooterMsg("Custom instructions are too long (max 8,000 characters).", "err");
    return;
  }

  const payload = {
    ollama_url:          els.ollamaUrl.value.trim() || DEFAULTS.ollama_url,
    model:               els.defaultModel.value.trim(),
    custom_instructions: customInstructions.trim(),
  };

  els.btnSave.disabled = true;
  els.btnSave.textContent = "Saving…";

  try {
    const r = await API.settingsSave(payload);
    if (!r || r.ok === false) throw new Error("Save failed");
    loaded = { ...payload };
    dirty = false;
    setFooterMsg("");
    flashSaved();

    // Verify the new Ollama URL actually responds, now that the backend
    // client has switched over to it.
    try {
      const resp = await API.models();
      const online = resp.online !== undefined ? resp.online : (resp.models || []).length > 0;
      setStatus(online ? "✓ Connected" : "Saved — but no models found, is Ollama running?",
                online ? "ok" : "err");
    } catch (_) {
      setStatus("Saved — but couldn't reach Ollama at this URL", "err");
    }
  } catch (err) {
    setFooterMsg("Could not save settings: " + err.message, "err");
  } finally {
    els.btnSave.disabled = false;
    els.btnSave.textContent = "Save changes";
  }
}

function flashSaved() {
  els.saveIndicator.classList.remove("hidden");
  clearTimeout(flashSaved._t);
  flashSaved._t = setTimeout(() => els.saveIndicator.classList.add("hidden"), 1800);
}

function resetToDefaults() {
  applyToForm(DEFAULTS);
  updateCharCount();
  markDirty();
  setStatus("", "");
  els.ollamaUrl.focus();
}

// ── Listeners ──────────────────────────────────────────────────────────────
function attachListeners() {
  els.ollamaUrl.addEventListener("input", () => {
    markDirty();
    checkOllamaUrl(els.ollamaUrl.value);
  });
  els.defaultModel.addEventListener("input", markDirty);
  els.customInstr.addEventListener("input", () => { markDirty(); updateCharCount(); });

  els.btnSave.addEventListener("click", saveSettings);
  els.btnReset.addEventListener("click", resetToDefaults);

  window.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveSettings(); }
  });

  window.addEventListener("beforeunload", e => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });
}

document.addEventListener("DOMContentLoaded", init);