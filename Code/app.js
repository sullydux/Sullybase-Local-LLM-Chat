/* ═══════════════════════════════════════════════════════════════
   Sullybase Local LLM Chat v2.3.0 — app.js
   ═══════════════════════════════════════════════════════════════ */

// ── API helpers ───────────────────────────────────────────────────────────────
const API = {
  models:      ()      => fetch("/api/models").then(r => r.json()),
  ps:          (model) => fetch(`/api/ps?model=${encodeURIComponent(model)}`).then(r => r.json()),
  title:       (model, text, reply) => fetch("/api/title", _json({model, text, reply})).then(r => r.json()),
  chats:       ()      => fetch("/api/chats").then(r => r.json()),
  search:      (q)     => fetch(`/api/search?q=${encodeURIComponent(q)}`).then(r => r.json()),
  chatGet:     (id)    => fetch(`/api/chats/${id}`).then(r => r.json()),
  chatSave:    (id, b) => fetch(`/api/chats/${id}`, _json(b)),
  chatDel:     (id)    => fetch(`/api/chats/${id}`, {method:"DELETE"}),
  settings:    ()      => fetch("/api/settings").then(r => r.json()),
  settingsSave:(d)     => fetch("/api/settings", _json(d)),
  context:     (path)  => fetch("/api/context", _json({path})).then(r => r.json()),
  browse:      (mode)  => fetch("/api/browse",  _json({mode})).then(r => r.json()),
};

function _json(body, method="POST") {
  return {method, headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)};
}

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  chatId:        null,
  messages:      [],
  contextFiles:  [],
  model:         "",
  streaming:     false,
  pendingDel:    null,
  firstTokenMs:  0,
  genMs:         0,
  lastTps:       0,
  lastPromptTok: 0,   // track used context tokens
  modelCtxLen:   0,   // track model max context
  titleOk:       false, // whether the AI-generated title succeeded for this chat
  abortCtrl:     null, // AbortController for the in-flight stream
  searchQuery:   "",
};

// ── Marked setup ──────────────────────────────────────────────────────────────
const renderer = new marked.Renderer();
renderer.code = (code, lang) => {
  const highlighted = lang && hljs.getLanguage(lang)
    ? hljs.highlight(code, {language: lang}).value
    : hljs.highlightAuto(code).value;
  const b64 = btoa(unescape(encodeURIComponent(code)));
  return `<div class="code-wrap">
    <div class="code-header">
      <span class="code-lang">${escHtml(lang || "code")}</span>
      <button class="btn-copy" data-code="${b64}">Copy</button>
    </div>
    <pre><code class="hljs language-${lang||''}">${highlighted}</code></pre>
  </div>`;
};
marked.use({ renderer, breaks: true, gfm: true });

// ── DOM ───────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const els = {
  modelSelect:      $("model-select"),
  chatList:         $("chat-list"),
  chatSearchInput:  $("chat-search-input"),
  chatSearchClear:  $("chat-search-clear"),
  chatTitle:        $("chat-title"),
  messages:         $("messages"),
  messagesWrap:     $("messages-wrap"),
  userInput:        $("user-input"),
  btnSend:          $("btn-send"),
  btnNewChat:       $("btn-new-chat"),
  btnRefreshModels: $("btn-refresh-models"),
  btnContext:       $("btn-context"),
  btnBrowse:        $("btn-browse"),
  btnClear:         $("btn-clear"),
  btnExport:        $("btn-export"),
  exportMenu:       $("export-menu"),
  contextPanel:     $("context-panel"),
  contextPathInput: $("context-path-input"),
  btnContextAdd:    $("btn-context-add"),
  contextStatus:    $("context-status"),
  contextFileList:  $("context-file-list"),
  contextBadge:     $("context-badge"),
  perfInfo:         $("perf-info"),
  statDevice:       $("stat-device"),
  statVram:         $("stat-vram"),
  statVramBar:      $("stat-vram-bar"),
  statVramBarWrap:  $("stat-vram-bar-wrap"),
  statCtx:          $("stat-ctx"),
  statCtxBar:       $("stat-ctx-bar"),
  statCtxBarWrap:   $("stat-ctx-bar-wrap"),
  statTps:          $("stat-tps"),
  statModelInfo:    $("stat-model-info"),
  deleteDialog:     $("delete-dialog"),
  deleteCancel:     $("delete-cancel"),
  deleteConfirm:    $("delete-confirm"),
  filePicker:       $("file-picker"),
};

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  await loadSettings();
  await loadModels();
  await loadChatList();
  attachEventListeners();
  startStatsPoller();
}

