import os
import platform
import re
import shutil
import subprocess
import time
import codecs
import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    import yaml
except ImportError:  # pragma: no cover - optional import fallback
    yaml = None


app = FastAPI(title="BitNet SSE API", version="1.0.0")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
API_KEY_CREDENTIAL = os.getenv("API_KEY", "change-me")
CONFIG_PATH = Path(APP_ROOT) / "config.yaml"
DEFAULT_MODELS = [
    "models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf",
    "models/BitNet-b1.58-2B-4T/ggml-model-i2_m.gguf",
]
DEFAULT_N_PREDICT = 256
DEFAULT_THREADS = 2
DEFAULT_CTX_SIZE = 2048
DEFAULT_TEMPERATURE = 0.8
DEFAULT_MAX_N_PREDICT = 4096
STATS_DIR = Path(APP_ROOT) / ".bitnet-stats"
STATS_FILE = STATS_DIR / "stats.json"
STATS_RETENTION_DAYS = max(30, int(os.getenv("BITNET_STATS_RETENTION_DAYS", "400")))
STATS_SAMPLE_INTERVAL_SECONDS = max(30, int(os.getenv("BITNET_STATS_SAMPLE_INTERVAL_SECONDS", "60")))


def _load_runtime_config() -> dict:
    defaults = {
        "stats_enabled": True,
        "models": list(DEFAULT_MODELS),
        "limits": {
            "threads": 4,
            "context_size": 4096,
            "n_predict": DEFAULT_MAX_N_PREDICT,
            "temperature": DEFAULT_TEMPERATURE,
        },
        "defaults": {
            "model": DEFAULT_MODELS[0],
            "threads": DEFAULT_THREADS,
            "context_size": DEFAULT_CTX_SIZE,
            "n_predict": DEFAULT_N_PREDICT,
            "temperature": DEFAULT_TEMPERATURE,
        },
        "repo_dir": "third_party/BitNet",
        "conversation_mode": True,
    }
    if not CONFIG_PATH.exists() or yaml is None:
        return defaults

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return defaults

    global_cfg = raw.get("global", {}) if isinstance(raw, dict) else {}
    bitnet_cfg = global_cfg.get("bitnet", {}) if isinstance(global_cfg, dict) else {}
    limits_cfg = bitnet_cfg.get("limits", {}) if isinstance(bitnet_cfg, dict) else {}
    defaults_cfg = bitnet_cfg.get("defaults", {}) if isinstance(bitnet_cfg, dict) else {}

    models = bitnet_cfg.get("models")
    if not isinstance(models, list) or not models:
        models = defaults["models"]
    else:
        models = [str(item) for item in models if isinstance(item, str) and item.strip()]
        if not models:
            models = defaults["models"]

    def _int_or(value, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _float_or(value, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    # Support both new nested format and older flat keys.
    limit_threads = _int_or(limits_cfg.get("threads", bitnet_cfg.get("max_threads")), defaults["limits"]["threads"])
    limit_context = _int_or(
        limits_cfg.get("context_size", bitnet_cfg.get("max_context_size")),
        defaults["limits"]["context_size"],
    )
    limit_n_predict = _int_or(limits_cfg.get("n_predict"), defaults["limits"]["n_predict"])
    limit_temperature = _float_or(
        limits_cfg.get("temperature", bitnet_cfg.get("max_temp", defaults_cfg.get("temperature"))),
        defaults["limits"]["temperature"],
    )

    default_model = str(defaults_cfg.get("model", models[0]))
    if default_model not in models:
        default_model = models[0]

    default_threads = _int_or(defaults_cfg.get("threads"), defaults["defaults"]["threads"])
    default_context = _int_or(defaults_cfg.get("context_size"), defaults["defaults"]["context_size"])
    default_n_predict = _int_or(defaults_cfg.get("n_predict"), defaults["defaults"]["n_predict"])
    default_temperature = _float_or(defaults_cfg.get("temperature"), defaults["defaults"]["temperature"])

    # Clamp defaults so they are always valid under configured limits.
    limit_threads = max(1, limit_threads)
    limit_context = max(1, limit_context)
    limit_n_predict = max(1, limit_n_predict)
    limit_temperature = max(0.0, limit_temperature)
    default_threads = min(max(1, default_threads), limit_threads)
    default_context = min(max(1, default_context), limit_context)
    default_n_predict = min(max(1, default_n_predict), limit_n_predict)
    default_temperature = min(max(0.0, default_temperature), limit_temperature)

    return {
        "stats_enabled": bool(global_cfg.get("stats_enabled", defaults["stats_enabled"])),
        "models": models,
        "limits": {
            "threads": limit_threads,
            "context_size": limit_context,
            "n_predict": limit_n_predict,
            "temperature": limit_temperature,
        },
        "defaults": {
            "model": default_model,
            "threads": default_threads,
            "context_size": default_context,
            "n_predict": default_n_predict,
            "temperature": default_temperature,
        },
        "repo_dir": str(bitnet_cfg.get("repo_dir", defaults["repo_dir"])),
        "conversation_mode": bool(bitnet_cfg.get("conversation_mode", defaults["conversation_mode"])),
    }


RUNTIME_CONFIG = _load_runtime_config()
MODEL_OPTIONS = RUNTIME_CONFIG["models"]
MAX_THREADS = RUNTIME_CONFIG["limits"]["threads"]
MAX_CONTEXT_SIZE = RUNTIME_CONFIG["limits"]["context_size"]
MAX_N_PREDICT = RUNTIME_CONFIG["limits"]["n_predict"]
MAX_TEMP = RUNTIME_CONFIG["limits"]["temperature"]
STATS_ENABLED = RUNTIME_CONFIG["stats_enabled"]
BITNET_REPO_DIR = RUNTIME_CONFIG["repo_dir"]
BITNET_RUNTIME_DIR = os.getenv("BITNET_RUNTIME_DIR", "/opt/bitnet-runtime")
# Conversation mode stays fixed to true for now.
CONVERSATION = True

DEFAULT_MODEL = RUNTIME_CONFIG["defaults"]["model"]
DEFAULT_THREADS = RUNTIME_CONFIG["defaults"]["threads"]
DEFAULT_CTX_SIZE = RUNTIME_CONFIG["defaults"]["context_size"]
DEFAULT_N_PREDICT = RUNTIME_CONFIG["defaults"]["n_predict"]
DEFAULT_TEMPERATURE = RUNTIME_CONFIG["defaults"]["temperature"]

_cpu_sample_cache: tuple[int, int] | None = None

ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse_event(data: str, event: str | None = None) -> str:
    lines = data.splitlines() or [""]
    payload = "".join(f"data: {line}\n" for line in lines)
    if event:
        payload = f"event: {event}\n" + payload
    return payload + "\n"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _sanitize_identifier(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", value.strip())[:64].strip("-._")
    return cleaned or fallback


def _resolve_user_id(x_api_key: str, x_user_id: str | None) -> str:
    fallback = f"api-{hashlib.sha256(x_api_key.encode('utf-8')).hexdigest()[:16]}"
    return _sanitize_identifier(x_user_id, fallback)


def _resolve_session_id(x_session_id: str | None) -> str:
    return _sanitize_identifier(x_session_id, "default-session")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _numeric_summary(values: list[float | None]) -> dict[str, float | None]:
    filtered = [float(value) for value in values if value is not None]
    if not filtered:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": _round_metric(min(filtered)),
        "max": _round_metric(max(filtered)),
        "avg": _round_metric(sum(filtered) / len(filtered)),
    }


def _period_start(timestamp: int, period: str) -> datetime:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if period == "minute":
        return dt.replace(second=0, microsecond=0)
    if period == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = dt - timedelta(days=dt.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported period '{period}'")


def _period_label(start: datetime, period: str) -> str:
    if period == "minute":
        return start.strftime("%H:%M")
    if period == "day":
        return start.strftime("%Y-%m-%d")
    if period == "week":
        iso_year, iso_week, _ = start.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "month":
        return start.strftime("%Y-%m")
    raise ValueError(f"Unsupported period '{period}'")


def _usage_bucket(events: list[dict[str, Any]], label: str, start_ts: int) -> dict[str, Any]:
    session_ids = {event.get("session_id") for event in events if event.get("session_id")}
    prompt_tokens = sum(int(event.get("prompt_tokens") or 0) for event in events)
    completion_tokens = sum(int(event.get("completion_tokens") or 0) for event in events)
    total_tokens = sum(int(event.get("total_tokens") or 0) for event in events)
    return {
        "label": label,
        "start_ts": start_ts,
        "messages": len(events),
        "sessions": len(session_ids),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "total_time_ms": _numeric_summary([_safe_float(event.get("total_time_ms")) for event in events]),
        "eval_tokens_per_second": _numeric_summary([_safe_float(event.get("eval_tokens_per_second")) for event in events]),
        "prompt_tokens_per_second": _numeric_summary([_safe_float(event.get("prompt_tokens_per_second")) for event in events]),
    }


def _runtime_bucket(samples: list[dict[str, Any]], label: str, start_ts: int) -> dict[str, Any]:
    return {
        "label": label,
        "start_ts": start_ts,
        "samples": len(samples),
        "cpu_usage_percent": _numeric_summary([_safe_float(sample.get("cpu_usage_percent")) for sample in samples]),
        "memory_used_percent": _numeric_summary([_safe_float(sample.get("memory_used_percent")) for sample in samples]),
        "gpu_utilization_percent": _numeric_summary([_safe_float(sample.get("gpu_utilization_percent")) for sample in samples]),
    }


def _group_by_period(records: list[dict[str, Any]], period: str, builder) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        timestamp = _safe_int(record.get("timestamp"))
        if timestamp is None:
            continue
        start = _period_start(timestamp, period)
        start_ts = int(start.timestamp())
        buckets.setdefault(start_ts, []).append(record)
    grouped: list[dict[str, Any]] = []
    for start_ts in sorted(buckets.keys(), reverse=True):
        start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        grouped.append(builder(buckets[start_ts], _period_label(start, period), start_ts))
    return grouped


class StatsStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _default_data(self) -> dict[str, Any]:
        return {"runtime_samples": [], "user_events": {}}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_data()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_data()
        if not isinstance(loaded, dict):
            return self._default_data()
        loaded.setdefault("runtime_samples", [])
        loaded.setdefault("user_events", {})
        return loaded

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.data, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        temp_path.replace(self.path)

    def _prune_locked(self, now_ts: int) -> None:
        cutoff = now_ts - (STATS_RETENTION_DAYS * 24 * 60 * 60)
        self.data["runtime_samples"] = [
            sample for sample in self.data.get("runtime_samples", [])
            if (_safe_int(sample.get("timestamp")) or 0) >= cutoff
        ]
        pruned_events: dict[str, list[dict[str, Any]]] = {}
        for user_id, events in self.data.get("user_events", {}).items():
            filtered = [event for event in events if (_safe_int(event.get("timestamp")) or 0) >= cutoff]
            if filtered:
                pruned_events[user_id] = filtered
        self.data["user_events"] = pruned_events

    def record_runtime_sample(self, sample: dict[str, Any]) -> None:
        now_ts = _safe_int(sample.get("timestamp")) or int(time.time())
        with self.lock:
            samples = self.data.setdefault("runtime_samples", [])
            if samples:
                last_ts = _safe_int(samples[-1].get("timestamp")) or 0
                if now_ts - last_ts < STATS_SAMPLE_INTERVAL_SECONDS:
                    return
            samples.append(sample)
            self._prune_locked(now_ts)
            self._save_locked()

    def record_chat_event(self, user_id: str, event: dict[str, Any]) -> None:
        now_ts = _safe_int(event.get("timestamp")) or int(time.time())
        with self.lock:
            user_events = self.data.setdefault("user_events", {}).setdefault(user_id, [])
            user_events.append(event)
            self._prune_locked(now_ts)
            self._save_locked()

    def build_runtime_history(self, now_ts: int) -> dict[str, Any]:
        with self.lock:
            samples = list(self.data.get("runtime_samples", []))
        last_hour_cutoff = now_ts - 3600
        last_hour = [sample for sample in samples if (_safe_int(sample.get("timestamp")) or 0) >= last_hour_cutoff]
        older = [sample for sample in samples if (_safe_int(sample.get("timestamp")) or 0) < last_hour_cutoff]
        return {
            "last_hour": sorted(last_hour, key=lambda sample: _safe_int(sample.get("timestamp")) or 0, reverse=True),
            "daily": _group_by_period(older, "day", _runtime_bucket)[:30],
            "weekly": _group_by_period(older, "week", _runtime_bucket)[:16],
            "monthly": _group_by_period(older, "month", _runtime_bucket)[:12],
        }

    def build_user_usage(self, user_id: str, now_ts: int) -> dict[str, Any]:
        with self.lock:
            events = list(self.data.get("user_events", {}).get(user_id, []))
        events.sort(key=lambda event: _safe_int(event.get("timestamp")) or 0, reverse=True)
        session_ids = {event.get("session_id") for event in events if event.get("session_id")}
        totals = {
            "sessions": len(session_ids),
            "messages": len(events),
            "prompt_tokens": sum(int(event.get("prompt_tokens") or 0) for event in events),
            "completion_tokens": sum(int(event.get("completion_tokens") or 0) for event in events),
            "total_tokens": sum(int(event.get("total_tokens") or 0) for event in events),
        }
        last_hour_cutoff = now_ts - 3600
        last_hour_events = [event for event in events if (_safe_int(event.get("timestamp")) or 0) >= last_hour_cutoff]
        older_events = [event for event in events if (_safe_int(event.get("timestamp")) or 0) < last_hour_cutoff]
        latest_chat = events[0] if events else None
        return {
            "user_id": user_id,
            "totals": totals,
            "latest_chat": latest_chat,
            "recent_messages": events[:10],
            "last_hour": _group_by_period(last_hour_events, "minute", _usage_bucket)[:60],
            "daily": _group_by_period(older_events, "day", _usage_bucket)[:30],
            "weekly": _group_by_period(older_events, "week", _usage_bucket)[:16],
            "monthly": _group_by_period(older_events, "month", _usage_bucket)[:12],
        }


def _runtime_history_sample() -> dict[str, Any]:
    memory = _read_memory_stats()
    gpus = _read_gpu_stats()
    gpu_utils = [gpu.get("utilization_percent") for gpu in gpus if gpu.get("utilization_percent") is not None]
    return {
        "timestamp": int(time.time()),
        "cpu_usage_percent": _read_cpu_percent(),
        "memory_used_percent": memory.get("used_percent") if memory else None,
        "gpu_utilization_percent": _round_metric(sum(gpu_utils) / len(gpu_utils)) if gpu_utils else None,
    }


STATS_STORE = StatsStore(STATS_FILE)


def _start_runtime_sampler() -> None:
    def _sampler() -> None:
        while True:
            try:
                STATS_STORE.record_runtime_sample(_runtime_history_sample())
            except Exception:
                pass
            time.sleep(STATS_SAMPLE_INTERVAL_SECONDS)

    threading.Thread(target=_sampler, name="bitnet-runtime-stats", daemon=True).start()


def _llama_cli_path(bitnet_repo_abs: str) -> str:
    if platform.system() == "Windows":
        release_path = os.path.join(bitnet_repo_abs, "build", "bin", "Release", "llama-cli.exe")
        if os.path.exists(release_path):
            return release_path
        return os.path.join(bitnet_repo_abs, "build", "bin", "llama-cli.exe")
    return os.path.join(bitnet_repo_abs, "build", "bin", "llama-cli")


def _resolve_bitnet_repo() -> tuple[str, str | None]:
    source_repo_abs = os.path.abspath(os.path.join(APP_ROOT, BITNET_REPO_DIR))
    runtime_repo_abs = os.path.abspath(BITNET_RUNTIME_DIR)

    if os.path.exists(_llama_cli_path(source_repo_abs)):
        return source_repo_abs, None

    if os.path.exists(_llama_cli_path(runtime_repo_abs)):
        return runtime_repo_abs, None

    return (
        source_repo_abs,
        "Missing inference binary: build/bin/llama-cli. "
        "Use the wrapper orchestrator to build dependencies inside the container first "
        "(bitNetRTR menu: Start stack / Rebuild + start). "
        "Optional override: provide a prebuilt runtime at BITNET_RUNTIME_DIR "
        f"({BITNET_RUNTIME_DIR}) with run_inference.py and build/bin/llama-cli.",
    )



@dataclass(frozen=True)
class RuntimeOptions:
    model: str
    n_predict: int
    threads: int
    ctx_size: int
    temperature: float


def _build_command(prompt: str, options: RuntimeOptions) -> list[str]:
    bitnet_repo_abs, repo_err = _resolve_bitnet_repo()
    if repo_err:
        raise RuntimeError(repo_err)
    inference_script = os.path.join(bitnet_repo_abs, "run_inference.py")
    model_path_arg = options.model if os.path.isabs(options.model) else os.path.abspath(os.path.join(APP_ROOT, options.model))
    formatted_prompt = _format_prompt(prompt)
    n_predict = _effective_n_predict(prompt, options.n_predict)
    cmd = [
        "python3",
        inference_script,
        "-m",
        model_path_arg,
        "-p",
        formatted_prompt,
        "-n",
        str(n_predict),
        "-t",
        str(options.threads),
        "-c",
        str(options.ctx_size),
        "-temp",
        str(options.temperature),
    ]
    # Do not enable llama-cli conversation mode for API calls.
    # In this context it can switch to interactive behavior and wait on stdin.
    if shutil.which("stdbuf"):
        cmd = ["stdbuf", "-o0", "-e0", *cmd]
    return cmd


def _effective_n_predict(prompt: str, n_predict: int) -> int:
    if len(prompt.strip()) <= 12:
        return min(n_predict, 64)
    return n_predict


def _is_short_prompt(prompt: str) -> bool:
    return len(prompt.strip()) <= 12


def _first_sentence(text: str) -> str:
    match = re.search(r"^(.+?[.!?])(?:\s|$)", text.strip())
    if match:
        return match.group(1).strip()
    return text.strip()


def _format_prompt(prompt: str) -> str:
    user_prompt = prompt.strip()
    return (
        "You are a helpful assistant. Reply directly and concisely.\\n"
        f"User: {user_prompt}\\n"
        "Assistant:"
    )


def _is_runtime_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True

    noise_prefixes = (
        "warning:",
        "build:",
        "main:",
        "llama_",
        "llm_",
        "common_",
        "system_info:",
        "sampler",
        "repeat_last_n",
        "top_k",
        "mirostat",
        "generate:",
        "CPU :",
        "check_double_bos_eos:",
    )
    if stripped.startswith(noise_prefixes):
        return True

    # Startup progress bars and separators.
    if set(stripped) <= {".", "*", "-", " ", "\t"}:
        return True

    return False


def _clean_generation_line(line: str) -> str:
    cleaned = line
    for marker in ("<|im_sep|>", "<|im_start|>", "<|im_end|>"):
        cleaned = cleaned.replace(marker, "")
    cleaned = cleaned.strip()

    # Drop common prompt-echo lines from the injected template.
    lowered = cleaned.lower()
    if lowered.startswith("you are a helpful assistant"):
        return ""
    if lowered.startswith("user:"):
        return ""

    while cleaned.lstrip().lower().startswith("response:"):
        cleaned = cleaned.split(":", 1)[1].strip() if ":" in cleaned else ""
    if cleaned.lower().startswith("assistant:"):
        cleaned = cleaned.split(":", 1)[1].strip() if ":" in cleaned else ""
    if "User:" in cleaned:
        cleaned = cleaned.split("User:", 1)[0].rstrip()
    # Collapse pathological repeated punctuation / emoji runs.
    cleaned = re.sub(r"([^\w\s])\1{5,}", r"\1\1\1", cleaned)
    if cleaned.strip().lower() == "[end of text]":
        return ""
    return cleaned.strip()


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_INLINE_RUNTIME_NOISE_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"llama_perf_[^\n]*|"
    r"llm_perf_[^\n]*|"
    r"sampling time\s*=\s*[^\n]*|"
    r"prompt eval time\s*=\s*[^\n]*|"
    r"eval time\s*=\s*[^\n]*|"
    r"total time\s*=\s*[^\n]*"
    r")",
    flags=re.IGNORECASE,
)
_INLINE_PERF_FRAGMENT_RE = re.compile(
    r"(?:"
    r"llama_perf_[^\n]*|"
    r"llm_perf_[^\n]*|"
    r"(?:load|sampling|prompt eval|eval|total)\s+time\s*=\s*[^\n]*|"
    r"\b\d+\s+(?:runs|tokens)\s*\(\s*[\d.]+\s*ms per token,\s*[\d.]+\s*tokens per second\s*\)|"
    r"\b[\d.]+\s*ms per token,\s*[\d.]+\s*tokens per second\b"
    r")",
    flags=re.IGNORECASE,
)


def _clean_generation_chunk(chunk: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", chunk)
    for marker in ("<|im_sep|>", "<|im_start|>", "<|im_end|>"):
        cleaned = cleaned.replace(marker, "")
    return cleaned.replace("\r", "")


def _strip_runtime_noise_from_text(text: str) -> str:
    # Runtime diagnostics (e.g. llama_perf_context_print) can be interleaved
    # in streamed output and should never be shown as assistant content.
    cleaned = _INLINE_RUNTIME_NOISE_RE.sub("", text)
    cleaned = cleaned.replace(")llama_perf_", ")\nllama_perf_")
    cleaned = _INLINE_RUNTIME_NOISE_RE.sub("", cleaned)
    cleaned = _INLINE_PERF_FRAGMENT_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned)
    if cleaned.strip().lower() == "tokens":
        return ""
    return cleaned.strip("\n\r")


def _extract_perf_metrics(raw_text: str) -> dict[str, float | int | None]:
    metrics: dict[str, float | int | None] = {
        "sampling_time_ms": None,
        "sampling_runs": None,
        "sampling_ms_per_token": None,
        "sampling_tokens_per_second": None,
        "load_time_ms": None,
        "prompt_eval_time_ms": None,
        "prompt_tokens": None,
        "prompt_ms_per_token": None,
        "prompt_tokens_per_second": None,
        "eval_time_ms": None,
        "completion_tokens": None,
        "eval_ms_per_token": None,
        "eval_tokens_per_second": None,
        "total_time_ms": None,
        "total_tokens": None,
    }
    patterns = {
        "sampling": re.compile(
            r"sampling time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*runs\s*\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\s*\)",
            flags=re.IGNORECASE,
        ),
        "load": re.compile(r"load time\s*=\s*([\d.]+)\s*ms", flags=re.IGNORECASE),
        "prompt": re.compile(
            r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\s*\)",
            flags=re.IGNORECASE,
        ),
        "eval": re.compile(
            r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*(?:runs|tokens)\s*\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\s*\)",
            flags=re.IGNORECASE,
        ),
        "total": re.compile(r"total time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens", flags=re.IGNORECASE),
    }
    if match := patterns["sampling"].search(raw_text):
        metrics["sampling_time_ms"] = _round_metric(float(match.group(1)))
        metrics["sampling_runs"] = int(match.group(2))
        metrics["sampling_ms_per_token"] = _round_metric(float(match.group(3)))
        metrics["sampling_tokens_per_second"] = _round_metric(float(match.group(4)))
    if match := patterns["load"].search(raw_text):
        metrics["load_time_ms"] = _round_metric(float(match.group(1)))
    if match := patterns["prompt"].search(raw_text):
        metrics["prompt_eval_time_ms"] = _round_metric(float(match.group(1)))
        metrics["prompt_tokens"] = int(match.group(2))
        metrics["prompt_ms_per_token"] = _round_metric(float(match.group(3)))
        metrics["prompt_tokens_per_second"] = _round_metric(float(match.group(4)))
    if match := patterns["eval"].search(raw_text):
        metrics["eval_time_ms"] = _round_metric(float(match.group(1)))
        metrics["completion_tokens"] = int(match.group(2))
        metrics["eval_ms_per_token"] = _round_metric(float(match.group(3)))
        metrics["eval_tokens_per_second"] = _round_metric(float(match.group(4)))
    if match := patterns["total"].search(raw_text):
        metrics["total_time_ms"] = _round_metric(float(match.group(1)))
        metrics["total_tokens"] = int(match.group(2))
    if metrics["total_tokens"] is None:
        prompt_tokens = int(metrics["prompt_tokens"] or 0)
        completion_tokens = int(metrics["completion_tokens"] or 0)
        if prompt_tokens or completion_tokens:
            metrics["total_tokens"] = prompt_tokens + completion_tokens
    return metrics


