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

from backend.app.config import ROOT, load_settings, project_path, resolve_tool
from backend.app.network import certificate_addresses, lan_addresses
from scripts.make_cert import find_mkcert
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
                            ("opencv-python", "cv2"), ("trimesh", "trimesh"), ("httpx", "httpx"), ("Pillow", "PIL"),
                            ("segno", "segno"), ("scikit-image", "skimage")):
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
                tools[name] = {"ok": True, "path": executable, "output": colmap_help(executable)[:6000]}
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                tools[name] = {"ok": False, "path": executable, "output": str(error)}
        else:
            args = ["--version"] if name == "blender" else ["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]
            tools[name] = {"path": executable, **probe([executable, *args])}
    python_ok = sys.version_info[:2] == (3, 11)
    capture_ready = python_ok and all(value["ok"] for key, value in dependencies.items() if key != "httpx")
    addresses = lan_addresses()
    certified = certificate_addresses(settings)
    cert_ok = project_path(settings.tls_cert).is_file() and project_path(settings.tls_key).is_file()
    phone = {"lan": settings.lan, "port": settings.port, "addresses": addresses, "certificate": cert_ok,
             "certificate_addresses": certified, "mkcert": find_mkcert()}
    phone["ok"] = settings.lan and cert_ok and bool(addresses) and (certified is None or addresses[0] in certified)
    return {
        "phone_connection": phone,
        "platform": platform.platform(), "python": platform.python_version(), "python_supported": python_ok,
        "mode": settings.mode, "free_disk_gib": round(shutil.disk_usage(ROOT).free / 1024**3, 1),
        "dependencies": dependencies, "tools": tools, "capture_ready": capture_ready,
        "reconstruction_prerequisites_found": capture_ready and tools["colmap"]["ok"] and tools["nvidia-smi"]["ok"],
        "real_reconstruction_verified": False,
        "segmentation": segmentation(settings),
        "limitations": ["Tool discovery does not prove GPU compatibility or a successful reconstruction.",
                        "SAM 2 is only checked for files here; the first reconstruction shows whether it ran (status message, sam2.log)."],
    }


def segmentation(settings) -> dict:
    """SAM 2 environment and checkpoint (see models/README.md)."""
    import hashlib
    from backend.app.ai_masks import sam2_tools
    python, checkpoint, problem = sam2_tools(settings)
    report = {"masks": settings.masks, "python": python, "checkpoint": str(checkpoint), "ok": problem is None,
              "problem": problem}
    if checkpoint.is_file():
        digest = hashlib.sha256()
        with checkpoint.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        report["checkpoint_sha256"] = digest.hexdigest()
    if settings.masks == "grabcut":
        report["note"] = "masks = grabcut: SAM 2 is not used."
    return report


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