async function loadSettings() {
  try {
    const s = await API.settings();
    if (s.model) state.model = s.model;
    if (s.current_chat_id) state.chatId = s.current_chat_id;
  } catch(_) {}
}

async function loadModels() {
  try {
    const resp   = await API.models();
    const models = resp.models || resp;
    const online = resp.online !== undefined ? resp.online : models.length > 0;

    els.modelSelect.innerHTML = "";

    if (!online || !models.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = online ? "No models — run: ollama pull …" : "Run Ollama";
      els.modelSelect.appendChild(opt);
      els.statDevice.textContent = "⬡ offline";
      els.statDevice.className = "stat-device-label";
      return;
    }

    models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = opt.textContent = m;
      if (m === state.model) opt.selected = true;
      els.modelSelect.appendChild(opt);
    });

    if (!state.model || !models.includes(state.model)) state.model = models[0];
    els.modelSelect.value = state.model;
  } catch(_) {
    els.modelSelect.innerHTML = '<option value="">Run Ollama</option>';
    els.statDevice.textContent = "⬡ offline";
    els.statDevice.className = "stat-device-label";
  }
}

// ── Chat list ─────────────────────────────────────────────────────────────────
async function loadChatList() {
  try {
    const chats = await API.chats();
    renderChatList(chats);
    if (state.chatId && chats.find(c => c.id === state.chatId)) {
      await loadChat(state.chatId);
      return;
    }
  } catch(_) {}
  newChat();
}

function renderChatList(chats) {
  els.chatList.innerHTML = "";

  if (!chats.length) {
    const empty = document.createElement("div");
    empty.className = "chat-search-empty";
    empty.textContent = state.searchQuery ? "No matching chats" : "No chats yet";
    els.chatList.appendChild(empty);
    return;
  }

  chats.forEach(c => {
    const item = document.createElement("div");
    item.className = "chat-item" + (c.id === state.chatId ? " active" : "");
    item.dataset.id = c.id;

    const titleHtml = highlightMatch(c.title || "New chat", state.searchQuery);
    const snippetHtml = c.snippet
      ? `<div class="chat-item-snippet">${highlightMatch(c.snippet, state.searchQuery)}</div>`
      : "";

    item.innerHTML = `
      <div class="chat-item-text">
        <span class="chat-item-title">${titleHtml}</span>
        ${snippetHtml}
      </div>
      <button class="chat-item-del" data-id="${c.id}" title="Delete chat">×</button>`;
    item.addEventListener("click", e => {
      if (e.target.classList.contains("chat-item-del")) {
        openDeleteDialog(e.target.dataset.id);
      } else {
        switchChat(c.id);
      }
    });
    els.chatList.appendChild(item);
  });
}

function highlightMatch(text, query) {
  const escaped = escHtml(text);
  if (!query) return escaped;
  const escQ = escHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return escaped.replace(new RegExp(escQ, "ig"), m => `<mark>${m}</mark>`);
}

let searchDebounce = null;

function onChatSearchInput() {
  const q = els.chatSearchInput.value.trim();
  state.searchQuery = q;
  els.chatSearchClear.classList.toggle("hidden", !q);

  clearTimeout(searchDebounce);
  if (!q) { refreshChatList(); return; }

  searchDebounce = setTimeout(async () => {
    try {
      const results = await API.search(q);
      renderChatList(results);
    } catch(_) {}
  }, 200);
}

function clearChatSearch() {
  els.chatSearchInput.value = "";
  state.searchQuery = "";
  els.chatSearchClear.classList.add("hidden");
  refreshChatList();
}

async function refreshChatList() {
  try {
    if (state.searchQuery) {
      const results = await API.search(state.searchQuery);
      renderChatList(results);
    } else {
      const chats = await API.chats();
      renderChatList(chats);
      setActiveChatItem(state.chatId);
    }
  } catch(_) {}
}

