# Partner build: Windows GPU laptop

Target reported by the user: RTX 3070 Ti Laptop, 8 GB VRAM, 32 GB RAM. This target has not yet run this version's real reconstruction test.

## 1. Verify capture without GPU dependencies

Install Python 3.11 x64, then from the repository root:

```powershell
./scripts/setup.ps1
./scripts/launch.ps1 -Mode capture
```

Set up the phone connection as in [PHONE_SETUP.md](PHONE_SETUP.md), run a guided scan from the phone, and tap Finish. Confirm the scan appears on `https://localhost:8443` with its photos and `scan.zip`. In capture mode the application does not claim to have created a model.

Setup installs constrained Python dependencies and runs CPU checks. It preserves existing config and does not download inference weights or GPU tools. Internet is needed for the initial dependency installation.

## 2. Install and inspect reconstruction tools

Install a Windows COLMAP build using [official installation guidance](https://colmap.github.io/install.html). Inspect the actual executable and available CUDA support; a working NVIDIA driver or sparse pipeline alone does not establish dense reconstruction support. Install Blender separately for independent model inspection and the future export adapter.

Copy `config.example.toml` to `config.local.toml` if setup has not already done so. Set `colmap` and `blender` to actual executable names on PATH or verified absolute paths. Use TOML single quotes for Windows paths. Do not paste guessed paths from another machine.

```powershell
.venv/Scripts/python.exe -m scripts.doctor --require-reconstruction --output data/partner-doctor.json
```

Doctor checks dependency imports, available disk, NVIDIA driver reporting, COLMAP's required CLI options, and Blender discovery. Its success means prerequisites were found; it explicitly leaves real reconstruction unverified. If it fails, inspect the report and fix the named tool/configuration problem before continuing. The report may contain local executable paths; share it deliberately and keep it out of Git.

## 3. Run a real reconstruction

Set `mode = "reconstruct"` in `config.local.toml`. Start with `quality = "low"` and a curated, overlapping set; the default 100-photo cap and 30-minute timeout are limits, not performance promises. Keep enough free disk for repeated dense workspaces.

For an interactive capture:

```powershell
./scripts/launch.ps1 -Mode reconstruct
```

For photos transferred from the development laptop, stop the web server, then run:

```powershell
.venv/Scripts/python.exe -m scripts.reconstruct 'scans/ACTUAL_CAPTURE_ID/images'
```

Replace the example folder with the actual path. The command copies validated JPEGs into a new scan folder, runs the same adapter, and writes `result.json` with elapsed time and output location. Never run a separate batch process alongside a heavy web job: execution limits apply within one process.

The adapter checks the installed `automatic_reconstructor -h` options, explicitly requests Poisson meshing, captures logs, limits execution time, and validates nonempty mesh export. It does not silently fall back to cloud processing or a fixture. Each attempt gets a fresh workspace so an old mesh cannot masquerade as new output.

## 4. Record acceptance evidence

- [ ] Record actual Python/dependency, COLMAP, NVIDIA driver, and Blender versions.
- [ ] Save the doctor report and exact input set locally.
- [ ] Run a real scan and inspect `colmap.log`, elapsed time, and peak VRAM.
- [ ] Open the resulting `mesh.glb` independently in Blender.
- [ ] Record missing surfaces, background contamination, material appearance, and scale limitations.
- [ ] Repeat with a guided scan from the Samsung phone.
- [ ] Repeat after disconnecting the internet while keeping the phone and laptop on the same Wi-Fi.

The current adapter exports a triangle mesh using trimesh. It does **not** provide UV texturing, clean quad topology, SAM 2 isolation, or a browser 3D viewer. These are remaining implementation gates in `PLAN.md`; a successful experimental GLB is useful evidence, not completion of the final product.

## Hardware failures

If COLMAP does not provide the required commands or fails dense reconstruction, retain the photos/logs and report the exact build and stage. If memory is exhausted, reduce image count or working image resolution and rerun into a new workspace. Do not assume a CPU flag enables dense reconstruction on unsupported hardware. Only evaluate an alternative backend explicitly and with its own real-data test.
