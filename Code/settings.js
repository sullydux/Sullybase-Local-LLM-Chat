/* ═══════════════════════════════════════════════════════════════
   Sullybase Local LLM Chat v2.5.1 — settings.js
   - Per-provider default models (Ollama + MLX)
   - Per-provider status checks (both backends checked independently)
   - Dynamic error messages based on the active backend
   ═══════════════════════════════════════════════════════════════ */

const API = {
  settings:     ()  => fetch("/api/settings").then(r => r.json()),
  settingsSave: (d) => fetch("/api/settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(d),
  }).then(r => r.json()),
  models:       (backend) => fetch(`/api/models?backend=${backend}`).then(r => r.json()),
  version:      ()  => fetch("/api/version").then(r => r.json()),
  // Check a specific backend by temporarily overriding it.
  check:        (backend, url) => fetch("/api/backend-info", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({backend, url}),
  }).then(r => r.json()),
};

const DEFAULTS = {
  backend:             "ollama",
  ollama_url:          "http://localhost:11434",
  mlx_url:             "http://localhost:8080",
  model_ollama:        "llama3.2",
  model_mlx:           "mlx-community/Llama-3.2-3B-Instruct-4bit",
  mlx_launch_button_enabled: true,
  custom_instructions: "",
  current_chat_id:     "",
  last_model_ollama:   "",
  last_model_mlx:      "",
};

const MLX_INSTALL_COMMAND = "pip install mlx-lm";
const MLX_START_COMMAND = "python -m mlx_lm.server --model mlx-community/Llama-3.2-3B-Instruct-4bit";

const $ = id => document.getElementById(id);
const els = {
  backend:        $("inp-backend"),
  ollamaUrl:      $("inp-ollama-url"),
  mlxUrl:         $("inp-mlx-url"),
  modelOllama:    $("inp-model-ollama"),
  modelMlx:       $("inp-model-mlx"),
  mlxLaunchButton:$("inp-mlx-launch-button"),
  mlxInstallCmd:  $("mlx-install-cmd"),
  mlxStartCmd:    $("mlx-start-cmd"),
  customInstr:    $("inp-custom-instructions"),
  ollamaStatus:   $("ollama-status"),
  mlxStatus:      $("mlx-status"),
  ollamaSection:  $("ollama-section"),
  mlxSection:     $("mlx-section"),
  ollamaDebug:    $("ollama-url-debug"),
  mlxDebug:       $("mlx-url-debug"),
  charCount:      $("char-count"),
  saveIndicator:  $("settings-save-indicator"),
  footerMsg:      $("settings-footer-msg"),
  btnSave:        $("btn-save"),
  btnReset:       $("btn-reset"),
  btnClose:       $("btn-close"),
  versionDisplay: $("about-version"),
};

let loaded = {...DEFAULTS};
let dirty  = false;

async function init() {
  await loadSettings();
  await loadVersion();
  attachListeners();
  updateBackendVisibility();
  // Check BOTH backends so the user can see at a glance which is online —
  // this matters because they may have one running and not the other.
  checkOllamaStatus();
  checkMLXStatus();
}

async function loadVersion() {
  try {
    const data = await API.version();
    if (els.versionDisplay && data.version) {
      els.versionDisplay.textContent = `v${data.version}`;
    }
  } catch (err) { console.error("loadVersion:", err); }
}

async function loadSettings() {
  try {
    const s = await API.settings();
    loaded = {
      backend:             s.backend             || DEFAULTS.backend,
      ollama_url:          s.ollama_url          || DEFAULTS.ollama_url,
      mlx_url:             s.mlx_url             || DEFAULTS.mlx_url,
      model_ollama:        s.model_ollama        || DEFAULTS.model_ollama,
      model_mlx:           s.model_mlx           || DEFAULTS.model_mlx,
      mlx_launch_button_enabled: s.mlx_launch_button_enabled !== false,
      custom_instructions: s.custom_instructions || "",
      current_chat_id:     s.current_chat_id     || "",
      last_model_ollama:   s.last_model_ollama   || "",
      last_model_mlx:      s.last_model_mlx      || "",
    };
    renderSettingsUI();
  } catch (err) {
    console.error("loadSettings:", err);
    if (els.footerMsg) els.footerMsg.textContent = "Error loading settings";
  }
}

