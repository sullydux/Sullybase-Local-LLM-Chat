#!/usr/bin/env python3
"""
Sullybase Local LLM Chat  v1.2.0
A production-quality Tkinter frontend for Ollama local LLMs.
"""

import gc
import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run:  pip install requests")
    sys.exit(1)

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ══════════════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════════════

APP_NAME    = "Sullybase Local LLM Chat"
APP_BUNDLE  = "Sullybase-LLM-Chat"
APP_VERSION = "1.1.0"

AI_SYSTEM_PROMPT = (
    "You are a Local LLM from Ollama, a helpful assistant. "
    "This application is Sullybase Local LLM Chat. "
    "You assist with coding, science, and general questions. "
    "Be clear, concise, and technically accurate.\n\n"
    "This front end is Copyright © 2026 Sullydux (GitHub). All rights reserved. Effective Date: June 13, 2026\n\n"
    "You have full Markdown rendering support in this chat. Use it freely:\n"
    "• **bold**, *italic*, ~~strikethrough~~, `inline code`\n"
    "• # Heading 1 / ## Heading 2 / ### Heading 3\n"
    "• Fenced code blocks with language hints (e.g. ```python) for syntax highlighting\n"
    "• - Unordered lists, 1. Ordered lists, nested lists (indent 2 spaces)\n"
    "• - [ ] Task checkboxes and - [x] checked boxes\n"
    "• > Blockquotes\n"
    "• | Tables | with | headers |\n"
    "• [Link text](https://url) and bare URLs\n"
    "• --- Horizontal rules\n\n"
    "Always use appropriate Markdown to make responses clear and readable."
)

# ── Palette ──────────────────────────────────────────────────────────────────
BG_ROOT       = "#1a1a1a"
BG_SIDEBAR    = "#141414"
BG_CHAT       = "#1e1e1e"
BG_INPUT      = "#252525"
BG_BUBBLE_U   = "#2a3a52"
BG_BUBBLE_A   = "#242424"
BG_BTN        = "#2d2d2d"
BG_BTN_HOV    = "#383838"
BG_BTN_SEND   = "#1a6b4a"
BG_BTN_SEND_H = "#1e8059"
BG_BTN_STOP   = "#6b2a2a"
BG_BTN_STOP_H = "#803030"
BG_BTN_NEW    = "#1e3a2e"
BG_BTN_NEW_H  = "#25503d"
BG_PATH_OK    = "#1e2e1e"
BG_PATH_BAD   = "#2e1e1e"
BG_CODE       = "#0d1117"   # code block background (GitHub-dark style)
BG_BLOCKQUOTE = "#1e2433"   # blockquote background
BG_TABLE_HDR  = "#1a2a3a"   # table header background
BG_TABLE_ROW  = "#1e1e1e"   # table row background
BG_TABLE_ALT  = "#222222"   # alternating table row

FG_PRIMARY  = "#e8e8e8"
FG_DIM      = "#888888"
FG_ACCENT   = "#4ec994"
FG_ERROR    = "#e07070"
FG_USER     = "#a8d0f0"
FG_ASST     = "#e8e8e8"
FG_BADGE    = "#7ab87a"
FG_PATH_OK  = "#7ab87a"
FG_PATH_BAD = "#e07070"
FG_CODE     = "#c9d1d9"    # code text
FG_LINK     = "#58a6ff"    # URL colour
FG_STRIKE   = "#888888"    # strikethrough text
FG_BLOCKQUOTE = "#aaaaaa"  # blockquote text
FG_TABLE_HDR  = "#4ec994"  # table header text

# ── Syntax highlight colours (minimal, readable on dark) ─────────────────────
SH_KEYWORD  = "#ff7b72"   # red  – keywords
SH_STRING   = "#a5d6ff"   # blue – strings
SH_COMMENT  = "#8b949e"   # grey – comments
SH_NUMBER   = "#f2cc60"   # gold – numbers
SH_FUNC     = "#d2a8ff"   # purple – functions/decorators
SH_BUILTIN  = "#ffa657"   # orange – builtins / types
SH_DEFAULT  = FG_CODE

# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_UI   = ("SF Pro Display", 12)
FONT_SM   = ("SF Pro Display", 10)
FONT_MONO = ("SF Mono", 11)
FONT_LBL  = ("SF Pro Display", 10)
FONT_BOLD = ("SF Pro Display", 11, "bold")

# ── Limits & tuning ──────────────────────────────────────────────────────────
OLLAMA_BASE          = "http://localhost:11434"
OLLAMA_TIMEOUT       = (10, 300)
OLLAMA_TITLE_TIMEOUT = (10, 30)
MAX_FILE_SIZE        = 2 * 1024 * 1024
NETWORK_RETRIES      = 3
NETWORK_RETRY_DELAY  = 1.0
DRAIN_INTERVAL_MS    = 30
DEFAULT_CHAT_TITLE   = "New chat"
MAX_TITLE_LEN        = 20

