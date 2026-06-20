#!/usr/bin/env python3
"""
Sullybase Local LLM Chat  v2.4.0
Desktop launcher — starts Flask then opens a pywebview window.
"""

import os
import sys
import time
import threading
import traceback
import logging
import logging.handlers
from pathlib import Path

APP_NAME    = "Sullybase Local LLM Chat"
APP_BUNDLE  = "Sullybase-LLM-Chat"
FLASK_HOST  = "127.0.0.1"
FLASK_PORT  = 5050


# ── Support dir & logger ──────────────────────────────────────────────────────
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


def _setup_logger() -> logging.Logger:
    log_dir = APP_SUPPORT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("sullybase")
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return log
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "sullybase.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    # Bundled/GUI-launched apps often have no controlling terminal, so
    # sys.stderr can default to an ASCII encoding and crash on any
    # non-ASCII character (em dashes, etc.) in a log message. Wrap stderr
    # in a UTF-8 TextIOWrapper so the stream handler never raises on emit.
    stderr_stream = sys.stderr
    try:
        import io
        stderr_stream = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="backslashreplace", line_buffering=True
        )
    except Exception:
        pass
    ch = logging.StreamHandler(stderr_stream)
    ch.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log


logger = _setup_logger()


# ── Dependency checks ─────────────────────────────────────────────────────────
def _check_dep(module: str, install: str):
    try:
        __import__(module)
    except ImportError:
        print(f"ERROR: '{module}' not installed.  Run:  pip install {install}")
        sys.exit(1)

_check_dep("requests", "requests")
_check_dep("flask",    "flask")
_check_dep("webview",  "pywebview")

import webview  # noqa: E402
from server import create_app, APP_VERSION  # noqa: E402

ICON_PATH = Path(__file__).parent / "Icon.png"


def _run_flask(app):
    import logging as _l
    _l.getLogger("werkzeug").setLevel(_l.ERROR)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False, threaded=True)


def _wait_for_flask(timeout: float = 15.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://{FLASK_HOST}:{FLASK_PORT}/api/ping", timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

    flask_app = create_app(APP_SUPPORT_DIR)

    flask_thread = threading.Thread(
        target=_run_flask, args=(flask_app,), daemon=True, name="flask"
    )
    flask_thread.start()

    if not _wait_for_flask():
        logger.critical("Flask did not start — aborting.")
        sys.exit(1)

    logger.info(f"Flask ready on http://{FLASK_HOST}:{FLASK_PORT}")

    icon = str(ICON_PATH) if ICON_PATH.exists() else None

    webview.create_window(
        title=f"{APP_NAME}  v{APP_VERSION}",
        url=f"http://{FLASK_HOST}:{FLASK_PORT}",
        width=1200,
        height=820,
        min_size=(760, 540),
        background_color="#111312",
    )

    webview.start(debug=False)
    logger.info("Window closed — exiting.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.critical(f"Fatal: {exc}\n{traceback.format_exc()}")
        print(f"FATAL ERROR: {exc}", file=sys.stderr)
        sys.exit(1)