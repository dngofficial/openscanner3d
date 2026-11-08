# Live and photo-based multi-view PLY scanner — VGGT

This version replaces the earlier COLMAP/SIFT alignment and per-frame depth-scale fitting with joint VGGT camera/depth prediction.

It supports:
- A live webcam preview and automatic selection of changed, usable frames.
- Periodic background reconstruction while capture continues.
- Uploaded JPEG/PNG/WebP images from different angles.
- Foreground masks, confidence filtering, multi-view depth consistency, voxel averaging and isolated-noise cleanup.
- An interactive point-cloud preview and colored PLY export.
- An optional independent Depth Anything preview.

The 3D model updates after a reconstruction pass finishes. This is periodic neural multi-view reconstruction, not video-rate SLAM. VGGT estimates the cameras for each snapshot; no ORB-SLAM3, Stella-VSLAM, COLMAP executable or ROS installation is required.

## Windows setup

Stop the old scanner first. Extract this version into its own folder. Use Python 3.10, 3.11 or 3.12. The commands below use Python 3.12; substitute -3.11 or -3.10 if appropriate.

In PowerShell, inside vggt_scanner:

    py -3.12 -m venv .venv-vggt
    .\.venv-vggt\Scripts\python.exe -m pip install --upgrade pip
    .\.venv-vggt\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv-vggt\Scripts\python.exe install_vggt.py
    .\.venv-vggt\Scripts\python.exe check_setup.py
    .\.venv-vggt\Scripts\python.exe server.py

Open http://localhost:8000.

If using an NVIDIA GPU, install the appropriate CUDA-enabled PyTorch build in this environment using the official selector at https://pytorch.org/get-started/locally/. Run check_setup.py and confirm “Usable PyTorch CUDA: True.” Installing the NVIDIA driver or CUDA toolkit alone does not turn a CPU-only PyTorch wheel into a CUDA build.

A fresh environment avoids the Windows cv2.pyd locking/package-conflict problem encountered in the earlier versions. This app uses only opencv-python-headless. install_vggt.py installs pinned upstream VGGT code with --no-deps so its OpenCV dependency does not add a second overlapping cv2 package. The dependencies needed by our adapter are listed explicitly in requirements.txt. The upstream demo helpers are not used.

## Linux/macOS setup

    python3.12 -m venv .venv-vggt
    .venv-vggt/bin/python -m pip install --upgrade pip
    .venv-vggt/bin/python -m pip install -r requirements.txt
    .venv-vggt/bin/python install_vggt.py
    .venv-vggt/bin/python check_setup.py
    .venv-vggt/bin/python server.py

The app selects CUDA when available, otherwise CPU. Apple MPS inference has not been implemented. CPU inference is substantially slower and needs sufficient RAM.

## Weights and memory

The default model is facebook/VGGT-1B. Its model.pt checkpoint is approximately 5 GB and is downloaded on first model load, including when the first automatic reconstruction starts. The ZIP does not include model weights. Subsequent starts reuse the local model_cache directory.

By default, the app uses at most 8 selected views and 224 × 224 model inputs. Increase the view budget or input resolution only when memory permits. Automatic previews use at most 8 views; a final pass uses the configured final budget. The app can retain 200 keyframes, but each pass processes only the selected budget sampled across the recording.

Memory preflights are conservative application checks:
- CPU loading requires at least 12 GiB of available RAM.
- CUDA loading requires at least 6 GiB of free GPU memory.
- Passing these checks does not guarantee capacity for larger view counts or resolutions.
- Model inference failures keep the last successful result. Memory failures pause automatic updates until you adjust settings and retry.

The checkpoint is memory-mapped and the module is initialized with Accelerate's empty-weight context to avoid two full CPU copies. GPU inference uses autocasting while preserving float32 model parameters, following the upstream model's mixed-precision head behavior.

Environment variables, set before starting the server:
- VGGT_CACHE: an alternate cache directory on a drive with sufficient free space.
- VGGT_CHECKPOINT: an existing local model.pt file; skips the checkpoint download.
- VGGT_DEVICE: auto, cuda or cpu.
- VGGT_MODEL: Hugging Face repository using the compatible VGGT-1B checkpoint format.
- VGGT_REVISION: checkpoint revision, default main.

Example PowerShell:

    $env:VGGT_CACHE = 'D:\ModelCache\VGGT'
    .\.venv-vggt\Scripts\python.exe server.py

Do not point VGGT_CHECKPOINT at an incompatible metric-depth or unrelated model. Upstream code and checkpoint licenses still apply; see https://github.com/facebookresearch/vggt.

## Live capture

1. Click Load VGGT model to preload it, or begin capture and let the first automatic pass load it. The page shows model status.
2. Choose your webcam or virtual webcam, click Start / resume live capture, and grant camera permission.
3. Move the camera around a stationary object. Include overlapping views and translation. Briefly hold still at useful angles. Aim initially for roughly 8–16 sharp, useful views.
4. The camera path selects changed frames independently of VGGT inference. It does not wait for the depth/reconstruction model to finish.
5. With automatic updates enabled, a new snapshot can start after at least three views are saved, and at least eight seconds after the previous start. A new revision must exist. Only one job per scan can be pending; new frames are coalesced into a later snapshot.
6. Click Reconstruct now to request a periodic pass, or Finish & export PLY to pause capture and request the final pass.
7. If another pass is running, Finish waits for it and then reconstructs the latest saved views with the final view budget.
8. Download merged.ply or results.zip. The viewer is sampled; the PLY contains the complete exported cloud.

If a frame is in flight, Finish waits for it before sending the final request. Capture may continue during an ordinary periodic pass. Capture is paused for the final handoff. Cancel disables automatic updates; an active model inference must return before cancellation completes.