# ── File exclusions ───────────────────────────────────────────────────────────
EXCLUDED_NAMES: set = {
    ".DS_Store", ".ds_store", "Thumbs.db", "thumbs.db",
    ".git", ".gitattributes", ".gitignore", "editorhost", "desktop.ini",
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

# ── Queue sentinels ───────────────────────────────────────────────────────────
_DONE  = object()
_ERROR = object()
_TOKEN = object()

# ── Syntax highlight keyword sets ────────────────────────────────────────────
_SH_RULES: Dict[str, List[Tuple[str, str]]] = {
    "python": [
        (r"\b(def|class|return|import|from|as|if|elif|else|for|while|in|not|and|or|is|None|True|False|try|except|finally|with|yield|lambda|pass|break|continue|raise|del|global|nonlocal|assert|async|await)\b", SH_KEYWORD),
        (r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', SH_STRING),
        (r"#[^\n]*", SH_COMMENT),
        (r"\b\d+\.?\d*\b", SH_NUMBER),
        (r"@\w+", SH_FUNC),
        (r"\b(print|len|range|type|int|str|float|list|dict|set|tuple|bool|open|zip|map|filter|enumerate|super|self)\b", SH_BUILTIN),
    ],
    "javascript": [
        (r"\b(const|let|var|function|return|import|export|from|if|else|for|while|in|of|class|extends|new|this|typeof|instanceof|null|undefined|true|false|try|catch|finally|throw|async|await|=>)\b", SH_KEYWORD),
        (r'`[^`]*`|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', SH_STRING),
        (r"//[^\n]*|/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b\d+\.?\d*\b", SH_NUMBER),
        (r"\b(console|document|window|Math|Array|Object|String|Number|Boolean|Promise|fetch)\b", SH_BUILTIN),
    ],
    "typescript": [],  # will fall through to javascript rules
    "bash": [
        (r"\b(if|then|else|elif|fi|for|do|done|while|case|esac|function|in|echo|exit|return|local|export|source)\b", SH_KEYWORD),
        (r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*\'', SH_STRING),
        (r"#[^\n]*", SH_COMMENT),
        (r"\$\w+|\$\{[^}]+\}", SH_FUNC),
        (r"\b\d+\b", SH_NUMBER),
    ],
    "sql": [
        (r"\b(SELECT|FROM|WHERE|JOIN|ON|AS|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|INDEX|DROP|ALTER|ADD|PRIMARY|KEY|FOREIGN|REFERENCES|NOT|NULL|AND|OR|IN|LIKE|BETWEEN|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|DISTINCT|COUNT|SUM|AVG|MAX|MIN|LEFT|RIGHT|INNER|OUTER|FULL|UNION|ALL|EXISTS|CASE|WHEN|THEN|ELSE|END)\b", SH_KEYWORD),
        (r"'[^']*'", SH_STRING),
        (r"--[^\n]*|/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b\d+\.?\d*\b", SH_NUMBER),
    ],
    "go": [
        (r"\b(func|var|const|type|struct|interface|return|import|package|if|else|for|range|switch|case|default|break|continue|go|defer|chan|map|make|new|nil|true|false|error)\b", SH_KEYWORD),
        (r'"[^"\\]*(?:\\.[^"\\]*)*"|`[^`]*`', SH_STRING),
        (r"//[^\n]*|/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b\d+\.?\d*\b", SH_NUMBER),
        (r"\b(fmt|os|io|net|http|strings|strconv|errors|log|sync|time|context)\b", SH_BUILTIN),
    ],
    "rust": [
        (r"\b(fn|let|mut|pub|struct|enum|impl|trait|use|mod|return|if|else|for|while|loop|match|break|continue|in|true|false|None|Some|Ok|Err|self|Self|super|crate|async|await|move|ref|type|where|const|static)\b", SH_KEYWORD),
        (r'"[^"\\]*(?:\\.[^"\\]*)*"|r#".*?"#', SH_STRING),
        (r"//[^\n]*|/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b\d+\.?\d*\b", SH_NUMBER),
        (r"\b(println!|print!|vec!|format!|panic!|assert!|Option|Result|String|Vec|HashMap)\b", SH_BUILTIN),
    ],
    "java": [
        (r"\b(public|private|protected|static|final|class|interface|extends|implements|return|import|package|if|else|for|while|do|switch|case|break|continue|new|this|super|null|true|false|void|int|long|double|float|boolean|String|try|catch|finally|throw|throws|abstract|synchronized|volatile)\b", SH_KEYWORD),
        (r'"[^"\\]*(?:\\.[^"\\]*)*"', SH_STRING),
        (r"//[^\n]*|/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b\d+\.?\d*[LlFfDd]?\b", SH_NUMBER),
        (r"@\w+", SH_FUNC),
    ],
    "css": [
        (r"[.#][\w-]+|:[\w-]+|::[\w-]+", SH_FUNC),
        (r"#[0-9a-fA-F]{3,6}|rgba?\([^)]+\)", SH_NUMBER),
        (r'"[^"]*"|\'[^\']*\'', SH_STRING),
        (r"/\*[\s\S]*?\*/", SH_COMMENT),
        (r"\b(color|background|margin|padding|font|border|display|position|width|height|flex|grid|transform|transition|animation)\b", SH_KEYWORD),
    ],
    "html": [
        (r"<!--[\s\S]*?-->", SH_COMMENT),
        (r'"[^"]*"|\'[^\']*\'', SH_STRING),
        (r"</?[\w]+|/>|>", SH_KEYWORD),
        (r"\b[\w-]+=", SH_FUNC),
    ],
    "json": [
        (r'"[^"\\]*(?:\\.[^"\\]*)*"', SH_STRING),
        (r"\b(true|false|null)\b", SH_KEYWORD),
        (r"\b-?\d+\.?\d*(?:[eE][+-]?\d+)?\b", SH_NUMBER),
    ],
    "yaml": [
        (r"#[^\n]*", SH_COMMENT),
        (r'"[^"]*"|\'[^\']*\'', SH_STRING),
        (r"^\s*[\w-]+:", SH_FUNC),
        (r"\b(true|false|null|yes|no)\b", SH_KEYWORD),
        (r"\b-?\d+\.?\d*\b", SH_NUMBER),
    ],
}
# aliases
_SH_RULES["js"] = _SH_RULES["javascript"]
_SH_RULES["ts"] = _SH_RULES["javascript"]
_SH_RULES["typescript"] = _SH_RULES["javascript"]
_SH_RULES["sh"] = _SH_RULES["bash"]
_SH_RULES["shell"] = _SH_RULES["bash"]


# ══════════════════════════════════════════════════════════════════════════════
#  Application support directory & logging
# ══════════════════════════════════════════════════════════════════════════════

def _get_support_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_BUNDLE
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_BUNDLE
    else:
        base = Path.home() / ".config" / APP_BUNDLE
    base.mkdir(parents=True, exist_ok=True)
    return base


APP_SUPPORT_DIR = _get_support_dir()
SETTINGS_FILE   = APP_SUPPORT_DIR / "settings.json"
CHAT_LOG_DIR    = APP_SUPPORT_DIR / "chat_history"
CHAT_INDEX_FILE = APP_SUPPORT_DIR / "chat_index.json"


def _setup_logger() -> logging.Logger:
    log_dir = APP_SUPPORT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("sullybase")
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return log
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "sullybase.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log


logger = _setup_logger()


# ══════════════════════════════════════════════════════════════════════════════
#  Data models
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChatMessage":
        return cls(role=d.get("role", ""), content=d.get("content", ""))


@dataclass
class ContextFile:
    label: str
    text:  str


@dataclass
class ChatMeta:
    id:       str
    title:    str
    created:  str
    updated:  str
    messages: List[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "title":    self.title,
            "created":  self.created,
            "updated":  self.updated,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChatMeta":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", DEFAULT_CHAT_TITLE),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
            messages=[ChatMessage.from_dict(m) for m in d.get("messages", [])],
        )


# ══════════════════════════════════════════════════════════════════════════════
#  File I/O helpers
# ══════════════════════════════════════════════════════════════════════════════

_file_lock = threading.RLock()


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with _file_lock:
            with open(path, "r", encoding="utf-8") as f:
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
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as exc:
        logger.error(f"_read_text {path}: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Path validation & context loading
# ══════════════════════════════════════════════════════════════════════════════

def _is_excluded(p: Path) -> bool:
    name = p.name
    if name in EXCLUDED_NAMES or name.lower() in EXCLUDED_NAMES:
        return True
    if p.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    if name.startswith(".") and p.suffix.lower() not in ALLOWED_TEXT_EXTS:
        return True
    return False


def _is_text(p: Path) -> bool:
    return p.suffix.lower() in ALLOWED_TEXT_EXTS


def validate_path(raw: str) -> Tuple[bool, str]:
    if not raw.strip():
        return False, ""
    try:
        p = Path(raw.strip()).expanduser().resolve()
        if not p.exists():
            return False, f"✗  Not found: {p}"
        if p.is_file():
            if _is_excluded(p):
                return False, f"✗  Excluded: {p.name}"
            if not _is_text(p):
                return False, f"✗  Not a text file: {p.suffix or '(no ext)'}"
            size = p.stat().st_size
            if size > MAX_FILE_SIZE:
                return False, f"✗  Too large ({size / 1024:.0f} KB > 2 MB)"
            return True, f"✓  File · {p.name}  ({size / 1024:.0f} KB)"
        if p.is_dir():
            count = 0
            for c in p.rglob("*"):
                if not c.is_file():
                    continue
                if ".git" in c.parts or _is_excluded(c) or not _is_text(c):
                    continue
                try:
                    if c.stat().st_size <= MAX_FILE_SIZE:
                        count += 1
                except OSError:
                    pass
            if count == 0:
                return False, "✗  No readable text files found"
            return True, f"✓  Folder · {p.name}  ({count} file{'s' if count != 1 else ''})"
        return False, "✗  Unknown path type"
    except Exception as exc:
        return False, f"✗  Error: {exc}"


def load_context_path(raw: str) -> List[ContextFile]:
    p = Path(raw.strip()).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")
    if p.is_file():
        if _is_excluded(p):
            raise ValueError(f"Excluded file: {p.name}")
        if not _is_text(p):
            raise ValueError(f"Not a text file: {p.name}")
        text = _read_text(p)
        if text is None:
            raise IOError(f"Could not read: {p.name}")
        return [ContextFile(label=f"📄 {p.name}  ({len(text):,} chars)", text=text)]
    if p.is_dir():
        results: List[ContextFile] = []
        for child in sorted(p.rglob("*")):
            if not child.is_file():
                continue
            if ".git" in child.parts or _is_excluded(child) or not _is_text(child):
                continue
            try:
                if child.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            text = _read_text(child)
            if text is None:
                continue
            rel = child.relative_to(p)
            results.append(ContextFile(
                label=f"📄 {rel}  ({len(text):,} chars)",
                text=f"### {rel}\n{text}",
            ))
        if not results:
            raise ValueError("No readable text files found in folder.")
        return results
    raise ValueError(f"Not a file or folder: {p}")


# ══════════════════════════════════════════════════════════════════════════════
#  Settings
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "model":            "",
    "window_geometry":  "1020x760",
    "ollama_url":       OLLAMA_BASE,
    "current_chat_id":  "current",
}


class Settings:
    def __init__(self):
        self._lock = threading.RLock()
        raw = _read_json(SETTINGS_FILE) or {}
        self._d: Dict[str, Any] = {**_DEFAULT_SETTINGS, **raw}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._d.get(key, default)

    def set(self, key: str, val: Any) -> None:
        with self._lock:
            self._d[key] = val

    def save(self) -> bool:
        with self._lock:
            return _write_json(SETTINGS_FILE, self._d)


# ══════════════════════════════════════════════════════════════════════════════
#  Chat persistence
# ══════════════════════════════════════════════════════════════════════════════

class ChatStore:
    @staticmethod
    def _path(chat_id: str) -> Path:
        CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        return CHAT_LOG_DIR / f"{chat_id}.json"

    @staticmethod
    def load(chat_id: str) -> Optional[ChatMeta]:
        d = _read_json(ChatStore._path(chat_id))
        if d is None:
            return None
        try:
            return ChatMeta.from_dict(d)
        except Exception as exc:
            logger.error(f"parse chat {chat_id}: {exc}")
            return None

    @staticmethod
    def save(chat: ChatMeta) -> bool:
        return _write_json(ChatStore._path(chat.id), chat.to_dict())

    @staticmethod
    def load_index() -> dict:
        return _read_json(CHAT_INDEX_FILE) or {"chats": []}

    @staticmethod
    def save_index(data: dict) -> bool:
        return _write_json(CHAT_INDEX_FILE, data)

    @staticmethod
    def now_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    @staticmethod
    def safe_title(text: str) -> str:
        text = " ".join(text.strip().split())
        return (text[:48] + "…") if len(text) > 48 else (text or DEFAULT_CHAT_TITLE)


# ══════════════════════════════════════════════════════════════════════════════
#  Ollama HTTP client
# ══════════════════════════════════════════════════════════════════════════════

class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def list_models(self) -> List[str]:
        for attempt in range(NETWORK_RETRIES):
            try:
                r = self._session.get(f"{self.base_url}/api/tags", timeout=10)
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
            except requests.exceptions.ConnectionError:
                if attempt < NETWORK_RETRIES - 1:
                    threading.Event().wait(NETWORK_RETRY_DELAY)
            except Exception as exc:
                logger.error(f"list_models: {exc}")
                return []
        return []

    def generate_title(self, model: str, user_text: str) -> str:
        prompt = (
            f"In {MAX_TITLE_LEN} characters or fewer, write a short title that "
            f"summarises this message. Reply with ONLY the title text — no quotes, "
            f"no punctuation at the end, no extra words.\n\nMessage: {user_text[:400]}"
        )
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=OLLAMA_TITLE_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("message", {}).get("content", "").strip()
            raw = raw.strip('"\'')
            if len(raw) > MAX_TITLE_LEN:
                raw = raw[:MAX_TITLE_LEN].rstrip()
            return raw or DEFAULT_CHAT_TITLE
        except Exception as exc:
            logger.warning(f"generate_title failed: {exc}")
            return DEFAULT_CHAT_TITLE

    def chat_stream(
        self,
        model: str,
        messages: List[dict],
        stop_event: threading.Event,
        on_token,
        on_done,
        on_error,
    ) -> threading.Thread:
        def _run():
            try:
                resp = self._session.post(
                    f"{self.base_url}/api/chat",
                    json={"model": model, "messages": messages, "stream": True},
                    stream=True,
                    timeout=OLLAMA_TIMEOUT,
                )
                if resp.status_code == 404:
                    on_error(f"Model '{model}' not found. Run: ollama pull {model}")
                    return
                resp.raise_for_status()
                for raw in resp.iter_lines():
                    if stop_event.is_set():
                        break
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    tok = data.get("message", {}).get("content", "")
                    if tok:
                        on_token(tok)
                    if data.get("done"):
                        break
                on_done()
            except requests.exceptions.ConnectionError:
                on_error("Cannot reach Ollama. Start it with:  ollama serve")
            except requests.exceptions.Timeout:
                on_error("Ollama timed out. Model may be overloaded.")
            except requests.exceptions.HTTPError as exc:
                on_error(f"Ollama HTTP {exc.response.status_code}")
            except Exception as exc:
                on_error(f"Unexpected error: {exc}")
                logger.error(traceback.format_exc())

        t = threading.Thread(target=_run, daemon=True, name="ollama-stream")
        t.start()
        return t


# ══════════════════════════════════════════════════════════════════════════════
#  Syntax highlighter
# ══════════════════════════════════════════════════════════════════════════════

def _highlight_code(widget: tk.Text, code: str, lang: str, start_idx: str) -> None:
    """
    Apply syntax-highlight tags to a code block already inserted into `widget`.
    `start_idx` is the tk index of the first character of `code` in the widget.
    """
    rules = _SH_RULES.get(lang.lower(), [])
    if not rules:
        return

    lines = code.split("\n")

    for pat, color in rules:
        tag = f"sh_{color.replace('#', '')}"
        widget.tag_configure(tag, foreground=color)
        pos = 0
        for line_no, line in enumerate(lines):
            for m in re.finditer(pat, line):
                row = int(start_idx.split(".")[0]) + line_no
                col_s = m.start()
                col_e = m.end()
                widget.tag_add(tag, f"{row}.{col_s}", f"{row}.{col_e}")
            pos += len(line) + 1


# ══════════════════════════════════════════════════════════════════════════════
#  Markdown renderer for tk.Text widgets  (v2 — extended)
# ══════════════════════════════════════════════════════════════════════════════

class MarkdownRenderer:
    """
    Renders markdown into a tk.Text widget.

    Supported syntax:
      Headings:       # / ## / ###
      Emphasis:       **bold**, *italic*, ~~strikethrough~~, `inline code`
      Links:          [text](url) and bare https?://...
      Code blocks:    ``` fenced (with optional language hint + syntax highlighting)
      Lists:          - / * / + unordered (nested via leading spaces)
                      1. ordered (nested)
      Task lists:     - [ ] unchecked   - [x] checked
      Blockquotes:    > text (nestable with >>)
      Tables:         | Col | Col |  with header separator
      Horizontal rule: --- / *** / ___
      Plain text with wrapping
    """

    # ── Compiled patterns ────────────────────────────────────────────────────
    _RE_FENCE   = re.compile(r"^(`{3,})([\w+-]*)")
    _RE_H3      = re.compile(r"^###\s+(.+)")
    _RE_H2      = re.compile(r"^##\s+(.+)")
    _RE_H1      = re.compile(r"^#\s+(.+)")
    _RE_UL      = re.compile(r"^(\s*)[-*+]\s+(.+)")
    _RE_OL      = re.compile(r"^(\s*)\d+\.\s+(.+)")
    _RE_TASK_U  = re.compile(r"^(\s*)[-*+]\s+\[ \]\s+(.+)")
    _RE_TASK_C  = re.compile(r"^(\s*)[-*+]\s+\[x\]\s+(.+)", re.IGNORECASE)
    _RE_BQ      = re.compile(r"^(>+)\s?(.*)")
    _RE_HR      = re.compile(r"^(\-{3,}|\*{3,}|_{3,})\s*$")
    _RE_TABLE_R = re.compile(r"^\|(.+)\|$")
    _RE_TABLE_S = re.compile(r"^\|[\s\-:|]+\|$")

    _RE_INLINE  = re.compile(
        r"(\*\*|__)(.+?)\1"                    # bold
        r"|~~(.+?)~~"                           # strikethrough
        r"|(\*|_)(.+?)\4"                       # italic
        r"|`([^`]+)`"                           # inline code
        r"|\[([^\]]+)\]\(([^)]+)\)"            # [text](url)
        r"|(https?://\S+)"                      # bare URL
    )

    def __init__(self, widget: tk.Text, bubble_bg: str, text_fg: str):
        self._w   = widget
        self._bg  = bubble_bg
        self._fg  = text_fg
        self._configure_tags()

    def _configure_tags(self):
        w = self._w
        w.tag_configure("h1",   font=("SF Pro Display", 17, "bold"),
                        foreground=FG_ACCENT, spacing3=6)
        w.tag_configure("h2",   font=("SF Pro Display", 14, "bold"),
                        foreground=FG_ACCENT, spacing3=4)
        w.tag_configure("h3",   font=("SF Pro Display", 12, "bold"),
                        foreground=FG_ACCENT, spacing3=2)
        w.tag_configure("bold",   font=("SF Pro Display", 12, "bold"),
                        foreground=self._fg)
        w.tag_configure("italic", font=("SF Pro Display", 12, "italic"),
                        foreground=self._fg)
        w.tag_configure("strike", font=("SF Pro Display", 12),
                        foreground=FG_STRIKE, overstrike=True)
        w.tag_configure("code_inline",
                        font=FONT_MONO, background=BG_CODE, foreground=FG_CODE)
        w.tag_configure("code_block",
                        font=FONT_MONO, background=BG_CODE, foreground=FG_CODE,
                        lmargin1=12, lmargin2=12, spacing1=3, spacing3=3)
        w.tag_configure("blockquote",
                        background=BG_BLOCKQUOTE, foreground=FG_BLOCKQUOTE,
                        lmargin1=20, lmargin2=20, spacing1=2, spacing3=2,
                        font=("SF Pro Display", 12, "italic"))
        w.tag_configure("ul_item",    lmargin1=16, lmargin2=28)
        w.tag_configure("ul_item_2",  lmargin1=36, lmargin2=48)
        w.tag_configure("ul_item_3",  lmargin1=56, lmargin2=68)
        w.tag_configure("ol_item",    lmargin1=16, lmargin2=32)
        w.tag_configure("ol_item_2",  lmargin1=36, lmargin2=52)
        w.tag_configure("task_box",   font=("SF Mono", 11), foreground=FG_ACCENT)
        w.tag_configure("task_done",  font=("SF Pro Display", 12),
                        foreground=FG_DIM, overstrike=True)
        w.tag_configure("hr",         foreground="#444444", spacing1=6, spacing3=6)
        w.tag_configure("link",       foreground=FG_LINK, underline=True)
        w.tag_configure("normal",     foreground=self._fg)
        # Table tags
        w.tag_configure("tbl_hdr",
                        font=("SF Pro Display", 11, "bold"),
                        foreground=FG_TABLE_HDR, background=BG_TABLE_HDR,
                        spacing1=3, spacing3=3, lmargin1=8, lmargin2=8)
        w.tag_configure("tbl_row",
                        font=("SF Pro Display", 11),
                        foreground=self._fg, background=BG_TABLE_ROW,
                        spacing1=2, spacing3=2, lmargin1=8, lmargin2=8)
        w.tag_configure("tbl_alt",
                        font=("SF Pro Display", 11),
                        foreground=self._fg, background=BG_TABLE_ALT,
                        spacing1=2, spacing3=2, lmargin1=8, lmargin2=8)

    # ── Public entry point ───────────────────────────────────────────────────

    def render(self, text: str) -> None:
        w = self._w
        lines = text.split("\n")
        i, n  = 0, len(lines)

        while i < n:
            line = lines[i]

            # ── Fenced code block ─────────────────────────────────────────
            fm = self._RE_FENCE.match(line)
            if fm:
                fence_char = fm.group(1)
                lang       = fm.group(2).strip()
                i += 1
                code_lines = []
                while i < n and not lines[i].startswith(fence_char):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing fence
                code_text = "\n".join(code_lines)
                start_idx = w.index("end-1c")
                w.insert("end", code_text + "\n", "code_block")
                if lang:
                    try:
                        _highlight_code(w, code_text, lang, start_idx)
                    except Exception:
                        pass
                continue

            # ── Table detection (collect all rows) ───────────────────────
            if self._RE_TABLE_R.match(line):
                table_lines = []
                while i < n and self._RE_TABLE_R.match(lines[i]):
                    table_lines.append(lines[i])
                    i += 1
                self._render_table(table_lines)
                continue

            # ── Blockquote ────────────────────────────────────────────────
            bm = self._RE_BQ.match(line)
            if bm:
                content = bm.group(2)
                w.insert("end", "▌ ", ("blockquote", "normal"))
                self._insert_inline(content, "blockquote")
                w.insert("end", "\n")
                i += 1
                continue

            # ── Headings ──────────────────────────────────────────────────
            m = self._RE_H3.match(line)
            if m:
                self._insert_inline(m.group(1), "h3")
                w.insert("end", "\n")
                i += 1
                continue
            m = self._RE_H2.match(line)
            if m:
                self._insert_inline(m.group(1), "h2")
                w.insert("end", "\n")
                i += 1
                continue
            m = self._RE_H1.match(line)
            if m:
                self._insert_inline(m.group(1), "h1")
                w.insert("end", "\n")
                i += 1
                continue

            # ── HR ────────────────────────────────────────────────────────
            if self._RE_HR.match(line):
                w.insert("end", "─" * 52 + "\n", "hr")
                i += 1
                continue

            # ── Task list (must check before plain UL) ────────────────────
            tm = self._RE_TASK_C.match(line)
            if tm:
                indent = len(tm.group(1)) // 2
                lvl_tag = f"ul_item{'_'+str(min(indent+1,3)) if indent else ''}"
                w.insert("end", "☑ ", ("task_box",))
                self._insert_inline(tm.group(2), "task_done")
                w.insert("end", "\n")
                i += 1
                continue
            tm = self._RE_TASK_U.match(line)
            if tm:
                w.insert("end", "☐ ", ("task_box",))
                self._insert_inline(tm.group(2), "normal")
                w.insert("end", "\n")
                i += 1
                continue

            # ── Unordered list ────────────────────────────────────────────
            m = self._RE_UL.match(line)
            if m:
                indent = len(m.group(1)) // 2
                bullets = ["•", "◦", "▸"]
                bul = bullets[min(indent, 2)]
                lvl_tag = ["ul_item", "ul_item_2", "ul_item_3"][min(indent, 2)]
                w.insert("end", f"{bul} ", (lvl_tag, "normal"))
                self._insert_inline(m.group(2), lvl_tag)
                w.insert("end", "\n")
                i += 1
                continue

            # ── Ordered list ──────────────────────────────────────────────
            m = self._RE_OL.match(line)
            if m:
                indent = len(m.group(1)) // 2
                lvl_tag = ["ol_item", "ol_item_2"][min(indent, 1)]
                num = line.lstrip().split(".")[0] + ". "
                w.insert("end", num, (lvl_tag, "normal"))
                self._insert_inline(m.group(2), lvl_tag)
                w.insert("end", "\n")
                i += 1
                continue

            # ── Plain / inline-styled line ────────────────────────────────
            self._insert_inline(line, "normal")
            w.insert("end", "\n")
            i += 1

    # ── Table renderer ───────────────────────────────────────────────────────

    def _render_table(self, rows: List[str]) -> None:
        w = self._w
        # Filter out separator rows
        data_rows = [r for r in rows if not self._RE_TABLE_S.match(r)]
        if not data_rows:
            return

        for row_idx, row in enumerate(data_rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "tbl_hdr" if row_idx == 0 else ("tbl_alt" if row_idx % 2 == 0 else "tbl_row")
            line = "  ".join(f"{cell:<20}" for cell in cells)
            w.insert("end", line + "\n", tag)

        w.insert("end", "\n")

    # ── Inline markdown ──────────────────────────────────────────────────────

    def _insert_inline(self, text: str, base_tag: str) -> None:
        w   = self._w
        pos = 0
        for m in self._RE_INLINE.finditer(text):
            if m.start() > pos:
                w.insert("end", text[pos:m.start()], (base_tag, "normal"))

            (bold_del, bold_txt,
             strike_txt,
             ital_del, ital_txt,
             code_txt,
             link_label, link_url,
             bare_url) = m.groups()

            if bold_txt is not None:
                w.insert("end", bold_txt, (base_tag, "bold"))
            elif strike_txt is not None:
                w.insert("end", strike_txt, (base_tag, "strike"))
            elif ital_txt is not None:
                w.insert("end", ital_txt, (base_tag, "italic"))
            elif code_txt is not None:
                w.insert("end", code_txt, "code_inline")
            elif link_label is not None:
                tag = f"link_{id(m)}"
                w.tag_configure(tag, foreground=FG_LINK, underline=True)
                w.tag_bind(tag, "<Button-1>",
                           lambda e, url=link_url: self._open_url(url))
                w.tag_bind(tag, "<Enter>", lambda e: w.config(cursor="hand2"))
                w.tag_bind(tag, "<Leave>", lambda e: w.config(cursor="arrow"))
                w.insert("end", link_label, (tag, "link"))
            elif bare_url is not None:
                tag = f"url_{id(m)}"
                w.tag_configure(tag, foreground=FG_LINK, underline=True)
                w.tag_bind(tag, "<Button-1>",
                           lambda e, url=bare_url: self._open_url(url))
                w.tag_bind(tag, "<Enter>", lambda e: w.config(cursor="hand2"))
                w.tag_bind(tag, "<Leave>", lambda e: w.config(cursor="arrow"))
                w.insert("end", bare_url, (tag, "link"))

            pos = m.end()

        if pos < len(text):
            w.insert("end", text[pos:], (base_tag, "normal"))

    @staticmethod
    def _open_url(url: str) -> None:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.warning(f"open_url {url}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  HoverButton
# ══════════════════════════════════════════════════════════════════════════════

class HoverButton(tk.Label):
    def __init__(self, parent, text, command,
                 bg=BG_BTN, fg=FG_PRIMARY, hover_bg=BG_BTN_HOV,
                 font=FONT_SM, padx=12, pady=6, **kw):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font,
                         padx=padx, pady=pady, cursor="hand2",
                         relief="flat", **kw)
        self._cmd       = command
        self._normal_bg = bg
        self._hover_bg  = hover_bg
        self._normal_fg = fg
        self._enabled   = True
        self.bind("<Enter>",    self._enter)
        self.bind("<Leave>",    self._leave)
        self.bind("<Button-1>", self._click)

    def _enter(self, _=None):
        if self._enabled:
            self.config(bg=self._hover_bg)

    def _leave(self, _=None):
        self.config(bg=self._normal_bg if self._enabled else "#111111")

    def _click(self, _=None):
        if self._enabled:
            self._cmd()

    def enable(self):
        self._enabled = True
        self.config(bg=self._normal_bg, fg=self._normal_fg, cursor="hand2")

    def disable(self):
        self._enabled = False
        self.config(bg="#111111", fg="#555555", cursor="arrow")


# ══════════════════════════════════════════════════════════════════════════════
#  Context Panel
# ══════════════════════════════════════════════════════════════════════════════

class ContextPanel(tk.Frame):
    def __init__(self, parent, on_change, **kw):
        super().__init__(parent, bg=BG_SIDEBAR, **kw)
        self._on_change = on_change
        self._items: List[ContextFile] = []

        hdr = tk.Frame(self, bg=BG_SIDEBAR)
        hdr.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(hdr, text="Context files", bg=BG_SIDEBAR, fg=FG_ACCENT,
                 font=("SF Pro Display", 10, "bold")).pack(side="left")
        HoverButton(hdr, "+ Add", self._add,
                    bg=BG_BTN, fg=FG_ACCENT, hover_bg=BG_BTN_HOV,
                    font=FONT_LBL, padx=8, pady=2).pack(side="right")

        self._rows = tk.Frame(self, bg=BG_SIDEBAR)
        self._rows.pack(fill="x", padx=6)
        self._rebuild()

    @property
    def items(self) -> List[ContextFile]:
        return list(self._items)

    def clear(self):
        self._items.clear()
        self._rebuild()
        self._on_change()

    def _add(self):
        top = self.winfo_toplevel()
        dlg = PathDialog(top)
        top.wait_window(dlg)
        if not dlg.result:
            return
        try:
            new = load_context_path(dlg.result)
        except Exception as exc:
            messagebox.showerror("Context Error", str(exc), parent=top)
            return
        self._items.extend(new)
        self._rebuild()
        self._on_change()

    def _remove(self, idx: int):
        if 0 <= idx < len(self._items):
            del self._items[idx]
            self._rebuild()
            self._on_change()

    def _rebuild(self):
        for w in self._rows.winfo_children():
            w.destroy()
        if not self._items:
            tk.Label(self._rows, text="No context loaded.",
                     bg=BG_SIDEBAR, fg=FG_DIM, font=FONT_LBL, anchor="w"
                     ).pack(fill="x", pady=2)
            return
        for i, cf in enumerate(self._items):
            row = tk.Frame(self._rows, bg=BG_SIDEBAR)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=cf.label, bg=BG_SIDEBAR, fg=FG_BADGE,
                     font=FONT_LBL, anchor="w", wraplength=180,
                     justify="left").pack(side="left", fill="x", expand=True)
            HoverButton(row, "X", lambda i=i: self._remove(i),
                        bg=BG_SIDEBAR, fg=FG_ERROR, hover_bg=BG_BTN,
                        font=FONT_LBL, padx=4, pady=0).pack(side="right")


# ══════════════════════════════════════════════════════════════════════════════
#  History Panel
# ══════════════════════════════════════════════════════════════════════════════

class HistoryPanel(tk.Frame):
    def __init__(self, parent, on_open, **kw):
        super().__init__(parent, bg=BG_SIDEBAR, **kw)
        self._on_open = on_open
        self._chats: List[dict] = []

        tk.Label(self, text="Saved chats", bg=BG_SIDEBAR, fg=FG_PRIMARY,
                 font=("SF Pro Display", 11, "bold"), pady=6
                 ).pack(fill="x", padx=8)

        self._lb = tk.Listbox(
            self, bg=BG_BTN, fg=FG_PRIMARY,
            selectbackground="#35516f", relief="flat",
            highlightthickness=0, activestyle="none", font=FONT_LBL
        )
        self._lb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._lb.bind("<Return>", lambda _: self._open())

        btn_frame = tk.Frame(self, bg=BG_SIDEBAR)
        btn_frame.pack(fill="x", padx=8, pady=(0, 6))

        HoverButton(btn_frame, "Open", self._open,
                    bg=BG_BTN, fg=FG_PRIMARY, hover_bg=BG_BTN_HOV,
                    font=FONT_LBL, padx=8, pady=5
                    ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        HoverButton(btn_frame, "Delete", self._delete,
                    bg=BG_BTN, fg=FG_ERROR, hover_bg=BG_BTN_HOV,
                    font=FONT_LBL, padx=8, pady=5
                    ).pack(side="left", fill="x", expand=True)

    def refresh(self):
        index = ChatStore.load_index()
        self._chats = sorted(
            index.get("chats", []),
            key=lambda x: x.get("updated", ""), reverse=True
        )
        self._lb.delete(0, "end")
        for c in self._chats:
            self._lb.insert("end", c.get("title", DEFAULT_CHAT_TITLE))

    def _open(self):
        sel = self._lb.curselection()
        if not sel:
            return
        self._on_open(self._chats[sel[0]].get("id", ""))

    def _delete(self):
        sel = self._lb.curselection()
        if not sel:
            messagebox.showwarning("No selection", "Select a chat to delete.",
                                  parent=self.winfo_toplevel())
            return
        chat_id    = self._chats[sel[0]].get("id", "")
        chat_title = self._chats[sel[0]].get("title", DEFAULT_CHAT_TITLE)
        if not messagebox.askyesno("Delete chat",
                                   f'Delete "{chat_title}"?\nThis cannot be undone.',
                                   parent=self.winfo_toplevel()):
            return
        try:
            chat_file = ChatStore._path(chat_id)
            if chat_file.exists():
                chat_file.unlink()
            index = ChatStore.load_index()
            index["chats"] = [c for c in index.get("chats", [])
                              if c.get("id") != chat_id]
            ChatStore.save_index(index)
            self.refresh()
            logger.info(f"Deleted chat: {chat_id}")
        except Exception as exc:
            messagebox.showerror("Delete Error", f"Failed to delete: {exc}",
                                parent=self.winfo_toplevel())
            logger.error(f"_delete: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════════════

class SullybaseApp(tk.Tk):

    def __init__(self):
        super().__init__()
        logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

        self._settings = Settings()
        self.title(APP_NAME)
        self.geometry(self._settings.get("window_geometry", "1020x760"))
        self.minsize(720, 520)
        self.configure(bg=BG_ROOT)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._client = OllamaClient(self._settings.get("ollama_url", OLLAMA_BASE))

        self._stop_evt   = threading.Event()
        self._generating = False
        self._stream_thr: Optional[threading.Thread] = None
        self._tok_queue: queue.Queue = queue.Queue()
        self._drain_id:  Optional[str] = None
        self._asst_tw:   Optional[tk.Text] = None
        self._asst_md:   Optional[MarkdownRenderer] = None
        self._asst_buf:  str = ""

        self._chat_id   = self._settings.get("current_chat_id", "current")
        self._chat_meta = self._blank_meta(self._chat_id)
        self._history:  List[ChatMessage] = []
        self._last_user = ""
        self._is_first_message = True   # True until first exchange in THIS chat

        self._model_var = tk.StringVar(value=self._settings.get("model", ""))

        self._style_ttk()
        self._build_ui()

        self._load_chat(self._chat_id)
        self._refresh_models()
        self._history_panel.refresh()
        self._start_drain()

    @staticmethod
    def _blank_meta(chat_id: str) -> ChatMeta:
        now = ChatStore.now_id()
        return ChatMeta(id=chat_id, title=DEFAULT_CHAT_TITLE,
                        created=now, updated=now)

    def _on_close(self):
        logger.info("Closing")
        try:
            if self._drain_id:
                self.after_cancel(self._drain_id)
                self._drain_id = None
            self._stop_evt.set()
            if self._stream_thr and self._stream_thr.is_alive():
                self._stream_thr.join(timeout=3.0)
            self._settings.set("window_geometry", self.geometry())
            self._settings.set("model", self._model_var.get())
            self._settings.set("current_chat_id", self._chat_id)
            self._settings.save()
            self._persist_chat()
            self._client.close()
        except Exception as exc:
            logger.error(f"_on_close: {exc}")
        finally:
            self.destroy()

    def _style_ttk(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",      background=BG_ROOT)
        s.configure("Chat.TFrame", background=BG_CHAT)
        s.configure("TCombobox",
            fieldbackground=BG_BTN, background=BG_BTN,
            foreground=FG_PRIMARY, selectbackground=BG_BTN,
            selectforeground=FG_PRIMARY, arrowcolor=FG_DIM)
        s.map("TCombobox",
            fieldbackground=[("readonly", BG_BTN)],
            background=[("readonly", BG_BTN)])
        s.configure("TScrollbar",
            troughcolor=BG_CHAT, background=BG_BTN,
            bordercolor=BG_CHAT, arrowcolor=FG_DIM)
        s.map("TScrollbar", background=[("active", BG_BTN_HOV)])

    def _build_ui(self):
        topbar = tk.Frame(self, bg=BG_SIDEBAR, height=52)
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)

        tk.Label(topbar, text="◈  Sullybase Local LLM Chat", bg=BG_SIDEBAR, fg=FG_ACCENT,
                 font=("SF Pro Display", 15, "bold"), padx=14
                 ).pack(side="left", pady=10)

        tk.Label(topbar, text="Model", bg=BG_SIDEBAR, fg=FG_DIM,
                 font=FONT_LBL).pack(side="left", padx=(24, 4))
        self._model_combo = ttk.Combobox(
            topbar, textvariable=self._model_var,
            state="readonly", width=24, font=FONT_SM
        )
        self._model_combo.pack(side="left", pady=10)
        self._model_combo.bind("<<ComboboxSelected>>", self._on_model_select)

        HoverButton(topbar, "Refresh", self._refresh_models,
                    bg=BG_BTN, hover_bg=BG_BTN_HOV, padx=8, pady=4
                    ).pack(side="left", padx=(4, 0), pady=10)

        HoverButton(topbar, "New Chat", self._new_chat,
                    bg=BG_BTN_NEW, fg=FG_ACCENT, hover_bg=BG_BTN_NEW_H,
                    font=FONT_BOLD
                    ).pack(side="right", padx=6, pady=10)

        mid = ttk.Frame(self, style="Chat.TFrame")
        mid.pack(side="top", fill="both", expand=True)

        left = tk.Frame(mid, bg=BG_SIDEBAR, width=230)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        self._history_panel = HistoryPanel(left, on_open=self._open_chat_readonly)
        self._history_panel.pack(fill="both", expand=True)

        tk.Frame(left, bg="#2a2a2a", height=1).pack(fill="x", padx=8, pady=2)

        self._ctx_panel = ContextPanel(left, on_change=self._on_ctx_change)
        self._ctx_panel.pack(fill="x", pady=(0, 6))

        chat_area = tk.Frame(mid, bg=BG_CHAT)
        chat_area.pack(side="left", fill="both", expand=True)

        self._vsb = ttk.Scrollbar(chat_area, orient="vertical")
        self._vsb.pack(side="right", fill="y")

        self._canvas = tk.Canvas(
            chat_area, bg=BG_CHAT, bd=0,
            highlightthickness=0,
            yscrollcommand=self._vsb.set,
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.config(command=self._canvas.yview)

        self._msgs_frame = tk.Frame(self._canvas, bg=BG_CHAT)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._msgs_frame, anchor="nw"
        )

        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._msgs_frame.bind("<Configure>", self._on_msgs_resize)

        for w in (self._canvas, self._msgs_frame):
            w.bind("<MouseWheel>", self._on_wheel)
            w.bind("<Button-4>",   self._on_wheel)
            w.bind("<Button-5>",   self._on_wheel)

        sbar = tk.Frame(self, bg=BG_SIDEBAR, height=22)
        sbar.pack(side="bottom", fill="x")
        sbar.pack_propagate(False)
        self._status_lbl = tk.Label(
            sbar, text="Ready", bg=BG_SIDEBAR, fg=FG_DIM,
            font=FONT_LBL, padx=10, anchor="w"
        )
        self._status_lbl.pack(side="left", fill="x", expand=True)
        tk.Label(sbar, text=f"v{APP_VERSION}  ·  {SETTINGS_FILE}",
                 bg=BG_SIDEBAR, fg="#555555", font=FONT_LBL, padx=10
                 ).pack(side="right")

        ia = tk.Frame(self, bg=BG_INPUT, pady=8)
        ia.pack(side="bottom", fill="x")

        row = tk.Frame(ia, bg=BG_INPUT)
        row.pack(fill="x", padx=12)

        self._input_box = tk.Text(
            row, height=4, bg=BG_BTN, fg=FG_PRIMARY,
            insertbackground=FG_ACCENT, relief="flat", font=FONT_UI,
            wrap="word", padx=10, pady=8, selectbackground="#2e4a6e",
        )
        self._input_box.pack(side="left", fill="x", expand=True)
        self._input_box.bind("<Return>",         self._on_return)
        self._input_box.bind("<Control-Return>", self._insert_newline)
        self._input_box.bind("<Shift-Return>",   self._insert_newline)
        self._input_box.bind("<Meta-Return>",    self._insert_newline)

        btn_col = tk.Frame(row, bg=BG_INPUT)
        btn_col.pack(side="left", padx=(8, 0))

        self._send_btn = HoverButton(
            btn_col, "Send", self._send_message,
            bg=BG_BTN_SEND, fg="#ffffff", hover_bg=BG_BTN_SEND_H, font=FONT_BOLD
        )
        self._send_btn.pack(fill="x")

        self._stop_btn = HoverButton(
            btn_col, "Stop", self._stop_generation,
            bg=BG_BTN_STOP, fg="#ffffff", hover_bg=BG_BTN_STOP_H, font=FONT_BOLD
        )
        self._stop_btn.pack(fill="x", pady=(6, 0))
        self._stop_btn.disable()

        tk.Label(ia, text="Return = send  ·  Shift+Return = newline",
                 bg=BG_INPUT, fg=FG_DIM, font=FONT_LBL
                 ).pack(pady=(3, 0))

    def _on_canvas_resize(self, event):
        self._canvas.itemconfig(self._canvas_win, width=event.width)

    def _on_msgs_resize(self, _=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_wheel(self, event):
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        elif event.delta:
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _scroll_to_bottom(self):
        self.after(10,  lambda: self._canvas.update_idletasks())
        self.after(20,  lambda: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self.after(30,  lambda: self._canvas.yview_moveto(1.0))

    def _add_bubble(self, role: str, text: str = "") -> Tuple[tk.Text, Optional[MarkdownRenderer]]:
        is_user   = (role == "user")
        bubble_bg = BG_BUBBLE_U if is_user else BG_BUBBLE_A
        text_fg   = FG_USER     if is_user else FG_ASST

        outer = tk.Frame(self._msgs_frame, bg=BG_CHAT)
        outer.pack(fill="x", padx=10, pady=(4, 0))

        hdr = tk.Frame(outer, bg=BG_CHAT)
        hdr.pack(fill="x")
        who = "You" if is_user else (self._model_var.get() or "Assistant")
        tk.Label(hdr, text=who, bg=BG_CHAT,
                 fg=FG_USER if is_user else FG_ACCENT,
                 font=("SF Pro Display", 10, "bold"), padx=2
                 ).pack(side="right" if is_user else "left")

        bubble = tk.Frame(outer, bg=bubble_bg, bd=0)
        bubble.pack(
            fill="x" if not is_user else None,
            anchor="e" if is_user else "w",
            pady=(2, 0),
        )

        tw = tk.Text(
            bubble,
            bg=bubble_bg, fg=text_fg,
            font=FONT_UI, relief="flat", bd=0,
            wrap="word", padx=12, pady=10,
            width=58 if is_user else 1,
            height=1,
            cursor="arrow",
            selectbackground="#2e4a6e",
            state="normal",
            yscrollcommand=lambda *a: None,
        )
        if is_user:
            tw.pack(anchor="e")
        else:
            tw.pack(fill="x", expand=True)

        renderer: Optional[MarkdownRenderer] = None
        if is_user:
            if text:
                tw.insert("1.0", text)
            tw.config(state="disabled")
        else:
            renderer = MarkdownRenderer(tw, bubble_bg, text_fg)
            if text:
                renderer.render(text)
                tw.config(state="disabled")

        self._fit_bubble(tw)
        self._scroll_to_bottom()
        return tw, renderer

    def _fit_bubble(self, tw: tk.Text) -> None:
        try:
            tw.update_idletasks()
            last_line = int(tw.index("end-1c").split(".")[0])
            tw.config(height=max(1, last_line))
        except Exception:
            pass

    def _append_token(self, tw: tk.Text, renderer: MarkdownRenderer, tok: str) -> None:
        self._asst_buf += tok
        try:
            tw.config(state="normal")
            tw.delete("1.0", "end")
            renderer.render(self._asst_buf)
            tw.config(state="disabled")
            self._fit_bubble(tw)
            self._scroll_to_bottom()
        except tk.TclError:
            pass
        except Exception as exc:
            logger.warning(f"_append_token: {exc}")

    def _add_system_note(self, text: str, color: str = FG_DIM) -> None:
        row = tk.Frame(self._msgs_frame, bg=BG_CHAT)
        row.pack(fill="x", padx=14, pady=2)
        tk.Label(row, text=text, bg=BG_CHAT, fg=color,
                 font=FONT_LBL, anchor="w", wraplength=900
                 ).pack(anchor="w")
        self._scroll_to_bottom()

    def _clear_widgets(self) -> None:
        for w in list(self._msgs_frame.winfo_children()):
            w.destroy()
        gc.collect()

    def _refresh_models(self):
        def _bg():
            models = self._client.list_models()
            self.after(0, lambda: self._set_models(models))
        threading.Thread(target=_bg, daemon=True, name="fetch-models").start()

    def _set_models(self, models: List[str]):
        if models:
            self._model_combo["values"] = models
            saved = self._model_var.get()
            if saved and saved in models:
                self._model_var.set(saved)
            elif not self._model_var.get() or self._model_var.get() not in models:
                self._model_combo.current(0)
            self._status(f"Ready — {len(models)} model(s)")
        else:
            self._model_combo["values"] = []
            self._model_var.set("")
            self._status("⚠  Ollama not reachable. Run:  ollama serve", FG_ERROR)

    def _on_model_select(self, _=None):
        self._settings.set("model", self._model_var.get())
        self._settings.save()

    def _on_ctx_change(self):
        items = self._ctx_panel.items
        if items:
            total = sum(len(cf.text) for cf in items)
            self._status(f"Context: {len(items)} file(s) · {total:,} chars")
        else:
            self._status("Ready")

    def _persist_chat(self):
        try:
            self._chat_meta.messages = list(self._history)
            self._chat_meta.updated  = ChatStore.now_id()
            ChatStore.save(self._chat_meta)
            index  = ChatStore.load_index()
            others = [c for c in index.get("chats", [])
                      if c.get("id") != self._chat_id]
            others.append({
                "id":      self._chat_id,
                "title":   self._chat_meta.title,
                "created": self._chat_meta.created,
                "updated": self._chat_meta.updated,
            })
            ChatStore.save_index({"chats": others})
        except Exception as exc:
            logger.error(f"_persist_chat: {exc}")

    def _request_llm_title(self, user_text: str) -> None:
        model = self._model_var.get()
        if not model:
            return

        def _bg():
            title = self._client.generate_title(model, user_text)
            def _update():
                self._chat_meta.title = title
                self._persist_chat()
                self._history_panel.refresh()
                logger.info(f"Chat title set by LLM: {title!r}")
            self.after(0, _update)

        threading.Thread(target=_bg, daemon=True, name="gen-title").start()

    def _load_chat(self, chat_id: str):
        self._clear_widgets()
        meta = ChatStore.load(chat_id)
        if meta:
            self._chat_meta = meta
            self._history   = list(meta.messages)
            for msg in self._history:
                self._add_bubble(msg.role, msg.content)
            self._status(f'Loaded "{meta.title}"')
            self._is_first_message = len(self._history) == 0
        else:
            self._history   = []
            self._chat_meta = self._blank_meta(chat_id)
            self._status("Ready")
            self._is_first_message = True

    def _new_chat(self):
        if self._generating:
            messagebox.showwarning("Busy", "Stop generation first.", parent=self)
            return
        self._persist_chat()
        new_id = ChatStore.now_id()
        self._chat_id   = new_id
        self._chat_meta = self._blank_meta(new_id)
        self._history.clear()
        self._clear_widgets()
        self._ctx_panel.clear()
        self._is_first_message = True
        self._settings.set("current_chat_id", new_id)
        self._settings.save()
        self._history_panel.refresh()
        self._status("New chat started")
        logger.info(f"New chat: {new_id}")

    def _open_chat_readonly(self, chat_id: str):
        meta = ChatStore.load(chat_id)
        if not meta:
            self._status("Chat file not found.", FG_ERROR)
            return
        win = tk.Toplevel(self)
        win.title(f"Read-only — {meta.title}")
        win.configure(bg=BG_ROOT)
        win.geometry("900x680")

        vsb = ttk.Scrollbar(win, orient="vertical")
        vsb.pack(side="right", fill="y")
        txt = tk.Text(win, bg=BG_CHAT, fg=FG_PRIMARY, relief="flat",
                      wrap="word", padx=14, pady=14, font=FONT_UI,
                      yscrollcommand=vsb.set)
        txt.pack(fill="both", expand=True)
        vsb.config(command=txt.yview)

        lines = [
            f"Title:   {meta.title}",
            f"Created: {meta.created}",
            f"Updated: {meta.updated}", "",
        ]
        for msg in meta.messages:
            lines.append(f"{'YOU' if msg.role == 'user' else 'ASSISTANT'}:")
            lines.append(msg.content)
            lines.append("")
        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")

    def _build_messages(self, user_text: str) -> List[dict]:
        """
        Build the message list to send to Ollama.

        The system prompt is injected only on the very first message of a chat
        (when self._history is empty). For subsequent messages, the model already
        has the system instructions in its context window, so we omit it to avoid
        token bloat.
        """
        msgs: List[dict] = []

        # ── System prompt: first message only ────────────────────────────────
        if not self._history:
            ctx_items = self._ctx_panel.items
            sys_content = AI_SYSTEM_PROMPT
            if ctx_items:
                sys_content += (
                    "\n\nThe following context files are loaded:\n\n"
                    + "\n\n---\n\n".join(cf.text for cf in ctx_items)
                )
            msgs.append({"role": "system", "content": sys_content})

        # ── Full conversation history ─────────────────────────────────────────
        msgs += [{"role": m.role, "content": m.content} for m in self._history]

        # ── Current user turn ────────────────────────────────────────────────
        msgs.append({"role": "user", "content": user_text})
        return msgs

    def _on_return(self, event) -> Optional[str]:
        if event.state & 0x1:
            return None
        self._send_message()
        return "break"

    def _insert_newline(self, _) -> str:
        self._input_box.insert("insert", "\n")
        return "break"

    def _send_message(self):
        if self._generating:
            return
        user_text = self._input_box.get("1.0", "end-1c").strip()
        if not user_text:
            return
        model = self._model_var.get()
        if not model:
            self._status("No model selected.", FG_ERROR)
            return

        self._last_user = user_text
        self._input_box.delete("1.0", "end")
        self._add_bubble("user", user_text)

        self._asst_tw, self._asst_md = self._add_bubble("assistant")
        self._asst_buf = ""

        if self._is_first_message:
            self._is_first_message = False
            self._request_llm_title(user_text)

        self._generating = True
        self._stop_evt.clear()
        self._send_btn.disable()
        self._stop_btn.enable()
        self._status("Generating…  (Stop to cancel)")

        self._stream_thr = self._client.chat_stream(
            model=model,
            messages=self._build_messages(user_text),
            stop_event=self._stop_evt,
            on_token=lambda tok: self._tok_queue.put((_TOKEN, tok)),
            on_done=lambda:        self._tok_queue.put(_DONE),
            on_error=lambda err:   self._tok_queue.put((_ERROR, err)),
        )

    def _start_drain(self):
        self._drain_id = self.after(DRAIN_INTERVAL_MS, self._drain)

    def _drain(self):
        try:
            for _ in range(200):
                item = self._tok_queue.get_nowait()
                if item is _DONE:
                    self._finish_generation()
                    break
                elif isinstance(item, tuple):
                    kind, payload = item
                    if kind is _TOKEN and self._asst_tw and self._asst_md and self._generating:
                        self._append_token(self._asst_tw, self._asst_md, payload)
                    elif kind is _ERROR:
                        self._show_error(payload)
                        break
        except queue.Empty:
            pass
        finally:
            self._drain_id = self.after(DRAIN_INTERVAL_MS, self._drain)

    def _finish_generation(self):
        if not self._generating:
            return
        try:
            final = self._asst_buf
            if self._asst_tw:
                self._asst_tw.config(state="disabled")
            self._history.append(ChatMessage("user",      self._last_user))
            self._history.append(ChatMessage("assistant", final))
            self._persist_chat()
            self._history_panel.refresh()
            logger.info(f"Generation done. History length: {len(self._history)}")
        except Exception as exc:
            logger.error(f"_finish_generation: {exc}")
            self._status("Error saving response.", FG_ERROR)
        finally:
            self._generating = False
            self._send_btn.enable()
            self._stop_btn.disable()
            self._status("Ready")

    def _stop_generation(self):
        self._stop_evt.set()
        if self._asst_buf:
            self._history.append(ChatMessage("user",      self._last_user))
            self._history.append(ChatMessage("assistant", self._asst_buf + " [stopped]"))
            self._persist_chat()
        self._add_system_note("— generation stopped —")
        self._generating = False
        self._send_btn.enable()
        self._stop_btn.disable()
        self._status("Stopped")

    def _show_error(self, msg: str):
        try:
            if self._asst_tw and self._asst_md:
                self._append_token(self._asst_tw, self._asst_md, f"\n\n⚠  {msg}")
            logger.error(f"stream error: {msg}")
        except Exception:
            pass
        finally:
            self._generating = False
            self._send_btn.enable()
            self._stop_btn.disable()
            self._status("Error: " + msg.splitlines()[0], FG_ERROR)

    def _status(self, msg: str, color: str = FG_DIM):
        self._status_lbl.config(text=msg, fg=color)


# ══════════════════════════════════════════════════════════════════════════════
#  Path Dialog
# ══════════════════════════════════════════════════════════════════════════════

class PathDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add Context")
        self.configure(bg=BG_ROOT)
        self.resizable(True, False)
        self.result: Optional[str] = None
        self._val_id: Optional[str] = None
        self.grab_set()
        self.transient(parent)
        self._build()
        self.geometry("600x220")
        self.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2 - 300
        py = parent.winfo_y() + parent.winfo_height() // 2 - 110
        self.geometry(f"+{px}+{py}")

    def _build(self):
        tk.Label(self, text="File or folder path:",
                 bg=BG_ROOT, fg=FG_PRIMARY, font=FONT_UI, pady=8
                 ).pack(anchor="w", padx=20)

        erow = tk.Frame(self, bg=BG_ROOT)
        erow.pack(fill="x", padx=20)

        self._path_var = tk.StringVar()
        entry = tk.Entry(
            erow, textvariable=self._path_var,
            bg=BG_BTN, fg=FG_PRIMARY, insertbackground=FG_ACCENT,
            relief="flat", font=FONT_MONO, selectbackground="#2e4a6e",
        )
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        entry.focus_set()
        entry.bind("<Return>", lambda _: self._ok())
        entry.bind("<Escape>", lambda _: self.destroy())

        HoverButton(erow, "File",   lambda: self._browse("file"),
                    bg=BG_BTN, hover_bg=BG_BTN_HOV, font=FONT_LBL,
                    padx=8, pady=6).pack(side="left", padx=(4, 0))
        HoverButton(erow, "Folder", lambda: self._browse("folder"),
                    bg=BG_BTN, hover_bg=BG_BTN_HOV, font=FONT_LBL,
                    padx=8, pady=6).pack(side="left", padx=(4, 0))

        self._status_lbl = tk.Label(
            self, text="", bg=BG_ROOT, fg=FG_DIM,
            font=("SF Mono", 10), anchor="w", padx=20, pady=2
        )
        self._status_lbl.pack(fill="x")

        tk.Label(self, text="Tip: paste a path, type ~/ for home, or use the buttons.",
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_LBL
                 ).pack(pady=(0, 4))

        brow = tk.Frame(self, bg=BG_ROOT)
        brow.pack(pady=8)

        self._load_btn = HoverButton(
            brow, "Load", self._ok,
            bg=BG_BTN, fg=FG_DIM, hover_bg=BG_BTN_HOV,
            font=FONT_BOLD, padx=28, pady=7
        )
        self._load_btn.pack(side="left", padx=6)
        self._load_btn.disable()

        HoverButton(brow, "Cancel", self.destroy,
                    bg=BG_BTN, fg=FG_PRIMARY, hover_bg=BG_BTN_HOV,
                    font=FONT_UI, padx=20, pady=7
                    ).pack(side="left", padx=6)

        self._path_var.trace_add("write", self._on_change)

    def _on_change(self, *_):
        if self._val_id:
            self.after_cancel(self._val_id)
        self._val_id = self.after(300, self._validate)

    def _validate(self):
        raw = self._path_var.get()
        if not raw.strip():
            self._status_lbl.config(text="", bg=BG_ROOT)
            self._load_btn.disable()
            return
        ok, msg = validate_path(raw)
        if ok:
            self._status_lbl.config(text=msg, fg=FG_PATH_OK, bg=BG_PATH_OK)
            self._load_btn._normal_bg = BG_BTN_SEND
            self._load_btn._hover_bg  = BG_BTN_SEND_H
            self._load_btn.enable()
            self._load_btn.config(bg=BG_BTN_SEND, fg="#ffffff")
        else:
            self._status_lbl.config(text=msg, fg=FG_PATH_BAD, bg=BG_PATH_BAD)
            self._load_btn._normal_bg = BG_BTN
            self._load_btn._hover_bg  = BG_BTN_HOV
            self._load_btn.disable()

    def _browse(self, mode: str):
        if mode == "file":
            path = filedialog.askopenfilename(parent=self, title="Select a text file")
        else:
            path = filedialog.askdirectory(parent=self, title="Select a folder")
        if path:
            self._path_var.set(path)

    def _ok(self):
        raw = self._path_var.get().strip()
        if not raw:
            return
        ok, msg = validate_path(raw)
        if not ok:
            self._status_lbl.config(text=msg, fg=FG_PATH_BAD, bg=BG_PATH_BAD)
            return
        self.result = raw
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        app = SullybaseApp()
        app.mainloop()
    except Exception as exc:
        logger.critical(f"Fatal: {exc}\n{traceback.format_exc()}")
        print(f"FATAL ERROR: {exc}", file=sys.stderr)
        sys.exit(1)