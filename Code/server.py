#!/usr/bin/env python3
"""
server.py — Flask backend for Sullybase Local LLM Chat v2.2.0
"""

import gc
import json
import logging
import os
import sys
import re
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

logger = logging.getLogger("sullybase")

APP_NAME    = "Sullybase Local LLM Chat"
APP_VERSION = "2.2.0"

AI_SYSTEM_PROMPT = (
    "You are a helpful local AI assistant running via Ollama. "
    "You assist with coding, science, writing, and general questions. "
    "Be clear, concise, and technically accurate.\n\n"
    "You have full Markdown rendering support. Use it freely:\n"
    "• **bold**, *italic*, `inline code`\n"
    "• # Heading 1 / ## Heading 2 / ### Heading 3\n"
    "• Fenced code blocks with language hints (```python) for syntax highlighting\n"
    "• - Unordered lists, 1. Ordered lists\n"
    "• > Blockquotes\n"
    "• | Tables | with | headers |\n"
    "• --- Horizontal rules\n\n"
    "Always use appropriate Markdown. Do NOT wrap everything in ```markdown."
)

OLLAMA_BASE          = "http://localhost:11434"
OLLAMA_TIMEOUT       = (10, 300)
OLLAMA_TITLE_TIMEOUT = (10, 30)
MAX_FILE_SIZE        = 2 * 1024 * 1024
NETWORK_RETRIES      = 2
NETWORK_RETRY_DELAY  = 0.8
DEFAULT_CHAT_TITLE   = "New chat"
MAX_TITLE_LEN        = 40

EXCLUDED_NAMES: set = {
    ".DS_Store", ".ds_store", "Thumbs.db", ".git",
    ".gitattributes", ".gitignore", "desktop.ini",
}
EXCLUDED_SUFFIXES: set = {
    ".ds_store", ".swp", ".swo", ".tmp", ".temp", ".bak",
    ".bin", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".class", ".jar", ".war",
    ".log", ".pid", ".lock",
}
ALLOWED_TEXT_EXTS: set = {
    ".md", ".markdown", ".txt", ".rst", ".py", ".js", ".ts",
    ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sh", ".bash",
    ".zsh", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".json", ".xml", ".html", ".htm", ".css", ".scss", ".sql",
    ".r", ".m", ".lua", ".tf", ".env", "",
}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    role: str
    content: str

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d): return cls(role=d.get("role",""), content=d.get("content",""))


@dataclass
class ChatMeta:
    id:       str
    title:    str
    created:  str
    updated:  str
    messages: List[ChatMessage] = field(default_factory=list)
    titleOk:  bool = False

    def to_dict(self):
        return {
            "id": self.id, "title": self.title,
            "created": self.created, "updated": self.updated,
            "messages": [m.to_dict() for m in self.messages],
            "titleOk": self.titleOk,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d.get("id",""), title=d.get("title", DEFAULT_CHAT_TITLE),
            created=d.get("created",""), updated=d.get("updated",""),
            messages=[ChatMessage.from_dict(m) for m in d.get("messages", [])],
            titleOk=bool(d.get("titleOk", False)),
        )


# ── File I/O ──────────────────────────────────────────────────────────────────

_file_lock = threading.RLock()


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists(): return None
    try:
        with _file_lock, open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error(f"_read_json {path}: {exc}")
        return None


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".{path.name}.tmp"
        with _file_lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp.replace(path)
        return True
    except Exception as exc:
        logger.error(f"_write_json {path}: {exc}")
        return False


