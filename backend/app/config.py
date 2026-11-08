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
    # Phone capture over the local network. LAN mode serves HTTPS (needed for the phone's camera and
    # motion sensors) on all interfaces, but only answers private-network clients holding the launch key.
    lan: bool = False
    port: int = 8443
    tls_cert: str = "certs/lan.pem"
    tls_key: str = "certs/lan-key.pem"
    # Object outlines: "auto" uses SAM 2 when its environment and checkpoint exist, else GrabCut.
    masks: str = "auto"
    segmentation_python: str = ""          # Empty: use .venv-sam2 in the repository if present.
    sam2_checkpoint: str = "models/sam2.1_hiera_tiny.pt"
    sam2_config: str = "configs/sam2.1/sam2.1_hiera_t.yaml"
    segmentation_device: str = "auto"
    # Game asset detail preset (triangle budget and texture size), see DETAIL.
    detail: str = "standard"


# Triangle budget and texture size of object.glb. "low" suits low-poly game props.
DETAIL = {"low": (1000, 1024), "standard": (5000, 2048), "high": (20000, 4096)}


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
    for key, allowed in (("masks", {"auto", "sam2", "grabcut"}), ("segmentation_device", {"auto", "cuda", "cpu"}),
                         ("detail", set(DETAIL))):
        if getattr(settings, key) not in allowed:
            raise ValueError(f"{key} must be one of {', '.join(sorted(allowed))}")
    for key in ("segmentation_python", "sam2_checkpoint", "sam2_config"):
        if not isinstance(getattr(settings, key), str):
            raise ValueError(f"{key} must be a string")
    if type(settings.lan) is not bool:
        raise ValueError("lan must be true or false")
    for key in ("tls_cert", "tls_key"):
        if not isinstance(getattr(settings, key), str) or not getattr(settings, key).strip():
            raise ValueError(f"{key} must be a file path")
    for key, low, high in (("timeout_seconds", 10, 14400), ("max_photos", 12, 500), ("port", 1024, 65535)):
        value = getattr(settings, key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("colmap", "blender"):
        if not isinstance(getattr(settings, key), str) or not getattr(settings, key).strip():
            raise ValueError(f"{key} must be an executable name or path")
    return settings


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def resolve_tool(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path('.'):
        candidate = candidate if candidate.is_absolute() else ROOT / candidate
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(value)