def _verify_api_key(x_api_key: str | None) -> None:
    if not API_KEY_CREDENTIAL or API_KEY_CREDENTIAL == "change-me":
        raise HTTPException(status_code=500, detail="API key is not configured")

    if x_api_key != API_KEY_CREDENTIAL:
        raise HTTPException(status_code=403, detail="Unauthorized")


def _resolve_runtime_options(payload: dict | None = None) -> RuntimeOptions:
    payload = payload or {}
    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    if model not in MODEL_OPTIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported model '{model}'")

    n_predict = int(payload.get("n_predict") or DEFAULT_N_PREDICT)
    if n_predict < 1 or n_predict > MAX_N_PREDICT:
        raise HTTPException(status_code=422, detail=f"n_predict must be between 1 and {MAX_N_PREDICT}")

    threads = int(payload.get("threads") or DEFAULT_THREADS)
    if threads < 1 or threads > MAX_THREADS:
        raise HTTPException(status_code=422, detail=f"threads must be between 1 and {MAX_THREADS}")

    ctx_size = int(payload.get("ctx_size") or DEFAULT_CTX_SIZE)
    if ctx_size < 1 or ctx_size > MAX_CONTEXT_SIZE:
        raise HTTPException(status_code=422, detail=f"ctx_size must be between 1 and {MAX_CONTEXT_SIZE}")

    temperature = float(payload.get("temperature") if payload.get("temperature") is not None else DEFAULT_TEMPERATURE)
    if temperature < 0 or temperature > MAX_TEMP:
        raise HTTPException(status_code=422, detail=f"temperature must be between 0 and {MAX_TEMP}")

    return RuntimeOptions(
        model=model,
        n_predict=n_predict,
        threads=threads,
        ctx_size=ctx_size,
        temperature=temperature,
    )


