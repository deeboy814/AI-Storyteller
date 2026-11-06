"""Detect this computer's hardware (local, free) and turn it into model advice."""
import os
import platform
import shutil
import subprocess
from pathlib import Path

# Local text models by how much GPU memory you have (Ollama names; sizes are the usual 4-bit downloads, in GB).
# Notes come from tests run for this app on a 12 GB card; treat them as guidance, not guarantees.
LOCAL_PICKS = [
    (24, [("qwen3:32b", 20.0, "strong all-rounder"), ("mistral-small3.2:24b", 15.0, "very good prose; follows long prompts well")]),
    (16, [("mistral-small3.2:24b", 15.0, "best quality that fits; keep the context size modest"), ("qwen3:14b", 9.3, "faster; follows rules well")]),
    (12, [("qwen3:14b", 9.3, "best balance on 12 GB in our tests"), ("mistral-nemo", 7.1, "fastest, but weaker at long formatted prompts")]),
    (8, [("qwen3:8b", 5.2, "good for its size"), ("llama3.1:8b", 4.9, "solid and quick")]),
    (4, [("qwen3:4b", 2.6, "small but usable"), ("gemma3:4b", 3.3, "small and fast")]),
]
SDXL_VRAM_GB = 6   # minimum for local SDXL-class image models


def _run(cmd: list[str], timeout: int = 4) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def detect(data_dir: Path | str = ".") -> dict:
    gpus = []
    for line in _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"]).splitlines():
        try:
            name, total, used = [x.strip() for x in line.split(",")]
            gpus.append({"name": name, "vram_gb": round(int(total) / 1024, 1), "used_gb": round(int(used) / 1024, 1)})
        except ValueError:
            continue
    ram = 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram = round(int(line.split()[1]) / 1024 / 1024, 1)
                break
    except OSError:
        try:
            ram = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
        except (ValueError, OSError, AttributeError):
            ram = 0.0
    cpu = platform.processor() or ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    if apple and not gpus and ram:   # Apple Silicon shares memory between CPU and GPU
        gpus.append({"name": "Apple Silicon (shared memory)", "vram_gb": round(ram * 0.65, 1), "used_gb": 0})
    try:
        free = round(shutil.disk_usage(data_dir).free / 1024**3, 1)
    except OSError:
        free = 0.0
    return {"gpus": gpus, "vram_gb": max((g["vram_gb"] for g in gpus), default=0.0), "ram_gb": ram, "cpu": cpu,
            "threads": os.cpu_count() or 0, "disk_free_gb": free}


def describe(hw: dict) -> str:
    g = hw["gpus"][0] if hw["gpus"] else None
    parts = [f"{g['name']} ({g['vram_gb']:g} GB video memory)" if g else "no dedicated GPU detected (models would run on the CPU, which is slow)"]
    if hw["ram_gb"]:
        parts.append(f"{hw['ram_gb']:g} GB RAM")
    return " · ".join(parts)


def fit(size_gb: float, hw: dict) -> str:
    """How a local model of this download size will run here: fits / partial (spills into RAM, slower) / cpu / too_big."""
    vram, ram = hw["vram_gb"], hw["ram_gb"]
    if vram <= 0:
        return "cpu" if size_gb <= ram * 0.6 else "too_big"
    if size_gb + 1.5 <= vram:
        return "fits"
    if size_gb <= vram + ram * 0.5:
        return "partial"
    return "too_big"


def local_picks(hw: dict) -> list[tuple[str, float, str]]:
    for need, picks in LOCAL_PICKS:
        if hw["vram_gb"] >= need:
            return picks
    return [("qwen3:4b", 2.6, "small; on a CPU-only computer expect slow replies")]
