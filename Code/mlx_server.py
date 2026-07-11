#!/usr/bin/env python3
"""
mlx_server.py — MLX backend for Sullybase.

Implements the shared backend interface by wrapping MLX LM Server's
OpenAI-compatible API. All MLX-specific logic is contained here.

MLX-LM's HTTP server is OpenAI-compatible: /v1/models, /v1/chat/completions
(streaming SSE via delta.content). It does not expose a /api/ps style
runtime-stats endpoint, so get_ps_info falls back to a macOS sysctl probe
for unified memory totals.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import os
import subprocess
import time
import traceback
from typing import Generator, List, Tuple

import requests

logger = logging.getLogger("sullybase.mlx")

# Configuration
MLX_BASE              = "http://localhost:8080"
MLX_TIMEOUT           = (10, 300)   # connect, read
MLX_TITLE_TIMEOUT     = (10, 30)
NETWORK_RETRIES       = 2
NETWORK_RETRY_DELAY   = 0.8
DEFAULT_CHAT_TITLE    = "New chat"
MAX_TITLE_LEN         = 40
DEFAULT_MAX_TOKENS    = 1024


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _is_apple_silicon() -> bool:
    return platform.machine() == "arm64" and platform.system() == "Darwin"


def _total_system_memory_mb() -> int:
    try:
        if _is_apple_silicon():
            r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                               capture_output=True, text=True)
            return int(r.stdout.strip()) // (1024 * 1024)
        import psutil  # type: ignore
        return psutil.virtual_memory().total // (1024 * 1024)
    except Exception:
        return 0


def _process_tree_rss_mb(proc) -> int:
    """Return RSS for a process and any child processes we can inspect."""
    try:
        import psutil  # type: ignore
    except Exception:
        return 0

    total_bytes = 0
    stack = [proc]
    seen = set()

    while stack:
        current = stack.pop()
        try:
            pid = current.pid
        except Exception:
            continue
        if pid in seen:
            continue
        seen.add(pid)

        try:
            total_bytes += current.memory_info().rss
            stack.extend(current.children(recursive=False))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return total_bytes


def _current_mlx_process_mb() -> int:
    """Best-effort RSS for the running MLX server process tree."""
    try:
        import psutil  # type: ignore

        current_pid = os.getpid()
        candidates = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            if proc.info.get("pid") == current_pid:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "mlx_lm.server" in cmdline or re.search(r"\bmlx_lm\b", cmdline):
                candidates.append(proc)

        if candidates:
            # Prefer the heaviest MLX process tree.  MLX server launches can
            # include a lightweight wrapper plus the actual model process, and
            # the wrapper is the one that often matches the shortest command
            # line.  Picking the largest RSS avoids under-reporting when the
            # real model lives in the child process.
            rss_values = [_process_tree_rss_mb(proc) for proc in candidates]
            return max(rss_values) // (1024 * 1024)
    except Exception:
        pass

    try:
        r = subprocess.run(["pgrep", "-f", "mlx_lm"],
                           capture_output=True, text=True, timeout=2)
        pids = [p for p in r.stdout.split() if p]
        if not pids:
            return 0
        r2 = subprocess.run(
            ["ps", "-o", "rss=", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=2,
        )
        rss_kb = [int(line) for line in r2.stdout.split() if line.isdigit()]
        return max(rss_kb) // 1024 if rss_kb else 0
    except Exception:
        return 0


class MLXBackend:
    """Backend implementation for MLX LM Server (OpenAI-compatible)."""

    PROVIDER = "mlx"

    def __init__(self, base_url: str = MLX_BASE):
        self.base_url = (base_url or MLX_BASE).rstrip("/")
        self._session = requests.Session()

    def set_base_url(self, base_url: str):
        if base_url and base_url.strip():
            self.base_url = base_url.strip().rstrip("/")

    # ── Health & discovery ──────────────────────────────────────────────
    def health_check(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/v1/models", timeout=3)
            return r.ok
        except Exception:
            return False

    def list_models(self) -> List[str]:
        for attempt in range(NETWORK_RETRIES):
            try:
                r = self._session.get(f"{self.base_url}/v1/models", timeout=8)
                r.raise_for_status()
                data = r.json()
                models = data.get("data", [])
                if isinstance(models, list):
                    return [m.get("id", "") for m in models if m.get("id")]
                return []
            except requests.exceptions.ConnectionError:
                if attempt < NETWORK_RETRIES - 1:
                    time.sleep(NETWORK_RETRY_DELAY)
            except Exception as exc:
                logger.error(f"list_models: {exc}")
                return []
        return []

    def get_model_info(self, model: str) -> dict:
        result = {"context_length": 0, "quantization": "",
                  "parameter_size": "", "family": ""}
        if not model:
            return result
        try:
            r = self._session.get(
                f"{self.base_url}/v1/models/{model}", timeout=8)
            if r.ok:
                data = r.json()
                # MLX-LM typically returns the context_length at the top level.
                result["context_length"] = int(data.get("context_length", 0) or 0)
        except Exception as exc:
            logger.debug(f"get_model_info: {exc}")
        return result

    def get_ps_info(self, model: str = "") -> dict:
        """MLX doesn't expose /api/ps — best-effort memory snapshot.

        On Apple Silicon we know memory is unified, so we report the
        system total + the mlx_lm process RSS as a reasonable
        'used / total' figure. Off macOS we just return zeros so the
        UI shows 'idle' instead of lying.
        """
        result = {
            "vram_used_mb": 0, "vram_free_mb": 0, "vram_total_mb": 0,
            "device": "", "accelerator": "", "resident_mb": 0,
            "model_loaded": False, "num_gpu_layers": 0,
            "memory_kind": "",
        }
        if self.health_check():
            result["model_loaded"] = True
            result["accelerator"] = "Metal" if _is_apple_silicon() else "GPU"
            result["device"]      = result["accelerator"]
        if _is_apple_silicon():
            result["memory_kind"] = "unified"
            total = _total_system_memory_mb()
            used  = _current_mlx_process_mb()
            result["vram_total_mb"] = total
            result["vram_used_mb"]  = used
            result["resident_mb"]   = used
            result["vram_free_mb"]  = max(0, total - used) if total and used else 0
        return result

    # ── Title generation ────────────────────────────────────────────────
    def generate_title(
        self, model: str, user_text: str, reply_text: str = ""
    ) -> Tuple[str, bool]:
        convo = f"User: {user_text[:400]}"
        if reply_text:
            convo += f"\nAssistant: {reply_text[:400]}"

        prompt = (
            "Summarize the topic of this chat in a short title.\n\n"
            f"{convo}\n\n"
            "Rules:\n"
            f"- {MAX_TITLE_LEN} characters or fewer\n"
            "- 3-6 words\n"
            "- Plain text only: no quotes, no markdown, no emoji, "
            "no trailing punctuation\n"
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
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0.3,
                    "max_tokens": 40,
                },
                timeout=MLX_TITLE_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return DEFAULT_CHAT_TITLE, False

            raw = choices[0].get("message", {}).get("content", "").strip()
            if not raw:
                return DEFAULT_CHAT_TITLE, False

            line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
            line = re.sub(r"^(title|chat title)\s*[:\-]\s*", "",
                          line, flags=re.IGNORECASE)
            line = line.strip(" \t\"'*`")
            line = re.sub(r"[.!?]+$", "", line).strip()

            if not line:
                return DEFAULT_CHAT_TITLE, False
            if len(line) > MAX_TITLE_LEN:
                line = line[:MAX_TITLE_LEN].rstrip()
            return line, True
        except requests.exceptions.ConnectionError:
            logger.warning("generate_title: cannot reach MLX server")
            return DEFAULT_CHAT_TITLE, False
        except Exception as exc:
            logger.warning(f"generate_title: {exc}")
            return DEFAULT_CHAT_TITLE, False

    # ── Streaming chat ──────────────────────────────────────────────────
    def send_prompt(
        self,
        model: str,
        messages: List[dict],
        system: str = "",
    ) -> Generator[str, None, None]:
        """Stream a chat completion from MLX (OpenAI-compatible).

        Yields SSE events: first_token, token, done, error.
        """
        final_messages: List[dict] = []
        if system and not any(m.get("role") == "system" for m in messages):
            final_messages.append({"role": "system", "content": system})
        final_messages.extend(messages)

        # A few chat templates will otherwise continue the final user turn and
        # reproduce it verbatim.  This instruction is deliberately concise so
        # it does not override the application's custom instructions.
        for message in final_messages:
            if message.get("role") == "system":
                message["content"] = (
                    f"{message.get('content', '').rstrip()}\n\n"
                    "Answer the user's latest message directly; do not echo it."
                ).strip()
                break
        else:
            final_messages.insert(0, {
                "role": "system",
                "content": "Answer the user's latest message directly; do not echo it.",
            })

        # Retain the final user turn so an accidental prompt echo can be
        # removed before anything reaches the browser.  MLX streams arbitrary
        # token boundaries, so this is handled below as a small prefix buffer.
        last_user_text = next(
            (str(m.get("content", "")) for m in reversed(final_messages)
             if m.get("role") == "user"),
            "",
        )

        try:
            resp = self._session.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": final_messages,
                    "stream": True,
                    "max_tokens": DEFAULT_MAX_TOKENS,
                    "temperature": 0.7,
                    "stream_options": {"include_usage": True},
                },
                stream=True,
                timeout=MLX_TIMEOUT,
            )
            if resp.status_code == 404:
                yield _sse("error", {
                    "message": f"Model '{model}' not found on MLX server",
                    "provider": "mlx",
                })
                return
            resp.raise_for_status()

            first_token_sent = False
            t_start = time.monotonic()
            usage_data: dict = {}
            echo_prefix = ""
            echo_removed = False
            emitted_content = False

            def emit_token(token: str) -> Generator[str, None, None]:
                """Yield a token, delaying an exact initial user-message echo."""
                nonlocal first_token_sent, echo_prefix, echo_removed, emitted_content
                if not token:
                    return

                # Only inspect the beginning of a completion.  Once normal
                # generated text has been emitted, repeated text is valid and
                # must never be altered.
                if not emitted_content and last_user_text and not echo_removed:
                    echo_prefix += token
                    if last_user_text.startswith(echo_prefix):
                        if echo_prefix == last_user_text:
                            echo_prefix = ""
                            echo_removed = True
                        return
                    token, echo_prefix = echo_prefix, ""

                if not token:
                    return
                emitted_content = True
                if not first_token_sent:
                    first_token_sent = True
                    yield _sse("first_token", {
                        "ms": round((time.monotonic() - t_start) * 1000)
                    })
                yield _sse("token", {"token": token})

            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue

                line_str = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line_str.startswith("data: "):
                    continue

                json_str = line_str[6:].strip()
                if json_str == "[DONE]":
                    # Flush a partial prefix that was not an exact echo.
                    if echo_prefix:
                        pending, echo_prefix = echo_prefix, ""
                        echo_removed = True
                        yield from emit_token(pending)
                    gen_ms = max(1, round((time.monotonic() - t_start) * 1000))
                    completion_tokens = int(usage_data.get("completion_tokens", 0) or 0)
                    tps = (round(completion_tokens / (gen_ms / 1000), 1)
                           if completion_tokens else 0)
                    if echo_removed and not emitted_content:
                        yield _sse("error", {
                            "message": ("MLX returned the prompt instead of an answer. "
                                        "Check that the selected model has a chat template."),
                            "provider": "mlx",
                        })
                        return
                    yield _sse("done", {
                        "prompt_tokens":     usage_data.get("prompt_tokens", 0),
                        "completion_tokens": completion_tokens,
                        "total_duration_ns": 0,
                        "eval_duration_ns":  gen_ms * 1_000_000,
                        "tokens_per_sec":    tps,
                        "gen_ms":            gen_ms,
                    })
                    return

                try:
                    data = json.loads(json_str)
                except (json.JSONDecodeError, ValueError):
                    continue

                # With stream_options.include_usage MLX emits a final usage
                # chunk with an empty choices list, so capture it first.
                if "usage" in data:
                    usage_data = data["usage"] or {}

                choices = data.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {}) or {}
                # MLX-LM uses delta.content.  Supporting text and reasoning
                # content as well keeps this adapter compatible with MLX
                # releases and OpenAI-compatible proxies that vary slightly.
                tok = delta.get("content", choice.get("text", "")) or ""
                if isinstance(tok, list):
                    tok = "".join(
                        str(part.get("text", "")) if isinstance(part, dict) else str(part)
                        for part in tok
                    )
                reasoning = delta.get("reasoning_content", "") or ""
                if reasoning:
                    tok = f"<think>{reasoning}</think>" + str(tok)

                if tok:
                    yield from emit_token(str(tok))

            # Stream ended without explicit [DONE].
            if echo_prefix:
                pending, echo_prefix = echo_prefix, ""
                echo_removed = True
                yield from emit_token(pending)
            if echo_removed and not emitted_content:
                yield _sse("error", {
                    "message": ("MLX returned the prompt instead of an answer. "
                                "Check that the selected model has a chat template."),
                    "provider": "mlx",
                })
            else:
                yield _sse("done", {})

        except requests.exceptions.ConnectionError:
            yield _sse("error", {
                "message": "Cannot reach MLX server — start it with `python -m mlx_lm.server --model <model>`",
                "provider": "mlx",
            })
        except requests.exceptions.Timeout:
            yield _sse("error", {
                "message": "MLX server timed out — the model may still be loading",
                "provider": "mlx",
            })
        except requests.exceptions.HTTPError as exc:
            yield _sse("error", {
                "message": f"MLX HTTP {exc.response.status_code}",
                "provider": "mlx",
            })
        except Exception as exc:
            logger.error(traceback.format_exc())
            yield _sse("error", {
                "message": f"Unexpected error: {exc}",
                "provider": "mlx",
            })