async function switchChat(id) {
  await loadChat(id);
  await loadModels(); // auto-refresh models on chat switch
}

async function loadChat(id) {
  try {
    const chat = await API.chatGet(id);
    state.chatId   = chat.id;
    state.messages = chat.messages || [];
    state.contextFiles = [];
    state.titleOk  = !!chat.titleOk;
    els.chatTitle.textContent = chat.title || "New chat";
    renderMessages();
    renderContextFiles();
    setActiveChatItem(id);
    saveSettings();
  } catch(_) { newChat(); }
}

function newChat() {
  state.chatId         = "chat-" + Date.now();
  state.messages       = [];
  state.lastPromptTok  = 0;
  state.contextFiles   = [];
  state.titleOk        = false;
  els.chatTitle.textContent = "New chat";
  els.perfInfo.textContent  = "";
  els.statTps.textContent   = "";
  els.statTps.className     = "stat-tps-label";
  renderMessages();
  renderContextFiles();
  setActiveChatItem(null);
  updateCtxStat();
  saveSettings();
}

function setActiveChatItem(id) {
  document.querySelectorAll(".chat-item").forEach(el => {
    el.classList.toggle("active", el.dataset.id === id);
  });
}

// ── Message rendering ─────────────────────────────────────────────────────────
function renderMessages() {
  els.messages.innerHTML = "";
  if (!state.messages.length) { showEmpty(true); return; }
  showEmpty(false);
  state.messages.forEach(m => appendMessage(m.role, m.content, false));
  scrollBottom(false);
}

function showEmpty(show) {
  let es = $("empty-state");
  if (!es) {
    es = document.createElement("div");
    es.id = "empty-state";
    es.innerHTML = `
      <div class="empty-icon">◈</div>
      <div class="empty-title">Sullybase Local LLM Chat</div>
      <div class="empty-sub">Local AI chat — powered by Ollama · v2.2.0</div>`;
    els.messages.prepend(es);
  }
  es.style.display = show ? "flex" : "none";
}

function appendMessage(role, content, stream=false) {
  showEmpty(false);

  const modelName = state.model
    ? state.model.split(":")[0].replace(/-/g," ")
    : "Assistant";

  const wrap  = document.createElement("div");
  wrap.className = `msg ${role}`;

  const label = document.createElement("div");
  label.className = "msg-label";
  label.textContent = role === "user" ? "You" : modelName;

  const body = document.createElement("div");
  body.className = "msg-body" + (stream ? " streaming" : "");

  if (role === "assistant") {
    body.innerHTML = renderAssistantContent(content);
    if (!stream) addCopyListeners(body);
  } else {
    body.textContent = content;
  }

  wrap.appendChild(label);
  wrap.appendChild(body);
  els.messages.appendChild(wrap);
  return body;
}

function renderAssistantContent(raw) {
  let thinkHtml = "";
  const cleaned = raw.replace(/<think>([\s\S]*?)<\/think>/gi, (_, inner) => {
    thinkHtml += makeThinkBlock(inner.trim());
    return "";
  }).trim();
  return thinkHtml + marked.parse(cleaned || "");
}

function makeThinkBlock(text) {
  return `<div class="think-block">
    <button class="think-toggle"><span class="think-chevron">▶</span> Thinking</button>
    <div class="think-body">${escHtml(text)}</div>
  </div>`;
}

function addCopyListeners(container) {
  container.querySelectorAll(".btn-copy").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        const code = decodeURIComponent(escape(atob(btn.dataset.code)));
        await navigator.clipboard.writeText(code);
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1500);
      } catch(_) {}
    });
  });
}

document.addEventListener("click", e => {
  const toggle = e.target.closest(".think-toggle");
  if (toggle) toggle.closest(".think-block").classList.toggle("open");
});

function scrollBottom(smooth=true) {
  els.messagesWrap.scrollTo({top: els.messagesWrap.scrollHeight, behavior: smooth ? "smooth" : "instant"});
}

// ── Send message ──────────────────────────────────────────────────────────────
function setSendingUI(sending) {
  els.btnSend.classList.toggle("sending", sending);
  els.btnSend.title = sending ? "Stop generating" : "Send (Enter)";
  els.btnSend.disabled = sending ? false : !els.userInput.value.trim();
}

