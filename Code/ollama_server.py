#!/usr/bin/env python3
"""
ollama_server.py — Ollama backend for Sullybase.

Implements the shared backend interface by wrapping Ollama's HTTP API:
  - health_check()
  - list_models()
  - get_model_info(model)
  - get_ps_info(model="")
  - generate_title(model, user_text, reply_text="") -> (title, ok)
  - send_prompt(model, messages, system="") -> yields SSE strings

All Ollama-specific logic is contained here; server.py calls only
through these methods. Error events are provider-tagged so the UI can
show Ollama-specific guidance.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import time
import traceback
from typing import Generator, List, Tuple

import requests

logger = logging.getLogger("sullybase.ollama")

# Configuration
OLLAMA_BASE          = "http://localhost:11434"
OLLAMA_TIMEOUT       = (10, 300)   # connect, read
OLLAMA_TITLE_TIMEOUT = (10, 30)
NETWORK_RETRIES      = 2
NETWORK_RETRY_DELAY  = 0.8
DEFAULT_CHAT_TITLE   = "New chat"
MAX_TITLE_LEN        = 40


def _sse(event: str, data: dict) -> str:
    """Format data as Server-Sent Event."""
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


class OllamaBackend:
    """Backend implementation for Ollama."""

    PROVIDER = "ollama"

    def __init__(self, base_url: str = OLLAMA_BASE):
        self.base_url = (base_url or OLLAMA_BASE).rstrip("/")
        self._session = requests.Session()

    def set_base_url(self, base_url: str):
        if base_url and base_url.strip():
            self.base_url = base_url.strip().rstrip("/")

    # ── Health & discovery ──────────────────────────────────────────────
    def health_check(self) -> bool:
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
            r = self._session.post(f"{self.base_url}/api/show",
                                   json={"name": model}, timeout=8)
            r.raise_for_status()
            data = r.json()
            model_info = data.get("model_info", {})
            for k in model_info:
                if "context_length" in k:
                    try:
                        result["context_length"] = int(model_info[k])
                    except (TypeError, ValueError):
                        pass
                    break
            details = data.get("details", {})
            result["quantization"]   = details.get("quantization_level", "")
            result["parameter_size"] = details.get("parameter_size", "")
            result["family"]         = details.get("family", "")
        except Exception as exc:
            logger.debug(f"get_model_info: {exc}")
        return result

    def get_ps_info(self, model: str = "") -> dict:
        """Runtime stats for the currently loaded model.

        On Apple Silicon the GPU memory is unified with system memory,
        so the 'VRAM' total is really the physical RAM total and we mark
        memory_kind='unified' so the UI labels it 'Memory'.
        """
        result = {
            "vram_used_mb": 0, "vram_free_mb": 0, "vram_total_mb": 0,
            "device": "", "accelerator": "", "resident_mb": 0,
            "model_loaded": False, "num_gpu_layers": 0,
            "memory_kind": "",
        }
        try:
            r = self._session.get(f"{self.base_url}/api/ps", timeout=5)
            r.raise_for_status()
            data = r.json()
            models_list = data.get("models", [])
            if not models_list:
                return result

            m = next((x for x in models_list if x.get("name") == model),
                     models_list[0])

            size_vram = m.get("size_vram", 0)
            size_total = m.get("size", 0)
            num_gpu = m.get("details", {}).get("num_gpu_layers", 0)
            gpu_offloaded = num_gpu > 0 or size_vram > 0

            result["vram_used_mb"]   = size_vram // (1024 * 1024)
            result["resident_mb"]    = size_total // (1024 * 1024)
            result["num_gpu_layers"] = num_gpu
            result["model_loaded"]   = True

            if gpu_offloaded:
                result["accelerator"] = "Metal" if _is_apple_silicon() else "GPU"
            else:
                result["accelerator"] = "CPU"
            result["device"] = result["accelerator"]

            gpu_info = data.get("gpu_info", [])
            if gpu_info:
                result["vram_total_mb"] = sum(g.get("total_memory", 0) for g in gpu_info) // (1024 * 1024)
                result["vram_free_mb"]  = sum(g.get("free_memory", 0)  for g in gpu_info) // (1024 * 1024)

            if _is_apple_silicon():
                result["memory_kind"] = "unified"
                if not result["vram_total_mb"]:
                    result["vram_total_mb"] = _total_system_memory_mb()
                if not result["vram_used_mb"] and result["resident_mb"]:
                    result["vram_used_mb"] = result["resident_mb"]
                if result["vram_total_mb"] and result["vram_used_mb"]:
                    result["vram_free_mb"] = max(0,
                        result["vram_total_mb"] - result["vram_used_mb"])
        except Exception as exc:
            logger.debug(f"get_ps_info: {exc}")
        return result

    # ── Title generation ────────────────────────────────────────────────
    def generate_title(
        self, model: str, user_text: str, reply_text: str = ""
    ) -> Tuple[str, bool]:
        convo = f"User: {user_text[:400]}"
        if reply_text:
            clean_reply = re.sub(
                r"<think>[\s\S]*?</think>", "", reply_text, flags=re.IGNORECASE
            ).strip()
            convo += f"\nAssistant: {clean_reply[:400]}"

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
            logger.warning("generate_title: cannot reach Ollama")
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
        """Stream a chat completion from Ollama.

        Yields SSE-formatted strings with events:
          first_token {ms}
          token       {token}
          done        {prompt_tokens, completion_tokens, tokens_per_sec, gen_ms, ...}
          error       {message}

        If a system prompt is provided it's prepended as a system message
        unless the caller already supplied one.
        """
        final_messages: List[dict] = []
        if system and not any(m.get("role") == "system" for m in messages):
            final_messages.append({"role": "system", "content": system})
        final_messages.extend(messages)

        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={"model": model, "messages": final_messages, "stream": True},
                stream=True,
                timeout=OLLAMA_TIMEOUT,
            )
            if resp.status_code == 404:
                yield _sse("error", {
                    "message": f"Model '{model}' not found. Run: ollama pull {model}",
                    "provider": "ollama",
                })
                return
            resp.raise_for_status()

            first_token_sent = False
            t_start = time.monotonic()
            in_thinking = False

            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                msg = data.get("message", {}) or {}
                think_tok = msg.get("thinking", "") or ""
                tok       = msg.get("content", "") or ""

                pieces: List[str] = []
                if think_tok:
                    if not in_thinking:
                        in_thinking = True
                        pieces.append("<think>")
                    pieces.append(think_tok)
                if tok:
                    if in_thinking:
                        in_thinking = False
                        pieces.append("</think>")
                    pieces.append(tok)

                for piece in pieces:
                    if not first_token_sent:
                        first_token_sent = True
                        yield _sse("first_token", {
                            "ms": round((time.monotonic() - t_start) * 1000)
                        })
                    yield _sse("token", {"token": piece})

                if data.get("done") and in_thinking:
                    yield _sse("token", {"token": "</think>"})
                    in_thinking = False

                if data.get("done"):
                    gen_ms = round((time.monotonic() - t_start) * 1000)
                    eval_dur = data.get("eval_duration", 0)
                    eval_count = data.get("eval_count", 0)
                    tps = (round(eval_count / (eval_dur / 1e9), 1)
                           if eval_dur else 0)
                    yield _sse("done", {
                        "prompt_tokens":     data.get("prompt_eval_count", 0),
                        "completion_tokens": eval_count,
                        "total_duration_ns": data.get("total_duration", 0),
                        "eval_duration_ns":  eval_dur,
                        "tokens_per_sec":    tps,
                        "gen_ms":            gen_ms,
                    })
                    return

            # Stream ended without an explicit done event.
            yield _sse("done", {})

        except requests.exceptions.ConnectionError:
            yield _sse("error", {
                "message": "Cannot reach Ollama — run: ollama serve",
                "provider": "ollama",
            })
        except requests.exceptions.Timeout:
            yield _sse("error", {
                "message": "Ollama timed out. Model may be overloaded.",
                "provider": "ollama",
            })
        except requests.exceptions.HTTPError as exc:
            yield _sse("error", {
                "message": f"Ollama HTTP {exc.response.status_code}",
                "provider": "ollama",
            })
        except Exception as exc:
            logger.error(traceback.format_exc())
            yield _sse("error", {
                "message": f"Unexpected error: {exc}",
                "provider": "ollama",
            })
