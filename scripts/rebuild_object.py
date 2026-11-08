"""Rebuild object.glb / object.obj from an existing COLMAP run, without rerunning COLMAP.

Usage (from the repository root, app stopped or idle):
    .venv/Scripts/python.exe -m scripts.rebuild_object scans/SCAN_ID
    .venv/Scripts/python.exe -m scripts.rebuild_object --workspace PATH\\runs\\RUN --images PATH\\images --output PATH

With just a scan folder it uses that scan's newest run under runs/ and its images/ folder.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from backend.app.silhouette import reconstruct_object


def newest_run(scan: Path) -> Path | None:
    runs = [run for run in (scan / "runs").glob("*") if (run / "sparse" / "0" / "images.bin").is_file()]
    return max(runs, key=lambda run: run.stat().st_mtime) if runs else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scan", nargs="?", type=Path, help="Scan folder containing runs/ and images/.")
    parser.add_argument("--workspace", type=Path, help="COLMAP workspace (contains sparse/0 and dense/0).")
    parser.add_argument("--images", type=Path, help="Folder of the photos COLMAP used.")
    parser.add_argument("--output", type=Path, help="Where to write object.glb and object.obj.")
    args = parser.parse_args()
    workspace = args.workspace or (newest_run(args.scan) if args.scan else None)
    images = args.images or (args.scan / "images" if args.scan else None)
    output = args.output or args.scan or workspace
    if not workspace or not (workspace / "sparse" / "0" / "images.bin").is_file():
        parser.error("No COLMAP workspace with sparse/0 found. Pass a scan folder or --workspace.")
    if not images or not images.is_dir():
        parser.error("Photo folder not found. Pass --images.")
    started = time.monotonic()
    try:
        info = reconstruct_object(workspace, images, output)
    except (ValueError, OSError) as error:
        print(f"Object reconstruction failed: {error}", file=sys.stderr)
        return 1
    info["seconds"] = round(time.monotonic() - started, 1)
    info["written"] = [str(output / "object.glb"), str(output / "object.obj")]
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