function stopGeneration() {
  if (state.abortCtrl) state.abortCtrl.abort();
}

async function sendMessage() {
  if (state.streaming) { stopGeneration(); return; }

  const text = els.userInput.value.trim();
  if (!text) return;

  const model = els.modelSelect.value;
  if (!model) { showStatus("Select a model — is Ollama running?", "err"); return; }

  showEmpty(false);
  state.messages.push({role: "user", content: text});
  appendMessage("user", text);
  els.userInput.value = "";
  resizeTextarea();
  setSendingUI(true);
  scrollBottom();

  const assistantBody = appendMessage("assistant", "", true);
  scrollBottom();

  state.streaming    = true;
  state.firstTokenMs = 0;
  els.perfInfo.textContent = "⏳ Waiting…";

  let accumulated = "";
  let lastEvent   = "";

  state.abortCtrl = new AbortController();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      signal: state.abortCtrl.signal,
      body: JSON.stringify({
        model,
        history:       state.messages.slice(0, -1),
        message:       text,
        context_files: state.contextFiles,
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const dec    = new TextDecoder();
    let buf = "";

    outer: while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});

      const lines = buf.split("\n");
      buf = lines.pop();

      for (const line of lines) {
        if (line.startsWith("event: ")) { lastEvent = line.slice(7).trim(); continue; }
        if (!line.startsWith("data: "))  continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;

        let payload;
        try { payload = JSON.parse(raw); } catch(_) { continue; }

        if (lastEvent === "token") {
          accumulated += payload.token || "";
          assistantBody.innerHTML = renderAssistantContent(accumulated);
          const cur = document.createElement("span");
          cur.className = "stream-cursor";
          assistantBody.appendChild(cur);
          scrollBottom(false);

        } else if (lastEvent === "first_token") {
          state.firstTokenMs = payload.ms || 0;
          els.perfInfo.textContent = `⚡ First token ${state.firstTokenMs}ms`;

        } else if (lastEvent === "error") {
          const msg = payload.message || "Unknown error";
          accumulated = `⚠ ${msg}`;
          assistantBody.innerHTML = `<span class="err-text">${escHtml(msg)}</span>`;
          assistantBody.classList.remove("streaming");
          break outer;

        } else if (lastEvent === "done") {
          finishStream(assistantBody, accumulated, payload);
          break outer;
        }
      }
    }
  } catch(err) {
    if (err.name === "AbortError") {
      accumulated += (accumulated ? "\n\n" : "") + "*Generation stopped.*";
    } else {
      const msg = err.message || "Connection error";
      accumulated = `⚠ ${msg}`;
      assistantBody.innerHTML = `<span class="err-text">${escHtml(msg)}</span>`;
    }
  }

  if (state.streaming) finishStream(assistantBody, accumulated, {});
}

function finishStream(bodyEl, content, stats) {
  if (!state.streaming) return;
  state.streaming = false;
  state.abortCtrl = null;
  setSendingUI(false);

  bodyEl.classList.remove("streaming");
  bodyEl.innerHTML = renderAssistantContent(content);
  addCopyListeners(bodyEl);
  scrollBottom();

  if (stats && stats.tokens_per_sec != null) {
    const tps  = stats.tokens_per_sec || 0;
    const ftMs = state.firstTokenMs || 0;
    const genS = stats.gen_ms ? (stats.gen_ms / 1000).toFixed(1) : "—";
    const ptok = stats.prompt_tokens || 0;
    const ctok = stats.completion_tokens || 0;
    state.lastTps       = tps;
    state.lastPromptTok = ptok;
    els.perfInfo.textContent = `${ptok}↑ ${ctok}↓ · ${tps} t/s · ft ${ftMs}ms · ${genS}s`;
    els.statTps.textContent  = tps ? `${tps} t/s` : "";
    els.statTps.className    = "stat-tps-label" + (tps >= 20 ? " tps-fast" : tps >= 8 ? " tps-mid" : tps ? " tps-slow" : "");
    updateCtxStat();
  }

  state.messages.push({role: "assistant", content});
  persistChat(content);
}

