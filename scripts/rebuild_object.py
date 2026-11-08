"""Rebuild object.glb / object.obj from an existing COLMAP run, without rerunning COLMAP.

Usage (from the repository root, app stopped or idle):
    .venv/Scripts/python.exe -m scripts.rebuild_object scans/SCAN_ID
    .venv/Scripts/python.exe -m scripts.rebuild_object --workspace PATH\\runs\\RUN --images PATH\\images --output PATH

With just a scan folder it uses that scan's newest run under runs/ and its images/ folder.
Masks follow config.local.toml (SAM 2 when installed, else GrabCut) and are cached in the run folder.
--detail low|standard|high picks the dashboard presets; --target-triangles / --texture-size override them.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from backend.app.ai_masks import make_segmenter
from backend.app.config import DETAIL, load_settings
from backend.app.reconstruction import newest_run
from backend.app.silhouette import reconstruct_object


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scan", nargs="?", type=Path, help="Scan folder containing runs/ and images/.")
    parser.add_argument("--workspace", type=Path, help="COLMAP workspace (contains sparse/0 and dense/0).")
    parser.add_argument("--images", type=Path, help="Folder of the photos COLMAP used.")
    parser.add_argument("--output", type=Path, help="Where to write object.glb and object.obj.")
    parser.add_argument("--detail", choices=sorted(DETAIL), help="Preset (default: detail in config.local.toml).")
    parser.add_argument("--target-triangles", type=int)
    parser.add_argument("--texture-size", type=int)
    parser.add_argument("--height-cm", type=float, help="Known object height in cm; export uses metres. Otherwise arbitrary scale.")
    args = parser.parse_args()
    workspace = args.workspace or (newest_run(args.scan) if args.scan else None)
    images = args.images or (args.scan / "images" if args.scan else None)
    output = args.output or args.scan or workspace
    if not workspace or not (workspace / "sparse" / "0" / "images.bin").is_file():
        parser.error("No COLMAP workspace with sparse/0 found. Pass a scan folder or --workspace.")
    if not images or not images.is_dir():
        parser.error("Photo folder not found. Pass --images.")
    settings = load_settings()
    triangles, texture = DETAIL[args.detail or settings.detail]
    started = time.monotonic()
    try:
        info = reconstruct_object(workspace, images, output, target_triangles=args.target_triangles or triangles,
                                  texture_size=args.texture_size or texture, height_cm=args.height_cm,
                                  segment=make_segmenter(settings, workspace), mask_cache=workspace / "object_masks",
                                  previews=output / "mask_previews")
    except (ValueError, OSError) as error:
        print(f"Object reconstruction failed: {error}", file=sys.stderr)
        return 1
    info["detail"] = (args.detail or settings.detail) if not (args.target_triangles or args.texture_size) else "custom"
    (output / "object.json").write_text(json.dumps(info, indent=2), encoding="utf-8")  # Read by the dashboard.
    info["seconds"] = round(time.monotonic() - started, 1)
    info["written"] = [str(output / name) for name in
                       ("object.glb", "object_hires.glb", "object.obj", "object.mtl", "object_albedo.png")
                       if (output / name).is_file()]
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
