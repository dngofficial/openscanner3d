# Local object scanner

The phone's browser connects directly to the laptop over your Wi-Fi (HTTPS, no app). It guides you to every missing angle of a stationary object and uploads the photos to the laptop, which stores and reconstructs them. Nothing leaves the local network. `PLAN.md` is the scope authority; `AGENTS.md` lists the project rules.

```text
phone browser (camera + motion sensors) --HTTPS on LAN--> laptop FastAPI --> scans/<id>/ --> scan.zip / COLMAP
```

## Complete setup on Windows (from scratch)

Run every command in **PowerShell**, from the repository folder. To open PowerShell in a folder, right-click inside it in File Explorer and choose **Open in Terminal**.

1. **Prerequisites:**
   - Python 3.11 x64 from python.org, with the py launcher (check with `py -3.11 --version`)
   - Git for Windows
   - mkcert (`winget install FiloSottile.mkcert`, then open a new PowerShell window)
2. **Get the code:**
   ```powershell
   git clone https://github.com/dngofficial/openscanner3d.git
   ```
   ```powershell
   cd openscanner3d
   ```
   ```powershell
   git checkout claude/mobile-guided-capture
   ```
3. **Base setup:** creates `.venv`, installs the pinned dependencies, copies `config.example.toml` to `config.local.toml` if missing, runs the CPU tests, and writes `data/doctor.json`.
   ```powershell
   ./scripts/setup.ps1
   ```
   If scripts are blocked, use `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`. If `py` isn't found, add `-Python 'C:\path\to\python.exe'`.
