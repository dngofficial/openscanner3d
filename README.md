# Local object scanner

Builds on the existing FastAPI + browser capture prototype. `PLAN.md` remains the scope authority; `docs/implementation_plan.md` is long-term background.

## Windows quick start

Install Python **3.11 x64**, clone/pull this repository, and run from its root:

```powershell
./scripts/setup.ps1
./scripts/launch.ps1 -Mode capture
```

If the Python launcher is unavailable, pass your installed interpreter: `./scripts/setup.ps1 -Python 'C:\actual\path\python.exe'`. Setup creates `.venv`, installs the constrained dependencies, runs CPU tests, and saves `data/doctor.json`. It does not install COLMAP, Blender, CUDA, or model weights. The existing local config is never overwritten.

Open **http://localhost:8000 on the laptop**, then press **Start camera**. Camo is preferred when available; the camera selector still supports other webcams. Refresh cameras after connecting a device. If permission is denied, allow camera access for this site and retry. Photo import works without a camera.

The default `capture` mode uses CPU OpenCV and saves frames without running reconstruction. A working discrete GPU is not required for capture or the automated tests.

## Controlled capture and review

1. **Start camera** opens a local live preview. It saves nothing.
2. Frame the stationary object, then press **Start scan**. Accepted frames appear in **Last saved photo** beside the live preview.
3. Move in small steps with the whole object visible. Automatic capture requires adequate exposure, sharpness and contrast, and spatially distributed, geometrically consistent matches with the last saved image. These are tunable scene-continuity heuristics, not object-specific coverage measurements.

   The quality checks run in this order: exposure, then sharpness, then contrast. A rejected frame reports only the first check that failed, with its measured value. Exposure fails only when more than 35% of pixels are pure white or pure black, or the average brightness is below 20/255. A bright table or a dark object is not rejected for its average brightness alone. Sharpness (Laplacian variance) must be at least 25, and contrast at least 12. The thresholds are in `backend/app/capture_quality.py`.
4. **Pause** stops saving while the local preview stays visible. Resume after returning toward the last accepted view. One already-submitted frame may finish before the pause acknowledgement; no later frames are accepted while paused.
5. **Finish & review** stops saving and opens thumbnails. Uncheck unrelated, obstructed, or moved-object frames. Imports go through this same review step, but do not receive automatic continuity validation.
6. **Confirm selected photos** requires at least 12 distinct frames. It freezes an input snapshot and settings; originals and excluded files remain unchanged. Capture mode offers `reviewed-images.zip`; reconstruction mode processes only the snapshot.

Manual **Capture this view** bypasses the cadence and duplicate-motion thresholds, but still obeys blur/exposure and continuity checks. If the scene no longer matches, return toward the last saved view or start a new scan. Camera switching starts a new preview-only session. Hiding the page pauses active capture. Tracking overlays are collapsed and unchecked by default.

## Turning the object by hand and AI object selection

Choose **How are you capturing?** before saving any photos:

- **Object still · I move around it** (default): the orbit workflow above, with scene-continuity checks.
- **I turn the object by hand**: hold the phone and turn the object a little between shots. The background stays still while the object moves, so whole-scene continuity does not apply. Instead, frames are saved when they pass the quality checks, are at least 0.7 s apart, and differ enough from the last saved view (`TURN_MIN_CHANGE`). Live camera-pose tracking is skipped.

In this mode, COLMAP has to ignore everything except the object, so **AI object masks are required**. Confirm refuses a turned-object selection without them. In the review panel:

1. Click a thumbnail to open it in the prompt canvas. Clicking a thumbnail selects it for prompts; the checkbox controls inclusion.
2. Click on the object. Click every distinct part you want kept, such as the top *and* the sides: on the reviewed set, one click on the top face selected only the top face. Shift-click your hand or the table to mark them as not the object. You can add clicks on several photos, for example after flipping the object.
3. **Run AI selection** runs SAM 2.1 tiny on this computer (see [models/README.md](models/README.md)) over the selected photos. It is a separate process, so the server never loads PyTorch, and GPU memory is released when it exits. It shares the single heavy-task lock with reconstruction.
4. Each thumbnail shows its own mask: the object tinted, everything else dimmed, and the mask's share of the photo. Untick frames where the mask is wrong, or add clicks and run it again. Frames with no object found are flagged.