def _read_cpu_percent() -> float | None:
    global _cpu_sample_cache
    try:
        cpu_line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        parts = cpu_line.split()
        values = [int(value) for value in parts[1:]]
        idle = values[3] + values[4]
        total = sum(values)
    except Exception:
        return None

    if _cpu_sample_cache is None:
        _cpu_sample_cache = (total, idle)
        return None

    prev_total, prev_idle = _cpu_sample_cache
    _cpu_sample_cache = (total, idle)
    total_diff = total - prev_total
    idle_diff = idle - prev_idle
    if total_diff <= 0:
        return None
    return round(100.0 * (1 - (idle_diff / total_diff)), 2)


def _read_memory_stats() -> dict | None:
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except Exception:
        return None

    values: dict[str, int] = {}
    for line in meminfo:
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw_num = raw.strip().split(" ")[0]
        if raw_num.isdigit():
            values[key] = int(raw_num) * 1024

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None

    used = total - available
    return {
        "total_bytes": total,
        "used_bytes": used,
        "used_percent": round((used / total) * 100.0, 2),
    }


def _read_gpu_stats() -> list[dict]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=2)
    except Exception:
        return []
    if result.returncode != 0:
        return []

    rows: list[dict] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            rows.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "utilization_percent": float(parts[2]),
                    "memory_used_mb": float(parts[3]),
                    "memory_total_mb": float(parts[4]),
                    "temperature_c": float(parts[5]),
                }
            )
        except ValueError:
            continue
    return rows


    _start_runtime_sampler()


