# Partner build: Windows GPU laptop

Target reported by the user: RTX 3070 Ti Laptop, 8 GB VRAM, 32 GB RAM. This target has not yet run this version's real reconstruction test.

**Installing from scratch, or after local files were deleted?** Follow [Complete setup on Windows](../README.md#complete-setup-on-windows-from-scratch) in the README first. It covers Python, SAM 2, COLMAP, OpenMVS, config and doctor, in order. This file adds detail and the acceptance checklist for the first real GPU run.

## 1. Verify capture without GPU dependencies

Install Python 3.11 x64, then from the repository root:

```powershell
./scripts/setup.ps1
./scripts/launch.ps1 -Mode capture
```

Use the existing Camo workflow at `http://localhost:8000`, or import a consistent photo set. Start camera previews without saving; press Start scan explicitly. Finish & review at least 12 useful views, exclude unwanted shots, and confirm the selection. Download `reviewed-images.zip` and extract its selected `images/` folder for transfer. The application reports capture-only mode and does not claim to have created a model.

Setup installs constrained Python dependencies and runs CPU checks. It preserves existing config and does not download inference weights or GPU tools. Internet is needed for the initial dependency installation.

## 2. Install and inspect reconstruction tools

Install a Windows COLMAP build using [official installation guidance](https://colmap.github.io/install.html). Inspect the actual executable and available CUDA support; a working NVIDIA driver or sparse pipeline alone does not establish dense reconstruction support. Install Blender separately for independent model inspection and the future export adapter.

Copy `config.example.toml` to `config.local.toml` if setup has not already done so. Set `colmap` and `blender` to actual executable names on PATH or verified absolute paths. Use TOML single quotes for Windows paths. Do not paste guessed paths from another machine.

```powershell
.venv/Scripts/python.exe -m scripts.doctor --require-reconstruction --output data/partner-doctor.json
```

Doctor checks dependency imports, available disk, NVIDIA driver reporting, COLMAP's required CLI options, and Blender discovery. Its success means prerequisites were found; it explicitly leaves real reconstruction unverified. If it fails, inspect the report and fix the named tool/configuration problem before continuing. The report may contain local executable paths; share it deliberately and keep it out of Git.

### Optional: texturing tools

For a UV-textured `textured.glb`, install OpenMVS (official [cdcseacave/openMVS](https://github.com/cdcseacave/openMVS) release or build; it is AGPL-3.0, so check the licence before redistributing). Set `openmvs_bin = 'C:\path\to\OpenMVS\bin'`, the folder containing `InterfaceCOLMAP.exe` and `TextureMesh.exe`. If Blender is installed, it is used for the GLB export; set `glb_exporter = "trimesh"` to avoid it. Doctor's `texturing` section must show `ok: true`. If an option check fails, report the OpenMVS version and its `--help` output; the flags assumed here have not been checked against a real build yet.

## 3. Run a real reconstruction

Doctor's `mask_path_supported` must be `true` for Camo input. By default, the adapter masks the Camo watermark corner during COLMAP feature extraction. If your build lacks `--mask_path`, reconstruction stops with an explanation instead of silently using the watermark. Disable masking with `feature_mask_regions = []` only for unwatermarked photos.

Set `mode = "reconstruct"` in `config.local.toml`. Start with `quality = "low"` and a curated, overlapping set; the default 100-photo cap and 30-minute timeout are limits, not performance promises. Keep enough free disk for repeated dense workspaces.

For an interactive capture:

```powershell
./scripts/launch.ps1 -Mode reconstruct
```

For photos transferred from the development laptop, stop the web server, then run:

```powershell
.venv/Scripts/python.exe -m scripts.reconstruct 'PATH_TO_EXTRACTED_REVIEWED_IMAGES/images'
```

If the extracted ZIP contains `object_masks/` beside `images/`, the batch command uses it automatically (or pass `--object-masks PATH`). Each mask is renamed with its own photo. A missing mask stops the run instead of being borrowed from another view. A ZIP from a turned-object capture is refused without its masks. With masks, the run also writes `object.glb`, the AI-isolated proposal, next to the unfiltered `mesh.glb`. Open both in Blender and record what isolation removed or wrongly kept.

To run AI selection on this laptop instead, install SAM 2 following [models/README.md](../models/README.md). For GPU inference use a CUDA PyTorch wheel; that variant has not been verified yet. Then confirm doctor's `segmentation.runtime` says `cuda`.

Replace the example folder with the actual path. The command copies validated JPEGs into a new scan folder, runs the same adapter, and writes `result.json` with elapsed time and output location. Never run a separate batch process alongside a heavy web job: execution limits apply within one process.

The adapter checks the installed `automatic_reconstructor -h` options, explicitly requests Poisson meshing, captures logs, limits execution time, and validates nonempty mesh export. It does not silently fall back to cloud processing or a fixture. Each attempt gets a fresh workspace so an old mesh cannot masquerade as new output.

## 4. Record acceptance evidence

- [ ] Record actual Python/dependency, COLMAP, NVIDIA driver, and Blender versions.
- [ ] Save the doctor report and exact input set locally.
- [ ] Run a real scan and inspect `colmap.log`, elapsed time, and peak VRAM.
- [ ] In the browser, watch the job's stage change, test **Cancel processing** once (check Task Manager for leftover `colmap` processes), then rerun.
- [ ] Open `mesh.glb`, `object.glb` and `textured.glb` in the web viewer and in Blender. Note what isolation removed or wrongly kept, and whether the texture is aligned.
- [ ] Label the best real result with `scripts.mark_fallback` and record its inputs.
- [ ] Record registered images out of the input count, and the mean reprojection error of the sparse model. This separates capture problems from dense/GPU problems.
- [ ] Open the resulting `mesh.glb` independently in Blender.
- [ ] Record missing surfaces, background contamination, material appearance, and scale limitations.
- [ ] Repeat with the Samsung/Camo input set.
- [ ] Repeat after disconnecting internet while retaining any needed local Camo connection.

The current adapter exports a triangle mesh with vertex colours using trimesh. It does **not** provide UV texturing, clean quad topology, or a browser 3D viewer. These are remaining implementation gates in `PLAN.md`; a successful experimental GLB is useful evidence, not completion of the final product.

## Hardware failures

If COLMAP does not provide the required commands or fails dense reconstruction, retain the photos/logs and report the exact build and stage. If memory is exhausted, reduce image count or working image resolution and rerun into a new workspace. Do not assume a CPU flag enables dense reconstruction on unsupported hardware. Only evaluate an alternative backend explicitly and with its own real-data test.