SAM 2's video predictor propagates your clicks through the sequence, but each frame's mask is predicted from that frame's own pixels; no mask is copied between views. A selection whose photos are not all masked is refused. AI selection works in orbit mode too and is optional there.

On confirm, the masks are frozen with the selection and shipped in the reviewed ZIP (`object_masks/<image>.png` plus `segmentation.json`, which records the clicks, device, runtime and checkpoint SHA-256). For reconstruction:

- **Feature extraction:** each image's COLMAP mask is its own object mask combined with the watermark mask, so camera poses come from object features only.
- **After meshing:** every mesh vertex is projected into every masked photo using the registered, distorted COLMAP camera (`isolation.py`). A vertex is kept if at least 2 photos see it and at least 70% of those put it inside the object mask. Small fragments are then dropped.
- **Output:** both meshes are kept. `mesh.glb` is unfiltered; `object.glb` is the AI-isolated proposal. Compare them.

There is no occlusion test: background hidden behind the object in every view can survive isolation. Dense stereo still sees the whole image. The isolation code is tested against known synthetic camera geometry and OpenCV's projection, but has not yet run on a real COLMAP reconstruction.

The reviewed ZIP contains only selected working JPEGs in `images/` and a `selection.json` manifest. Extract it on the partner laptop and pass that extracted `images/` folder to the batch reconstruction command. Do not transfer the entire unreviewed capture folder as the default pipeline input.

## Samsung and iPhone through Camo

Use the same route for either phone:

**Phone Camo Camera → laptop Camo Studio → laptop browser selects Camo → local FastAPI server.**

See [the Samsung/Camo guide](docs/PHONE_SETUP.md). Camo documents Android support on Windows, but a physical Galaxy S26 Ultra capture has not been verified here. Keep the laptop server on localhost for this route. Opening localhost on a phone addresses the phone, not the laptop.

For photos already on disk, choose **Import photos**. JPEG, PNG, and WebP are accepted (HEIC is not). Limits: 100 files per selection, 25 MB per file, at most 16 megapixels and 8192 pixels per side. Original imported bytes are retained in `scans/<id>/originals/`; orientation-normalized working JPEGs (maximum long edge 3200, so a 12 MP photo keeps most of its detail) are stored in `images/`. COLMAP's `quality` preset downsamples again internally, so use `medium` or `high` on the partner laptop to benefit from imported photos; check `colmap.log` for the size it actually used. Use one camera/lens and consistent resolution/orientation per scan. The server rejects changes in working dimensions instead of silently reusing incorrect calibration. Camo camera frames are stored as received browser JPEGs.

## Partner GPU build

Target: **Windows, RTX 3070 Ti Laptop / 8 GB VRAM, 32 GB RAM**. The development machine currently has usable integrated graphics only.

Follow [PARTNER_SETUP.md](docs/PARTNER_SETUP.md). After the CPU setup works, configure the actual COLMAP executable in ignored `config.local.toml`, run doctor, and explicitly enable reconstruction. The existing COLMAP pipeline now has an executable/CLI check, one active reconstruction per server process, a 30-minute default timeout, logs, and a fresh workspace for every attempt. Initial quality is `low`; fitting the target GPU remains to be measured.

```powershell
.venv/Scripts/python.exe -m scripts.doctor --require-reconstruction --output data/partner-doctor.json
./scripts/launch.ps1 -Mode reconstruct
```

