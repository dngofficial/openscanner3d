"""Label an existing, real scan result as the demo's saved fallback.

The app then shows it with a "Saved fallback" badge so it is never presented as a new scan.
The label records what was captured and the SHA-256 of each model, so the fallback can be matched
to its documented inputs. Usage:
    python -m scripts.mark_fallback SCAN_ID --label "Power bank, 77 Camo frames, 2026-11-07"
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys

import trimesh

from backend.app.config import ROOT

MODELS = ("textured.glb", "object.glb", "mesh.glb")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scan_id")
    parser.add_argument("--label", required=True, help="What was captured, how, and when.")
    parser.add_argument("--remove", action="store_true", help="Remove the fallback label instead.")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}", args.scan_id):
        parser.error("Not a scan id")
    folder = ROOT / "scans" / args.scan_id
    if args.remove:
        (folder / "fallback.json").unlink(missing_ok=True)
        return 0
    models = {}
    for name in MODELS:
        path = folder / name
        if path.is_file():
            if not len(trimesh.load(path, force="mesh").faces):
                parser.error(f"{name} has no faces")
            models[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not models:
        parser.error("This scan has no exported model to use as a fallback")
    record = dict(label=args.label, marked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), models=models)
    (folder / "fallback.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
