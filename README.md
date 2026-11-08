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
3. Move in small steps with the whole object visible. Automatic capture requires adequate sharpness/exposure and spatially distributed, geometrically consistent matches with the last saved image. These are tunable scene-continuity heuristics, not object-specific coverage measurements.
4. **Pause** stops saving while the local preview stays visible. Resume after returning toward the last accepted view. One already-submitted frame may finish before the pause acknowledgement; no later frames are accepted while paused.
5. **Finish & review** stops saving and opens thumbnails. Uncheck unrelated, obstructed, or moved-object frames. Imports go through this same review step, but do not receive automatic continuity validation.
6. **Confirm selected photos** requires at least 12 distinct frames. It freezes an input snapshot and settings; originals and excluded files remain unchanged. Capture mode offers `reviewed-images.zip`; reconstruction mode processes only the snapshot.

Manual **Capture this view** bypasses the cadence and duplicate-motion thresholds, but still obeys blur/exposure and continuity checks. If the scene no longer matches, return toward the last saved view or start a new scan. Camera switching starts a new preview-only session. Hiding the page pauses active capture. Tracking overlays are collapsed and unchecked by default.

The reviewed ZIP contains only selected working JPEGs in `images/` and a `selection.json` manifest. Extract it on the partner laptop and pass that extracted `images/` folder to the batch reconstruction command. Do not transfer the entire unreviewed capture folder as the default pipeline input.

## Samsung and iPhone through Camo

Use the same route for either phone:

**Phone Camo Camera → laptop Camo Studio → laptop browser selects Camo → local FastAPI server.**

See [the Samsung/Camo guide](docs/PHONE_SETUP.md). Camo documents Android support on Windows, but a physical Galaxy S26 Ultra capture has not been verified here. Keep the laptop server on localhost for this route. Opening localhost on a phone addresses the phone, not the laptop.

For photos already on disk, choose **Import photos**. JPEG, PNG, and WebP are accepted (HEIC is not). Limits: 100 files per selection, 25 MB per file, at most 16 megapixels and 8192 pixels per side. Original imported bytes are retained in `scans/<id>/originals/`; orientation-normalized working JPEGs (maximum long edge 1280) are stored in `images/`. Use one camera/lens and consistent resolution/orientation per scan. The server rejects changes in working dimensions instead of silently reusing incorrect calibration. Camo camera frames are stored as received browser JPEGs.

## Partner GPU build

Target: **Windows, RTX 3070 Ti Laptop / 8 GB VRAM, 32 GB RAM**. The development machine currently has usable integrated graphics only.

Follow [PARTNER_SETUP.md](docs/PARTNER_SETUP.md). After the CPU setup works, configure the actual COLMAP executable in ignored `config.local.toml`, run doctor, and explicitly enable reconstruction. The existing COLMAP pipeline now has an executable/CLI check, one active reconstruction per server process, a 30-minute default timeout, logs, and a fresh workspace for every attempt. Initial quality is `low`; fitting the target GPU remains to be measured.

```powershell
.venv/Scripts/python.exe -m scripts.doctor --require-reconstruction --output data/partner-doctor.json
./scripts/launch.ps1 -Mode reconstruct
```

Tool discovery is not proof of successful reconstruction. The current adapter produces an **experimental mesh**, not a verified textured or quad-topology model. UV texturing, Blender processing, SAM 2, and the 3D viewer remain unimplemented. Blender discovery in doctor is informational; the current export uses trimesh.

## Storage and configuration

- `config.example.toml`: documented defaults. Copy to ignored `config.local.toml` for machine-specific values; setup does this only if missing.
- `scans/<id>/images/`: saved working JPEGs, usable for a partner-machine reconstruction test.
- `scans/<id>/runs/<attempt>/`: separate COLMAP workspace for each reconstruction attempt.
- `scans/<id>/selections/<selection-id>/`: immutable reviewed input copies and settings manifest.
- `scans/<id>/reviewed-images.zip`: most recently confirmed selection for partner transfer.
- `scans/<id>/colmap.log`: most recent reconstruction log.
- `scans/<id>/mesh.glb` and `mesh.obj`: published only after successful mesh export validation.
- `sample-data/`, `scans/`, `data/`, `.venv/`, `.tools/`, local config, keys, and weights are ignored.

A failed rerun keeps the previous export on disk and does not advertise it as a new result. Closing the browser does not cancel an already-running subprocess; browser reconnection and cancellation are still pending. Stop the app before using the batch reconstruction command; concurrency control is within one server process, not across multiple app/CLI instances.

## Repository layout

```text
backend/app/       FastAPI capture, configuration, image validation, reconstruction adapter
backend/           Runtime/dev requirements and tested version constraints
frontend/          Existing HTML interface and camera helpers (no bundler required)
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

Tracking uses guessed intrinsics and can include background features; coverage and distance prompts are approximate, and scale is arbitrary. Keep the object stationary and avoid lens changes, Camo effects, auto-framing, or rotating the phone mid-scan. There is no guarantee of watertightness, quad topology, physical accuracy, or complete unseen surfaces.

Direct phone-browser access over LAN requires trusted HTTPS and access controls; that deployment is not configured by these scripts. The supported initial route is Camo into a laptop-local browser. After initial dependency installation, our runtime has no remote font or cloud API dependency; verify Camo's operation with the chosen connection during the offline rehearsal.
