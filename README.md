# Live object scanner

## Repository guide

`PLAN.md` defines the current hackathon scope; `AGENTS.md` defines engineering rules. The longer `docs/implementation_plan.md` is background, not the current backlog.

```text
frontend/
  index.html                 # Existing camera UI (plain HTML/JavaScript)
backend/
  app/main.py                # Existing FastAPI capture and reconstruction prototype
  requirements.txt           # Runtime dependencies
  requirements-dev.txt       # Runtime + capture-check dependencies
scripts/
  launch.ps1                 # Windows launcher using the root .venv
tests/
  check_capture.py           # Synthetic capture and WebSocket checks
docs/
  implementation_plan.md     # Long-term reference
models/README.md              # Checkpoint policy; no weights bundled
demo/README.md                # Real-data validation and fallback checklist
PLAN.md
AGENTS.md
```

Keep the prototype operational while validating reconstruction. React/Vite migration, segmentation, textured export, and the 3D viewer are future work; this organization does not implement them. The capture and COLMAP logic remain together in `backend/app/main.py` until the pipeline is validated.

Local `.venv/`, `sample-data/`, and `scans/` are ignored. Existing capture storage stays at repository-root `scans/` so this move does not relocate or invalidate saved files. The planned `data/scans/` layout can be introduced with the new storage layer.

Capture a camera or virtual webcam in a browser on the same computer as the server. Frames are JPEG-encoded in the browser, sent over a WebSocket, and processed with OpenCV SIFT, two-view initialization, triangulation and PnP. Feedback is approximate. Selected original frames are saved as JPEG keyframes; Finish exports a sparse PLY and, when COLMAP succeeds, a dense mesh.

## Start

Install Python 3.10 or newer, then in this folder:

    python -m venv .venv
    # Windows PowerShell:
    .venv\Scripts\Activate.ps1
    # Linux/macOS:
    source .venv/bin/activate
    python -m pip install -r backend/requirements.txt
    python -m backend.app.main

Run these commands from the repository root. Python 3.11 is the project target. On Windows, after creating the root `.venv`, `./scripts/launch.ps1` also starts the app without requiring activation. Dependency versions are inherited and currently unpinned; pin versions after a working environment and real reconstruction have been verified.

Open http://localhost:8000, grant camera permission, and choose a camera. A phone acting as a virtual webcam (such as Camo) can be selected if its desktop software exposes it as a video input. This does not automatically capture a video playing in another browser tab. Start with the object visible, then move slowly sideways and orbit it, keeping it in view. Good lighting, a textured matte surface, and overlap between images help tracking. When you have at least 12 views, click Finish. Download links appear for files that were produced. Use Start over to begin another scan; previous scans remain in the scans directory.

The localhost server can access only cameras exposed to the browser on the same machine; a separate phone opening localhost would address the phone itself. It binds to 127.0.0.1 by default. Camera access needs browser permission.

## Reconstruction and limits

Install COLMAP separately and make its executable available on PATH before starting the server. Its automatic reconstruction and dense pipeline have platform, GPU and CUDA requirements depending on the installed build. If COLMAP is absent, the selected photos and sparse PLY remain available in scans/<session>/; Finish can be retried. Check colmap.log if the command fails. Dense reconstruction may take several minutes. A mesh is not guaranteed, and GLB color depends on the data actually produced by COLMAP; no texture creation is promised.

This is an experimental structure-from-motion guide, not a calibrated or metric scanner. The focal length is guessed, the scene center comes from tracked points including possible background, and the assumed vertical axis comes from the initial camera orientation. Coverage, camera directions, object size and distance prompts are heuristics and can be misleading; scale is arbitrary. Reflective, plain or moving objects and weak parallax can cause tracking failure. For best results use a stable camera orientation, keep the object centered, avoid moving the object independently of the scene, and examine the outputs. Switching cameras starts a new scan because intrinsics and tracking change.

The images contain the full camera view, including any background. Files are stored locally in scans/<session>/; remove old folders manually when no longer needed. Closing the page stops the live session, while already saved files stay on disk. Finish is synchronous within a scan and cannot be canceled from the page.

## Verification

From the repository root, with the virtual environment active:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m tests.check_capture
```

The development requirements include `httpx`, used by FastAPI's test client. The capture check uses synthetic images and mocks parts of tracking and executable discovery; it does not validate COLMAP, texturing, GPU support, or exported models. Real reconstruction and independent GLB inspection remain required by `PLAN.md`.

The repository reorganization was checked for path consistency, JavaScript syntax, and PowerShell syntax. Python execution and reconstruction have not been verified in this workspace because a usable Python environment and reconstruction tools have not yet been located.
