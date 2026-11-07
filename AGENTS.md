# AGENTS.md

## Purpose

Build the application described in `PLAN.md`: phone photos become a locally reconstructed, textured, exportable 3D object, with a meaningful pretrained ML segmentation feature.

This file applies to this repository and its subdirectories. Follow explicit user instructions first. Use `PLAN.md` for scope and acceptance criteria. The earlier long-term `docs/implementation_plan.md` is background; do not reintroduce its production features into this hackathon.

## Product constraints

- Run inference and reconstruction locally on the user's computer.
- Phone and computer may communicate over local Wi-Fi. Phone-only reconstruction is not the current scope.
- No cloud APIs, hosted inference, public tunnels, analytics, remote fonts, or runtime CDN dependencies.
- Initial dependency/model downloads are allowed; scans must work offline afterward.
- Live camera preview is supported as time permits. Continuous live mesh generation is out of scope.
- Produce an actual textured GLB. A point cloud, mask, turntable video, or splat is not a substitute for an exportable triangle mesh.
- Use pretrained models. Do not start training a new reconstruction or segmentation model.

## Start here

1. Read `PLAN.md` and inspect existing repository code before creating a scaffold.
2. Check available hardware, installed tools, dependency manifests, and any documented setup.
3. Validate reconstruction, texturing, and GLB export on real photos before polishing UI.
4. Validate local segmentation inference on a real input.
5. Build the upload-to-result vertical slice, then integrate masks, then phone capture.
6. Record assumptions, actual commands, and measured limitations in README.

Do not claim a stage works because its wrapper exists or its tests mock the underlying executable. If hardware prevents reconstruction, report the specific blocker and preserve useful application work. Do not silently change to cloud processing.

## Implementation defaults

- React + TypeScript + Vite frontend; Three.js viewer.
- Python + FastAPI backend, Pydantic request validation.
- COLMAP behind a narrow adapter; use only validated installed commands.
- Local SAM 2 image predictor behind a segmentation adapter.
- Blender background script for tested GLB conversion.
- Filesystem storage and atomic JSON state; one active heavy job.
- One API process with one bounded worker execution context. Keep CPU/GPU work off the request event loop.
- Prefer the smallest working implementation. No Redis, Celery, database, containers, account systems, or generic plugin architecture without a concrete blocker that justifies them.

These are defaults for an empty repo. Preserve a compatible working stack instead of rewriting solely to match the list.

## Reconstruction and ML rules

- Keep original RGB inputs unchanged. Store normalized working images and masks separately.
- Freeze selected inputs/settings when a run starts.
- Preserve orientation, image dimensions, camera intrinsics, and distortion mappings consistently.
- Estimate camera poses from full images for the stationary-object baseline unless an alternative is actually tested.
- Do not apply a mask from one view to another view.
- Do not assume blacking out a background implements supported reconstruction masking.
- Validate any mask-to-geometry integration with actual camera projections and visual inspection.
- A single-view mask can propose a coarse region, not guarantee isolated 3D geometry.
- Expose a visible mask overlay and confirmation. Clearly label preview-only functionality if masks do not yet affect export.
- Traditional photogrammetry and blur heuristics are computer vision; describe pretrained segmentation as the ML component.
- Release segmentation GPU memory before demanding dense reconstruction if necessary.
- Do not invent invisible geometry or advertise arbitrary-scale output as measured dimensions.
- Preserve texture coordinates during mesh filtering; rebake if an operation invalidates UVs.

## Browser and LAN rules

- Use trusted HTTPS for live camera access from a phone on the LAN.
- Do not rely on the desktop's localhost exception for a phone, or recommend bypassing certificate warnings.
- Keep a working file-upload fallback.
- Bound pending camera frames and stop camera tracks on exit.
- Serve UI and API under one origin where practical.
- Restrict exposure to the intended private LAN, apply simple per-launch access control, and avoid public port forwarding.
- Never commit certificates, private keys, tokens, model weights, or actual user scans.

## Backend rules

- Generate IDs and storage paths on the server; never accept arbitrary filesystem paths.
- Decode uploads and check image type, byte size, and decoded dimensions.
- Use subprocess argument arrays with no shell interpolation of user input.
- Keep one heavy task active; return a clear busy response instead of adding a scheduler.
- Return promptly when starting work and expose polling status.
- Record real stage names and warnings. Do not fabricate progress percentages or completion times.
- Capture logs locally, expose safe messages, validate output files before marking ready.
- Cancellation must stop the process tree, not merely change a UI label.
- On restart, mark unfinished runs interrupted; clean rerun is sufficient for the demo.
- Do not overwrite a successful output with an incomplete rerun.

## Repository and dependencies

- Keep frontend, backend, scripts, tests, and documentation separated as described in PLAN.md.
- Pin working dependency versions and preserve lockfiles.
- Document the exact toolchain and checkpoint source/hash.
- Verify licenses for the actual tools/checkpoints selected, especially before redistributing binaries or weights.
- Put local config values in an ignored configuration file and provide safe examples.
- Add setup/doctor and launch scripts only for the chosen demo platform first.
- Do not invent executable paths, dependency versions, or commands. Discover them and record what was verified.

## Verification

Run checks appropriate to the implemented change:

- Frontend build and TypeScript checks.
- Focused backend tests for uploads, exclusive execution, status transitions, and safe paths.
- Projection/mask alignment tests using known synthetic camera geometry when implementing isolation.
- Real end-to-end reconstruction smoke test on a small permitted dataset.
- GLB inspection in the browser and an independent viewer such as Blender.
- Offline runtime check with internet disconnected and LAN retained.
- Target-phone capture/upload test, including camera permission failure.

Mocks may test orchestration, but do not count as reconstruction or ML validation. Avoid broad test scaffolding for cosmetic changes. If checks cannot run, report which and why.

## Demo integrity and time management

- Prioritize working capture/import → ML selection → reconstruction → viewer → GLB.
- Cut optional automation, history, extra formats, and advanced editing before weakening the core flow.
- Maintain a pre-captured input set and its real saved output as labeled fallbacks.
- Never present a cached model as a newly completed scan, or a manual crop as learned segmentation.
- Do not claim “real-time reconstruction,” “3D-print ready,” “accurate measurements,” or “all processing on phone” without implementing and verifying it.
- Reserve the final portion of the event for integration and rehearsal.

## Completion report

State what changed, how it was verified, and remaining blockers. Distinguish implemented features from stubs, mocked data, manual setup, and cached examples. Keep README launch steps synchronized with the actual code. Proceed with reasonable reversible implementation decisions within scope; ask only when missing information blocks a consequential choice.