// ── Context stat (used / total) ───────────────────────────────────────────────
function updateCtxStat() {
  const used  = state.lastPromptTok;
  const total = state.modelCtxLen;
  if (!total && !used) {
    els.statCtx.textContent = "—";
    els.statCtxBarWrap.classList.add("hidden");
    return;
  }
  if (!total) {
    els.statCtx.textContent = used ? fmtK(used) + " tok" : "—";
    els.statCtxBarWrap.classList.add("hidden");
    return;
  }
  const pct = Math.min(100, Math.round((used / total) * 100));
  els.statCtx.textContent = `${fmtK(used)} / ${fmtK(total)} (${pct}%)`;
  els.statCtxBar.style.width = pct + "%";
  els.statCtxBar.className = "stat-bar" + (pct > 85 ? " bar-warn" : pct > 95 ? " bar-danger" : "");
  els.statCtxBarWrap.classList.remove("hidden");
}

function fmtK(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

// ── Persist & title ───────────────────────────────────────────────────────────

/** Best-effort fallback title from a string, used while/if AI title gen is unavailable */
function fallbackTitle(text) {
  // Strip markdown, think tags, leading symbols
  let clean = text
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/[#*`>_~\[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  // Drop a common filler opener so the title gets to the actual topic faster,
  // e.g. "Hey can you help me write a poem" -> "write a poem"
  clean = clean.replace(
    /^(hi|hey|hello|ok|okay|so|well|please|thanks|thank you)[,!. ]+/i, ""
  ).replace(
    /^(can|could|would|will) you (please )?(help me )?/i, ""
  ).replace(
    /^i (need|want|would like) (you )?to /i, ""
  ).trim();

  if (!clean) clean = text.trim();

  const words = clean.split(" ").filter(Boolean);
  if (!words.length) return "New chat";
  const title = words.slice(0, 7).join(" ");
  return title.length > 40 ? title.slice(0, 40).trimEnd() + "…" : title;
}

async function persistChat(lastReply = "") {
  const now  = new Date().toISOString();
  let title  = els.chatTitle.textContent;

  // Try (or retry) AI title generation for the first couple of exchanges.
  // If a prior attempt failed (titleOk === false), keep retrying on each new
  // message rather than getting permanently stuck on the word-sliced fallback.
  const shouldTryTitle = !state.titleOk && state.messages.length <= 4;

  if (shouldTryTitle) {
    const model     = els.modelSelect.value;
    const userMsg   = state.messages[0]?.content || "";
    let   aiTitle   = "";
    let   ok        = false;
    try {
      const r = await API.title(model, userMsg, lastReply);
      aiTitle = (r.title || "").trim();
      ok      = !!r.ok;
    } catch(_) {}

    if (ok && aiTitle && aiTitle !== "New chat") {
      title = aiTitle;
      state.titleOk = true;
    } else if (title === "New chat" || !state.titleOk) {
      // Use a reasonable fallback in the meantime, but leave titleOk false
      // so we try again next message.
      title = fallbackTitle(lastReply || userMsg);
    }
    els.chatTitle.textContent = title;
  }

  const chat = {
    id: state.chatId, title, created: now, updated: now,
    messages: state.messages, titleOk: state.titleOk,
  };
  await API.chatSave(state.chatId, chat);
  await refreshChatList();
}

// ── Export chat ───────────────────────────────────────────────────────────────
function toggleExportMenu() {
  els.exportMenu.classList.toggle("hidden");
}

function closeExportMenu() {
  els.exportMenu.classList.add("hidden");
}

function exportChat(fmt) {
  if (!state.messages.length) {
    showStatus("Nothing to export yet", "err");
    closeExportMenu();
    return;
  }

  const title = els.chatTitle.textContent || "chat";
  const safeName = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "chat";
  let content, mime, ext;

  if (fmt === "json") {
    content = JSON.stringify({
      id: state.chatId, title, model: state.model,
      exported: new Date().toISOString(), messages: state.messages,
    }, null, 2);
    mime = "application/json";
    ext  = "json";

  } else if (fmt === "txt") {
    content = state.messages.map(m => {
      const who = m.role === "user" ? "You" : (state.model ? state.model.split(":")[0] : "Assistant");
      return `${who}:\n${stripThink(m.content)}`;
    }).join("\n\n" + "─".repeat(40) + "\n\n");
    mime = "text/plain";
    ext  = "txt";

  } else { // markdown
    const lines = [`# ${title}`, ""];
    state.messages.forEach(m => {
      const who = m.role === "user" ? "You" : (state.model ? state.model.split(":")[0] : "Assistant");
      lines.push(`### ${who}`, "", stripThink(m.content), "");
    });
    content = lines.join("\n");
    mime = "text/markdown";
    ext  = "md";
  }

  const blob = new Blob([content], {type: mime + ";charset=utf-8"});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url;
  a.download = `${safeName}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  closeExportMenu();
}

function stripThink(text) {
  return text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
}

// ── Context files ─────────────────────────────────────────────────────────────
function toggleContextPanel() {
  const hidden = els.contextPanel.classList.toggle("hidden");
  els.btnContext.classList.toggle("active", !hidden);
}

async function addContextPath(pathOverride) {
  const raw = pathOverride || els.contextPathInput.value.trim();
  if (!raw) return;
  setContextStatus("Loading…", "");

  try {
    const r = await API.context(raw);
    if (r.error) { setContextStatus(r.error, "err"); return; }
    r.files.forEach(f => {
      if (!state.contextFiles.find(x => x.path === f.path)) state.contextFiles.push(f);
    });
    setContextStatus(`Added ${r.files.length} file(s)`, "ok");
    els.contextPathInput.value = "";
    renderContextFiles();
  } catch(err) {
    setContextStatus(`Error: ${err.message}`, "err");
  }
}

function setContextStatus(msg, cls) {
  els.contextStatus.textContent = msg;
  els.contextStatus.className   = cls;
}

function renderContextFiles() {
  els.contextFileList.innerHTML = "";
  state.contextFiles.forEach((f, i) => {
    const item = document.createElement("div");
    item.className = "ctx-file-item";
    item.innerHTML = `
      <span class="ctx-file-label">${escHtml(f.label)}</span>
      <button class="ctx-file-del" data-idx="${i}" title="Remove">×</button>`;
    item.querySelector(".ctx-file-del").addEventListener("click", () => {
      state.contextFiles.splice(i, 1);
      renderContextFiles();
    });
    els.contextFileList.appendChild(item);
  });

  const n = state.contextFiles.length;
  if (n > 0) {
    els.contextBadge.textContent = `${n} file${n!==1?"s":""} attached`;
    els.contextBadge.classList.remove("hidden");
  } else {
    els.contextBadge.classList.add("hidden");
  }
}

// ── File browser (native dialog via server) ───────────────────────────────────
async function openFileBrowser() {
  try {
    const d = await API.browse("file");
    if (!d.path) return;
    // Open context panel
    els.contextPanel.classList.remove("hidden");
    els.btnContext.classList.add("active");
    els.contextPathInput.value = d.path;
    addContextPath(d.path);
  } catch(err) {
    showStatus("Browse failed: " + err.message, "err");
  }
}

// ── Stats polling ─────────────────────────────────────────────────────────────
let statsTimer = null;

function startStatsPoller() {
  updateStats();
  statsTimer = setInterval(updateStats, 10000);
}

async function updateStats() {
  const model = els.modelSelect.value;
  try {
    const s = await API.ps(model);

    // Device badge
    if (s.device) {
      els.statDevice.textContent = `⬡ ${s.device}`;
      els.statDevice.className = "stat-device-label " + (s.device === "GPU" ? "device-gpu" : "device-cpu");
    } else {
      els.statDevice.textContent = "⬡ Ollama";
      els.statDevice.className = "stat-device-label";
    }

    // Model info row: "7B · Q4_K_M"
    const parts = [];
    if (s.parameter_size) parts.push(s.parameter_size);
    if (s.quantization)   parts.push(s.quantization);
    els.statModelInfo.textContent = parts.length ? parts.join(" · ") : (model ? model.split(":")[0] : "—");

    // VRAM bar
    if (s.vram_used_mb && s.vram_total_mb) {
      const pct = Math.min(100, Math.round((s.vram_used_mb / s.vram_total_mb) * 100));
      els.statVram.textContent = `${fmtMB(s.vram_used_mb)} / ${fmtMB(s.vram_total_mb)} (${pct}%)`;
      els.statVramBar.style.width = pct + "%";
      els.statVramBar.className = "stat-bar" + (pct > 85 ? " bar-warn" : pct > 95 ? " bar-danger" : "");
      els.statVramBarWrap.classList.remove("hidden");
    } else if (s.vram_used_mb) {
      els.statVram.textContent = `${fmtMB(s.vram_used_mb)} used`;
      els.statVramBarWrap.classList.add("hidden");
    } else {
      els.statVram.textContent = s.device === "CPU" ? "CPU only" : "—";
      els.statVramBarWrap.classList.add("hidden");
    }

    // Store model context length for ctx% display
    if (s.context_length) state.modelCtxLen = s.context_length;
    updateCtxStat();
  } catch(_) {
    els.statDevice.textContent = "⬡ offline";
    els.statDevice.className = "stat-device-label";
  }
}

function fmtMB(mb) {
  if (!mb) return "0 MB";
  if (mb >= 1024) return (mb / 1024).toFixed(1) + " GB";
  return mb + " MB";
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function resizeTextarea() {
  els.userInput.style.height = "auto";
  els.userInput.style.height = Math.min(els.userInput.scrollHeight, 180) + "px";
}

function saveSettings() {
  API.settingsSave({model: state.model, current_chat_id: state.chatId}).catch(()=>{});
}

function showStatus(msg, type) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function clearContext() {
  if (!state.contextFiles.length) return;
  state.contextFiles = [];
  renderContextFiles();
  setContextStatus("Context cleared", "ok");
}

// ── Delete dialog ─────────────────────────────────────────────────────────────
function openDeleteDialog(id) {
  state.pendingDel = id;
  els.deleteDialog.showModal();
}

async function confirmDelete() {
  const id = state.pendingDel;
  els.deleteDialog.close();
  if (!id) return;
  await API.chatDel(id);
  if (id === state.chatId) newChat();
  else await refreshChatList();
}

// ── Event listeners ───────────────────────────────────────────────────────────
function attachEventListeners() {
  els.btnSend.addEventListener("click", sendMessage);

  els.userInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  els.userInput.addEventListener("input", () => {
    if (!state.streaming) els.btnSend.disabled = !els.userInput.value.trim();
    resizeTextarea();
  });

  els.modelSelect.addEventListener("change", () => {
    state.model    = els.modelSelect.value;
    state.modelCtxLen = 0;
    saveSettings();
    updateStats();
    document.querySelectorAll(".msg.assistant .msg-label").forEach(el => {
      el.textContent = state.model
        ? state.model.split(":")[0].replace(/-/g," ")
        : "Assistant";
    });
  });

  els.btnNewChat.addEventListener("click", newChat);
  els.btnClear.addEventListener("click", clearContext);
  els.btnContext.addEventListener("click", toggleContextPanel);
  els.btnBrowse.addEventListener("click", openFileBrowser);

  els.btnExport.addEventListener("click", e => { e.stopPropagation(); toggleExportMenu(); });
  els.exportMenu.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => exportChat(btn.dataset.fmt));
  });
  document.addEventListener("click", e => {
    if (!els.exportMenu.classList.contains("hidden") &&
        !e.target.closest("#export-wrap")) closeExportMenu();
  });

  els.btnRefreshModels.addEventListener("click", async () => {
    els.btnRefreshModels.classList.add("spinning");
    await loadModels();
    els.btnRefreshModels.classList.remove("spinning");
  });

  els.btnContextAdd.addEventListener("click", () => addContextPath());
  els.contextPathInput.addEventListener("keydown", e => {
    if (e.key === "Enter") addContextPath();
  });

  els.deleteCancel.addEventListener("click",  () => els.deleteDialog.close());
  els.deleteConfirm.addEventListener("click", confirmDelete);
  els.deleteDialog.addEventListener("click",  e => {
    if (e.target === els.deleteDialog) els.deleteDialog.close();
  });

  els.chatSearchInput.addEventListener("input", onChatSearchInput);
  els.chatSearchClear.addEventListener("click", clearChatSearch);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", init);