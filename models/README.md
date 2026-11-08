# Local model checkpoints: SAM 2

The object step uses **SAM 2** (Meta's pretrained Segment Anything 2 image predictor) to outline the object in every photo. It runs on this laptop in its own Python environment; nothing is uploaded, and no weights are bundled or committed (this folder is git-ignored apart from this file).

| | |
|---|---|
| Code | https://github.com/facebookresearch/sam2 at commit `2b90b9f5ceec907a1c18123530e92e794ad901a4` |
| Checkpoint | `sam2.1_hiera_tiny.pt` from `https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt` |
| SHA-256 | `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69` |
| Config | `configs/sam2.1/sam2.1_hiera_t.yaml` (bundled with the code) |
| Licence | Apache 2.0 for the SAM 2 code and checkpoints, according to the upstream repository. Recheck before redistributing. |

Do **not** `pip install sam2` from PyPI. That package name is a third-party repackage of a fork, not Meta's release.

## Install (verified on the development laptop, CPU only)

SAM 2 needs PyTorch, which the web server does not, so it lives in `.venv-sam2` next to `.venv`. The scanner finds `.venv-sam2` and `models/sam2.1_hiera_tiny.pt` automatically; no config change is needed. From the repository folder in PowerShell:

```powershell
py -3.11 -m venv .venv-sam2
.venv-sam2/Scripts/python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple torch==2.5.1 torchvision==0.20.1
.venv-sam2/Scripts/python.exe -m pip install "setuptools>=61" wheel
$env:SAM2_BUILD_CUDA = '0'
.venv-sam2/Scripts/python.exe -m pip install --no-build-isolation "git+https://github.com/facebookresearch/sam2.git@2b90b9f5ceec907a1c18123530e92e794ad901a4"
Invoke-WebRequest https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt -OutFile models/sam2.1_hiera_tiny.pt
(Get-FileHash models/sam2.1_hiera_tiny.pt -Algorithm SHA256).Hash   # must match the table above
```

This needs Git for Windows (for the SAM 2 source). The checkpoint must be `sam2.1_hiera_tiny.pt`; the older `sam2_hiera_tiny.pt` has a different hash and does not match the bundled 2.1 config.

**GPU (partner laptop, not yet verified):** use the PyTorch CUDA wheel index that matches the driver (for example `https://download.pytorch.org/whl/cu124`) instead of `/cpu`. `segmentation_device = "auto"` picks CUDA when it is available.

Then run `.venv/Scripts/python.exe -m scripts.doctor`: its `segmentation` section shows whether the environment and checkpoint were found and the checkpoint hash.

## How the scanner uses it

1. A rough first carving (GrabCut outlines, classical CV) locates the object in 3D.
2. For **each photo**, the scanner projects that rough shape into the photo to get a box, and adds one point on the object: the spot tapped on the phone at the start of the scan (its camera ray meets the rough shape), or the shape's centre if there was no tap.
3. SAM 2 predicts that photo's mask from its own box and point (`scripts/sam2_masks.py`, one subprocess per reconstruction). No mask is copied between views.
4. A SAM 2 mask replaces the GrabCut outline only where the two broadly agree (overlap ≥ 35%, area within 3×); otherwise that photo keeps GrabCut.
5. The chosen masks carve the final shape and select the pixels that texture it. They are cached with the COLMAP run, so a detail-only rebuild skips SAM 2.

The dashboard shows every mask as a green overlay and says which method produced them. `masks = "grabcut"` in `config.local.toml` turns SAM 2 off; without `.venv-sam2` or the checkpoint the scanner uses GrabCut and says so in the status message.

## Measured on real input

36 Samsung photos (1080×1920) of a matte black bottle with a carabiner clip, development laptop CPU, torch 2.5.1+cpu:

- SAM 2 tiny: 59 s for all 36 photos (model load included). All 36 masks passed the agreement check; visually they follow the bottle and leave out the table, shadow and the separate clip.
- Object step total: about 4.5 minutes for the first run (SAM 2, carving, mesh, bake), about 80 s for a detail-only rebuild with cached masks.
- The tap geometry was checked on the same scan: a tap on the bottle in photo 1 projected inside the object mask in 36 of 36 photos; a tap on the background was ignored.
