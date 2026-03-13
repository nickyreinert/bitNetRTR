import os
import platform
import re
import shutil
import subprocess
import time
import codecs
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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
    return cleaned


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


def stream_bitnet(prompt: str, options: RuntimeOptions) -> Iterable[str]:
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
    pending = ""
    generated_so_far = ""
    short_sent_len = 0
    stream_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    try:
        while True:
            raw_chunk = process.stdout.read(256)
            if not raw_chunk:
                break

            chunk = _clean_generation_chunk(stream_decoder.decode(raw_chunk, final=False))
            if not chunk:
                continue

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
                if sentence:
                    delta = sentence[short_sent_len:]
                    if delta:
                        yield _sse_event(delta)
                        short_sent_len = len(sentence)
                if re.search(r"[.!?](?:\s|$)", sentence or ""):
                    stopped_early = True
                    if process.poll() is None:
                        process.terminate()
                    break
            else:
                yield _sse_event(emit_text)

        tail_chunk = _clean_generation_chunk(stream_decoder.decode(b"", final=True))
        if tail_chunk:
            pending += tail_chunk

        if pending and generation_started and not short_prompt_mode:
            pending = _strip_runtime_noise_from_text(pending)
            if pending.strip():
                yield _sse_event(pending)

        return_code = process.wait()
        if return_code != 0 and not stopped_early:
            yield _sse_event(f"Inference process exited with code {return_code}", event="error")

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
async def runtime_stats(x_api_key: str | None = Header(default=None)) -> dict:
    _verify_api_key(x_api_key)
    if not STATS_ENABLED:
        raise HTTPException(status_code=404, detail="Stats are disabled")
    return {
        "timestamp": int(time.time()),
        "cpu": {
            "usage_percent": _read_cpu_percent(),
            "cores": os.cpu_count(),
        },
        "memory": _read_memory_stats(),
        "gpus": _read_gpu_stats(),
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
) -> StreamingResponse:
    _verify_api_key(x_api_key)
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
        stream_bitnet(prompt, options),
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
) -> StreamingResponse:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt cannot be empty")

    _verify_api_key(x_api_key)
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
        stream_bitnet(prompt, options),
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
