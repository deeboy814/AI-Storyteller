"""Neural read-aloud (optional): Piper voices that run on this computer. Nothing is installed until the player asks for it in the Read-aloud voices window:
the engine (the piper-tts package, into the app's own Python folder) and each voice (one model file from the Piper voices collection, into data/voices).
Speech is made locally; the text never leaves the computer."""
import asyncio
import importlib
import io
import re
import sys
import wave

from . import store

VOICE_DIR = store.DATA / "voices"
NAME_OK = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$")
# A short list to pick from; any other Piper voice name can be typed in.
CATALOG = [
    {"id": "en_US-lessac-medium", "name": "Lessac (US, female, clear)", "mb": 63},
    {"id": "en_US-ryan-medium", "name": "Ryan (US, male)", "mb": 63},
    {"id": "en_US-amy-medium", "name": "Amy (US, female, warm)", "mb": 63},
    {"id": "en_US-joe-medium", "name": "Joe (US, male, deep)", "mb": 63},
    {"id": "en_US-kristin-medium", "name": "Kristin (US, female)", "mb": 63},
    {"id": "en_GB-alan-medium", "name": "Alan (British, male)", "mb": 63},
    {"id": "en_GB-jenny_dioco-medium", "name": "Jenny (British, female)", "mb": 63},
    {"id": "en_GB-northern_english_male-medium", "name": "Northern English (British, male)", "mb": 63},
]
_loaded: dict = {}
_lock = asyncio.Lock()
_busy = {"what": ""}


def engine_ready() -> bool:
    try:
        importlib.import_module("piper")
        return True
    except Exception:
        return False


def installed() -> list[str]:
    if not VOICE_DIR.exists():
        return []
    return sorted(p.stem for p in VOICE_DIR.glob("*.onnx") if (VOICE_DIR / (p.name + ".json")).exists() and p.stat().st_size > 1_000_000)


def status() -> dict:
    have = installed()
    return {"engine": engine_ready(), "voices": have, "busy": _busy["what"],
            "catalog": [{**c, "installed": c["id"] in have} for c in CATALOG]}


async def install_engine() -> None:
    """pip install piper-tts into the Python that runs the app."""
    if engine_ready():
        return
    _busy["what"] = "Installing the voice engine…"
    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "piper-tts",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        importlib.invalidate_caches()
        if proc.returncode != 0 or not engine_ready():
            raise RuntimeError("Installing the voice engine failed: " + out.decode("utf-8", "replace")[-400:])
    finally:
        _busy["what"] = ""


async def download(name: str) -> None:
    if not NAME_OK.match(name):
        raise ValueError("That is not a Piper voice name. They look like en_US-lessac-medium.")
    if not engine_ready():
        raise RuntimeError("Install the voice engine first.")
    from piper.download_voices import download_voice
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    _busy["what"] = f"Downloading {name}…"
    try:
        await asyncio.to_thread(download_voice, name, VOICE_DIR)
    except Exception as e:
        for p in VOICE_DIR.glob(name + ".onnx*"):      # no half-downloaded files left behind
            p.unlink(missing_ok=True)
        raise RuntimeError(f"Could not download {name}: {e}")
    finally:
        _busy["what"] = ""
    if name not in installed():
        raise RuntimeError(f"Could not download {name}: the voice was not found in the Piper collection.")


def remove(name: str) -> None:
    if not NAME_OK.match(name):
        raise ValueError("Unknown voice")
    _loaded.pop(name, None)
    for p in VOICE_DIR.glob(name + ".onnx*"):
        p.unlink(missing_ok=True)


def _synth(name: str, text: str, speed: float) -> bytes:
    from piper import PiperVoice, SynthesisConfig
    v = _loaded.get(name)
    if v is None:
        if len(_loaded) >= 4:                  # each loaded voice holds memory; keep the few in use
            _loaded.pop(next(iter(_loaded)))
        v = _loaded[name] = PiperVoice.load(VOICE_DIR / (name + ".onnx"))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        v.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed))))
    return buf.getvalue()


async def speak(name: str, text: str, speed: float = 1.0) -> bytes:
    if name not in installed():
        raise ValueError("That voice is not installed.")
    async with _lock:              # one piece at a time: speech is CPU work
        return await asyncio.to_thread(_synth, name, text[:1200], speed)
