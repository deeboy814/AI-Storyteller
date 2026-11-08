"""Whole-app backup: everything the app keeps in its data folder (stories, pictures, avatars, usage log, settings) in one zip.

API keys are left out unless asked for. Restoring puts the files back (a safety copy of the current data is made first) and keeps
the API keys you already have saved. Automatic backups can be switched on in Settings -> Backup."""
import json
import re
import time
import zipfile
from pathlib import Path

from . import store

FORMAT = "storyteller-backup-1"
DIRS = ("stories", "portraits", "images")
FILES = ("avatars.json", "usage.jsonl", "config.json")
NO_COMPRESS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED = re.compile(r"^(stories|portraits|images)/.+|^(avatars\.json|usage\.jsonl|config\.json|backup-info\.json)$")


def default_dir() -> Path:
    return store.DATA / "backups" / "full"


def backup_dir(cfg: dict) -> Path:
    return Path(cfg.get("backup_dir") or default_dir()).expanduser()


def _config_text(keys: bool) -> str:
    cfg = json.loads(store.CONFIG.read_text(encoding="utf-8")) if store.CONFIG.exists() else store.load_config()
    if not keys:
        for p in cfg.get("providers", []) + cfg.get("image_providers", []):
            p["api_key"] = ""
    return json.dumps(cfg, indent=2, ensure_ascii=False)


def make_zip(dest: Path, keys: bool = False) -> dict:
    """Write the backup zip to dest. Returns {"path", "bytes", "files"}."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    n = 0
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("backup-info.json", json.dumps({"format": FORMAT, "created": time.time(), "keys_included": keys}), zipfile.ZIP_DEFLATED)
        for d in DIRS:
            root = store.DATA / d
            if not root.exists():
                continue
            for f in sorted(root.rglob("*")):
                if f.is_file() and not store.is_scratch(f.relative_to(root)):
                    z.write(f, f.relative_to(store.DATA).as_posix(), zipfile.ZIP_STORED if f.suffix.lower() in NO_COMPRESS else zipfile.ZIP_DEFLATED)
                    n += 1
        for name in FILES:
            f = store.DATA / name
            if name == "config.json":
                z.writestr(name, _config_text(keys), zipfile.ZIP_DEFLATED)
                n += 1
            elif f.exists():
                z.write(f, name, zipfile.ZIP_DEFLATED)
                n += 1
    tmp.replace(dest)
    return {"path": str(dest), "bytes": dest.stat().st_size, "files": n}


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def list_backups(cfg: dict) -> list[dict]:
    d = backup_dir(cfg)
    out = []
    for f in sorted(d.glob("storyteller-backup-*.zip"), reverse=True) if d.exists() else []:
        out.append({"name": f.name, "bytes": f.stat().st_size, "when": f.stat().st_mtime})
    return out


def run_auto(cfg: dict, force: bool = False) -> dict | None:
    """Make an automatic backup if one is due (or force), then trim old ones. Returns the info, or None if nothing was due."""
    days = float(cfg.get("backup_auto_days") or 0)
    have = list_backups(cfg)
    if not force and (days <= 0 or (have and time.time() - have[0]["when"] < days * 86400)):
        return None
    info = make_zip(backup_dir(cfg) / f"storyteller-backup-{stamp()}.zip", keys=False)
    keep = max(1, int(cfg.get("backup_keep") or 5))
    for old in list_backups(cfg)[keep:]:
        (backup_dir(cfg) / old["name"]).unlink(missing_ok=True)
    return info


def restore(zip_path: Path, current_cfg: dict) -> dict:
    """Put a backup back. Existing files with the same names are replaced; nothing else is deleted. API keys already saved are kept."""
    counts = {"files": 0, "stories": 0}
    with zipfile.ZipFile(zip_path) as z:
        try:
            info = json.loads(z.read("backup-info.json"))
        except Exception:
            raise ValueError("That is not a backup made by this app.")
        if info.get("format") != FORMAT:
            raise ValueError("Unknown backup version.")
        for item in z.infolist():
            name = item.filename
            if item.is_dir() or ".." in name.split("/") or name.startswith("/") or not ALLOWED.match(name) or name == "backup-info.json":
                continue
            target = (store.DATA / name).resolve()
            if not target.is_relative_to(store.DATA.resolve()):
                continue
            if name == "config.json":
                new = json.loads(z.read(name))
                old_keys = {p["id"]: p.get("api_key", "") for p in current_cfg.get("providers", []) + current_cfg.get("image_providers", [])}
                for p in new.get("providers", []) + new.get("image_providers", []):
                    if not p.get("api_key"):
                        p["api_key"] = old_keys.get(p["id"], "")
                new["rt_comfy_cmd"] = current_cfg.get("rt_comfy_cmd", "")
                store.save_config(new)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
            counts["files"] += 1
            if name.startswith("stories/") and name.endswith(".json"):
                counts["stories"] += 1
    return counts
