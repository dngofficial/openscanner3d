# PLAN.md — Local AI-Assisted 3D Scanner

## Project objective

Build a hackathon demo that turns phone photographs of a real object into a textured, downloadable 3D mesh. Phone and computer communicate over local Wi-Fi. All inference and reconstruction run on the computer; internet is needed only for initial dependency/model downloads.

**Demo flow:** capture/import photos → select object with AI → reconstruct → inspect model → download GLB.

Assume a 24–48-hour event, one processing computer, and one active scan. The current machine split is recorded below; team size and remaining event time are not yet confirmed. Live means camera preview and guidance; the final mesh is generated after capture, not continuously. This document supersedes the earlier production-scale implementation plan for hackathon scope.

### Current machine split (November 7, 2026)

- Development laptop: integrated graphics usable; CPU capture and orchestration tests can run here.
- Partner reconstruction laptop: Windows, RTX 3070 Ti Laptop with 8 GB VRAM, 32 GB RAM (user-reported; real reconstruction validation pending).
- Initial phone route: Camo Camera on iPhone or Samsung Galaxy S26 Ultra feeds Camo Studio on the laptop; the laptop-local browser selects the Camo virtual webcam. Partner reports the initial iPhone setup works; Samsung physical-device acceptance remains pending.
- Implemented foundation: capture/reconstruct modes, configured tool paths, constrained Python dependencies, Windows setup/launch scripts, doctor report, bounded reconstruction attempts, and photo-import fallback. See `docs/PARTNER_SETUP.md` and `docs/PHONE_SETUP.md`.
- Phase 0's real GPU/textured-model gate moves to the partner machine. CPU tests or a synthetic mesh export do not satisfy it. Segmentation, texturing and export adapters, the viewer and job handling are implemented (see status below); none has produced a real reconstruction yet.

### Current capture implementation status

This is progress within the existing plan, not a replacement roadmap. The capture work supports Phase 1's frozen inputs and Phase 3's start/pause/finish and thumbnail review requirements while the partner validates Phase 0.

