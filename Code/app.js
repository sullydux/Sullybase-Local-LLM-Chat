/* ═══════════════════════════════════════════════════════════════
   Sullybase Local LLM Chat v2.4.0 — app.js
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
  appVersion:    "",
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
  btnScrollBottom:  $("btn-scroll-bottom"),
  userInput:        $("user-input"),
  btnSend:          $("btn-send"),
  btnNewChat:       $("btn-new-chat"),
  btnRefreshModels: $("btn-refresh-models"),
  btnContext:       $("btn-context"),
  btnBrowse:        $("btn-browse"),
  btnClear:         $("btn-clear"),
  contextPanel:     $("context-panel"),
  contextPathInput: $("context-path-input"),
  btnContextAdd:    $("btn-context-add"),
  contextStatus:    $("context-status"),
  contextFileList:  $("context-file-list"),
  contextBadge:     $("context-badge"),
  perfInfo:         $("perf-info"),
  statDevice:       $("stat-device"),
  statVram:         $("stat-vram"),
  statVramKey:      $("stat-vram-key"),
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
    if (s.version) {
      state.appVersion = s.version;
      const vStr = "v" + s.version;
      const logoVer = $("logo-version");
      if (logoVer) logoVer.textContent = vStr;
      const emptyVer = $("empty-version");
      if (emptyVer) emptyVer.textContent = vStr;
      document.title = "Sullybase Local LLM Chat " + vStr;
    }
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
    loadDraft();
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
  els.btnScrollBottom.classList.remove("show");
  renderMessages();
  renderContextFiles();
  setActiveChatItem(null);
  updateCtxStat();
  loadDraft();
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
  state.messages.forEach(m => appendMessage(m.role, m.content, false, m.ts || ""));
  scrollBottom(false);
  refreshMsgActions();
}

function showEmpty(show) {
  let es = $("empty-state");
  if (!es) {
    es = document.createElement("div");
    es.id = "empty-state";
    const vStr = state.appVersion ? "v" + state.appVersion : "v…";
    es.innerHTML = `
      <div class="empty-icon">◈</div>
      <div class="empty-title">Sullybase Local LLM Chat</div>
      <div class="empty-sub">Local AI chat — powered by Ollama · <span id="empty-version">${vStr}</span></div>`;
    els.messages.prepend(es);
  }
  es.style.display = show ? "flex" : "none";
}

function appendMessage(role, content, stream=false, ts="") {
  showEmpty(false);

  const modelName = state.model
    ? state.model.split(":")[0].replace(/-/g," ")
    : "Assistant";

  const wrap  = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.dataset.role = role;

  // Assistant messages get a dim timestamp above the label. User
  // messages don't (per spec) — keeps the user side clean.
  if (role === "assistant" && ts) {
    const tsEl = document.createElement("div");
    tsEl.className = "msg-ts";
    tsEl.textContent = fmtTs(ts);
    wrap.appendChild(tsEl);
  }

  const label = document.createElement("div");
  label.className = "msg-label";
  label.textContent = role === "user" ? "You" : modelName;
  wrap.appendChild(label);

  const body = document.createElement("div");
  body.className = "msg-body" + (stream ? " streaming" : "");

  if (role === "assistant") {
    body.innerHTML = renderAssistantContent(content);
    if (!stream) addCopyListeners(body);
  } else {
    body.textContent = content;
  }
  wrap.appendChild(body);

  // Hover action row. Only attach for finalized (non-streaming)
  // messages — the streaming assistant bubble gets its actions once
  // it completes, in finishStream.
  if (!stream) appendMsgActions(wrap, role);

  els.messages.appendChild(wrap);
  return body;
}

// Format an ISO timestamp as "2:45 PM" when same day as now, else a
// short date like "Jun 18". Used for the assistant message label.
function fmtTs(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  if (sameDay) {
    let h = d.getHours();
    const m = String(d.getMinutes()).padStart(2, "0");
    const ampm = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return `${h}:${m} ${ampm}`;
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// Build the hover action buttons for a message. Regenerate only
// applies to the *last* assistant message — the caller is
// responsible for stripping it from older ones (refreshMsgActions).
function appendMsgActions(wrap, role) {
  const row = document.createElement("div");
  row.className = "msg-actions";

  if (role === "assistant") {
    const regen = document.createElement("button");
    regen.type = "button";
    regen.className = "btn-regen";
    regen.textContent = "↻ Regenerate";
    regen.title = "Regenerate reply";
    regen.addEventListener("click", () => regenerateAt(wrap));
    row.appendChild(regen);
  } else {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn-edit";
    edit.textContent = "✎ Edit";
    edit.title = "Edit & resend";
    edit.addEventListener("click", () => beginEdit(wrap));
    row.appendChild(edit);
  }

  wrap.appendChild(row);
}

// After a render or a new reply, only the final assistant message
// should show Regenerate. Walk the list and hide/remove the action
// row on every assistant message except the last one.
function refreshMsgActions() {
  const assistantMsgs = els.messages.querySelectorAll(".msg.assistant");
  assistantMsgs.forEach((m, i, arr) => {
    const isLast = i === arr.length - 1;
    let row = m.querySelector(".msg-actions");
    if (isLast && !row) appendMsgActions(m, "assistant");
    else if (!isLast && row) row.remove();
  });
}

function renderAssistantContent(raw) {
  // Captures both fully-closed <think>…</think> blocks and an unclosed
  // <think>… running to the end of the stream. Thinking models emit the
  // opening tag first and stream their reasoning token-by-token before the
  // closing tag arrives, so the trailing-`$` alternative is what keeps that
  // live reasoning inside the collapsible block instead of leaking into the
  // rendered answer.
  let thinkHtml = "";
  let clean     = "";
  let last      = 0;
  const re = /<think>([\s\S]*?)(<\/think>|$)/gi;
  let m;
  while ((m = re.exec(raw)) !== null) {
    clean    += raw.slice(last, m.index);
    const inner  = m[1].trim();
    const closed = m[2].toLowerCase() === "</think>";
    // Skip empty closed blocks (e.g. a non-thinking model echoing the tags)
    // but always render a live one so the streaming cursor has a home.
    if (inner || !closed) thinkHtml += makeThinkBlock(inner, !closed);
    last = m.index + m[0].length;
  }
  clean += raw.slice(last);
  return thinkHtml + marked.parse(clean.trim() || "");
}

function makeThinkBlock(text, streaming=false) {
  // While streaming (no closing tag yet) keep the block expanded so the user
  // can watch the reasoning arrive live; once closed it collapses to a summary
  // they can re-expand on demand.
  const open  = streaming ? " open" : "";
  const label = streaming ? "Thinking…" : "Thought process";
  return `<div class="think-block${open}">
    <button class="think-toggle"><span class="think-chevron">▶</span> ${label}</button>
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
  const userTs = new Date().toISOString();
  state.messages.push({role: "user", content: text, ts: userTs});
  appendMessage("user", text, false, userTs);
  els.userInput.value = "";
  clearDraft();
  resizeTextarea();
  setSendingUI(true);
  scrollBottom();

  const assistantBody = appendMessage("assistant", "", true);
  scrollBottom();

  await runStream(model, state.messages.slice(0, -1), text, assistantBody);
}

// Shared streaming core used by sendMessage, regenerateAt and
// beginEdit's save. Streams from /api/chat into the given assistant
// body element, updating state.messages and persisting on completion.
// `history` is the conversation before the final user turn; `userText`
// is that final user prompt (already pushed to state.messages by the
// caller when sending new, but for regenerate there is no new user
// turn — see the override below).
async function runStream(model, history, userText, assistantBody, opts={}) {
  // For regenerate: we reuse the existing trailing user turn as the
  // prompt and stream a fresh assistant reply into a fresh body.
  const sendingNew = !opts.regenerate;
  if (sendingNew) {
    state.messages.push({role: "assistant", content: ""});
  }

  state.streaming    = true;
  state.firstTokenMs = 0;
  state.abortCtrl    = new AbortController();
  els.perfInfo.textContent = "⏳ Waiting…";

  let accumulated = "";
  let lastEvent   = "";

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      signal: state.abortCtrl.signal,
      body: JSON.stringify({
        model,
        history,
        message:       userText,
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
          // Preserve the user's manual collapse of a live thinking block across
          // the full re-render below — otherwise it snaps back open on the
          // next token (the block auto-expands while its closing tag hasn't
          // arrived yet).
          const prevThink = assistantBody.querySelector(".think-block");
          const userCollapsed = prevThink && !prevThink.classList.contains("open");
          assistantBody.innerHTML = renderAssistantContent(accumulated);
          if (userCollapsed) {
            const fresh = assistantBody.querySelector(".think-block");
            if (fresh) fresh.classList.remove("open");
          }
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

// Regenerate the assistant reply whose .msg wrapper is `wrap`. Drops
// that reply from state and re-streams from the preceding user turn.
async function regenerateAt(wrap) {
  if (state.streaming) return;
  const model = els.modelSelect.value;
  if (!model) { showStatus("Select a model — is Ollama running?", "err"); return; }

  // Find this message's index among rendered messages so we can line
  // it up with state.messages (they share order: user/assistant pairs).
  const allMsgs = [...els.messages.querySelectorAll(".msg")];
  const idx = allMsgs.indexOf(wrap);
  if (idx === -1) return;

  // Truncate state to everything before this assistant reply, then
  // drop the assistant turn itself; the trailing user turn becomes
  // the prompt for the regeneration.
  state.messages = state.messages.slice(0, idx);
  const userTurn = state.messages[state.messages.length - 1];
  if (!userTurn || userTurn.role !== "user") return;
  const userText = userTurn.content;
  const history  = state.messages.slice(0, -1);

  // Replace the old reply bubble with a fresh streaming one in place.
  const freshBody = document.createElement("div");
  freshBody.className = "msg-body streaming";
  const oldBody = wrap.querySelector(".msg-body");
  oldBody.replaceWith(freshBody);
  // Drop any stale action row so finishStream can re-add the new one.
  wrap.querySelector(".msg-actions")?.remove();

  setSendingUI(true);
  await runStream(model, history, userText, freshBody, {regenerate: true});
}

// Inline-edit a user message: swap its body for a textarea. Save
// truncates the conversation to that point and regenerates from the
// edited prompt (discard-and-resend, per the chosen behavior).
function beginEdit(wrap) {
  if (state.streaming) return;
  const allMsgs = [...els.messages.querySelectorAll(".msg")];
  const idx = allMsgs.indexOf(wrap);
  if (idx === -1) return;

  const body = wrap.querySelector(".msg-body");
  if (!body || wrap.querySelector(".msg-edit-wrap")) return; // already editing

  const original = state.messages[idx]?.content || "";
  const holder = document.createElement("div");
  holder.className = "msg-edit-wrap";
  const area = document.createElement("textarea");
  area.className = "msg-edit-area";
  area.value = original;
  const btns = document.createElement("div");
  btns.className = "msg-edit-btns";
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.textContent = "Cancel";
  cancel.className = "btn-copy";
  const save = document.createElement("button");
  save.type = "button"; save.textContent = "Save & resend";
  save.className = "btn-copy";
  btns.appendChild(cancel); btns.appendChild(save);
  holder.appendChild(area); holder.appendChild(btns);
  body.replaceWith(holder);
  area.focus();
  // Grow to fit and place the cursor at the end.
  const grow = () => { area.style.height = "auto"; area.style.height = Math.min(area.scrollHeight, 220) + "px"; };
  area.addEventListener("input", grow); grow();
  area.setSelectionRange(area.value.length, area.value.length);

  cancel.addEventListener("click", () => renderMessages());

  save.addEventListener("click", async () => {
    const next = area.value.trim();
    if (!next || !area.value.trim()) { renderMessages(); return; }

    // Truncate everything from this user message onward, then push the
    // edited text as the new prompt and regenerate the assistant reply.
    state.messages = state.messages.slice(0, idx);
    const userTs = new Date().toISOString();
    state.messages.push({role: "user", content: next, ts: userTs});

    renderMessages();
    setSendingUI(true);
    const assistantBody = appendMessage("assistant", "", true);
    scrollBottom();

    const model = els.modelSelect.value;
    if (!model) { showStatus("Select a model — is Ollama running?", "err"); return; }
    await runStream(model, state.messages.slice(0, -1), next, assistantBody);
  });
}

function finishStream(bodyEl, content, stats) {
  if (!state.streaming) return;
  state.streaming = false;
  state.abortCtrl = null;
  setSendingUI(false);

  const assistantTs = new Date().toISOString();

  bodyEl.classList.remove("streaming");
  bodyEl.innerHTML = renderAssistantContent(content);
  addCopyListeners(bodyEl);

  // Attach the timestamp + hover-action row to this message wrapper
  // (the streaming bubble had neither), then re-evaluate which
  // assistant message owns the Regenerate button.
  const wrap = bodyEl.closest(".msg");
  if (wrap) {
    // Fresh timestamp above the label, since none was rendered while
    // streaming. Skip if one already exists (e.g. a re-rendered chat).
    if (!wrap.querySelector(".msg-ts")) {
      const tsEl = document.createElement("div");
      tsEl.className = "msg-ts";
      tsEl.textContent = fmtTs(assistantTs);
      const label = wrap.querySelector(".msg-label");
      if (label) label.before(tsEl); else wrap.prepend(tsEl);
    }
    if (!wrap.querySelector(".msg-actions")) appendMsgActions(wrap, "assistant");
  }

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

  // runStream pre-pushed a placeholder assistant turn — finalize it
  // in place with the real content + timestamp rather than pushing a
  // second entry.
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "assistant") {
    last.content = content;
    last.ts = assistantTs;
  } else {
    state.messages.push({role: "assistant", content, ts: assistantTs});
  }

  if (wrap) refreshMsgActions();
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
  els.statCtxBar.className = "stat-bar" +
    (pct > 95 ? " bar-danger" : pct > 85 ? " bar-warn" : "");
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

  // No model selected yet — show an idle state, not stale numbers.
  if (!model) {
    els.statDevice.textContent     = "⬡ no model";
    els.statDevice.className       = "stat-device-label";
    els.statModelInfo.textContent  = "—";
    els.statVram.textContent       = "—";
    els.statVramKey.textContent    = "Memory";
    els.statVramBarWrap.classList.add("hidden");
    updateCtxStat();
    return;
  }

  try {
    const s = await API.ps(model);

    // ── Device / accelerator badge ────────────────────────────────
    // accelerator is one of: Metal (Apple Silicon), GPU (discrete/CUDA),
    // or CPU (no offload). Color accordingly so the row is meaningful
    // on and off Apple Silicon.
    const accel = s.accelerator || s.device || "";
    if (accel) {
      els.statDevice.textContent = `⬡ ${accel}`;
      let cls = "stat-device-label";
      if (accel === "Metal" || accel === "GPU") cls += " device-gpu";
      else if (accel === "CPU")                  cls += " device-cpu";
      els.statDevice.className = cls;
    } else {
      els.statDevice.textContent = "⬡ Ollama";
      els.statDevice.className = "stat-device-label";
    }

    // ── Model info row: "7B · Q4_K_M" (or model name if details missing) ──
    const parts = [];
    if (s.parameter_size) parts.push(s.parameter_size);
    if (s.quantization)   parts.push(s.quantization);
    els.statModelInfo.textContent = parts.length
      ? parts.join(" · ")
      : (model ? model.split(":")[0] : "—");

    // ── Memory row ─────────────────────────────────────────────────
    // Label adapts to the machine: "VRAM" for discrete GPUs, "Memory"
    // for Apple Silicon unified memory, or when running on CPU.
    const unified = s.memory_kind === "unified" || accel === "Metal" || accel === "CPU";
    els.statVramKey.textContent = unified ? "Memory" : "VRAM";

    const used  = s.vram_used_mb  || 0;
    const total = s.vram_total_mb || 0;

    if (used && total) {
      const pct = Math.min(100, Math.round((used / total) * 100));
      els.statVram.textContent = `${fmtMB(used)} / ${fmtMB(total)} (${pct}%)`;
      els.statVramBar.style.width = pct + "%";
      // Order matters: check the higher threshold first.
      els.statVramBar.className = "stat-bar" +
        (pct > 95 ? " bar-danger" : pct > 85 ? " bar-warn" : "");
      els.statVramBarWrap.classList.remove("hidden");
    } else if (used) {
      els.statVram.textContent = `${fmtMB(used)} used`;
      els.statVramBarWrap.classList.add("hidden");
    } else if (accel === "CPU") {
      els.statVram.textContent = "CPU only";
      els.statVramBarWrap.classList.add("hidden");
    } else if (s.model_loaded === false) {
      // Model isn't resident yet (e.g. just selected, never chatted).
      els.statVram.textContent = "idle";
      els.statVramBarWrap.classList.add("hidden");
    } else {
      els.statVram.textContent = "—";
      els.statVramBarWrap.classList.add("hidden");
    }

    // Store model context length for the ctx% display.
    if (s.context_length) state.modelCtxLen = s.context_length;
    updateCtxStat();
  } catch(_) {
    els.statDevice.textContent    = "⬡ offline";
    els.statDevice.className      = "stat-device-label";
    els.statVramKey.textContent   = "Memory";
    els.statVram.textContent      = "—";
    els.statVramBarWrap.classList.add("hidden");
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

// ── Draft persistence ─────────────────────────────────────────────────────────
// Stash unsent textarea text per-chat so switching chats (or reloading)
// doesn't lose what the user was typing. Lives in localStorage; cleared
// on send. Keyed by chat id.
function draftKey() { return `sullybase:draft:${state.chatId || "default"}`; }
function saveDraft() {
  const v = els.userInput.value;
  try {
    if (v) localStorage.setItem(draftKey(), v);
    else   localStorage.removeItem(draftKey());
  } catch(_) {}
}
function loadDraft() {
  try {
    els.userInput.value = localStorage.getItem(draftKey()) || "";
  } catch(_) { els.userInput.value = ""; }
  resizeTextarea();
  if (!state.streaming) els.btnSend.disabled = !els.userInput.value.trim();
}
function clearDraft() {
  try { localStorage.removeItem(draftKey()); } catch(_) {}
}

// ── Scroll-to-bottom button ───────────────────────────────────────────────────
// Show a floating ↓ when the user has scrolled up from the newest
// message; hide it while streaming (auto-scroll is already active).
function onMessagesScroll() {
  if (state.streaming) { els.btnScrollBottom.classList.remove("show"); return; }
  const wrap = els.messagesWrap;
  const distFromBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight;
  els.btnScrollBottom.classList.toggle("show", distFromBottom > 150);
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
// Cmd/Ctrl+N → new chat, Cmd/Ctrl+K → focus search, Esc → close panels/dialogs.
// Suppressed while typing in a text field for N/K; Esc always works.
function onGlobalKeydown(e) {
  const mod = e.metaKey || e.ctrlKey;
  const typing = /^(input|textarea)$/i.test(e.target.tagName) || e.target.isContentEditable;

  if (e.key === "Escape") {
    if (els.deleteDialog.open) { els.deleteDialog.close(); e.preventDefault(); return; }
    if (!els.contextPanel.classList.contains("hidden")) {
      els.contextPanel.classList.add("hidden");
      els.btnContext.classList.remove("active");
      e.preventDefault();
      return;
    }
  }

  if (mod && (e.key === "n" || e.key === "N") && !typing) {
    e.preventDefault();
    newChat();
    return;
  }
  if (mod && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    els.chatSearchInput.focus();
    els.chatSearchInput.select();
    return;
  }
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
    saveDraft();
  });

  // Scroll-to-bottom button + live show/hide on scroll.
  els.btnScrollBottom.addEventListener("click", () => scrollBottom(true));
  els.messagesWrap.addEventListener("scroll", onMessagesScroll);

  // Global keyboard shortcuts.
  document.addEventListener("keydown", onGlobalKeydown);

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