# Validation record

Windows HTTPS and QR update, November 8, 2026:
- 27 Python tests pass, including selector-loop selection, policy cleanup, propagation of application
  failures, and decoding the real QR image back to the exact scan-specific pairing URL.
- `segno==1.6.6` is installed in the VGGT environment and included in requirements.txt.
  QR generation is local; the laptop offers network address selection and a direct-link fallback.
- A separate real Uvicorn HTTPS process on a temporary loopback port remained responsive after
  40 deliberately reset TLS/TCP connections. The client verified the generated certificate using
  phone-root-ca.pem. No Proactor teardown tracebacks appeared. The user's running server was untouched.
- Both UI fixture suites and the six guidance math checks pass. Hardware phone scanning and browser
  visual checks remain pending; this check does not establish real-model reconstruction quality.
- mkcert 1.4.4 is now installed. The certificate helper uses system-only trust to avoid Java cacerts
  access failures; its two regression tests pass. Actual certificates are present under ignored certs/.

Phone integration, November 8, 2026:
- 23 Python tests pass, including real photo decoding/storage, paired private-LAN access,
  cross-origin and WebSocket access checks, angle coverage, undo and VGGT Finish handoff.
- Six guidance math tests pass. The desktop and phone control tests pass with DOM/API/camera/sensor
  fixtures. These are orchestration checks; VGGT predictions in the new backend tests are synthetic.
- The local doctor reports Python 3.12.10, torch 2.7.1+cu128, VGGT 0.0.1,
  OpenCV headless 4.11.0.86, FastAPI 0.162.2 and NumPy 1.26.4.
- CUDA is available on the RTX 3070 Ti Laptop GPU; the check reported 6.9 GiB free GPU memory,
  4.6 GiB available system RAM, and a passing CUDA memory preflight. Inference was not run for
  this capture change; a preflight pass does not prove inference or reconstruction quality.
- Browser visual inspection could not run because the browser-access host was unavailable.
  Trusted phone HTTPS, actual motion sensors, gallery fallback on hardware, and pretrained
  reconstruction from phone photos remain unverified. See PHONE_SETUP.md for that checklist.
- mkcert is not installed in this workspace. The certificate helper and LAN launch arguments
  are implemented; no CA was installed and no phone certificate trust was changed here.

Earlier VGGT validation:

Passed:
- 16 Python backend/geometry/resource tests.
- JavaScript control-flow checks using DOM, API and WebSocket fixtures, including manual-only capture, one in-flight photo, Finish handoff and automatic-mode compatibility.
- Manual photos through the real WebSocket/API path with a fixture reconstruction backend, ending in colored PLY export.
- Rotating-item input masking, preserving photo framing and foreground colors, with rejection of missing or empty masks.
- Python syntax compilation and JavaScript syntax validation.
- Real upstream VGGT construction with empty weights.
- Real pretrained checkpoint parameter-schema matching for the pinned camera/depth architecture, with weights kept on the meta device.

The tests demonstrate that the application can keep capturing during a background job, use an immutable snapshot, queue a final pass, write colored PLY from valid predicted geometry, and preserve a successful result after a later failure or cancellation.

Not completed:
- Full pretrained VGGT inference in this workspace.
- Reconstruction quality on the user's captured object.
- Full browser visual/hardware-webcam testing.
- CUDA execution on the user's GPU.

A large-model check preceded the execution environment becoming unavailable. Its cause was not conclusively established. This version adds CPU/GPU memory preflights and uses smaller defaults; the large pretrained check was not repeated. Run check_setup.py, then smoke_test.py with overlapping photos to validate the full model on your own machine.

Manual capture and rotating-item masking do not establish real-model reconstruction quality. In particular, physical object rotation with hands, shadows or reflections requires validation with actual captured photos.

The headless Chromium check could not launch because this execution workspace rejected its socket creation. Browser control flow was verified with the JavaScript fixtures; visual layout and hardware webcam behavior remain unverified.
