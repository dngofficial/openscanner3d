# Photo-to-3D object reconstruction

This version replaces the live camera scanner with a batch-photo workflow. Select photos from different angles, inspect image quality, and build a point cloud or mesh with COLMAP. The app runs at http://localhost:8000.

## Install and start

Use Python 3.10 or newer. In this folder:

    python -m venv .venv

Windows PowerShell:

    .venv\Scripts\Activate.ps1

Linux / macOS:

    source .venv/bin/activate

Then:

    python -m pip install -r requirements.txt
    python server.py

Open http://localhost:8000 in your browser. If replacing an earlier version, stop the old server first and refresh the page after restarting. Use a fresh virtual environment to avoid conflicts with the old OpenCV package.

### Install COLMAP separately

The Python requirements do not install COLMAP. Install a COLMAP release/build for your operating system from https://colmap.github.io/install.html and ensure its command-line launcher is available.

Check it in the same terminal that will run the server:

    colmap -h

On Windows the launcher may be COLMAP.bat. If it is not on PATH, point the server to it before starting:

    # Windows PowerShell (replace with your actual path):
    $env:COLMAP_EXE = 'C:\Tools\COLMAP\COLMAP.bat'
    python server.py

    # Linux / macOS (replace with your actual path):
    export COLMAP_EXE=/path/to/colmap
    python server.py

The header shows whether a launcher was found; this is not a GPU or full installation health check. Missing shared libraries, CUDA support or driver issues will appear in the build log.

CPU mode runs sparse feature extraction, matching and camera alignment on the CPU and exports a sparse point cloud. Mesh mode adds COLMAP PatchMatch stereo, fusion and Poisson meshing. That dense pipeline requires a CUDA-capable COLMAP build and compatible NVIDIA GPU. A standard macOS or CPU-only installation can use sparse mode. The code checks command help to support both newer FeatureExtraction/FeatureMatching options and older SiftExtraction/SiftMatching names.

## Use it

1. Click the upload area or drop photos onto it. JPEG, PNG and WebP are supported. Convert HEIC/RAW files first.
2. Start with about 20–80 photos of one stationary object. The app allows at least 3 to attempt alignment; three photos rarely provide a complete object.
3. Click a thumbnail to inspect it. Cyan dots and the amber feature-region box indicate available image detail, including background detail. Quality warnings are advisory and never silently discard your images.
4. Remove accidental or poor views using ×. Exact duplicates after normalization are skipped.
5. Choose a sparse CPU point cloud or dense mesh. Only enable shared-camera calibration when all photos use the same camera, lens, fixed zoom and matching dimensions.
6. Click Build 3D model. Progress reports pipeline stages, not estimated time; large sets can take a long time.
7. After alignment, rotate the sparse point-cloud preview with a drag, or zoom using a wheel/pinch. The preview is a sample of up to 6,000 sparse points, not a rendered mesh.
8. Download the results. Mesh GLB/OBJ/PLY files appear only after a successful mesh build. Sparse PLY, logs and a report remain available if the later dense stage fails.

For a useful set, walk around the object while keeping it still. Take overlapping views all around it at a middle height and again from higher angles. Translation between camera positions is important; rotating the camera in place provides insufficient depth information. Keep lighting soft and consistent. A matte, textured object is easier to reconstruct than a smooth, reflective, transparent or moving one. A few unrelated front/side/back images are usually not enough.

The background can be reconstructed along with the object. The app does not remove the background, identify object boundaries, invent unseen surfaces, guarantee a watertight mesh or establish real-world scale. Crop/clean the result in Blender or MeshLab. Exported meshes may contain vertex colors if produced upstream; UV texture generation is not included.

## What runs

    Photo upload and orientation normalization
      → CPU feature extraction
      → exhaustive feature matching
      → incremental camera alignment / sparse geometry
      → largest connected model selection
      → sparse PLY + interactive point-cloud preview
      → optional image undistortion
      → optional CUDA depth estimation and fusion
      → optional Poisson mesh
      → optional OBJ + GLB export

Builds run in a background worker so the page remains responsive. Only one reconstruction runs at a time; additional photo sets queue. Cancel stops the current COLMAP process and retains the uploaded photos. Restarting the server marks unfinished jobs as interrupted so they can be started again. Each retry uses a new workspace/database and retains earlier work folders.

## Files and limits

Everything stays under scans/<scan-id>/ next to server.py. The browser remembers the latest photo-set ID, so refreshing can reopen it. New photo set leaves old folders on disk. Delete old folders manually when no longer needed.

- Maximum: 200 photos, 20 MB per uploaded file, 36 megapixels per input photo.
- Images are rotated using EXIF orientation, converted to RGB JPEG and resized to fit 3,200 × 3,200. Original uploads are not retained; use your originals as the archive.
- Camera intrinsics are estimated by COLMAP from the normalized images; the app does not carry over original camera EXIF metadata.
- inputs: images/, thumbs/, session.json
- working files: run_<timestamp>/database.db, sparse/, dense/
- exports when available: sparse.ply, dense.ply, mesh.ply, mesh.obj, mesh.glb
- diagnostics: reconstruction.log, report.json, preview.json
- results.zip contains available exports, diagnostics and the selected COLMAP sparse model; it does not include all input photos or dense intermediate files.

Image changes invalidate current exports so stale results are not presented as a new build. Prior run folders remain available. Feature matching is exhaustive, so larger sets increase work substantially. One COLMAP command is allowed three hours by default; advanced users may set COLMAP_STEP_TIMEOUT to a number of seconds.

The server binds to 127.0.0.1. No webcam permission is required. Keep the terminal open while building.

## Verification

    python -m pip install httpx
    python -m unittest -v test_photo_pipeline.py

The tests check upload validation, orientation, duplicate handling, safe filenames, deletion, build locking, cancellation and interrupted-session restoration. Fixture COLMAP outputs verify stage orchestration, old/new command options, largest-model selection, preview parsing, mesh export and sparse-result preservation after a dense failure. They do not validate actual camera alignment or reconstruction quality from real photos. COLMAP and a photo dataset are required for that.

Official pipeline documentation: https://colmap.github.io/cli.html
Dense/CPU requirements: https://colmap.github.io/faq.html

Optional JavaScript state checks (requires Node.js, no npm packages): `node test_ui.cjs`. These exercise upload/gallery state, photo-overlay rendering calls, build locking, download links and removal using DOM/API fixtures. They do not replace a full browser visual test.
