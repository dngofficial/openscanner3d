# Live camera → point clouds → one aligned cloud

This version implements the live workflow: the browser sends camera frames, the server preprocesses each received frame and estimates relative depth, and a local colored point cloud is displayed. Changed camera views are saved as RGB/depth/mask/cloud records. Finish aligns the usable saved views and fuses them into one downloadable merged.ply.

There is no photo-upload step in this interface. The live clouds are unregistered previews; the merged viewer fills after final alignment.

## Windows setup

Stop any older scanner server first. In this folder, create a new environment to avoid conflicts between the earlier OpenCV variants:

    py -m venv .venv-cloud
    .\.venv-cloud\Scripts\python.exe -m pip install --upgrade pip
    .\.venv-cloud\Scripts\python.exe -m pip install -r requirements-depth.txt
    .\.venv-cloud\Scripts\python.exe server.py

Open http://localhost:8000. No activation script or administrator terminal is needed. If py is not installed, use your installed Python launcher in the first command.

If you want to explicitly install CPU PyTorch first, use:

    .\.venv-cloud\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    .\.venv-cloud\Scripts\python.exe -m pip install -r requirements-depth.txt

For NVIDIA acceleration, install a compatible CUDA-enabled PyTorch build using https://pytorch.org/get-started/locally/. The app chooses CUDA when torch.cuda.is_available() is true. CPU inference is also supported.

Linux/macOS:

    python3 -m venv .venv-cloud
    .venv-cloud/bin/python -m pip install -r requirements-depth.txt
    .venv-cloud/bin/python server.py

### Final alignment requires COLMAP

Install COLMAP separately from https://colmap.github.io/install.html. Its executable must be on PATH, or set COLMAP_EXE before starting the server:

    # PowerShell; replace this with the actual installed path:
    $env:COLMAP_EXE = "C:\Users\Devan_\Downloads\colmap-x64-windows-cuda\COLMAP.bat"
    .\.venv-cloud\Scripts\python.exe server.py

    # Linux/macOS:
    export COLMAP_EXE=/path/to/colmap
    .venv-cloud/bin/python server.py

CPU feature extraction, matching and mapping are used for final alignment. This live point-cloud workflow does not require COLMAP's CUDA dense stereo pipeline. The server still contains the older optional photo/mesh APIs for compatibility, but the main interface uses live capture and calibrated depth-cloud fusion.

## Capture and finish

1. Click Load depth model. The first load downloads the public depth-anything/Depth-Anything-V2-Small-hf model from Hugging Face into the machine's normal model cache. Later loads use the cache. If the computer is offline, set DEPTH_MODEL to a local directory containing this model's files.
2. Select the webcam or virtual webcam and click Start live capture. Grant camera permission.
3. Drag a rectangle around the object. The full object must remain inside it as the camera moves. Inspect the foreground-mask preview. Disable Remove background for whole-scene scans or if the mask cuts away the subject.
4. Move the camera slowly around a stationary object with overlapping views and translation. The server processes one frame at a time; performance depends on hardware. A webcam may preview at 30 FPS while depth processing runs much slower.
5. Every processed frame produces a local cloud. A changed view can be saved at most once per second, with a 12-pixel median feature-motion criterion or an image-change fallback. Save cloud now saves the next processed view explicitly. Avoid repeated identical captures.
6. Collect around 20–80 overlapping views as a starting point. At least three saved views are needed to attempt alignment; that is not enough to guarantee a useful scan.
7. Click Finish & align clouds. The browser waits for the in-flight frame, pauses capture and starts final registration/fusion. View stage progress or cancel the job.
8. Inspect the merged point-cloud preview and download merged.ply, results.zip and alignment.json. Open the complete PLY in Blender, MeshLab or CloudCompare. The preview samples up to 6,000 points.

The camera preview and live depth/cloud panels show the capture stage. The final panel is a separate viewer for the merged result. Both cloud viewers support drag rotation and wheel/pinch zoom.

## Background and noise processing

Background cleanup uses OpenCV GrabCut inside the selected rectangle at reduced resolution. Bilateral filtering reduces image noise while retaining edges. Mask cleanup removes small components, closes small holes and erodes the mask boundary.

Original images, including stationary background detail, are retained for COLMAP camera tracking. Relative depth is estimated from the full scene with optional light denoising. The foreground mask is applied to live and merged cloud geometry, so removed background pixels do not become output points. Masking the tracking images too aggressively can leave too few matching features; this version avoids that failure.

This is color-based foreground isolation, not semantic object detection. It can fail when foreground and background have similar colors, or with thin parts, shadows, transparency or multiple overlapping objects. Watch the mask rather than assuming it identified the subject correctly. A tight rectangle and contrasting, simple background help. Changing the rectangle can clip different parts across frames.

Final fusion rejects strong depth discontinuities and implausible depth ranges, averages points into voxels, requires voxel support from at least two separate frames and removes statistical isolated outliers using neighboring-point distances. Some real thin or sparsely observed surfaces can be filtered too. alignment.json reports skipped clouds, calibration errors, filtering counts and the voxel limit.

Do not subtract one fixed background-reference image while orbiting the camera; its pixels no longer describe the same background.

## Alignment method and limits

Depth Anything V2 Small predicts relative inverse depth. The live preview converts that into arbitrary positive display depth using guessed camera intrinsics. It is not measured sensor depth, and live previews do not yet share one coordinate frame.

