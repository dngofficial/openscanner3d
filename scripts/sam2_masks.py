"""Object masks from SAM 2 (pretrained Segment Anything 2), one independent prediction per photo.

Runs in its own process and environment (it needs PyTorch, the scanner does not). The scanner
supplies, for every photo, a box around the object and a point on it, both projected from the
first rough 3D carving, so no mask is ever copied from one photo to another.

Request JSON: {"images": "<folder>", "items": [{"name": "kf_0000.jpg", "box": [x0, y0, x1, y1],
               "points": [[x, y]]}, ...]}   (pixel coordinates in the full-size photo)
Writes <output>/<name>.png (255 = object) and <output>/report.json.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    import torch
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    request = json.loads(args.request.read_text(encoding="utf-8"))
    device = "cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu"
    started = time.monotonic()
    predictor = SAM2ImagePredictor(build_sam2(args.config, str(args.checkpoint), device=device))
    args.output.mkdir(parents=True, exist_ok=True)
    scores = {}
    autocast = torch.autocast("cuda", dtype=torch.bfloat16) if device == "cuda" else torch.autocast("cpu", enabled=False)
    with torch.inference_mode(), autocast:
        for item in request["items"]:
            with Image.open(Path(request["images"]) / item["name"]) as opened:
                predictor.set_image(np.asarray(opened.convert("RGB")))
            points = np.array(item.get("points") or [], np.float32).reshape(-1, 2)
            masks, quality, _ = predictor.predict(
                point_coords=points if len(points) else None,
                point_labels=np.ones(len(points), np.int32) if len(points) else None,
                box=np.array(item["box"], np.float32), multimask_output=False)
            Image.fromarray((masks[0] > 0).astype(np.uint8) * 255).save(args.output / f"{item['name']}.png")
            scores[item["name"]] = round(float(quality[0]), 4)
    digest = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    report = dict(model="SAM 2", checkpoint=args.checkpoint.name, checkpoint_sha256=digest, config=args.config,
                  device=device, torch=torch.__version__, photos=len(scores), scores=scores,
                  seconds=round(time.monotonic() - started, 1))
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "scores"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