def _read_text(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_SIZE: return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as exc:
        logger.error(f"_read_text {path}: {exc}")
        return None


# ── Path helpers ──────────────────────────────────────────────────────────────

def _is_excluded(p: Path) -> bool:
    if p.name in EXCLUDED_NAMES or p.name.lower() in EXCLUDED_NAMES: return True
    if p.suffix.lower() in EXCLUDED_SUFFIXES: return True
    if p.name.startswith(".") and p.suffix.lower() not in ALLOWED_TEXT_EXTS: return True
    return False


def _is_text(p: Path) -> bool:
    return p.suffix.lower() in ALLOWED_TEXT_EXTS


def validate_path(raw: str) -> Tuple[bool, str]:
    if not raw.strip(): return False, ""
    try:
        p = Path(raw.strip()).expanduser().resolve()
        if not p.exists(): return False, f"✗ Not found: {p}"
        if p.is_file():
            if _is_excluded(p): return False, f"✗ Excluded: {p.name}"
            if not _is_text(p): return False, f"✗ Not a text file: {p.suffix or '(no ext)'}"
            size = p.stat().st_size
            if size > MAX_FILE_SIZE: return False, f"✗ Too large ({size/1024:.0f} KB > 2 MB)"
            return True, f"✓ File · {p.name} ({size/1024:.0f} KB)"
        if p.is_dir():
            count = sum(
                1 for c in p.rglob("*")
                if c.is_file() and ".git" not in c.parts
                and not _is_excluded(c) and _is_text(c)
                and c.stat().st_size <= MAX_FILE_SIZE
            )
            if count == 0: return False, "✗ No readable text files found"
            return True, f"✓ Folder · {p.name} ({count} file{'s' if count!=1 else ''})"
        return False, "✗ Unknown path type"
    except Exception as exc:
        return False, f"✗ Error: {exc}"


def load_context_files(raw: str) -> List[dict]:
    p = Path(raw.strip()).expanduser().resolve()
    if not p.exists(): raise FileNotFoundError(f"Not found: {p}")
    if p.is_file():
        if _is_excluded(p): raise ValueError(f"Excluded: {p.name}")
        if not _is_text(p): raise ValueError(f"Not a text file: {p.suffix}")
        text = _read_text(p)
        if text is None: raise IOError(f"Could not read: {p.name}")
        return [{"label": f"📄 {p.name} ({len(text):,} chars)", "text": text, "path": str(p)}]
    if p.is_dir():
        results = []
        for child in sorted(p.rglob("*")):
            if not child.is_file(): continue
            if ".git" in child.parts or _is_excluded(child) or not _is_text(child): continue
            try:
                if child.stat().st_size > MAX_FILE_SIZE: continue
            except OSError:
                continue
            text = _read_text(child)
            if text is None: continue
            rel = child.relative_to(p)
            results.append({"label": f"📄 {rel} ({len(text):,} chars)",
                            "text": f"### {rel}\n{text}", "path": str(child)})
        if not results: raise ValueError("No readable text files found.")
        return results
    raise ValueError(f"Not a file or folder: {p}")


# ── Chat store ────────────────────────────────────────────────────────────────

class ChatStore:
    def __init__(self, support_dir: Path):
        self.chat_dir   = support_dir / "chats"
        self.index_file = support_dir / "chat_index.json"
        self.chat_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: str) -> Path:
        return self.chat_dir / f"{chat_id}.json"

    def load(self, chat_id: str) -> Optional[ChatMeta]:
        d = _read_json(self._path(chat_id))
        return ChatMeta.from_dict(d) if d else None

    def save(self, chat: ChatMeta) -> bool:
        return _write_json(self._path(chat.id), chat.to_dict())

    def delete(self, chat_id: str) -> bool:
        p = self._path(chat_id)
        if p.exists(): p.unlink()
        index = self.load_index()
        index["chats"] = [c for c in index.get("chats",[]) if c.get("id") != chat_id]
        return self.save_index(index)

    def load_index(self) -> dict:
        return _read_json(self.index_file) or {"chats": []}

    def save_index(self, data: dict) -> bool:
        return _write_json(self.index_file, data)

    def upsert_index(self, chat: ChatMeta):
        index  = self.load_index()
        others = [c for c in index.get("chats",[]) if c.get("id") != chat.id]
        others.append({"id": chat.id, "title": chat.title,
                       "created": chat.created, "updated": chat.updated})
        self.save_index({"chats": others})


# ── Settings store ────────────────────────────────────────────────────────────

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": "", "ollama_url": OLLAMA_BASE, "current_chat_id": "",
}


class SettingsStore:
    def __init__(self, support_dir: Path):
        self._path = support_dir / "settings.json"
        self._lock = threading.RLock()
        raw = _read_json(self._path) or {}
        self._d: Dict[str, Any] = {**_DEFAULT_SETTINGS, **raw}

    def get_all(self) -> dict:
        with self._lock: return dict(self._d)

    def update(self, data: dict):
        with self._lock:
            self._d.update(data)
            _write_json(self._path, self._d)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock: return self._d.get(key, default)


# ── Ollama client ─────────────────────────────────────────────────────────────