On Finish:

1. COLMAP matches original, unmasked RGB frames and jointly estimates camera poses and sparse 3D landmarks. The largest connected registered model is selected.
2. Each saved inverse-depth map is robustly calibrated against the positive camera-space depths of shared SfM landmarks. At least 25 anchors are required; poorly supported or inconsistent fits are rejected.
3. The calibrated depth is back-projected with camera intrinsics scaled to the depth-map resolution and lens-distortion correction. Each cloud is transformed with the inverse of its COLMAP world-to-camera pose.
4. World points are averaged into one voxel cloud, filtered and exported.

This uses shared camera geometry and per-view depth calibration rather than blindly registering arbitrary-scale clouds with ICP. No ICP refinement is currently applied. Unregistered views and unreliable depth fits are skipped and listed in alignment.json; the app does not force every cloud into the result.

A webcam alone cannot guarantee accurate or metric 3D geometry. Scale remains arbitrary, depth estimates can be biased, and aggressive foreground masking can remove real object parts. Keep the object rigid and still. Camera rotation in place, blur, smooth or reflective surfaces, moving backgrounds and weak overlap can prevent alignment. No unseen surfaces or watertight mesh are generated. For reliable measured geometry, a calibrated RGB-D or stereo sensor is a better input.

## Saved data

Each scan is stored under scans/<scan-id>/ next to server.py:

- images/: original RGB keyframes, up to 1280 pixels wide, for camera alignment.
- depth/: saved relative inverse-depth maps in compressed NPZ.
- masks/: binary foreground geometry masks (image-name.jpg.png). They are not used to hide camera-tracking background context.
- clouds/: local relative-depth PLY clouds before alignment.
- thumbs/: small RGB previews.
- run_<timestamp>/: COLMAP working database and sparse models.
- merged.ply: final aligned and filtered point cloud.
- merged_preview.json: a smaller browser preview.
- alignment.json: accepted/skipped views, per-view depth scale fits and filtering counts.
- sparse.ply, report.json, reconstruction.log: alignment geometry and logs.
- diagnostics.json: feature counts and verified-match connectivity from the COLMAP database/log.
- results.zip: available final exports, diagnostics and selected COLMAP sparse model. It excludes the raw per-frame input/depth/local-cloud directories.

The browser remembers the current scan ID, allowing refresh/reopen. Capture stays paused after reload; click Start to resume. New scan keeps old folders. Remove old folders manually when no longer needed. Scan data remains on this computer; only model weights need an initial network download.

Up to 200 changed views are saved. Camera frames are sent and saved at up to 1280 pixels wide, while depth inference stays at 640 pixels wide. The code adjusts camera calibration between those resolutions. A 640-pixel camera is not upscaled for tracking. Fusion retains at most 500,000 voxels; the report records points ignored at this memory limit. Uploaded photo limits from the older API do not affect live capture.

Starting another capture after a finished merge invalidates that merge's public exports once a new cloud is saved; earlier COLMAP run folders remain. Restarting during alignment marks the job interrupted and retains the collected data for retry. The server binds to 127.0.0.1.

## Verification

    python -m pip install httpx
    python -m unittest discover -p "test_*.py" -v
    node test_ui.cjs

Node.js is optional and used only for the interface-state test.

Tests cover live WebSocket frame processing with a test depth backend, per-frame cloud/depth/mask persistence, synthetic foreground removal, robust inverse-depth fitting, known camera transforms and fusion of three synthetic views. Additional pipeline tests cover COLMAP command orchestration, cancellation and preservation of sparse results. The JavaScript test checks capture backpressure, waiting for an in-flight frame before Finish, build locking and final downloads using fixtures.

An actual CPU Depth Anything model load/inference and masked local-cloud generation were also checked in the development environment. Real COLMAP alignment of a captured object and complete browser visual behavior still need validation with your camera/data. Fixture tests do not establish real reconstruction quality.

Model and command references:
- https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf
- https://colmap.github.io/cli.html
- https://colmap.github.io/format.html

## If the mapper cannot find an initial image pair

A message such as `No good initial image pair found` means the feature/matching commands ran, but usable overlapping camera geometry could not be initialized. It is not an installation or CUDA error by itself.

This release preserves original tracking images, uses a more sensitive SIFT peak threshold when supported, and enables guided matching when supported. It retains normal geometric initialization checks rather than forcing a bad camera solution. The new diagnostics report shows feature counts, verified pair counts, connected images and largest connected component when a database is available.

Stop the old server and replace the code, including video_capture.py, cloud_fusion.py, reconstruction.py and diagnostics.py. Restart and refresh the browser. Choose New scan before recapturing: older images saved by the previous release already have their background painted out, and no update can restore those removed pixels. Older depth maps are still supported with foreground-only depth anchors, but the saved dataset may remain insufficient.

Keep the object and background stationary while moving the camera. Use small view changes with overlap. A textured stationary surface behind or beside the object can now help camera tracking while remaining excluded from the output cloud by the foreground mask. Inspect that mask to ensure it retains the whole object.

A failed old scan can be retried without deleting its scan folder; stronger feature settings may help, but successful registration is not guaranteed. Only the log and report are insufficient to validate a repair on that dataset; the captured images/depth maps are needed.
