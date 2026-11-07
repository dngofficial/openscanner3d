"""Local machine configuration; never accepts settings from browser inputs."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    mode: str = "capture"
    colmap: str = "colmap"
    blender: str = "blender"
    quality: str = "low"
    timeout_seconds: int = 1800
    max_photos: int = 100


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.local.toml"
    values = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if os.environ.get("SCANNER_MODE"):
        values["mode"] = os.environ["SCANNER_MODE"]
    unknown = set(values) - set(Settings.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    settings = Settings(**values)
    if settings.mode not in {"capture", "reconstruct"}:
        raise ValueError("mode must be capture or reconstruct")
    if settings.quality not in {"low", "medium", "high"}:
        raise ValueError("quality must be low, medium, or high")
    for key, low, high in (("timeout_seconds", 10, 14400), ("max_photos", 12, 500)):
        value = getattr(settings, key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("colmap", "blender"):
        if not isinstance(getattr(settings, key), str) or not getattr(settings, key).strip():
            raise ValueError(f"{key} must be an executable name or path")
    return settings


def resolve_tool(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path('.'):
        candidate = candidate if candidate.is_absolute() else ROOT / candidate
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(value)
