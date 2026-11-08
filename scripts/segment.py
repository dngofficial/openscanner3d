"""Run the SAM 2 video predictor over reviewed frames and write one object mask per frame.

Runs as its own process (optionally from a separate PyTorch environment), so the web server never
imports torch and GPU memory is released before reconstruction. Imports only third-party packages.

Request JSON: {"frames": ["kf_0000.jpg", ...], "prompts": [{"frame": "kf_0003.jpg",
"points": [[x, y, label], ...]}]} with x/y as fractions of the image and label 1 = object, 0 = not.
Output: masks/<frame>.png (255 = object), previews/<frame>.jpg, segmentation.json.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

import numpy as np
from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def preview(image: Path, mask: np.ndarray, target: Path):
    with Image.open(image) as opened:
        rgb = np.asarray(opened.convert("RGB"), np.float32)
    tint = rgb.copy()
    tint[~mask] *= .35  # Dim everything outside the object so the selection is obvious.
    tint[mask] = tint[mask] * .7 + np.array([40, 200, 160]) * .3
    small = Image.fromarray(tint.astype(np.uint8))
    small.thumbnail((480, 480))
    small.save(target, quality=85)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    import torch
    from sam2.build_sam import build_sam2_video_predictor

    request = json.loads(args.request.read_text(encoding="utf-8"))
    frames = request["frames"]
    index = {name: position for position, name in enumerate(frames)}
    device = "cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu"
    started = time.monotonic()
    args.output.mkdir(parents=True)
    (args.output / "masks").mkdir()
    (args.output / "previews").mkdir()
    with tempfile.TemporaryDirectory() as temporary:
        # The video predictor reads a folder of integer-named JPEG frames in order.
        for name, position in index.items():
            shutil.copyfile(args.images / name, Path(temporary) / f"{position:05d}.jpg")
        with Image.open(args.images / frames[0]) as first:
            width, height = first.size
        predictor = build_sam2_video_predictor(args.config, str(args.checkpoint), device=device)
        autocast = torch.autocast("cuda", dtype=torch.bfloat16) if device == "cuda" else torch.autocast("cpu", enabled=False)
        with torch.inference_mode(), autocast:
            state = predictor.init_state(video_path=temporary, offload_video_to_cpu=True,
                                         offload_state_to_cpu=device == "cpu")
            for prompt in request["prompts"]:
                points = np.array([[x * width, y * height] for x, y, _ in prompt["points"]], np.float32)
                labels = np.array([label for _, _, label in prompt["points"]], np.int32)
                predictor.add_new_points_or_box(inference_state=state, frame_idx=index[prompt["frame"]],
                                                obj_id=1, points=points, labels=labels)
            masks = {}
            # Forward from the first prompt, then backward to reach frames before it.
            for reverse in (False, True):
                for position, _, logits in predictor.propagate_in_video(state, reverse=reverse):
                    masks[position] = (logits[0, 0] > 0).cpu().numpy()
        del predictor, state
    results = {}
    for name, position in index.items():
        mask = masks[position]
        if mask.shape != (height, width):
            raise ValueError(f"Mask for {name} has shape {mask.shape}, expected {(height, width)}")
        Image.fromarray(mask.astype(np.uint8) * 255).save(args.output / "masks" / f"{name}.png")
        preview(args.images / name, mask, args.output / "previews" / name)
        results[name] = round(float(mask.mean()), 5)
    report = dict(frames=results, prompts=request["prompts"], device=device, torch=torch.__version__,
                  checkpoint=args.checkpoint.name, checkpoint_sha256=sha256(args.checkpoint), config=args.config,
                  image_size=[width, height], seconds=round(time.monotonic() - started, 1))
    (args.output / "segmentation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "frames"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
