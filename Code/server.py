#!/usr/bin/env python3
"""
server.py — Sullybase Local LLM Chat backend orchestrator.

Single Flask app that routes all calls to the active backend
(Ollama or MLX LM Server). Settings + chats persist to disk so
the app survives restarts.

Endpoints (aligned with app.js + settings.js):

  GET  /                          -> index.html
  GET  /settings.html             -> settings page
  GET  /style.css / settings.css  -> static assets
  GET  /app.js / settings.js      -> static assets

  GET  /api/version
  GET  /api/ping

  GET  /api/settings              -> full settings dict
  POST /api/settings              -> replace settings
  POST /api/settings/<key>        -> set a single key

  GET  /api/models                -> {models, online, backend}
  GET  /api/ps?model=...          -> runtime stats for selected model
  GET  /api/backend-info          -> {backend, status, ...ps}
  GET  /api/model/<model>/info    -> model details

  POST /api/title                 -> {title, ok, backend}
  POST /api/chat                  -> SSE stream of assistant reply
                                     body: {model, history, message, context_files}

  GET  /api/chats                 -> {chats: [list with id, title, snippet, updated]}
  GET  /api/chat/<id>             -> single chat object
  POST /api/chat/<id>             -> save chat object
  DELETE /api/chat/<id>           -> delete chat
  GET  /api/search?q=...          -> filtered chat list

  POST /api/context               -> attach file/folder as context
  POST /api/browse                -> {mode:"file"} -> native file picker (best-effort)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import traceback
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from flask import (
    Flask, request, Response, jsonify, send_from_directory, abort,
)
from flask_cors import CORS

from ollama_server import OllamaBackend
from mlx_server import MLXBackend

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

def _load_app_version() -> str:
    """Read the release version from the single canonical version file."""
    try:
        version = (Path(__file__).with_name("version.txt")
                   .read_text(encoding="utf-8").strip())
        if version:
            return version
    except OSError:
        pass
    # Keep a usable version in packaged builds where the text file was omitted.
    return "2.5.1"


APP_VERSION = _load_app_version()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sullybase")

# Persistence lives under ~/.sullybase/ (or overridden by SULLYBASE_HOME).
SUPPORT_DIR = Path(os.environ.get("SULLYBASE_HOME", Path.home() / ".sullybase"))
CHATS_DIR   = SUPPORT_DIR / "chats"

PORT        = int(os.environ.get("SULLYBASE_PORT", "5050"))
OLLAMA_BASE = "http://localhost:11434"
MLX_BASE    = "http://localhost:8080"

# Separate default model per provider — these are sensible pick-first
# placeholders; the user can override them in Settings. Empty string
# means "use whatever the backend reports as the first available model".
DEFAULT_MODEL_OLLAMA = "llama3.2"
DEFAULT_MODEL_MLX    = "mlx-community/Llama-3.2-3B-Instruct-4bit"

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "backend":             "ollama",
    "ollama_url":          OLLAMA_BASE,
    "mlx_url":             MLX_BASE,
    "model_ollama":        DEFAULT_MODEL_OLLAMA,
    "model_mlx":           DEFAULT_MODEL_MLX,
    "mlx_launch_button_enabled": True,
    "current_chat_id":     "",
    "custom_instructions": "",
    "last_model_ollama":   "",  # last user-selected model under ollama
    "last_model_mlx":      "",  # last user-selected model under mlx
}


# ═══════════════════════════════════════════════════════════════════════════
# File I/O helpers (atomic, crash-safe)
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"_read_json({path}): {e}")
        return None


def _write_json(path: Path, data: Any) -> bool:
    """Atomic write: tmp file + rename. Survives partial writes."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"_write_json({path}): {e}")
        return False


def _read_chats() -> List[dict]:
    """Load every chat as a list (sorted newest-first)."""
    out: List[dict] = []
    if CHATS_DIR.exists():
        for chat_file in CHATS_DIR.glob("*.json"):
            data = _read_json(chat_file)
            if not data or "id" not in data:
                continue
            snippet = ""
            msgs = data.get("messages", [])
            if msgs:
                # First user message makes the most useful preview.
                first_user = next((m for m in msgs if m.get("role") == "user"), None)
                snippet = (first_user or msgs[0]).get("content", "")[:120].replace("\n", " ")
            out.append({
                "id":       data["id"],
                "title":    data.get("title") or "New chat",
                "snippet":  snippet,
                "updated":  data.get("updated") or data.get("created") or "",
                "created":  data.get("created") or "",
                "titleOk":  data.get("titleOk", False),
            })
    out.sort(key=lambda c: c.get("updated", ""), reverse=True)
    return out