function renderSettingsUI() {
  if (els.backend)     els.backend.value        = loaded.backend || "ollama";
  if (els.ollamaUrl)   els.ollamaUrl.value      = loaded.ollama_url;
  if (els.mlxUrl)      els.mlxUrl.value         = loaded.mlx_url;
  if (els.modelOllama) els.modelOllama.value    = loaded.model_ollama;
  if (els.modelMlx)    els.modelMlx.value       = loaded.model_mlx;
  if (els.mlxLaunchButton) els.mlxLaunchButton.checked = !!loaded.mlx_launch_button_enabled;
  if (els.mlxInstallCmd) els.mlxInstallCmd.textContent = MLX_INSTALL_COMMAND;
  updateMlxStartCommandPreview();
  if (els.customInstr) els.customInstr.value    = loaded.custom_instructions;
  updateCharCount();
  updateBackendVisibility();
  updateDebugInfo();
}

function updateBackendVisibility() {
  const isOllama = els.backend ? els.backend.value === "ollama" : true;
  if (els.ollamaSection) els.ollamaSection.style.display = isOllama ? "block" : "none";
  if (els.mlxSection)    els.mlxSection.style.display    = isOllama ? "none"  : "block";
  updateDebugInfo();
}

function updateDebugInfo() {
  if (els.ollamaDebug && els.ollamaUrl) {
    els.ollamaDebug.textContent = "Checking: " + els.ollamaUrl.value + "/api/tags";
  }
  if (els.mlxDebug && els.mlxUrl) {
    els.mlxDebug.textContent = "Checking: " + els.mlxUrl.value + "/v1/models";
  }
}

function updateMlxStartCommandPreview() {
  if (!els.mlxStartCmd || !els.modelMlx) return;
  const model = (els.modelMlx.value || DEFAULTS.model_mlx).trim() || DEFAULTS.model_mlx;
  els.mlxStartCmd.textContent = `python -m mlx_lm.server --model ${model}`;
}

function attachListeners() {
  if (els.backend)  els.backend.addEventListener("change", onBackendChange);
  if (els.ollamaUrl) {
    els.ollamaUrl.addEventListener("input",  onURLChange);
    els.ollamaUrl.addEventListener("change", () => { onURLChange(); checkOllamaStatus(); });
  }
  if (els.mlxUrl) {
    els.mlxUrl.addEventListener("input",  onURLChange);
    els.mlxUrl.addEventListener("change", () => { onURLChange(); checkMLXStatus(); });
  }
  if (els.modelOllama) els.modelOllama.addEventListener("input", onInput);
  if (els.modelMlx)    els.modelMlx.addEventListener("input",    onInput);
  if (els.mlxLaunchButton) els.mlxLaunchButton.addEventListener("change", onInput);
  if (els.customInstr) els.customInstr.addEventListener("input", onCustomInstrChange);
  if (els.btnSave)     els.btnSave.addEventListener("click",  saveSettings);
  if (els.btnReset)    els.btnReset.addEventListener("click",  resetSettings);
  if (els.btnClose)    els.btnClose.addEventListener("click",  closeSettings);
  document.querySelectorAll("[data-copy-target]").forEach(btn => {
    btn.addEventListener("click", () => copyCommand(btn));
  });

  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key === "w") closeSettings();
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveSettings(); }
  });
}

function onBackendChange() { updateBackendVisibility(); markDirty(); }
function onURLChange()     { updateDebugInfo(); markDirty(); }
function onInput()         { updateMlxStartCommandPreview(); markDirty(); }
function onCustomInstrChange() { updateCharCount(); markDirty(); }

function updateCharCount() {
  if (!els.customInstr) return;
  const count = els.customInstr.value.length;
  if (els.charCount) {
    els.charCount.textContent = count.toLocaleString() + " character" + (count !== 1 ? "s" : "");
  }
}

function markDirty() {
  dirty = true;
  if (els.saveIndicator) els.saveIndicator.classList.add("hidden");
}

function closeSettings() {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({type: "closeSettings"}, "*");
  } else if (window.opener) {
    window.close();
  } else {
    window.history.back();
  }
}

// ── Provider-specific status checks ──────────────────────────────────────────
// We temporarily switch the active backend (without saving), probe, then
// restore the user's selection. This makes the error message match the
// provider the user is configuring, regardless of which backend is
// currently selected as active.

async function _probeBackend(backend, url) {
  // Save current settings, swap to the requested backend + url, probe,
  // then restore. We use a fresh fetch with explicit params via query
  // string so the server can pick it up without persisting.
  try {
    const r = await fetch(`/api/ps?probe=1&backend=${encodeURIComponent(backend)}&url=${encodeURIComponent(url)}`,
                          {signal: AbortSignal.timeout(4000)});
    if (!r.ok) return {online: false, error: `HTTP ${r.status}`};
    const data = await r.json();
    return {online: !!data.model_loaded || data.status === "online", data};
  } catch (err) {
    return {online: false, error: err.name === "AbortError"
        ? "timeout"
        : (err.message || "connection failed")};
  }
}

