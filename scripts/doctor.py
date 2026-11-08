"""Print machine capabilities without requiring CUDA or importing the web app."""
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from backend.app.config import ROOT, load_settings, resolve_tool
from backend.app.reconstruction import colmap_help


def probe(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip()[:4000]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "output": str(error)}


def inspect_machine() -> dict:
    settings = load_settings()
    dependencies = {}
    for package, module in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("numpy", "numpy"),
                            ("opencv-python", "cv2"), ("trimesh", "trimesh"), ("httpx", "httpx"), ("Pillow", "PIL")):
        try:
            imported = importlib.import_module(module)
            if module == "cv2":
                imported.SIFT_create()
            dependencies[package] = {"ok": True, "version": importlib.metadata.version(package)}
        except Exception as error:
            dependencies[package] = {"ok": False, "error": str(error)}
    tools = {}
    for name, value in (("colmap", settings.colmap), ("blender", settings.blender), ("nvidia-smi", "nvidia-smi")):
        executable = resolve_tool(value)
        if not executable:
            tools[name] = {"ok": False, "output": "Not found; configure the executable path or PATH."}
        elif name == "colmap":
            try:
                output = colmap_help(executable)
                tools[name] = {"ok": True, "path": executable, "mask_path_supported": "--mask_path" in output,
                               "output": output[:6000]}
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                tools[name] = {"ok": False, "path": executable, "output": str(error)}
        else:
            args = ["--version"] if name == "blender" else ["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]
            tools[name] = {"path": executable, **probe([executable, *args])}
    python_ok = sys.version_info[:2] == (3, 11)
    capture_ready = python_ok and all(value["ok"] for key, value in dependencies.items() if key != "httpx")
    return {
        "platform": platform.platform(), "python": platform.python_version(), "python_supported": python_ok,
        "mode": settings.mode, "free_disk_gib": round(shutil.disk_usage(ROOT).free / 1024**3, 1),
        "dependencies": dependencies, "tools": tools, "capture_ready": capture_ready,
        "reconstruction_prerequisites_found": capture_ready and tools["colmap"]["ok"] and tools["nvidia-smi"]["ok"],
        "real_reconstruction_verified": False,
        "limitations": ["Tool discovery does not prove GPU compatibility or a successful reconstruction.",
                        "Current export is an experimental mesh; UV texturing, Blender cleanup, and SAM 2 integration are not implemented."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-reconstruction", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional local JSON report (contains machine paths).")
    args = parser.parse_args()
    try:
        report = inspect_machine()
    except (ValueError, TypeError, OSError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    required = "reconstruction_prerequisites_found" if args.require_reconstruction else "capture_ready"
    return 0 if report[required] else 1


if __name__ == "__main__":
    raise SystemExit(main())