4. **HTTPS certificate for the phone** (once, and again whenever the laptop's Wi-Fi address changes):
   ```powershell
   .venv/Scripts/python.exe -m scripts.make_cert
   ```
   Then install `certs/phone-root-ca.pem` on the phone as described in [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md).
5. **Config:** open `config.local.toml` (`notepad config.local.toml`) and set `lan = true`.
6. **Check** that `phone_connection.ok` is `true` in the output:
   ```powershell
   .venv/Scripts/python.exe -m scripts.doctor
   ```
7. **Run:**
   ```powershell
   ./scripts/launch.ps1
   ```
   Open `https://localhost:8443` on the laptop and scan the QR code with the phone. If Windows Firewall asks, allow Python on **Private networks** only.

Without `lan = true`, the scanner runs on `http://localhost:8000` for the laptop only. The phone can't connect in that mode, but the dashboard and processing still work.

## Scanning

The phone page tells you where to go next. It covers **12 sides × 3 heights**: two photos per side at low and middle height, one from high above, 60 in total. Photos are taken automatically when you reach a missing angle and hold still, and each is checked on the laptop (exposure, then sharpness, then contrast; a rejection names the cause with its measured value). Full instructions, the checklist map and troubleshooting are in [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md).

The angles come from the phone's motion sensors and assume you keep the object centred while walking around it. They are coverage guidance stored with each photo (`poses.json`), not measured camera poses.

**No live camera?** If camera or motion access is blocked or unsupported, the phone page offers **Upload photos**: take 40–60 photos with the camera app and upload them. They are turned upright and limited to 3200 px, the originals are kept in `originals/`, and they get the same quality checks but no angle data.

## On the laptop

`https://localhost:8443` (or `http://localhost:8000` without LAN mode) shows:

- **Connect your phone:** a QR code with the phone link and this launch's key, or the exact problem stopping the connection (LAN off, no certificate, address changed, not on Wi-Fi).
- **Scans:** for each scan, the checklist map, photo count, angles covered, thumbnails, `scan.zip` (photos, `poses.json`, `scan.json`) and, in reconstruct mode, **Reconstruct**.

### Reconstruction (GPU laptop)

Set `mode = "reconstruct"` and the `colmap` path in `config.local.toml` (COLMAP Windows **CUDA** build). Then press **Reconstruct** on a finished scan. It runs in the background; the scan shows `running`, then `ready` or `failed`, and offers `mesh.glb`, `mesh.obj` and `colmap.log`.

To process a scan from another laptop, extract its `scan.zip`, then run:
```powershell
.venv/Scripts/python.exe -m scripts.reconstruct 'path\to\images'
```

A feed-forward reconstruction path (seconds instead of minutes) is planned next; see PLAN.md.

## Security model

- **Network:** LAN mode listens on all interfaces but answers only loopback and private-network addresses (10/8, 172.16/12, 192.168/16, private IPv6). Everything else gets 403.
- **Key:** phones need the per-launch key from the laptop's QR code. It is moved to an HttpOnly, Secure, SameSite=Strict cookie and removed from the URL. Only the laptop itself can read the key (`/api/connect`).
- **Cross-site requests:** POST and DELETE requests carrying another site's `Origin` are refused.
- **Uploads:** decoded and bounded (25 MB, at most 16 MP, 8192 px per side). Image size and orientation must stay consistent within a scan. Scan IDs and file names are generated by the server; downloads come from a fixed list of names.
- **Local files:** certificates, keys, `config.local.toml`, `scans/` and `data/` are git-ignored. Don't port-forward or tunnel the scanner to the internet.

## Storage

- `scans/<id>/images/kf_NNNN.jpg`: accepted photos (camera frames as uploaded; uploaded gallery photos upright and at most 3200 px).
- `scans/<id>/originals/`: uploaded gallery photos exactly as received.
- `scans/<id>/poses.json`: per-photo azimuth, pitch, ring, side, time and quality measurements.
- `scans/<id>/scan.json`: creation time, image size, finished flag.
- `scans/<id>/scan.zip`: written on Finish.
- `scans/<id>/status.json`: reconstruction state; a run interrupted by a restart is marked `interrupted`.
- `scans/<id>/mesh.glb`, `mesh.obj`, `colmap.log`: reconstruction outputs.

## Repository layout

```text
backend/app/   main.py        API, LAN guard, scan storage
               network.py     private-network check, launch key, LAN addresses
               coverage.py    12 x 3 angle checklist
               quality.py     exposure / sharpness / contrast checks
               images.py      bounded decoding, upload normalisation
               reconstruction.py  COLMAP adapter
               config.py      config.local.toml validation
frontend/      phone.html (guided capture), index.html (laptop dashboard), guidance.mjs (angle maths, shared)
scripts/       setup.ps1, launch.ps1, doctor.py, make_cert.py, reconstruct.py
tests/         test_capture_api.py, test_config.py, test_reconstruction.py, guidance.test.mjs, check_capture.py (setup smoke check)
docs/          PHONE_SETUP.md, PARTNER_SETUP.md, implementation_plan.md (long-term background)
demo/          demo validation checklist
models/        notes for local model weights (none used on this branch)
```

## Verification

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'
```
```powershell
node --test tests/guidance.test.mjs
```
```powershell
.venv/Scripts/python.exe -m tests.check_capture
```

These cover:
- the guidance maths: orientation to camera direction, angle bins, next-step instructions, steadiness, exposure;
- access control: outsiders refused, key-to-cookie exchange, laptop-only key, cross-site POST refused;
- uploads: quality rejection, orientation change, gallery-photo normalisation with originals kept, finish and ZIP, undo, background reconstruction start;
- config validation and the COLMAP adapter (mocked COLMAP, real subprocess time limits).

The phone page was also driven in a browser with a synthetic camera and simulated orientation events: auto-capture, a sideways step for the second view, walk-left instructions, the checklist map and Finish. HTTPS serving was checked with a test certificate.

**Not verified:** a real phone (iPhone or Android) with real sensors and the mkcert certificate, and real COLMAP. Those are the next acceptance steps.

## Current limitations

- Angle guidance assumes a stationary, centred object; turning the object instead is not supported in this mode.
- The phone's heading drifts slowly, and compass interference is possible indoors.
- Photos are the browser's video frames (requested at 1920×1080), not full-resolution stills.
- Reconstruction is COLMAP's experimental untextured mesh; scale is arbitrary.
