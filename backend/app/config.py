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
    # Fractional [x0, y0, x1, y1] regions excluded from feature detection. The default covers the
    # Camo free-tier watermark, measured on a 1280x720 Camo stream; use [] for unwatermarked input.
    feature_mask_regions: tuple = ((0.875, 0.785, 0.975, 0.975),)
    # SAM 2 runs in a separate process, optionally from its own environment (e.g. a CUDA PyTorch venv).
    # An empty segmentation_python uses the server's interpreter.
    segmentation_python: str = ""
    sam2_checkpoint: str = "models/sam2.1_hiera_tiny.pt"
    sam2_config: str = "configs/sam2.1/sam2.1_hiera_t.yaml"
    segmentation_device: str = "auto"
    # UV texturing via OpenMVS (InterfaceCOLMAP + TextureMesh). auto: texture when the tools are found.
    openmvs_bin: str = ""
    texturing: str = "auto"
    glb_exporter: str = "auto"       # auto: Blender when found, else trimesh; or "blender" / "trimesh"


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.local.toml"
    values = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if os.environ.get("SCANNER_MODE"):
        values["mode"] = os.environ["SCANNER_MODE"]
    unknown = set(values) - set(Settings.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    if "feature_mask_regions" in values:
        values["feature_mask_regions"] = parse_regions(values["feature_mask_regions"])
    settings = Settings(**values)
    if settings.mode not in {"capture", "reconstruct"}:
        raise ValueError("mode must be capture or reconstruct")
    if settings.quality not in {"low", "medium", "high"}:
        raise ValueError("quality must be low, medium, or high")
    for key, low, high in (("timeout_seconds", 10, 14400), ("max_photos", 12, 500)):
        value = getattr(settings, key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("colmap", "blender", "sam2_checkpoint", "sam2_config"):
        if not isinstance(getattr(settings, key), str) or not getattr(settings, key).strip():
            raise ValueError(f"{key} must be an executable name or path")
    if not isinstance(settings.segmentation_python, str):
        raise ValueError("segmentation_python must be an interpreter path, or empty for the server's Python")
    if settings.segmentation_device not in {"auto", "cuda", "cpu"}:
        raise ValueError("segmentation_device must be auto, cuda, or cpu")
    if settings.texturing not in {"auto", "off"}:
        raise ValueError("texturing must be auto or off")
    if settings.glb_exporter not in {"auto", "blender", "trimesh"}:
        raise ValueError("glb_exporter must be auto, blender, or trimesh")
    if not isinstance(settings.openmvs_bin, str):
        raise ValueError("openmvs_bin must be a folder path, or empty to search PATH")
    return settings


def parse_regions(value) -> tuple:
    message = "feature_mask_regions must be a list of up to 8 [x0, y0, x1, y1] fractions with x0 < x1 and y0 < y1"
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError(message)
    regions = []
    for region in value:
        if (not isinstance(region, list) or len(region) != 4
                or not all(type(v) in (int, float) and 0 <= v <= 1 for v in region)
                or region[0] >= region[2] or region[1] >= region[3]):
            raise ValueError(message)
        regions.append(tuple(float(v) for v in region))
    return tuple(regions)


def resolve_tool(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path('.'):
        candidate = candidate if candidate.is_absolute() else ROOT / candidate
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(value)
