"""Counts the faces in a finished picture, so an adult picture with a doubled head can be redrawn with another seed.
Uses InsightFace's detector from ComfyUI's own Python (the app's Python does not have it); when that is not there, nothing is checked."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

_SCRIPT = r'''
import sys, json, cv2
from insightface.app import FaceAnalysis
a = FaceAnalysis(name="buffalo_l", root=sys.argv[2], allowed_modules=["detection"], providers=["CPUExecutionProvider"])
a.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.5)
im = cv2.imread(sys.argv[1])
print(json.dumps([round(float(f.det_score), 2) for f in a.get(im)] if im is not None else []))
'''


def _python_and_root():
    home = Path(os.environ.get("COMFYUI_DIR") or Path.home() / "ComfyUI")
    for rel in ("venv/bin/python", ".venv/bin/python", "venv/Scripts/python.exe", ".venv/Scripts/python.exe"):
        py = home / rel
        if py.exists():
            root = home / "models" / "insightface"
            return (str(py), str(root)) if (root / "models" / "buffalo_l").exists() else (None, None)
    return None, None


async def count_faces(data: bytes) -> "int | None":
    """How many faces the detector finds (None = could not check)."""
    py, root = _python_and_root()
    if not py:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(py, "-c", _SCRIPT, path, root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 60)
            line = [l for l in out.decode().splitlines() if l.startswith("[")]
            return len(json.loads(line[-1])) if line else None
        finally:
            os.unlink(path)
    except Exception:
        return None