def stream_bitnet(prompt: str, options: RuntimeOptions, user_id: str, session_id: str) -> Iterable[str]:
    try:
        cmd = _build_command(prompt, options)
    except RuntimeError as exc:
        yield _sse_event(str(exc), event="error")
        yield _sse_event("stream-end", event="done")
        return
    short_prompt_mode = _is_short_prompt(prompt)

    try:
        bitnet_repo_abs = os.path.abspath(os.path.join(APP_ROOT, BITNET_REPO_DIR))
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            cwd=bitnet_repo_abs,
        )
    except OSError as exc:
        yield _sse_event(f"Failed to start inference process: {exc}", event="error")
        return

    yield _sse_event("stream-start", event="status")

    if process.stdout is None:
        yield _sse_event("No stdout stream available from inference process", event="error")
        return

    generation_started = False
    stopped_early = False
    completed_normally = False
    pending = ""
    generated_so_far = ""
    short_sent_len = 0
    short_prompt_complete = False
    stream_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    raw_output_parts: list[str] = []
    assistant_text_parts: list[str] = []
    started_at = time.time()
    return_code: int | None = None

    try:
        while True:
            raw_chunk = process.stdout.read(256)
            if not raw_chunk:
                break

            chunk = _clean_generation_chunk(stream_decoder.decode(raw_chunk, final=False))
            if not chunk:
                continue
            raw_output_parts.append(chunk)

            pending += chunk

            if not generation_started:
                if "Assistant:" in pending:
                    generation_started = True
                    pending = pending.split("Assistant:", 1)[1]
                else:
                    while "\n" in pending and not generation_started:
                        line, pending = pending.split("\n", 1)
                        if _is_runtime_noise_line(line):
                            continue
                        cleaned_line = _clean_generation_line(line)
                        if cleaned_line:
                            generation_started = True
                            pending = f"{cleaned_line}\n{pending}"
                    if not generation_started:
                        continue

            emit_text = pending
            pending = ""
            if not emit_text:
                continue

            # Remove any delayed prompt echo fragments that appear before generation.
            emit_text = emit_text.replace("You are a helpful assistant.", "")
            emit_text = emit_text.replace("User:", "")
            emit_text = emit_text.replace("Assistant:", "")
            emit_text = _strip_runtime_noise_from_text(emit_text)
            if not emit_text.strip():
                continue

            if short_prompt_mode:
                generated_so_far += emit_text
                sentence = _first_sentence(generated_so_far)
                if sentence and not short_prompt_complete:
                    delta = sentence[short_sent_len:]
                    if delta:
                        assistant_text_parts.append(delta)
                        yield _sse_event(delta)
                        short_sent_len = len(sentence)
                if re.search(r"[.!?](?:\s|$)", sentence or ""):
                    short_prompt_complete = True
            else:
                assistant_text_parts.append(emit_text)
                yield _sse_event(emit_text)

        tail_chunk = _clean_generation_chunk(stream_decoder.decode(b"", final=True))
        if tail_chunk:
            pending += tail_chunk

        if pending and generation_started and not short_prompt_mode:
            pending = _strip_runtime_noise_from_text(pending)
            if pending.strip():
                assistant_text_parts.append(pending)
                yield _sse_event(pending)

        return_code = process.wait()
        if return_code != 0 and not stopped_early:
            yield _sse_event(f"Inference process exited with code {return_code}", event="error")

        completed_normally = True
        yield _sse_event("stream-end", event="done")
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass

        # If client aborts the stream, make sure inference does not keep running.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

        if return_code is None:
            polled_return_code = process.poll()
            return_code = polled_return_code if polled_return_code is not None else -1

        raw_output = "".join(raw_output_parts)
        assistant_text = "".join(assistant_text_parts).strip()
        perf_metrics = _extract_perf_metrics(raw_output)
        if assistant_text or raw_output:
            STATS_STORE.record_chat_event(
                user_id,
                {
                    "timestamp": int(time.time()),
                    "session_id": session_id,
                    "model": options.model,
                    "prompt_chars": len(prompt),
                    "response_chars": len(assistant_text),
                    "status": "completed" if completed_normally else "interrupted",
                    "return_code": return_code,
                    "duration_ms": _round_metric((time.time() - started_at) * 1000.0),
                    **perf_metrics,
                },
            )


