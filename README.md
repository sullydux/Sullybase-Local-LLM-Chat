<div align="center">
<img src="Code/Icon.png" alt="Sullybase Icon" style="float: left; margin-right: 15px; width: 240px; height: 240px;">
</div>

<div align="center">

# [Sullybase Local LLM Chat](https://sullydux.github.io/Sullybase-Local-LLM-Chat/)

v2.4.1
</div>

A lightweight desktop app for chatting with local LLMs via **Ollama**. All conversations stay on your device — no external data transmission.

---

## Architecture

The app is built on a **Flask + pywebview** stack:

- `server.py` — Flask backend: Ollama API proxy, chat/settings persistence, context file loading, file browser
- `app.py` — Desktop launcher: starts Flask in a daemon thread, waits for it to be ready, then opens a pywebview window
- `index.html` / `app.js` / `style.css` — Frontend served by Flask as static files

Chats and settings are stored as JSON files in the app support directory:
- **macOS**: `~/Library/Application Support/Sullybase-LLM-Chat/`

---

## Features

### Sidebar
- **Model selector** with refresh button — lists all locally available Ollama models
- **Chat history** — browse, search (with snippet preview), and switch between past conversations
- **Stats panel** — live GPU/CPU device badge, VRAM usage bar, context window usage bar, tokens/sec

### Chat Interface
- **Streaming responses** with blinking cursor and stop button
- **Markdown rendering** — headings, code blocks with syntax highlighting and copy button, tables, blockquotes, lists
- **Thinking block support** — collapsible `<think>...</think>` sections for reasoning models
- **AI-generated chat titles** with word-sliced fallback; retries on subsequent messages if the first attempt fails
- **Context files** — attach local files or folders (up to 2 MB per file) to inject into the system prompt; re-read from disk on each message
- **Regenerate & edit** — re-roll the last reply, or edit a prior user message to resend from that point (the following replies are discarded and regenerated)
- **Draft persistence** — unsent text is kept per-chat across switches and reloads
- **Keyboard shortcuts** — `Cmd/Ctrl+N` new chat, `Cmd/Ctrl+K` search, `Esc` closes panels
- **Scroll-to-bottom button** — appears when you've scrolled up in a long chat
- **Message timestamps** — each assistant reply shows when it was sent (time same-day, date otherwise)

### Performance info bar
Shows prompt tokens ↑, completion tokens ↓, tokens/sec, first-token latency, and total generation time after each response.

---

## Requirements

- **Python 3.14+**
- Dependencies: see `requirements.txt` (`flask`, `pywebview`, `requests`)
- **[Ollama](https://ollama.comhttps://ollama.comvv)** installed and running. Install by:
• Clicking the link above, or
• Running this in terminal
  ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

---

## Setup

1. **Download** the [Code](https://github.com/sullydux/Sullybase-Local-LLM-Chat/releases/latest) folder from this repository
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Run**:
   ```bash
   python app.py
   ```
4. **Connect Ollama** — open Ollama, then click **↻** in the sidebar to load models

### Optional: macOS Automator Shortcut

You can create a double-clickable launcher using Automator:

1. Open **Automator** → New Document → **Application**
2. Add a **Run Shell Script** action
3. Set the script to:
   ```bash
   cd /path/to/repo
   python app.py
   ```
4. Save the Automator app anywhere (e.g. Applications or your Desktop)

---

## Notes

- **Privacy**: All data stays local — no external network calls except to `localhost:11434` (Ollama)
- **Logging**: Rotating logs written to the app support directory (`logs/sullybase.log`)
- **Thinking models**: Thinking is parsed and shown as collapsible sections
- **macOS file browser**: Uses `osascript` (AppleScript) to avoid thread-safety issues with tkinter

---

## Model Support

Developed and tested on an Apple M2 Air (8 GB RAM) with `qwen2.5-coder:3b` and 'qwen3.5:4b-mlx'. Any Ollama-compatible model should work given sufficient RAM and compute.

---

## Contributing

1. Open an issue to discuss the change first
2. Fork, implement, and submit a pull request

Use **GitHub Issues** for bug reports and feature requests.

---

## Possible Updates

- Agentic mode