Camo's free tier burns a watermark into the bottom-right of every frame. Because it never moves, it produces zero-motion matches that conflict with real camera motion. By default, `feature_mask_regions` excludes that corner from live SIFT tracking. The reconstruction adapter also writes one COLMAP mask per image (`runs/<attempt>/masks/<image>.png`) and passes `--mask_path`. Reconstruction refuses to start if the installed COLMAP lacks that option, and doctor reports `mask_path_supported`. On the reviewed 77-frame Camo set, masking cut near-zero-motion SIFT matches between frames four apart from 87 to 7. That measures feature cleanup, not reconstruction success. Masks affect feature extraction only, not dense stereo. Set `feature_mask_regions = []` for photos without a watermark.

Tool discovery is not proof of successful reconstruction. The adapter produces an **experimental mesh** with vertex colours, plus a UV-textured GLB when OpenMVS is installed. No real reconstruction, isolation or texture has yet been produced by this code, since the development laptop has no COLMAP or CUDA GPU; that is the partner laptop's acceptance run. There is no quad remeshing and no hole filling. Blender discovery in doctor is informational; the current export uses trimesh.

## Storage and configuration

- `config.example.toml`: documented defaults. Copy to ignored `config.local.toml` for machine-specific values; setup does this only if missing.
- `scans/<id>/images/`: saved working JPEGs, usable for a partner-machine reconstruction test.
- `scans/<id>/runs/<attempt>/`: separate COLMAP workspace for each reconstruction attempt.
- `scans/<id>/selections/<selection-id>/`: immutable reviewed input copies and settings manifest.
- `scans/<id>/reviewed-images.zip`: most recently confirmed selection for partner transfer (with `object_masks/` when AI selection ran).
- `scans/<id>/object_masks/`: latest AI selection (`masks/`, `previews/`, `segmentation.json`); `segmentation.log` holds its process output.
- `scans/<id>/object.glb` and `object.obj`: AI-isolated mesh, published only with a mesh from the same attempt.
- `scans/<id>/textured.glb`: UV-textured model from OpenMVS, published only with a mesh from the same attempt.
- `scans/<id>/job.json`: background job state and stage; `fallback.json`: saved-fallback label and model hashes.
- `scans/<id>/colmap.log`: most recent reconstruction log.
- `scans/<id>/mesh.glb` and `mesh.obj`: published only after successful mesh export validation.
- `sample-data/`, `scans/`, `data/`, `.venv/`, `.tools/`, local config, keys, and weights are ignored.

A failed rerun keeps the previous export on disk and does not advertise it as a new result. Stop the app before using the batch reconstruction command; concurrency control is within one server process, not across multiple app/CLI instances.

## Processing, results and the 3D viewer

In `reconstruct` mode, **Confirm selected photos** starts a background job and returns immediately. The job does not depend on the browser connection:

- **Status** is written atomically to `scans/<id>/job.json`, and the page polls `GET /api/scans/<id>` every 2 seconds. It shows the state (`running`, `ready`, `failed`, `cancelled`, `interrupted`) and the pipeline's real stage names. During COLMAP's step, it also shows the latest section heading found in `colmap.log`, such as "feature matching" or "dense stereo". There are no percentages or time estimates.
- **Cancel processing** (`POST /api/scans/<id>/cancel`) stops the whole process tree, not just a label. A test runs a `.bat` wrapper that launches a child process and confirms both are gone. **Cancel AI selection** does the same for SAM 2.
- **Closing or reloading the page** leaves the job running. On load, the page reattaches to a running job.
- **Server restart:** a job left marked `running` becomes `interrupted`. Start a clean rerun from review. Stopping the server cancels its running job.
- **One heavy task at a time:** reconstruction and AI selection share a lock, and a second request gets a clear "busy" message.

**Results** lists earlier scans with their state, downloads, and a **View** button. The viewer is Three.js 0.170.0, vendored in `frontend/vendor/three` (MIT licence included; npm package SHA-1 `6087f97aab79e9e9312f9c89fcef6808642dfbb7`), so no CDN is used at runtime. It shows `textured.glb`, `object.glb` and `mesh.glb`:

- drag to rotate, scroll or pinch to zoom, right-drag to pan;
- it reports the triangle count and whether the model has a UV texture or only vertex colours;
- it links each file for download.

Viewer controls, model switching, reattaching after reload, cancellation and the interrupted-on-restart path were checked in the browser. COLMAP was a fake script and the models were synthetic GLB fixtures, because this laptop has no COLMAP or CUDA GPU.

### Saved fallback for the demo

After a real scan succeeds, label it as the fallback:

```powershell
.venv/Scripts/python.exe -m scripts.mark_fallback SCAN_ID --label "Power bank, 60 photos, partner laptop, 2026-11-08"
```

This writes `fallback.json` with the label and the SHA-256 of each model. Results and the viewer then show a **Saved fallback** badge, so it is never presented as a new scan. Use `--remove` to clear the label.

### Texturing (OpenMVS) and GLB export

COLMAP does not texture meshes. When OpenMVS is found (`openmvs_bin` in `config.local.toml`, or on PATH), the steps after meshing are:

1. Write COLMAP's dense model as text (`model_converter`).
2. `InterfaceCOLMAP` converts it to an OpenMVS scene.
3. `TextureMesh` UV-maps the photos onto the AI-isolated object, or onto the whole mesh if there is no isolated object. The mesh is first cleaned without breaking UVs: degenerate and duplicate faces and unused vertices are removed.
4. The textured OBJ becomes `textured.glb`. Blender is used when found (`scripts/blender_export_glb.py`: removes loose geometry and degenerate faces, then exports glTF); otherwise trimesh. The GLB is reloaded and must contain UVs and an embedded base-colour texture.

Each tool's options are checked first. Texturing failures leave the untextured meshes available, and a stale `textured.glb` is never kept beside a newer mesh. Doctor reports a `texturing` section. **Not yet verified:** none of OpenMVS, Blender or a real texture has run. Only the orchestration and the trimesh texture round trip are tested.

## Repository layout

```text
backend/app/       FastAPI capture, configuration, image validation, reconstruction adapter
backend/           Runtime/dev requirements and tested version constraints
frontend/          HTML interface, camera/selection helpers, Three.js viewer (vendored, no bundler)
scripts/           Windows setup/launch, environment doctor, batch reconstruction
tests/             CPU capture/backend tests and Node camera-helper tests
docs/              Partner setup, phone setup, longer-term plan
models/            Checkpoint documentation; weights remain local
demo/              Real-data validation and fallback checklist
```

## Verification

```powershell
.venv/Scripts/python.exe -m tests.check_capture
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/camera.test.mjs
.venv/Scripts/python.exe -m scripts.doctor --output data/doctor.json
```

Node is optional for the application and used only for camera-helper tests. CPU tests exercise imports, capture, malformed input, orientation rejection, configuration, exclusive execution, failed reruns, and actual subprocess timeout handling. They do not prove COLMAP, CUDA, textures, or physical camera compatibility. Dependency constraints were resolved on Windows x64 / Python 3.11.18. The current Starlette test client emits an httpx deprecation warning; the tests still pass.

## Current limitations

`sparse.ply` is the browser's live tracking map: a few hundred points from guessed intrinsics. It is not COLMAP output and not a mesh; converting it to OBJ/STL only produces point markers. Tracking uses guessed intrinsics and can include background features; coverage and distance prompts are approximate, and scale is arbitrary. Keep the object stationary and avoid lens changes, Camo effects, auto-framing, or rotating the phone mid-scan. There is no guarantee of watertightness, quad topology, physical accuracy, or complete unseen surfaces.

Direct phone-browser access over LAN requires trusted HTTPS and access controls; that deployment is not configured by these scripts. The supported initial route is Camo into a laptop-local browser. After initial dependency installation, our runtime has no remote font or cloud API dependency; verify Camo's operation with the chosen connection during the offline rehearsal.