async function checkOllamaStatus() {
  if (!els.ollamaStatus) return;
  els.ollamaStatus.textContent = "Checking…";
  els.ollamaStatus.className = "field-status";

  try {
    const resp = await fetch(els.ollamaUrl.value + "/api/tags",
                              {signal: AbortSignal.timeout(4000)});
    if (!resp.ok) {
      els.ollamaStatus.textContent = `✗ Ollama error (HTTP ${resp.status})`;
      els.ollamaStatus.className = "field-status err";
      return;
    }
    const data = await resp.json();
    const models = data.models || [];
    if (models.length > 0) {
      els.ollamaStatus.textContent = `✓ Ollama online (${models.length} model${models.length !== 1 ? "s" : ""})`;
      els.ollamaStatus.className = "field-status ok";
    } else {
      els.ollamaStatus.textContent = "✓ Ollama online · no models pulled yet";
      els.ollamaStatus.className = "field-status warn";
    }
  } catch (err) {
    const msg = err.name === "AbortError"
      ? "not responding (timeout) — is `ollama serve` running?"
      : `not reachable — ${err.message || "connection refused"}`;
    els.ollamaStatus.textContent = "✗ Ollama " + msg;
    els.ollamaStatus.className = "field-status err";
  }
}

async function checkMLXStatus() {
  if (!els.mlxStatus) return;
  els.mlxStatus.textContent = "Checking…";
  els.mlxStatus.className = "field-status";

  try {
    const resp = await fetch(els.mlxUrl.value + "/v1/models",
                              {signal: AbortSignal.timeout(4000)});
    if (!resp.ok) {
      els.mlxStatus.textContent = `✗ MLX error (HTTP ${resp.status})`;
      els.mlxStatus.className = "field-status err";
      return;
    }
    const data = await resp.json();
    const models = data.data || [];
    if (models.length > 0) {
      els.mlxStatus.textContent = `✓ MLX online (${models.length} model${models.length !== 1 ? "s" : ""})`;
      els.mlxStatus.className = "field-status ok";
    } else {
      els.mlxStatus.textContent = "✓ MLX online · no models loaded";
      els.mlxStatus.className = "field-status warn";
    }
  } catch (err) {
    const msg = err.name === "AbortError"
      ? "not responding (timeout) — start `" + MLX_START_COMMAND.replace("mlx-community/Llama-3.2-3B-Instruct-4bit", "<model>") + "`"
      : `not reachable — ${err.message || "connection refused"}`;
    els.mlxStatus.textContent = "✗ MLX " + msg;
    els.mlxStatus.className = "field-status err";
  }
}

async function saveSettings() {
  try {
    const payload = {
      backend:             els.backend ? els.backend.value : "ollama",
      ollama_url:          (els.ollamaUrl ? els.ollamaUrl.value : "") || DEFAULTS.ollama_url,
      mlx_url:             (els.mlxUrl    ? els.mlxUrl.value    : "") || DEFAULTS.mlx_url,
      model_ollama:        els.modelOllama ? els.modelOllama.value : "",
      model_mlx:           els.modelMlx    ? els.modelMlx.value    : "",
      mlx_launch_button_enabled: els.mlxLaunchButton ? !!els.mlxLaunchButton.checked : DEFAULTS.mlx_launch_button_enabled,
      custom_instructions: els.customInstr ? els.customInstr.value : "",
      current_chat_id:     loaded.current_chat_id || "",
      last_model_ollama:   loaded.last_model_ollama || "",
      last_model_mlx:      loaded.last_model_mlx    || "",
    };
    const result = await API.settingsSave(payload);
    if (result.ok) {
      loaded = {...loaded, ...payload};
      dirty = false;
      if (els.saveIndicator) els.saveIndicator.classList.remove("hidden");
      els.footerMsg.textContent = "✓ Settings saved";
      setTimeout(() => { els.footerMsg.textContent = ""; }, 2000);
      checkOllamaStatus();
      checkMLXStatus();
    } else {
      els.footerMsg.textContent = "✗ Save failed";
    }
  } catch (err) {
    console.error("saveSettings:", err);
    els.footerMsg.textContent = "✗ Network error";
  }
}

function resetSettings() {
  renderSettingsUI();
  dirty = false;
  els.footerMsg.textContent = "";
}

async function copyCommand(btn) {
  const targetId = btn.getAttribute("data-copy-target");
  const target = targetId ? document.getElementById(targetId) : null;
  const text = target ? target.textContent.trim() : "";
  if (!text) return;

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const oldText = btn.textContent;
    btn.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => {
      btn.textContent = oldText;
      btn.classList.remove("copied");
    }, 1500);
  } catch (err) {
    console.error("copyCommand:", err);
    if (els.footerMsg) els.footerMsg.textContent = "Copy failed";
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