- [x] Separate camera preview from server-authorized saving with Start scan, Pause/Resume, and Finish & review.
- [x] Show the last accepted photo alongside the live preview.
- [x] Reject poor initial image quality and weak/inconsistent scene matches during automatic capture; manual capture cannot bypass these checks.
- [x] Review thumbnails and exclude unwanted frames without deleting original files.
- [x] Freeze only the confirmed selection/settings for reconstruction and provide a reviewed image ZIP for partner transfer.
- [x] Verify preview/pause behavior, unrelated-scene rejection, and selected-only input snapshots in automated tests.
- [x] Verify browser import → thumbnail review → exclusion → confirmation → ZIP link using synthetic images.
- [x] Mask the static Camo watermark during live SIFT and COLMAP feature extraction (`--mask_path`, refused if unsupported). Verified on the real reviewed set: mask placement plus fewer zero-motion matches. Its effect on COLMAP registration is not yet measured.
- [x] Raise the import working size to 3200 px so native phone photos keep detail. Give each capture-quality rejection a single cause with its measured value; judge exposure by clipped pixels.
- [x] Add a turned-object capture mode for a handheld phone: no scene-continuity check, steady cadence, AI masks required before confirm.
- [x] Local SAM 2.1 tiny segmentation in a separate process, using the video predictor so clicks on one or more frames propagate. Each frame gets its own predicted mask with a visible overlay; prompts, device and checkpoint hash are recorded. Real run on the reviewed set: 77 frames in 180 s on CPU, and 14 frames through the UI in 31.5 s.
- [x] Masks frozen with the selection and shipped in the ZIP. COLMAP features come from object pixels only. After meshing, vertices are filtered by projection into the masks (`object.glb`), keeping the unfiltered mesh for comparison. Projection is tested against synthetic cameras and OpenCV.
- [x] Background reconstruction job with atomic `job.json`, 2 s polling, real stage names (including COLMAP's own log headings), process-tree cancellation, reattach after reload, interrupted-on-restart, Results list and labelled saved fallback (`scripts/mark_fallback.py`). Checked in the browser with a fake COLMAP script.
- [x] Three.js viewer (vendored 0.170.0, no CDN) with rotate, zoom, pan, model switching and downloads. Checked in the browser with synthetic textured and vertex-coloured GLBs.
- [x] OpenMVS texturing adapter (InterfaceCOLMAP + TextureMesh, option checks, UV-safe cleanup) and textured GLB export (Blender script, trimesh fallback, texture validated on reload). Orchestration tested; not yet run with real OpenMVS or Blender.
- [ ] Validate object masks and isolation on a real COLMAP reconstruction (partner laptop), for both a turned-object capture and an orbit capture.
- [ ] Recapture with a full orbit at 2–3 heights. The first reviewed set covers only about 90–120° of a dark, low-texture object.
- [ ] Rehearse the new controls with Samsung/Camo on a stationary textured object; measure acceptance/rejection behavior and tune thresholds if necessary.
- [ ] Complete real GPU reconstruction and independently inspect the textured result on the partner machine (Phase 0 remains open).

Continuity checks use full-scene feature matching and do not establish object-specific overlap, camera coverage, or reconstruction success. Imported photos require human review. Existing scan files are preserved; new review sessions do not automatically reopen older sessions.

## Success criteria

- A real set of overlapping phone photos produces a textured GLB locally.
- A pretrained segmentation model runs locally and displays a visible object mask.
- AI masks contribute to object isolation in the exported geometry through a validated integration; an overlay alone is not evidence of AI-assisted reconstruction.
- A browser viewer rotates/zooms the actual generated model.
- Downloaded GLB opens in an independent application with textures intact.
- The whole flow works without internet once dependencies are installed.
- A saved demonstration dataset and its actual result are available as clearly labeled backups.

## Scope

| Required | Stretch | Deferred |
| --- | --- | --- |
| Photo import, real local reconstruction | Phone browser camera capture | Native apps and phone-only reconstruction |
| Local prompted segmentation | Automatic capture and blur warnings | Continuous mesh updates |
| Validated segmentation-assisted isolation | Multiple prompted views | Training reconstruction models |
| Processing stages and errors | Simple scan history | Accounts, cloud storage, remote access |
| Three.js viewer and GLB export | Basic crop tool | Production job systems and crash recovery |
| LAN phone workflow, at least file upload | Reliable camera HTTPS setup | STL, CAD accuracy, unseen-surface completion |

Camera capture is the preferred presentation feature. Photo import remains available so certificate or browser problems cannot block the core pipeline. If the event requires live camera scanning, promote camera support to required immediately.

## Stack and architecture

| Component | Choice |
| --- | --- |
| UI | React, TypeScript, Vite |
| Viewer | Three.js, optionally React Three Fiber/Drei |
| API | FastAPI, Pydantic |
| Image checks | Pillow and OpenCV |
| ML | Local SAM 2 image predictor with a supported small checkpoint |
| Reconstruction | COLMAP, plus a verified texturing backend |
| Export | Blender background script to GLB |
| Persistence | Scan folders and atomic JSON status files |
| Execution | One bounded background worker; one API process |

Serve the built frontend and API from the same origin. In development, proxy `/api` through Vite. Avoid Redis, Celery, Docker, databases, and orchestration unless an existing working setup makes them cheaper than the simple approach.

**Data route:** phone/desktop browser → local API → scan folder → worker → segmentation/reconstruction/export → GLB → browser viewer.

## Phase 0 — Prove the difficult part first

Timebox to the first 4–6 hours of a 48-hour event, or 2–3 hours of a 24-hour event.

- [ ] Record OS, GPU/VRAM, RAM, tool versions, and available disk space.
- [ ] Download dependencies and model checkpoint; record source and checkpoint hash.
- [ ] Choose a matte, textured, stationary object in even lighting.
- [ ] Capture roughly 60–100 overlapping views at several heights as an initial target; tune rather than treating this count as a guarantee.
- [ ] Run sparse reconstruction, dense reconstruction, meshing, texturing, and GLB export manually.
- [ ] Open the GLB in Blender and a browser viewer.
- [ ] Record runtime and inspect missing surfaces, textures, and scene clutter.
- [x] Run a real local segmentation prediction and save its mask (SAM 2.1 tiny, CPU; see `models/README.md`).

**Gate:** verify the full textured-mesh pipeline on the actual machine. CPU sparse reconstruction does not imply CPU dense reconstruction is supported. Check the installed build and selected backend. Do not assume every COLMAP build has the same commands or texturing capability.

If the selected build lacks texturing, evaluate a compatible OpenMVS path, respecting its license and setup cost. If a complete backend still cannot run within the timebox, expose the blocker early. Do not silently change to cloud processing or substitute a canned model for a reconstruction.

## Phase 1 — Build the smallest complete application

1. Create a scan folder with a server-generated UUID.
2. Import JPEG/PNG files with multipart upload. Normalize orientation, decode safely, and preserve original images.
3. Start a processing task and return HTTP 202 immediately.
4. Run the validated CLI pipeline from a separate worker execution context.
5. Persist stage, timestamps, safe error messages, and output paths.
6. Poll status approximately every two seconds.
7. Serve the generated GLB and display it with orbit controls.
8. Add download and reset/new-scan actions.

Do not build the camera UI before this vertical slice works. Use stage labels rather than invented percentage completion or unsupported remaining-time estimates.

## Phase 2 — Make AI visible and useful

### Segmentation interaction

- Display a selected photograph and accept a bounding box or positive/negative clicks.
- Run the pretrained model on the computer; overlay the returned mask.
- Provide accept and retry controls. Keep elaborate brush editing out of scope.
- Store image ID, prompts, checkpoint identity, and full-resolution aligned mask.
- Prompt additional images separately if needed; never reuse one view's mask for another camera angle.

### Concrete integration: segmentation-assisted 3D isolation

Keep pose estimation on full RGB images for the stationary-object baseline. Background features can help registration. After reconstruction, use masks to select the object geometry:

1. Read camera intrinsics and poses for each prompted, registered image.
2. Project reconstructed points into those images using the correct camera model and image dimensions.
3. Test projected pixels against the accepted masks. Preserve distortion/undistortion mappings; original-image masks cannot simply be indexed with undistorted coordinates.
4. Use points supported by multiple useful views to propose an object region. Ignore views where the point is outside the image; account for occlusion when depth data is available.
5. Preview the proposed region and require user confirmation; masks define view volumes, so hidden background and loose boundaries can survive naive intersection.
6. Filter dense points or triangles using the validated region, then mesh and texture, or preserve the texture coordinates when selecting existing mesh faces.
7. Compare the unfiltered and isolated result on the demonstration object.

Treat this integration as its own feasibility check. A single mask and a simple bounding box can provide a coarse region proposal, but must be described as such. If geometric mask integration is not ready, ship the model preview with an explicit “AI selection preview” label and report the limitation; do not claim the export used the masks.

No training is needed. Blur metrics and photogrammetry alone should not be presented as the trained ML contribution. Segmentation provides the explicit ML feature.

## Phase 3 — Phone capture and local networking

- Connect phone and computer to the same private network without client isolation.
- Display the computer's LAN URL; optionally generate a QR code locally.
- Keep the computer backend running and awake throughout the demo.
- First verify phone photo-file uploads.
- For live browser capture, configure locally trusted HTTPS with a certificate covering the actual LAN hostname/IP and install/trust the public CA certificate on the phone. Keep private keys off the phone and out of Git.
- Do not depend on plain HTTP at a LAN IP, warning bypasses, public tunnels, or remote certificate services for camera access.
- Request rear camera, display actual delivered resolution, and use user-triggered capture first.
- Add start/pause/finish, image count, thumbnail review, and retry for failed uploads.
- Bound pending image memory; stop capture before exhausting it. Warn the user not to close the tab while uploads are pending.
- Stop camera tracks on exit. Handle denied permission with a photo-import fallback.

Keep network controls proportional: private demo LAN only, restricted origins/hosts, a per-launch access token or equivalent simple shared access, file limits, and no public port forwarding. A full pairing/account system is deferred.

## Minimal API

| Method | Route | Result |
| --- | --- | --- |
| GET | /api/health | Availability and basic capability status |
| POST | /api/scans | Create scan, return ID |
| POST | /api/scans/{id}/images | Upload images; return accepted IDs |
| GET | /api/scans/{id} | Images, job state, warnings, result availability |
| POST | /api/scans/{id}/segment | Queue prediction for an image and prompts |
| POST | /api/scans/{id}/reconstruct | Freeze selected inputs and start processing |
| POST | /api/scans/{id}/cancel | Request cancellation |
| GET | /api/scans/{id}/model | Stream completed GLB |

Use one active heavy task at a time, including segmentation. Return 409 if busy rather than building a general queue. Job states: `idle`, `running`, `ready`, `failed`, `cancelled`, `interrupted`. Stage is a separate field. On restart, mark unfinished tasks interrupted and allow a clean rerun; automatic recovery is deferred.

Server-generated paths only. Reject traversal, unsupported files, oversized files, and extreme decoded dimensions. Do not use uploaded filenames in shell commands. Run tools with argument arrays, capture logs, verify outputs, and terminate child processes on cancellation. A zero exit code alone does not prove a valid model.

## Suggested files

```text
project/
  PLAN.md
  AGENTS.md
  README.md
  frontend/src/
    App.tsx
    api.ts
    components/PhotoImport.tsx
    components/CameraCapture.tsx
    components/ObjectSelection.tsx
    components/ProcessingStatus.tsx
    components/ModelViewer.tsx
  backend/app/
    main.py
    schemas.py
    storage.py
    worker.py
    segmentation.py
    reconstruction.py
    isolation.py
  scripts/
    doctor.py
    launch.py
    export_glb.py
  tests/
  config.example.toml
  models/README.md
  demo/README.md
```

Store actual images, masks, work files, JSON state, logs, and outputs under `data/scans/<uuid>/`. Git-ignore data, weights, certificates, keys, and local environment files. Keep any permitted sample data separate from user scans.

## 48-hour suggested schedule

| Hours | Deliverable |
| --- | --- |
| 0–6 | Hardware validation, actual textured GLB, local model inference |
| 6–16 | Upload → backend pipeline → viewer → download |
| 16–26 | Object selection UI and validated mask-assisted isolation |
| 26–34 | Phone workflow, HTTPS, camera capture |
| 34–40 | Errors, cancellation, offline check, simple visual polish |
| 40–48 | Feature freeze, rehearsals, backup assets, pitch |

For 24 hours, halve the timeboxes and keep manual photo capture/import. Limit AI interaction to prompted views and remove optional capture automation. If the team has multiple members, ownership can be frontend, reconstruction, and ML/integration, but agree on API payloads early and reserve integration time.

## Feature-cut order

Cut automatic frame sampling, blur warnings, QR convenience, scan history, extra viewer controls, and secondary export formats before cutting the actual reconstruction or local ML. Keep one tested object and one supported machine. Prefer a reliable complete demo over broad hardware support.

If reconstruction is too slow for stage time, start a real run, show its actual progress, and switch to a clearly labeled earlier result of the same pipeline. Never relabel a cached result as a fresh scan.

## Verification checklist

- [ ] One real capture set completes the entire pipeline on the target computer.
- [ ] AI mask is generated locally from the supplied image.
- [ ] Exported object isolation actually uses masks, or the preview-only limitation is explicit.
- [ ] GLB contains its textures and opens outside the app.
- [ ] Camera failure leads to photo import rather than a dead end.
- [ ] Busy, invalid upload, insufficient overlap, missing model, and missing tool failures are understandable.
- [ ] Cancel stops reconstruction subprocesses.
- [ ] API stays responsive while reconstruction runs.
- [ ] No cloud requests occur in a full run with internet disconnected and LAN retained.
- [ ] Backup example is clearly labeled and matches its documented inputs.
- [ ] README records exact setup commands, versions, hardware, launch URL, limitations, and demo steps.

Use focused automated tests for upload limits, job exclusivity, mask coordinate projection, and artifact validity. Run frontend build/type checks. A real pipeline smoke test is required; mocks only verify application plumbing.

## Demo narrative

1. Show the physical object and capture/import its photos.
2. Tap/select the object and reveal the local AI mask.
3. Start reconstruction and explain that geometry comes from multiple views.
4. Show the isolated textured object in the viewer.
5. Download GLB and open it independently.
6. Explain that inference and image processing stayed on the local computer.

Suggested pitch: “AI-assisted object scanning that turns your phone photos into reusable 3D assets while keeping processing on your own computer.”

## References and implementation checks

Use official documentation and verify commands against the versions actually installed. These are reference links, not proof that the user's machine supports a particular pipeline.

- https://colmap.github.io/tutorial.html
- https://colmap.github.io/install.html
- https://colmap.github.io/faq.html
- https://github.com/cdcseacave/openMVS
- https://github.com/facebookresearch/sam2
- https://github.com/FiloSottile/mkcert
- https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- https://threejs.org/docs/

This is an implementation plan; no application code or hardware validation has been completed by creating this document.
