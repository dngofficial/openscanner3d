"""Make a scan folder show up on the dashboard (e.g. one made by scripts.reconstruct or copied from another laptop).

Usage (from the repository root):
    .venv/Scripts/python.exe -m scripts.register_scan scans\\20261108_101904_0614403d

The dashboard lists folders in scans/ that have scan.json. This writes the files it reads, only where
they are missing (existing ones are kept): scan.json (finished, photo size), poses.json (photos without
angle data), run.json (the newest COLMAP run, so Rebuild object works) and status.json (ready or failed,
from object.json / result.json). Photos, models and runs are not touched.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from PIL import Image

SCAN_ID = r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}"


def write_new(path: Path, value) -> bool:
    if path.exists():
        return False
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return True


def register(scan: Path) -> list[str]:
    from backend.app.reconstruction import describe_object, newest_run
    if not re.fullmatch(SCAN_ID, scan.name):
        raise ValueError(f"{scan.name} is not a scan ID (YYYYMMDD_HHMMSS_xxxxxxxx)")
    photos = sorted((scan / "images").glob("kf_*.jpg"))
    if not photos:
        raise ValueError(f"No kf_*.jpg photos in {scan / 'images'}")
    with Image.open(photos[0]) as first:
        size = list(first.size)
    created = datetime.strptime(scan.name[:15], "%Y%m%d_%H%M%S").astimezone(timezone.utc).isoformat(timespec="seconds")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = []
    if write_new(scan / "scan.json", dict(created=created, finished=True, finished_at=now, size=size)):
        written.append("scan.json")
    poses = [dict(name=photo.name, azimuth=None, pitch=None, ring=None, sector=None, imported=True, time=created, quality={})
             for photo in photos]
    if write_new(scan / "poses.json", poses):
        written.append("poses.json")
    run = newest_run(scan)
    if run and write_new(scan / "run.json", dict(run=run.name)):
        written.append("run.json")
    try:
        message = describe_object(json.loads((scan / "object.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        try:
            message = json.loads((scan / "result.json").read_text(encoding="utf-8")).get("msg", "")
        except (OSError, ValueError):
            message = ""
    ready = (scan / "object.glb").is_file() or (scan / "mesh.glb").is_file()
    status = dict(state="ready" if ready else "failed", finished=now,
                  message=message or ("Imported results." if ready else "No model files in this folder."))
    if write_new(scan / "status.json", status):
        written.append("status.json")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scans", nargs="+", type=Path, help="Scan folders under scans/.")
    args = parser.parse_args()
    code = 0
    for scan in args.scans:
        try:
            written = register(scan.resolve())
            print(f"{scan.name}: {'wrote ' + ', '.join(written) if written else 'already registered'}")
        except (ValueError, OSError) as error:
            print(f"{scan.name}: {error}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
