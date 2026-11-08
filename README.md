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

First, tap the object on the phone screen: that takes the first photo and tells the AI segmenter which object to cut out (**Skip** works too). Then the phone page tells you where to go next. It covers **12 sides × 3 heights**: one photo per side at each height, 36 in total. Photos are taken automatically when you reach a missing angle and hold still, and each is checked on the laptop (exposure, then sharpness, then contrast; a rejection names the cause with its measured value). Full instructions, the checklist map and troubleshooting are in [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md).

The angles come from the phone's motion sensors and assume you keep the object centred while walking around it. They are coverage guidance stored with each photo (`poses.json`), not measured camera poses.

**No live camera?** If camera or motion access is blocked or unsupported, the phone page offers **Upload photos**: take 36–48 photos with the camera app and upload them. They are turned upright and limited to 3200 px, the originals are kept in `originals/`, and they get the same quality checks but no angle data.

## On the laptop

`https://localhost:8443` (or `http://localhost:8000` without LAN mode) shows:

- **Status bar:** capture or reconstruct mode, and whether a phone is connected (with when it was last seen).
- **Connect a phone:** a QR code with the phone link and this launch's key, or the exact problem stopping the connection (LAN off, no certificate, address changed, not on Wi-Fi). It shrinks once a phone is connected.
- **Scans:** a list with each scan's angle dial, photo count, coverage and state (capturing, ready to reconstruct, reconstructing, model ready, failed). It updates live while the phone captures.
- **Scan detail:**
  - the next action (Reconstruct, Retry or Reconstruct again) and the latest status message;
  - a **3D viewer** for the object and the whole scene: drag to orbit, scroll to zoom, with wireframe, turntable and reset-view controls, plus the triangle count and colour type. It uses Three.js vendored in `frontend/vendor`, so it works offline;
  - **Game asset detail:** Low poly (1,000 triangles, 1024 px texture), Standard (5,000, 2048 px) or High (20,000, 4096 px), and **Rebuild object**, which re-meshes from the existing COLMAP run in about a minute without rerunning COLMAP or SAM 2. If a rebuild fails, the previous files stay;
  - **Object masks:** every photo's mask as a green overlay, and whether SAM 2 or GrabCut produced it;
  - key facts, labelled downloads and all photos.

### Reconstruction (GPU laptop)

Set `mode = "reconstruct"` and the `colmap` path in `config.local.toml` (COLMAP Windows **CUDA** build). Then press **Reconstruct** on a finished scan. It runs in the background; the scan shows `running`, then `ready` or `failed`, and offers these files:

- **`object.glb` (plus `object.obj` / `object.mtl` / `object_albedo.png`): the scanned object as a game asset.** It is built from the object's outlines (silhouette carving, or "visual hull") using COLMAP's camera positions, so it works on plain objects that dense stereo can't see, such as a matte black bottle. It is then:
  - reduced to the detail preset's triangle budget (`detail` in `config.local.toml`, default Standard = 5,000), in halving steps that keep the surface closed;
  - UV-unwrapped (xatlas);
  - given a colour texture (2048 px for Standard) baked directly from the photos;
  - exported as a single material, upright, with its origin at the base centre.
- **`object_hires.glb`: the same object at full detail** (about 70k triangles, vertex colours).

On the first real scan, the game asset had 5,000 triangles, was watertight, got photo colour on 90% of its surface (the rest is filled from neighbouring texture), and the bottle's printed logo is legible in the texture. For low-poly props pick **Low poly** on the dashboard and press Rebuild object, or set `detail = "low"`. With SAM 2 masks the same scan gave a watertight 1,000-triangle asset. For any other budget: `scripts.rebuild_object --target-triangles N --texture-size N`. Add `--height-cm N` to scale the model to a height you measured yourself; otherwise the scale is arbitrary.
- **`mesh.glb` / `mesh.obj`: the whole scene** from COLMAP dense stereo and Poisson meshing, usually dominated by the table.
- **`colmap.log`.**

How the object step works (`backend/app/silhouette.py`):
1. Find the table as the dominant plane in COLMAP's dense points.
2. Place the object where the cameras' viewing directions meet, then centre and size the search area on the 3D points standing on the table there. This matters when the photos were taken from far back: a fixed-size area took in a sofa and a window behind a soup can. Objects with too few points (plain, dark) keep the fixed area, and a fitted area grows if the object reaches its edge.
3. Outline it in every photo with GrabCut, told that table pixels away from the object are background (classical computer vision), and carve a rough shape.
4. **ML step: SAM 2** (Meta's pretrained Segment Anything 2, run locally) outlines the object in each photo, prompted per photo with a box around the projected rough shape and a point from the phone tap (or the shape's centre). Each SAM 2 mask is used where it broadly agrees with that photo's GrabCut outline; otherwise the photo keeps GrabCut. Setup, checkpoint hash and measurements: [models/README.md](models/README.md). Without SAM 2 installed, the step uses GrabCut and says so.
5. Carve the space inside all but a couple of outlines, mesh it, smooth it, close tunnels a few voxels wide, and colour it.

Limits of the object model:
- A visual hull is an outer bound. Concavities no outline reveals stay filled, such as the space inside a handle loop or a cup.
- Scale is arbitrary.

