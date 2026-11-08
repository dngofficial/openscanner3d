# Partner build: Windows GPU laptop

Target: RTX 3070 Ti Laptop, 8 GB VRAM, 32 GB RAM (user-reported). This version has not yet run a real reconstruction on it.

## 1. Base setup and phone capture

Follow [Complete setup on Windows](../README.md#complete-setup-on-windows-from-scratch) in the README, including the phone certificate from [PHONE_SETUP.md](PHONE_SETUP.md). Then run a guided scan from the phone and tap **Finish**. Confirm the scan appears on `https://localhost:8443` with its photos and `scan.zip`. In capture mode the app never claims to have made a model.

## 2. Reconstruction tools

- **COLMAP:** download the Windows **CUDA** release from <https://github.com/colmap/colmap/releases> and extract it, for example to `C:\Tools\COLMAP`. The CUDA build is required for dense reconstruction; the no-CUDA build stops after camera positions.
- **Blender** (optional, for inspecting results): install it from blender.org.

In `config.local.toml`, use single quotes for Windows paths, and only paths you have checked exist:

```toml
mode = "reconstruct"
colmap = 'C:\Tools\COLMAP\COLMAP.bat'
lan = true
```

Then check the prerequisites:

```powershell
.venv/Scripts/python.exe -m scripts.doctor --require-reconstruction --output data/partner-doctor.json
```

Doctor checks:
- Python dependencies and free disk space;
- that the NVIDIA driver reports the GPU;
- COLMAP's required command options;
- that Blender can be found;
- the phone connection (`phone_connection.ok`).

Passing means the prerequisites were found, not that reconstruction works. The report contains local paths, so share it deliberately and keep it out of Git.

## 3. Run a real reconstruction

Use `quality = "medium"` (dense stereo up to 1600 px) unless 8 GB of VRAM runs out; `low` (1000 px) throws away most of a phone photo. Each finished run produces `object.glb` (the object from its outlines) next to `mesh.glb` (the whole scene). For scans COLMAP already processed, rebuild the object without rerunning COLMAP:

```powershell
.venv/Scripts/python.exe -m scripts.rebuild_object scans\SCAN_ID
```

**From the browser:**
```powershell
./scripts/launch.ps1 -Mode reconstruct
```
After a scan is finished, press **Reconstruct** on `https://localhost:8443`. The scan shows `running`, then `ready` with `mesh.glb`, `mesh.obj` and `colmap.log`, or `failed` with the reason.

**From a scan made on another laptop:** stop the app, extract that scan's `scan.zip`, then run
```powershell
.venv/Scripts/python.exe -m scripts.reconstruct 'C:\path\to\extracted\images'
```
It copies the validated JPEGs into a new scan folder, runs the same adapter, and writes `result.json` with the elapsed time. Don't run it while the app is reconstructing: the one-job limit applies within a single process.

How the adapter behaves:
- It checks COLMAP's `automatic_reconstructor -h` options, requests Poisson meshing, logs output, enforces a time limit (`timeout_seconds`, 30 minutes by default) and checks that the exported mesh isn't empty.
- Each attempt uses a fresh workspace, so an old mesh can't pose as a new result.
- It never falls back to cloud processing or a stored fixture.
- Start with `quality = "low"`; try `medium` once you know it fits in VRAM.

## 4. Record acceptance evidence

- [ ] Record Python, COLMAP, NVIDIA driver and Blender versions.
- [ ] Keep the doctor report and the exact input set locally.
- [ ] Run a real scan; note `colmap.log`, elapsed time and peak VRAM (`nvidia-smi --query-gpu=memory.used --format=csv -l 2` in a second window).
- [ ] Open the resulting `mesh.glb` in Blender.
- [ ] Record missing surfaces, background clutter, appearance and scale.
- [ ] Repeat with a guided scan from the Samsung phone.
- [ ] Repeat with the internet disconnected and the phone and laptop still on the same Wi-Fi.

The adapter exports an untextured triangle mesh through trimesh. UV texturing, quad remeshing, object isolation and a browser 3D viewer are not on this branch yet (see PLAN.md).

## Hardware failures

If COLMAP lacks the required commands or fails during dense reconstruction:
- keep the photos and logs;
- report the exact build and the stage it failed at.

If it runs out of memory, use fewer photos or `quality = "low"` and rerun; every attempt gets a new workspace. Don't assume a CPU flag makes dense reconstruction work on unsupported hardware.
