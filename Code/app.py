#!/usr/bin/env python3
"""
Sullybase Local LLM Chat  v2.5.1
Desktop launcher.

- Headless mode (default on Linux servers / dev boxes):
      python app.py            -> Flask on 127.0.0.1:5050
- GUI mode (default on macOS with pywebview installed):
      python app.py --gui      -> opens a pywebview window pointing at Flask
- Force headless:
      python app.py --no-gui
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from pathlib import Path

APP_NAME    = "Sullybase Local LLM Chat"
APP_BUNDLE  = "Sullybase-LLM-Chat"
FLASK_HOST  = "127.0.0.1"
FLASK_PORT  = int(os.environ.get("SULLYBASE_PORT", "5050"))


# ── Support dir & logger ──────────────────────────────────────────────────────
def _get_support_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_BUNDLE
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_BUNDLE
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_BUNDLE
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
        log_dir / "sullybase.log", maxBytes=5 * 1024 * 1024,
        backupCount=3, encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    # Wrap stderr so non-ASCII log messages never crash the stream handler.
    stderr_stream = sys.stderr
    try:
        import io
        stderr_stream = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8",
            errors="backslashreplace", line_buffering=True,
        )
    except Exception:
        pass
    ch = logging.StreamHandler(stderr_stream)
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            "%Y-%m-%d %H:%M:%S")
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
_check_dep("flask_cors", "flask-cors")

# pywebview is optional — only required for GUI mode.
try:
    import webview  # type: ignore  # noqa: E402
    _HAS_WEBVIEW = True
except Exception:
    webview = None  # type: ignore
    _HAS_WEBVIEW = False

from server import create_app, APP_VERSION  # noqa: E402

ICON_PATH = Path(__file__).parent / "Icon.png"


def _run_flask(app, port: int = FLASK_PORT):
    import logging as _l
    _l.getLogger("werkzeug").setLevel(_l.ERROR)
    app.run(host=FLASK_HOST, port=port, debug=False,
            use_reloader=False, threaded=True)


def _wait_for_flask(port: int, timeout: float = 15.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(
                f"http://{FLASK_HOST}:{port}/api/ping", timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--gui", action="store_true",
                        help="Open a pywebview window (default on macOS if available)")
    parser.add_argument("--no-gui", action="store_true",
                        help="Force headless mode (no pywebview window)")
    parser.add_argument("--port", type=int, default=FLASK_PORT,
                        help=f"Flask port (default {FLASK_PORT})")
    args = parser.parse_args()

    use_gui = args.gui or (sys.platform == "darwin" and _HAS_WEBVIEW)
    if args.no_gui:
        use_gui = False
    if use_gui and not _HAS_WEBVIEW:
        logger.warning("pywebview not installed — falling back to headless mode.")
        use_gui = False

    logger.info(f"Starting {APP_NAME} v{APP_VERSION} (gui={use_gui})")

    flask_app = create_app(APP_SUPPORT_DIR)

    flask_thread = threading.Thread(
        target=_run_flask, args=(flask_app, args.port),
        daemon=True, name="flask",
    )
    flask_thread.start()

    if not _wait_for_flask(args.port):
        logger.critical("Flask did not start — aborting.")
        sys.exit(1)

    logger.info(f"Flask ready on http://{FLASK_HOST}:{args.port}")

    if not use_gui:
        # Headless: keep the main thread alive for the Flask thread.
        print(f"\n  {APP_NAME} running at http://{FLASK_HOST}:{args.port}\n")
        print("  Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down (KeyboardInterrupt).")
        return

    icon = str(ICON_PATH) if ICON_PATH.exists() else None
    webview.create_window(
        title=f"{APP_NAME}  v{APP_VERSION}",
        url=f"http://{FLASK_HOST}:{args.port}",
        width=1200, height=820, min_size=(760, 540),
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