On the first real scan (36 Samsung photos of a black bottle), the scene mesh was a table disc with a hole where the bottle stood. The object step gave an upright bottle. With SAM 2 on a laptop CPU it took about 4.5 minutes: 59 s of that was SAM 2, which was accepted in all 36 photos. A detail-only rebuild took about 80 s.

To rebuild `object.glb` for a scan that COLMAP already processed, without rerunning COLMAP:
```powershell
.venv/Scripts/python.exe -m scripts.rebuild_object scans\SCAN_ID
```

Use `quality = "medium"` (the default in `config.example.toml`) or `high`. `low` downsizes photos to 1000 px for dense stereo.

To process a scan from another laptop, extract its `scan.zip`, then run:
```powershell
.venv/Scripts/python.exe -m scripts.reconstruct 'path\to\images'
```

The result appears on the dashboard like a phone scan. To show a scan folder copied from another laptop (or made by an older version), put it in `scans/` and run:
```powershell
.venv/Scripts/python.exe -m scripts.register_scan scans\SCAN_ID
```
It only adds the missing dashboard files (`scan.json`, `poses.json`, `run.json`, `status.json`); photos and models are untouched.

A feed-forward reconstruction path (seconds instead of minutes) is planned next; see PLAN.md.

## Security model

- **Network:** LAN mode listens on all interfaces but answers only loopback and private-network addresses (10/8, 172.16/12, 192.168/16, private IPv6). Everything else gets 403.
- **Key:** phones need the per-launch key from the laptop's QR code. It is moved to an HttpOnly, Secure, SameSite=Lax cookie and removed from the URL. Only the laptop itself can read the key (`/api/connect`).
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
- `scans/<id>/poses.json` also holds `tap` (where the object was tapped, as fractions of the first photo).
- `scans/<id>/mesh.glb`, `mesh.obj`, `colmap.log`, `object*.{glb,obj,mtl,png}`, `object.json`: reconstruction outputs and the object step's summary.
- `scans/<id>/mask_previews/`: small mask overlays for the dashboard; `runs/<run>/object_masks/`: full-size masks reused by rebuilds; `run.json`: the latest COLMAP run.

## Repository layout

```text
backend/app/   main.py        API, LAN guard, scan storage
               network.py     private-network check, launch key, LAN addresses
               coverage.py    12 x 3 angle checklist
               quality.py     exposure / sharpness / contrast checks
               images.py      bounded decoding, upload normalisation
               reconstruction.py  COLMAP adapter, object step, detail rebuild
               silhouette.py  table, GrabCut, carving, meshing (object model)
               ai_masks.py    SAM 2 prompts, per-photo fallback, mask cache and previews
               game_asset.py  decimation, UV unwrap, photo-baked texture
               config.py      config.local.toml validation
frontend/      phone.html (guided capture), index.html (laptop dashboard), guidance.mjs (angle maths, shared)
scripts/       setup.ps1, launch.ps1, doctor.py, make_cert.py, reconstruct.py, rebuild_object.py,
               sam2_masks.py (runs inside .venv-sam2)
tests/         test_capture_api.py, test_config.py, test_reconstruction.py, guidance.test.mjs, check_capture.py (setup smoke check)
docs/          PHONE_SETUP.md, PARTNER_SETUP.md, implementation_plan.md (long-term background)
demo/          demo validation checklist
models/        SAM 2 checkpoint (git-ignored) and its setup notes
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
- config validation and the COLMAP adapter (mocked COLMAP, real subprocess time limits);
- the object step on a synthetic scene with known geometry: carving, game asset, SAM 2 prompts (boxes, tap ray), per-photo fallback, mask cache reuse, low-poly budgets that stay watertight. SAM 2 itself is a stand-in in these tests; it was checked on the real scan above.

**UI/UX review harness** (dev only, needs a running scanner with one reconstructed scan): `tests/ui_review/review.py` drives the phone page in Edge with a simulated camera (real scan photos) and simulated motion sensors, and the dashboard at 1440/1024/390 px. It saves screenshots of every state plus automated checks: cut-off content, horizontal scroll, WCAG contrast, touch-target size, accessible names, focus visibility, console and HTTP errors. The scoring rubric is [docs/UX_REVIEW.md](docs/UX_REVIEW.md). Setup and run:
```powershell
py -3.11 -m venv .venv-review; .venv-review/Scripts/python.exe -m pip install playwright==1.49.1
```
```powershell
.venv-review/Scripts/python.exe tests/ui_review/review.py --base http://localhost:8000 --out data/ui_review/latest
```

The phone page was also driven in a browser with a synthetic camera and simulated orientation events: auto-capture, a sideways step for the second view, walk-left instructions, the checklist map and Finish. HTTPS serving was checked with a test certificate.

**Not verified:** a real phone (iPhone or Android) with real sensors and the mkcert certificate, and real COLMAP. Those are the next acceptance steps.

## Current limitations

- Angle guidance assumes a stationary, centred object; turning the object instead is not supported in this mode.
- The phone's heading drifts slowly, and compass interference is possible indoors.
- Photos are the browser's video frames (4K requested; many phones deliver 1920×1080), not full-resolution stills.
- `object.glb` is a silhouette hull: smooth surfaces come out well, but concavities fill in. It needs a visible supporting surface (table) in the dense points.
- SAM 2 is checked on one real scan (CPU). On the GPU laptop it needs a CUDA PyTorch build in `.venv-sam2` (not yet verified).
- Scale is arbitrary.