def _read_chat(chat_id: str) -> Optional[dict]:
    return _read_json(CHATS_DIR / f"{chat_id}.json")


def _write_chat(chat_id: str, data: dict) -> bool:
    return _write_json(CHATS_DIR / f"{chat_id}.json", data)


def _delete_chat(chat_id: str) -> bool:
    try:
        p = CHATS_DIR / f"{chat_id}.json"
        if p.exists():
            p.unlink()
        return True
    except Exception as e:
        logger.error(f"_delete_chat({chat_id}): {e}")
        return False


def _get_version() -> str:
    return APP_VERSION


def _logs_root() -> Path:
    return SUPPORT_DIR / "logs"


def _mlx_launch_root() -> Path:
    return _logs_root() / "mlx-launch"


def _create_mlx_launch_dir() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    base = _mlx_launch_root() / ts
    path = base
    suffix = 1
    while path.exists():
        path = _mlx_launch_root() / f"{ts}-{suffix}"
        suffix += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _launch_mlx_server(settings: Dict[str, Any]) -> Dict[str, Any]:
    backend = MLXBackend(settings.get("mlx_url") or MLX_BASE)
    if backend.health_check():
        return {
            "ok": True,
            "status": "already_running",
            "message": "MLX server is already reachable.",
            "command": None,
            "log_dir": None,
        }

    model = get_default_model_for("mlx", settings).strip()
    if not model:
        return {
            "ok": False,
            "status": "error",
            "message": "No MLX default model is configured in Settings.",
            "log_dir": None,
        }

    launch_dir = _create_mlx_launch_dir()
    command_display = f"python -m mlx_lm.server --model {shlex.quote(model)}"
    command_args = [sys.executable, "-u", "-m", "mlx_lm.server", "--model", model]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    meta = {
        "command": command_display,
        "executable": sys.executable,
        "model": model,
        "backend_url": settings.get("mlx_url") or MLX_BASE,
        "cwd": str(Path(__file__).resolve().parent),
        "started_at": datetime.now().isoformat(),
    }
    _write_text(launch_dir / "command.txt", command_display + "\n")
    _write_json(launch_dir / "launch.json", meta)

    stdout_path = launch_dir / "stdout.log"
    stderr_path = launch_dir / "stderr.log"
    try:
        with stdout_path.open("ab") as stdout_fh, stderr_path.open("ab") as stderr_fh:
            proc = subprocess.Popen(
                command_args,
                stdout=stdout_fh,
                stderr=stderr_fh,
                cwd=str(Path(__file__).resolve().parent),
                env=env,
                start_new_session=True,
            )
            meta["pid"] = proc.pid
            meta["status"] = "starting"
            _write_json(launch_dir / "launch.json", meta)

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                rc = proc.poll()
                if rc is not None:
                    time.sleep(0.1)
                    stderr_text = ""
                    try:
                        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
                    except Exception:
                        pass
                    meta["status"] = "error"
                    meta["returncode"] = rc
                    meta["finished_at"] = datetime.now().isoformat()
                    _write_json(launch_dir / "launch.json", meta)
                    return {
                        "ok": False,
                        "status": "error",
                        "message": f"MLX server exited immediately with code {rc}.",
                        "returncode": rc,
                        "log_dir": str(launch_dir),
                        "command": command_display,
                        "stderr": stderr_text[-4000:] if stderr_text else "",
                    }
                time.sleep(0.1)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        _write_text(stderr_path, error_text + "\n")
        meta["status"] = "error"
        meta["error"] = error_text
        _write_json(launch_dir / "launch.json", meta)
        return {
            "ok": False,
            "status": "error",
            "message": f"Failed to start MLX server: {exc}",
            "log_dir": str(launch_dir),
            "command": command_display,
        }

    return {
        "ok": True,
        "status": "starting",
        "message": "MLX server launch started.",
        "pid": meta.get("pid"),
        "log_dir": str(launch_dir),
        "command": command_display,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Settings store
# ═══════════════════════════════════════════════════════════════════════════

class SettingsStore:
    def __init__(self, support_dir: Path):
        self.settings_file = support_dir / "settings.json"
        support_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        loaded = _read_json(self.settings_file)
        if loaded is None:
            data = dict(_DEFAULT_SETTINGS)
            self.save(data)
            return data
        # Backfill any newly added default keys.
        for k, v in _DEFAULT_SETTINGS.items():
            loaded.setdefault(k, v)
        return loaded

    def save(self, data: Dict[str, Any]) -> bool:
        # Strip unknown keys but keep existing values for fields the caller
        # omitted.  This lets UI code save partial updates safely.
        current = _read_json(self.settings_file) or {}
        merged = {**_DEFAULT_SETTINGS, **current, **(data or {})}
        clean = {k: merged.get(k, _DEFAULT_SETTINGS.get(k)) for k in _DEFAULT_SETTINGS}
        return _write_json(self.settings_file, clean)

    def get(self, key: str, default: Any = None) -> Any:
        v = self.load().get(key)
        if v is None or v == "":
            return default if default is not None else _DEFAULT_SETTINGS.get(key, "")
        return v

    def set(self, key: str, value: Any) -> bool:
        data = self.load()
        data[key] = value
        return self.save(data)


_settings = SettingsStore(SUPPORT_DIR)


# ═══════════════════════════════════════════════════════════════════════════
# Backend selection
# ═══════════════════════════════════════════════════════════════════════════

def _make_backend(name: str, settings: Dict[str, Any]):
    if name == "mlx":
        return MLXBackend(settings.get("mlx_url") or MLX_BASE)
    return OllamaBackend(settings.get("ollama_url") or OLLAMA_BASE)


def get_active_backend(settings_obj: Optional[SettingsStore] = None):
    """Return the currently active backend instance."""
    s = settings_obj or _settings
    data = s.load()
    return _make_backend(data.get("backend", "ollama"), data)


def get_default_model_for(backend_name: str, settings: Dict[str, Any]) -> str:
    """The model to auto-select when nothing else is set."""
    if backend_name == "mlx":
        return settings.get("model_mlx") or DEFAULT_MODEL_MLX
    return settings.get("model_ollama") or DEFAULT_MODEL_OLLAMA


# ═══════════════════════════════════════════════════════════════════════════
# App factory
# ═══════════════════════════════════════════════════════════════════════════

def create_app(support_dir: Optional[Path] = None) -> Flask:
    if support_dir is not None:
        global SUPPORT_DIR, CHATS_DIR, _settings
        SUPPORT_DIR = support_dir
        CHATS_DIR   = support_dir / "chats"
        _settings   = SettingsStore(support_dir)
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHATS_DIR.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, static_folder=None)
    CORS(app)
    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:

    # ── Static pages / assets ────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory(_static_dir(), "index.html")

    @app.route("/<path:filename>")
    def static_files(filename: str):
        # Allow only known asset names, not arbitrary traversal.
        allowed = {"index.html", "settings.html", "style.css",
                   "settings.css", "app.js", "settings.js"}
        if filename not in allowed:
            abort(404)
        return send_from_directory(_static_dir(), filename)

    @app.route("/api/ping")
    def api_ping():
        return jsonify({"ok": True, "ts": time.time()})

    @app.route("/api/version")
    def api_version():
        return jsonify({"version": _get_version()})

    # ── Settings ─────────────────────────────────────────────────────────
    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "GET":
            data = _settings.load()
            data["version"] = _get_version()
            data["platform"] = sys.platform
            data["is_macos"] = sys.platform == "darwin"
            return jsonify(data)
        try:
            data = request.get_json(silent=True) or {}
            ok = _settings.save(data)
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"api_settings POST: {e}")
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/settings/<key>", methods=["GET", "POST"])
    def api_settings_key(key: str):
        if request.method == "GET":
            return jsonify({key: _settings.get(key)})
        try:
            data = request.get_json(silent=True) or {}
            ok = _settings.set(key, data.get("value"))
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"api_settings_key {key}: {e}")
            return jsonify({"ok": False, "error": str(e)}), 400

    # ── Models / backend info ───────────────────────────────────────────
    @app.route("/api/models")
    def api_models():
        settings = _settings.load()
        backend_name = settings.get("backend", "ollama")
        backend = _make_backend(backend_name, settings)
        try:
            online = backend.health_check()
            models = backend.list_models() if online else []
            return jsonify({
                "models":  models,
                "online":  online,
                "backend": backend_name,
            })
        except Exception as e:
            logger.error(f"api_models: {e}")
            return jsonify({
                "models": [], "online": False,
                "backend": backend_name, "error": str(e),
            }), 500

    @app.route("/api/ps")
    def api_ps():
        """Runtime stats for the currently selected model."""
        model = request.args.get("model", "")
        settings = _settings.load()
        backend_name = settings.get("backend", "ollama")
        backend = _make_backend(backend_name, settings)
        try:
            ps = backend.get_ps_info(model)
            # Attach model details so the frontend can render the
            # context-length bar in one round-trip.
            info = backend.get_model_info(model) if model else {}
            ps.update(info)
            ps["backend"] = backend_name
            return jsonify(ps)
        except Exception as e:
            logger.error(f"api_ps: {e}")
            return jsonify({"backend": backend_name, "error": str(e)}), 500

    @app.route("/api/backend-info")
    def api_backend_info():
        settings = _settings.load()
        backend_name = settings.get("backend", "ollama")
        backend = _make_backend(backend_name, settings)
        try:
            online = backend.health_check()
            ps = backend.get_ps_info()
            return jsonify({
                "backend": backend_name,
                "status":  "online" if online else "offline",
                **ps,
            })
        except Exception as e:
            logger.error(f"api_backend_info: {e}")
            return jsonify({"backend": backend_name, "status": "error",
                            "error": str(e)}), 500

    @app.route("/api/mlx/start", methods=["POST"])
    def api_mlx_start():
        if sys.platform != "darwin":
            return jsonify({
                "ok": False,
                "status": "unsupported",
                "message": "MLX launch is only available on macOS.",
            }), 400
        settings = _settings.load()
        try:
            result = _launch_mlx_server(settings)
            status = 200 if result.get("ok") else 500
            return jsonify(result), status
        except Exception as e:
            logger.error(f"api_mlx_start: {e}")
            return jsonify({
                "ok": False,
                "status": "error",
                "message": str(e),
            }), 500

    @app.route("/api/model/<path:model>/info")
    def api_model_info(model: str):
        backend = get_active_backend()
        try:
            info = backend.get_model_info(model)
            return jsonify({"model": model, **info})
        except Exception as e:
            logger.error(f"api_model_info({model}): {e}")
            return jsonify({"error": str(e)}), 500

    # ── Title generation ────────────────────────────────────────────────
    @app.route("/api/title", methods=["POST"])
    def api_title():
        data = request.get_json(silent=True) or {}
        model    = data.get("model", "")
        text     = data.get("text", "") or data.get("message", "")
        reply    = data.get("reply", "")
        if not model or not text:
            return jsonify({"title": "New chat", "ok": False}), 400
        backend = get_active_backend()
        try:
            title, ok = backend.generate_title(model, text, reply)
            return jsonify({"title": title, "ok": ok})
        except Exception as e:
            logger.error(f"api_title: {e}")
            return jsonify({"title": "New chat", "ok": False, "error": str(e)})

    # Backwards-compat alias for older callers.
    @app.route("/api/generate-title", methods=["POST"])
    def api_generate_title_alias():
        return api_title()

    # ── Streaming chat ──────────────────────────────────────────────────
    @app.route("/api/chat", methods=["POST"])
    def api_chat_stream():
        data = request.get_json(silent=True) or {}
        model         = data.get("model", "")
        history       = data.get("history", [])
        message       = data.get("message", "")
        context_files = data.get("context_files", [])

        settings = _settings.load()
        system_prompt = _build_system_prompt(settings.get("custom_instructions", ""))

        if not model:
            return _provider_error(settings, "No model specified"), 400
        if not message and not history:
            return _provider_error(settings, "No messages provided"), 400

        messages: List[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # Inline context files as a system-ish user preamble.
        ctx_blob = _format_context_files(context_files)
        if ctx_blob:
            messages.append({"role": "user", "content": ctx_blob})
            messages.append({"role": "assistant",
                             "content": "Understood — I'll use these files as context."})
        for m in history:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("user", "assistant", "system") and content:
                messages.append({"role": role, "content": content})
        if message:
            messages.append({"role": "user", "content": message})

        backend = get_active_backend()

        def generate() -> Generator[str, None, None]:
            try:
                for event in backend.send_prompt(model, messages):
                    yield event
            except Exception as e:
                logger.error(f"api_chat stream error: {e}\n{traceback.format_exc()}")
                err_msg = _human_error(settings.get("backend", "ollama"), e)
                yield _sse("error", {"message": err_msg})

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    # Backwards-compat alias for older clients.
    @app.route("/api/prompt", methods=["POST"])
    def api_prompt_alias():
        data = request.get_json(silent=True) or {}
        model    = data.get("model", "")
        messages = data.get("messages", [])
        system   = data.get("system", "")
        if not model:
            return _provider_error(_settings.load(), "No model specified"), 400
        backend = get_active_backend()
        def gen():
            try:
                for ev in backend.send_prompt(model, messages, system):
                    yield ev
            except Exception as e:
                yield _sse("error", {"message": str(e)})
        return Response(gen(), mimetype="text/event-stream")

    # ── Chat persistence ────────────────────────────────────────────────
    @app.route("/api/chats")
    def api_chats():
        return jsonify({"chats": _read_chats()})

    @app.route("/api/chat/<chat_id>", methods=["GET", "POST", "DELETE"])
    def api_chat(chat_id: str):
        if request.method == "GET":
            data = _read_chat(chat_id)
            return jsonify(data or {})
        if request.method == "DELETE":
            ok = _delete_chat(chat_id)
            return jsonify({"ok": ok})
        try:
            data = request.get_json(silent=True) or {}
            # Force id consistency — never let a save corrupt the filename.
            data["id"] = chat_id
            now = datetime.utcnow().isoformat() + "Z"
            data.setdefault("created", now)
            data["updated"] = now
            ok = _write_chat(chat_id, data)
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"api_chat POST {chat_id}: {e}")
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip().lower()
        if not q:
            return jsonify({"chats": _read_chats()})
        matches = [c for c in _read_chats()
                   if q in (c.get("title") or "").lower()
                   or q in (c.get("snippet") or "").lower()]
        return jsonify({"chats": matches})

    # ── Context files ───────────────────────────────────────────────────
    @app.route("/api/context", methods=["POST"])
    def api_context():
        data = request.get_json(silent=True) or {}
        path = data.get("path", "").strip()
        if not path:
            return jsonify({"error": "No path provided"}), 400
        p = Path(path).expanduser()
        if not p.exists():
            return jsonify({"error": f"Path not found: {path}"})
        files: List[dict] = []
        if p.is_file():
            files.append(_read_context_file(p))
        else:
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.stat().st_size < 512 * 1024:
                    files.append(_read_context_file(child))
        return jsonify({"files": files})

    @app.route("/api/browse", methods=["POST"])
    def api_browse():
        """Best-effort native file picker. Returns {path} or {path:''}."""
        # Tkinter is available almost everywhere Python is; fall back
        # gracefully on headless boxes.
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(parent=root)
            root.destroy()
            return jsonify({"path": path or ""})
        except Exception as e:
            logger.warning(f"api_browse: {e}")
            return jsonify({"path": "", "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _static_dir() -> Path:
    """Where index.html / app.js / etc. live — same dir as server.py."""
    return Path(__file__).resolve().parent


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _build_system_prompt(custom: str) -> str:
    base = ("Full Markdown rendering is auto applied. "
            "Use minimal emojis. Be clear and concise.")
    custom = (custom or "").strip()
    return f"{base}\n\n{custom}" if custom else base


def _format_context_files(files: List[dict]) -> str:
    if not files:
        return ""
    parts = ["# Context files\n"]
    for f in files:
        parts.append(f"\n## {f.get('label', f.get('path', 'unknown'))}\n")
        parts.append("```\n" + (f.get("content") or "")[:8192] + "\n```")
    return "\n".join(parts)


def _read_context_file(p: Path) -> dict:
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        content = f"[unreadable: {e}]"
    return {"path": str(p), "label": p.name, "content": content}


def _provider_error(settings: Dict[str, Any], msg: str) -> Response:
    """Return an error Response whose text is provider-aware."""
    backend = settings.get("backend", "ollama")
    return jsonify({
        "ok": False,
        "error": msg,
        "backend": backend,
        "backend_url": settings.get("mlx_url" if backend == "mlx" else "ollama_url"),
    })


def _human_error(backend_name: str, exc: Exception) -> str:
    """Convert raw exceptions into provider-specific advice."""
    msg = str(exc).lower()
    if backend_name == "ollama":
        if "connection" in msg or "refused" in msg or "max retries" in msg:
            return "Cannot reach Ollama — start it with `ollama serve` or open the Ollama app."
        if "404" in msg or "not found" in msg:
            return "Ollama model not found — run `ollama pull <model>` and try again."
        if "timeout" in msg or "timed out" in msg:
            return "Ollama timed out — the model may still be loading. Try again."
        return f"Ollama error: {exc}"
    if backend_name == "mlx":
        if "connection" in msg or "refused" in msg or "max retries" in msg:
            return ("Cannot reach MLX server — start it with "
                    "`python -m mlx_lm.server --model <model>`.")
        if "404" in msg or "not found" in msg:
            return "MLX model not found — check the model id in your MLX server command."
        if "timeout" in msg or "timed out" in msg:
            return "MLX server timed out — the model may still be loading. Try again."
        return f"MLX error: {exc}"
    return f"Error: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# Standalone singleton (used by `python server.py` and by app.py)
# ═══════════════════════════════════════════════════════════════════════════

app = create_app()


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info(f"Sullybase v{_get_version()} starting on port {PORT}")
    logger.info(f"Support dir: {SUPPORT_DIR}")
    logger.info(f"Chats dir:   {CHATS_DIR}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