class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def _is_online(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/api/tags", timeout=3)
            return r.ok
        except Exception:
            return False

    def list_models(self) -> List[str]:
        for attempt in range(NETWORK_RETRIES):
            try:
                r = self._session.get(f"{self.base_url}/api/tags", timeout=8)
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
            except requests.exceptions.ConnectionError:
                if attempt < NETWORK_RETRIES - 1: time.sleep(NETWORK_RETRY_DELAY)
            except Exception as exc:
                logger.error(f"list_models: {exc}")
                return []
        return []

    def get_model_info(self, model: str) -> dict:
        result = {"context_length": 0, "quantization": "", "parameter_size": "", "family": ""}
        if not model: return result
        try:
            r = self._session.post(f"{self.base_url}/api/show",
                                   json={"name": model}, timeout=8)
            r.raise_for_status()
            data = r.json()
            model_info = data.get("model_info", {})
            for k in model_info:
                if "context_length" in k:
                    result["context_length"] = int(model_info[k]); break
            details = data.get("details", {})
            result["quantization"]  = details.get("quantization_level", "")
            result["parameter_size"] = details.get("parameter_size", "")
            result["family"]        = details.get("family", "")
        except Exception as exc:
            logger.debug(f"get_model_info: {exc}")
        return result

    def get_ps_info(self) -> dict:
        result = {"vram_used_mb": 0, "vram_free_mb": 0, "vram_total_mb": 0, "device": ""}
        try:
            r = self._session.get(f"{self.base_url}/api/ps", timeout=5)
            r.raise_for_status()
            data = r.json()
            models_list = data.get("models", [])
            if not models_list: return result
            m = models_list[0]
            size_vram = m.get("size_vram", 0)
            result["vram_used_mb"] = size_vram // (1024 * 1024)
            num_gpu = m.get("details", {}).get("num_gpu_layers", -1)
            result["device"] = "GPU" if (num_gpu > 0 or size_vram > 0) else "CPU"
            gpu_info = data.get("gpu_info", [])
            if gpu_info:
                result["vram_total_mb"] = sum(g.get("total_memory",0) for g in gpu_info) // (1024*1024)
                result["vram_free_mb"]  = sum(g.get("free_memory", 0) for g in gpu_info) // (1024*1024)
        except Exception as exc:
            logger.debug(f"get_ps_info: {exc}")
        return result

    def generate_title(self, model: str, user_text: str, reply_text: str = "") -> Tuple[str, bool]:
        """Generate a short chat title. Returns (title, ok) where ok=False means
        generation failed/was empty and the caller should consider retrying later
        rather than treating this as a final answer."""
        convo = f"User: {user_text[:400]}"
        if reply_text:
            convo += f"\nAssistant: {reply_text[:400]}"

        prompt = (
            "Summarize the topic of this chat in a short title.\n\n"
            f"{convo}\n\n"
            "Rules:\n"
            f"- {MAX_TITLE_LEN} characters or fewer\n"
            "- 3-6 words\n"
            "- Plain text only: no quotes, no markdown, no emoji, no trailing punctuation\n"
            "- Describe the topic, don't restate the message as a command\n"
            "- Reply with ONLY the title, nothing else\n\n"
            "Examples:\n"
            "Fixing a Python KeyError\n"
            "Trip planning for Lisbon\n"
            "Resume feedback for engineer\n"
            "Recipe for sourdough bread"
        )
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 40},
                },
                timeout=OLLAMA_TITLE_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("message", {}).get("content", "").strip()
            raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
            raw = re.sub(r"</?think[^>]*>", "", raw, flags=re.IGNORECASE).strip()
            if not raw:
                return DEFAULT_CHAT_TITLE, False

            # Take the first non-empty line, strip wrapping quotes/markdown/labels
            line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
            line = re.sub(r'^(title|chat title)\s*[:\-]\s*', "", line, flags=re.IGNORECASE)
            line = line.strip(" \t\"'*`")
            line = re.sub(r"[.!?]+$", "", line).strip()

            if not line:
                return DEFAULT_CHAT_TITLE, False
            if len(line) > MAX_TITLE_LEN:
                line = line[:MAX_TITLE_LEN].rstrip()
            return line, True
        except Exception as exc:
            logger.warning(f"generate_title: {exc}")
            return DEFAULT_CHAT_TITLE, False


    def chat_stream_sse(self, model: str, messages: List[dict]) -> Generator[str, None, None]:
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": True},
                stream=True, timeout=OLLAMA_TIMEOUT,
            )
            if resp.status_code == 404:
                yield _sse("error", {"message": f"Model '{model}' not found. Run: ollama pull {model}"})
                return
            resp.raise_for_status()

            first_token_sent = False
            t_start = time.monotonic()

            for raw_line in resp.iter_lines():
                if not raw_line: continue
                try: data = json.loads(raw_line)
                except json.JSONDecodeError: continue

                tok = data.get("message", {}).get("content", "")
                if tok:
                    if not first_token_sent:
                        first_token_sent = True
                        yield _sse("first_token", {"ms": round((time.monotonic() - t_start)*1000)})
                    yield _sse("token", {"token": tok})

                if data.get("done"):
                    gen_ms = round((time.monotonic() - t_start) * 1000)
                    eval_dur = data.get("eval_duration", 0)
                    eval_count = data.get("eval_count", 0)
                    tps = round(eval_count / (eval_dur / 1e9), 1) if eval_dur else 0
                    yield _sse("done", {
                        "prompt_tokens":     data.get("prompt_eval_count", 0),
                        "completion_tokens": eval_count,
                        "total_duration_ns": data.get("total_duration", 0),
                        "eval_duration_ns":  eval_dur,
                        "tokens_per_sec":    tps,
                        "gen_ms":            gen_ms,
                    })
                    return

            yield _sse("done", {})

        except requests.exceptions.ConnectionError:
            yield _sse("error", {"message": "Cannot reach Ollama — run: ollama serve"})
        except requests.exceptions.Timeout:
            yield _sse("error", {"message": "Ollama timed out. Model may be overloaded."})
        except requests.exceptions.HTTPError as exc:
            yield _sse("error", {"message": f"Ollama HTTP {exc.response.status_code}"})
        except Exception as exc:
            logger.error(traceback.format_exc())
            yield _sse("error", {"message": f"Unexpected error: {exc}"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── Flask app factory ─────────────────────────────────────────────────────────

def create_app(support_dir: Path) -> Flask:
    static_folder = Path(__file__).parent
    app = Flask(__name__, static_folder=str(static_folder), static_url_path="")

    store    = ChatStore(support_dir)
    settings = SettingsStore(support_dir)
    ollama   = OllamaClient(settings.get("ollama_url", OLLAMA_BASE))

    @app.route("/api/ping")
    def ping(): return jsonify({"ok": True})

    @app.route("/")
    def index(): return send_from_directory(str(static_folder), "index.html")

    @app.route("/api/models")
    def api_models():
        models = ollama.list_models()
        return jsonify({"models": models, "online": len(models) > 0})

    @app.route("/api/ps")
    def api_ps():
        model = request.args.get("model", "")
        ps   = ollama.get_ps_info()
        info = ollama.get_model_info(model) if model else {}
        return jsonify({**ps, **info})

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        body       = request.get_json(force=True)
        model      = body.get("model", "")
        history    = body.get("history", [])
        user_text  = body.get("message", "")
        ctx_files  = body.get("context_files", [])

        if not model or not user_text:
            return jsonify({"error": "model and message required"}), 400

        sys_content = AI_SYSTEM_PROMPT
        if ctx_files:
            sections = []
            for cf in ctx_files:
                path_str = cf.get("path", "")
                text     = cf.get("text", "")
                if path_str:
                    try:
                        p = Path(path_str)
                        if p.exists():
                            fresh = _read_text(p)
                            if fresh is not None:
                                header_end = text.index("\n") + 1 if text.startswith("### ") else 0
                                text = text[:header_end] + fresh if header_end else fresh
                    except Exception as exc:
                        logger.warning(f"re-read {path_str}: {exc}")
                sections.append(text)
            sys_content += (
                "\n\nAttached context files — refer to them as needed:\n\n"
                + "\n\n---\n\n".join(sections)
            )

        messages = [{"role": "system", "content": sys_content}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": user_text})

        return Response(
            stream_with_context(ollama.chat_stream_sse(model, messages)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/title", methods=["POST"])
    def api_title():
        body  = request.get_json(force=True)
        model = body.get("model", "")
        text  = body.get("text", "")
        reply = body.get("reply", "")
        if not model or not text:
            return jsonify({"title": DEFAULT_CHAT_TITLE, "ok": False})
        title, ok = ollama.generate_title(model, text, reply)
        return jsonify({"title": title, "ok": ok})

    @app.route("/api/chats")
    def api_chats_list():
        index = store.load_index()
        chats = sorted(index.get("chats",[]), key=lambda x: x.get("updated",""), reverse=True)
        return jsonify(chats)

    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip().lower()
        if not q:
            return jsonify([])

        index = store.load_index()
        results = []
        for entry in index.get("chats", []):
            chat_id = entry.get("id")
            title   = entry.get("title", "") or ""
            title_hit = q in title.lower()
            snippet   = ""

            meta = store.load(chat_id)
            if meta:
                for m in meta.messages:
                    pos = m.content.lower().find(q)
                    if pos != -1:
                        start = max(0, pos - 30)
                        end   = min(len(m.content), pos + len(q) + 50)
                        prefix = "…" if start > 0 else ""
                        suffix = "…" if end < len(m.content) else ""
                        snippet = f"{prefix}{m.content[start:end].strip()}{suffix}"
                        break

            if title_hit or snippet:
                results.append({
                    "id": chat_id, "title": title,
                    "updated": entry.get("updated", ""),
                    "snippet": snippet,
                })

        results.sort(key=lambda x: x.get("updated",""), reverse=True)
        return jsonify(results[:50])

    @app.route("/api/chats/<chat_id>", methods=["GET"])
    def api_chat_get(chat_id: str):
        meta = store.load(chat_id)
        if not meta: return jsonify({"error": "not found"}), 404
        return jsonify(meta.to_dict())

    @app.route("/api/chats/<chat_id>", methods=["POST"])
    def api_chat_save(chat_id: str):
        body = request.get_json(force=True)
        meta = ChatMeta.from_dict({**body, "id": chat_id})
        store.save(meta)
        store.upsert_index(meta)
        return jsonify({"ok": True})

    @app.route("/api/chats/<chat_id>", methods=["DELETE"])
    def api_chat_delete(chat_id: str):
        store.delete(chat_id)
        return jsonify({"ok": True})

    @app.route("/api/settings", methods=["GET"])
    def api_settings_get(): return jsonify(settings.get_all())

    @app.route("/api/settings", methods=["POST"])
    def api_settings_save():
        settings.update(request.get_json(force=True))
        return jsonify({"ok": True})

    @app.route("/api/context", methods=["POST"])
    def api_context():
        raw = request.get_json(force=True).get("path", "")
        ok, msg = validate_path(raw)
        if not ok: return jsonify({"error": msg}), 400
        try:
            files = load_context_files(raw)
            return jsonify({"files": files})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/validate-path", methods=["POST"])
    def api_validate_path():
        raw = request.get_json(force=True).get("path", "")
        ok, msg = validate_path(raw)
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/browse", methods=["POST"])
    def api_browse():
        """Open a native file/folder dialog and return the chosen path.

        On macOS we use osascript (AppleScript) so the dialog is raised on the
        main AppKit thread — calling tkinter from a background thread triggers a
        SIGTRAP / trace-trap and kills the process.  On other platforms we fall
        back to tkinter with a threading workaround.
        """
        import subprocess
        body   = request.get_json(force=True) or {}
        mode   = body.get("mode", "file")   # "file" | "folder"
        chosen = None

        # ── macOS: use osascript (safe from any thread) ───────────────────
        if sys.platform == "darwin":
            try:
                if mode == "folder":
                    script = (
                        'tell application "System Events"\n'
                        '  activate\n'
                        'end tell\n'
                        'set f to choose folder with prompt "Select folder"\n'
                        'POSIX path of f'
                    )
                else:
                    script = (
                        'tell application "System Events"\n'
                        '  activate\n'
                        'end tell\n'
                        'set f to choose file with prompt "Select a text or code file"\n'
                        'POSIX path of f'
                    )
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=120
                )
                raw = result.stdout.strip()
                if raw:
                    chosen = raw
                else:
                    # user cancelled (osascript exits 1)
                    return jsonify({"path": None})
            except subprocess.TimeoutExpired:
                return jsonify({"path": None})
            except Exception as exc:
                logger.warning(f"api_browse osascript: {exc}")
                return jsonify({"error": str(exc)}), 500

        # ── Other platforms: tkinter via a dedicated thread ───────────────
        else:
            import threading
            result_holder: list = []
            exc_holder:    list = []

            def _run_dialog():
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.lift()
                    root.focus_force()
                    if mode == "folder":
                        path = filedialog.askdirectory(parent=root, title="Select folder")
                    else:
                        path = filedialog.askopenfilename(
                            parent=root,
                            title="Select file",
                            filetypes=[
                                ("Text / code files",
                                 "*.txt *.md *.py *.js *.ts *.jsx *.tsx *.html *.css "
                                 "*.json *.yaml *.yml *.toml *.sh *.rs *.go *.java *.c *.cpp *.h"),
                                ("All files", "*.*"),
                            ],
                        )
                    root.destroy()
                    result_holder.append(path or "")
                except Exception as e:
                    exc_holder.append(e)

            t = threading.Thread(target=_run_dialog, daemon=True)
            t.start()
            t.join(timeout=120)

            if exc_holder:
                logger.warning(f"api_browse tkinter: {exc_holder[0]}")
                return jsonify({"error": str(exc_holder[0])}), 500

            chosen = result_holder[0] if result_holder else ""

        if not chosen:
            return jsonify({"path": None})
        return jsonify({"path": str(Path(chosen).resolve())})

    return app