At most one reconstruction runs globally at a time. Multiple scans can queue, with at most one pending job per scan.

## Upload photos

Click Add photos and select JPEG, PNG or WebP files. Convert HEIC/RAW first. Uploaded images can share a set with camera keyframes. Uploads are limited to 20 MB each and 36 megapixels, normalized for EXIF orientation, converted to RGB JPEG and resized to fit 1600 × 1600. Exact normalized duplicates are skipped.

At least three views are accepted to attempt a reconstruction. Three views are not a guarantee of useful coverage or accurate shape. Use smaller viewpoint changes with overlap; multiple near-identical frames add little information. Keep the object still and lighting consistent.

Each selected angle appears as a thumbnail. Remove accidental frames with × when no reconstruction is running. Background masks use the selected capture region; if uploaded images have different framing or the mask is unreliable, disable Object-only cloud for reconstruction.

## Foreground and noise handling

Original full RGB images feed VGGT. They are not painted over with a flat background. The original image context helps camera/geometry prediction.

The foreground mask is estimated with GrabCut inside a draggable rectangle and cleaned morphologically. It is applied to predicted 3D points, along with a validity mask that excludes letterbox padding. It is color-based segmentation, not semantic object detection; inspect the mask and disable Object-only cloud if it cuts out the subject. Keep the full object inside the rectangle throughout capture.

Masks are stored per selected frame using its capture settings. Disabling Object-only cloud bypasses them for reconstruction; turning the setting on does not retroactively improve a mask captured under different settings.

The confidence control drops the weakest percentile of valid predictions, not a calibrated probability. The default drops 35%. Cross-view consistency, enabled by default, requires a candidate point to agree with the depth and foreground mask of at least one other predicted camera view within a relative depth tolerance. It can remove real thin surfaces or poorly observed regions. The filter can be disabled for inspection, but uncertain geometry may then remain.

Filtered points are voxel-averaged, bounded against extreme outliers and optionally filtered using neighbor distances. The export is capped at 300,000 points. These filters cannot prove that neural predictions are correct.

## Optional quick depth preview

Install the optional dependencies:

    .\.venv-vggt\Scripts\python.exe -m pip install -r requirements-preview.txt

Enable the independent Depth Anything preview in the collapsible panel. It downloads the smaller Depth Anything V2 Small model on first use. It runs in a bounded latest-frame worker and is serialized with VGGT computation, so it may pause while a joint pass runs. It defaults to CPU and does not block camera selection.

These single-frame relative-depth clouds are display-only. They are not appended to the joint VGGT result.

## Files and saved results

Each scan is stored under scans/<scan-id>/:

- images/: original selected RGB views.
- masks/: per-view foreground masks.
- thumbs/: thumbnail gallery images.
- session.json: saved capture/settings/job state and pointer to the latest successful result.
- reconstructions/<job-id>/: one immutable joint reconstruction snapshot.

A successful job exports:
- merged.ply: colored point cloud.
- preview.json: up to 6,000 points for the browser.
- cameras.json: predicted world-to-camera extrinsics and intrinsics, with letterbox transforms and original-image intrinsics.
- predictions.npz: joint depth, confidence and camera predictions.
- report.json: selected IDs, input counts, filtering counts, timing and arbitrary-unit scale note.
- reconstruction.log: report or failure traceback.
- results.zip: the result files above, excluding the raw input photos.

The browser remembers the scan ID. Refreshing restores the saved set and previous result, with capture paused. A server restart marks unfinished jobs as interrupted; saved images and previous results remain.

New frames do not erase a successful result. The UI labels how many views that snapshot used and whether newer views/settings are waiting. Each successful pass replaces the displayed map; independent VGGT predictions are never blindly accumulated together because their coordinate frames and scales can change. Earlier job folders remain on disk.

Use New scan for a separate object. Delete old scan folders manually to recover disk space.

## What this version does and does not establish

This is an image-based estimated 3D point model. It does not guarantee metric dimensions, unseen geometry, a watertight mesh or measurement accuracy. The colored PLY has vertices, not mesh faces. Shiny, transparent, moving or texture-poor subjects and weak view coverage remain difficult. The app is not a camera-rate SLAM system and does not provide a continuously optimized metric trajectory.

The server binds to 127.0.0.1. The web camera requires browser permission and localhost access. Internet is needed for dependency/source installation and first model downloads; images are processed locally by the running server.

## Validation

    .\.venv-vggt\Scripts\python.exe -m pip install httpx
    .\.venv-vggt\Scripts\python.exe -m unittest discover -p "test_*.py" -v
    node test_ui.cjs

Node.js is optional and used only for control-flow tests.

Tests use a synthetic camera/depth backend to check capture during reconstruction, snapshot isolation, final-pass handoff, bounded jobs, PLY writing, filtering, cancellation and preservation of the previous result. They also test letterboxing, pose inversion and memory preflights. The JavaScript test uses DOM/API/socket fixtures; it is not a full browser visual test.

The real downloaded VGGT checkpoint's parameter schema was checked against the pinned upstream camera/depth model without allocating the full weight tensors. Full pretrained inference and resulting object quality were not completed in the development workspace because of its memory limits.

For a real-model end-to-end test on your machine:

    .\.venv-vggt\Scripts\python.exe smoke_test.py front.jpg side.jpg angled.jpg --size 224

Use sharp overlapping views of the same rigid scene. This performs actual VGGT inference and writes a colored PLY under smoke_output/reconstructions/<job-id>/.

Upstream code is pinned to revision a288dd0f14786c93483e45524328726ab7b1b4ce. See https://github.com/facebookresearch/vggt for the model documentation and https://huggingface.co/facebook/VGGT-1B for its checkpoint.