class ChatRequest(BaseModel):
    prompt: str
    model: str | None = None
    n_predict: int | None = Field(default=None, alias="n_predict")
    threads: int | None = None
    ctx_size: int | None = Field(default=None, alias="ctx_size")
    temperature: float | None = None


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/frontend-config")
async def frontend_config(x_api_key: str | None = Header(default=None)) -> dict:
    _verify_api_key(x_api_key)
    return {
        "stats_enabled": STATS_ENABLED,
        "bitnet": {
            "models": MODEL_OPTIONS,
            "defaults": {
                "model": DEFAULT_MODEL,
                "n_predict": DEFAULT_N_PREDICT,
                "threads": DEFAULT_THREADS,
                "ctx_size": DEFAULT_CTX_SIZE,
                "temperature": DEFAULT_TEMPERATURE,
            },
            "limits": {
                "max_threads": MAX_THREADS,
                "max_context_size": MAX_CONTEXT_SIZE,
                "max_temp": MAX_TEMP,
                "max_n_predict": MAX_N_PREDICT,
            },
            "conversation_mode": CONVERSATION,
        },
    }


@app.get("/stats")
async def runtime_stats(
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> dict:
    _verify_api_key(x_api_key)
    if not STATS_ENABLED:
        raise HTTPException(status_code=404, detail="Stats are disabled")
    assert x_api_key is not None
    user_id = _resolve_user_id(x_api_key, x_user_id)
    now_ts = int(time.time())
    STATS_STORE.record_runtime_sample(_runtime_history_sample())
    current_memory = _read_memory_stats()
    current_gpus = _read_gpu_stats()
    return {
        "timestamp": now_ts,
        "runtime": {
            "cpu": {
                "usage_percent": _read_cpu_percent(),
                "cores": os.cpu_count(),
            },
            "memory": current_memory,
            "gpus": current_gpus,
        },
        "runtime_history": STATS_STORE.build_runtime_history(now_ts),
        "usage": STATS_STORE.build_user_usage(user_id, now_ts),
    }


@app.get("/chat")
async def chat(
    prompt: str = Query(..., min_length=1),
    model: str | None = Query(default=None),
    n_predict: int | None = Query(default=None),
    threads: int | None = Query(default=None),
    ctx_size: int | None = Query(default=None),
    temperature: float | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse:
    _verify_api_key(x_api_key)
    assert x_api_key is not None
    options = _resolve_runtime_options(
        {
            "model": model,
            "n_predict": n_predict,
            "threads": threads,
            "ctx_size": ctx_size,
            "temperature": temperature,
        }
    )

    return StreamingResponse(
        stream_bitnet(prompt, options, _resolve_user_id(x_api_key, x_user_id), _resolve_session_id(x_session_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat")
async def chat_post(
    payload: ChatRequest,
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt cannot be empty")

    _verify_api_key(x_api_key)
    assert x_api_key is not None
    options = _resolve_runtime_options(
        {
            "model": payload.model,
            "n_predict": payload.n_predict,
            "threads": payload.threads,
            "ctx_size": payload.ctx_size,
            "temperature": payload.temperature,
        }
    )

    return StreamingResponse(
        stream_bitnet(prompt, options, _resolve_user_id(x_api_key, x_user_id), _resolve_session_id(x_session_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
