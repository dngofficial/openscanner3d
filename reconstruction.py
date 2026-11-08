"""COLMAP batch pipeline. No live camera or guessed live poses are required."""
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import zipfile

from diagnostics import collect as collect_diagnostics, alignment_failure_message


class Cancelled(Exception):
    pass


def find_colmap():
    override = os.environ.get("COLMAP_EXE", "").strip().strip('"')
    if override:
        candidate = Path(override).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else shutil.which(override)
    return shutil.which("colmap") or shutil.which("COLMAP.bat")


def execute(exe, command, args, log, cancel):
    if cancel.is_set():
        raise Cancelled()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    argv = [exe, command, *map(str, args)]
    with log.open("a", encoding="utf-8") as stream:
        stream.write("\n$ " + subprocess.list2cmdline(argv) + "\n")
        stream.flush()
        process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + float(os.environ.get("COLMAP_STEP_TIMEOUT", "10800"))
        try:
            while process.poll() is None:
                if cancel.wait(.2):
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{command} exceeded its time limit. See reconstruction.log.")
            if process.returncode:
                raise RuntimeError(f"{command} failed (exit {process.returncode}). See reconstruction.log.")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def help_options(exe, command):
    result = subprocess.run([exe, command, "-h"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", timeout=30,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    if result.returncode:
        raise RuntimeError(f"Cannot query {command} options. Check your COLMAP installation.")
    return result.stdout


def compatible_option(help_text, *names):
    for name in names:
        if "--" + name in help_text:
            return "--" + name
    raise RuntimeError("This COLMAP build lacks expected options: " + ", ".join(names))


def binary_count(path):
    if not path.is_file():
        return 0
    with path.open("rb") as f:
        data = f.read(8)
    return struct.unpack("<Q", data)[0] if len(data) == 8 else 0


def create_preview(points_path, output, limit=6000):
    # Read the TXT export without holding the complete sparse model in memory.
    count = 0
    sample = []
    import random
    rng = random.Random(9)
    with points_path.open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split()
            point = [float(v) for v in fields[1:4]]
            color = [int(v) for v in fields[4:7]]
            if len(point) != 3 or len(color) != 3 or not all(math.isfinite(v) for v in point):
                continue
            count += 1
            item = point + color
            if len(sample) < limit:
                sample.append(item)
            else:
                index = rng.randrange(count)
                if index < limit:
                    sample[index] = item
    output.write_text(json.dumps({"points": sample, "total": count}), encoding="utf-8")


def package_results(root, model):
    with zipfile.ZipFile(root / "results.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("sparse.ply", "dense.ply", "mesh.ply", "mesh.obj", "mesh.glb",
                     "preview.json", "report.json", "reconstruction.log", "merged.ply", "merged_preview.json", "alignment.json", "diagnostics.json"):
            path = root / name
            if path.is_file():
                archive.write(path, name)
        if model:
            for file in model.iterdir():
                if file.is_file():
                    archive.write(file, "colmap_model/" + file.name)


def reconstruct(root, dense, shared_camera, cancel, update, merge_live=False):
    root = Path(root)
    log = root / "reconstruction.log"
    model = None
    sparse_ready = False
    report = {"registered_images": 0, "points": 0}
    database = None
    try:
        exe = find_colmap()
        if not exe:
            raise RuntimeError("COLMAP was not found. Install it and add it to PATH, or set COLMAP_EXE, then restart the server.")
        attempt = root / ("run_" + str(time.time_ns()))
        sparse = attempt / "sparse"
        sparse.mkdir(parents=True)
        database = attempt / "database.db"
        extract_help = help_options(exe, "feature_extractor")
        match_help = help_options(exe, "exhaustive_matcher")
        gpu_extract = compatible_option(extract_help, "FeatureExtraction.use_gpu", "SiftExtraction.use_gpu")
        size_option = compatible_option(extract_help, "FeatureExtraction.max_image_size", "SiftExtraction.max_image_size")
        gpu_match = compatible_option(match_help, "FeatureMatching.use_gpu", "SiftMatching.use_gpu")
        update(stage="features", progress=10, message="Finding image features on the CPU…")
        extraction = ["--database_path", database, "--image_path", root / "images",
                      "--ImageReader.camera_model", "SIMPLE_RADIAL",
                      "--ImageReader.single_camera", "1" if shared_camera else "0",
                      gpu_extract, "0", size_option, "2400"]
        # Keep stationary background context for camera alignment. Cloud masks are applied during fusion.
        if merge_live and "--SiftExtraction.peak_threshold" in extract_help:
            extraction += ["--SiftExtraction.peak_threshold","0.003"]
        execute(exe, "feature_extractor", extraction, log, cancel)
        update(stage="matching", progress=25, message="Matching photos to find overlapping views…")
        matching=["--database_path",database,gpu_match,"0"]
        for option in ("FeatureMatching.guided_matching","SiftMatching.guided_matching"):
            if "--"+option in match_help:
                matching += ["--"+option,"1"];break
        execute(exe,"exhaustive_matcher",matching,log,cancel)
        report["diagnostics"]=collect_diagnostics(root,database)
        update(stage="sparse", progress=40, message="Estimating camera positions and reconstructing sparse geometry…")
        execute(exe, "mapper", ["--database_path", database, "--image_path", root / "images",
                              "--output_path", sparse], log, cancel)
        models = [p for p in sparse.iterdir() if p.is_dir() and (p / "images.bin").is_file()]
        if not models:
            raise RuntimeError("No connected 3D model could be reconstructed. Add more overlapping, sharp views with camera translation.")
        model = max(models, key=lambda p: (binary_count(p / "images.bin"), binary_count(p / "points3D.bin")))
        report.update(registered_images=binary_count(model / "images.bin"),
                      points=binary_count(model / "points3D.bin"), disconnected_models=len(models))
        if report["registered_images"] < 3 or not report["points"]:
            raise RuntimeError("Too few views registered into a usable 3D model. Add overlapping angles and try again.")
        update(stage="export", progress=55, message="Exporting the sparse point cloud…", **report)
        execute(exe, "model_converter", ["--input_path", model, "--output_path", root / "sparse.ply",
                                        "--output_type", "PLY"], log, cancel)
        text_model = attempt / "model_text"
        text_model.mkdir()
        execute(exe, "model_converter", ["--input_path", model, "--output_path", text_model,
                                        "--output_type", "TXT"], log, cancel)
        create_preview(text_model / "points3D.txt", root / "preview.json")
        sparse_ready = True
        update(stage="sparse_ready", progress=60, message="Sparse reconstruction is ready.", **report)
        if merge_live:
            from cloud_fusion import merge_live_clouds
            merged = merge_live_clouds(root, text_model, cancel, update)
            report.update(merged)
            update(**{k: merged[k] for k in ("accepted_clouds", "skipped_clouds", "merged_points")})
        if dense:
            workspace = attempt / "dense"
            workspace.mkdir()
            update(stage="undistort", progress=65, message="Preparing images for dense reconstruction…")
            execute(exe, "image_undistorter", ["--image_path", root / "images", "--input_path", model,
                                             "--output_path", workspace, "--output_type", "COLMAP",
                                             "--max_image_size", "1600"], log, cancel)
            update(stage="depth", progress=72, message="Estimating dense depth maps (requires a CUDA-enabled COLMAP build and NVIDIA GPU)…")
            execute(exe, "patch_match_stereo", ["--workspace_path", workspace, "--workspace_format", "COLMAP",
                                              "--PatchMatchStereo.geom_consistency", "true"], log, cancel)
            update(stage="fusion", progress=85, message="Fusing depth maps into a dense point cloud…")
            execute(exe, "stereo_fusion", ["--workspace_path", workspace, "--workspace_format", "COLMAP",
                                         "--input_type", "geometric", "--output_path", root / "dense.ply"], log, cancel)
            update(stage="mesh", progress=92, message="Building the mesh surface…")
            execute(exe, "poisson_mesher", ["--input_path", root / "dense.ply",
                                          "--output_path", root / "mesh.ply"], log, cancel)
            import trimesh
            mesh = trimesh.load(root / "mesh.ply", force="mesh")
            if not len(mesh.vertices) or not len(mesh.faces):
                raise RuntimeError("Meshing returned an empty surface.")
            mesh.export(root / "mesh.obj")
            mesh.export(root / "mesh.glb")
        report.update(dense_requested=dense, shared_camera=shared_camera)
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if cancel.is_set():
            raise Cancelled()
        package_results(root, model)
        update(state="completed", stage="done", progress=100,
               message=(f"Aligned {report['accepted_clouds']} clouds into one point cloud; {report['skipped_clouds']} skipped."
                        if merge_live else "Mesh reconstruction complete." if dense
                        else "Sparse point cloud complete. Choose mesh mode for a surface."),
               **report)
    except Cancelled:
        update(state="cancelled", stage="cancelled", message="Reconstruction cancelled. Uploaded photos are kept.")
    except Exception as error:
        info=collect_diagnostics(root,database)
        report["diagnostics"]=info
        if info.get("initial_pair_failure") and not sparse_ready:
            error=RuntimeError(alignment_failure_message(info))
        with log.open("a", encoding="utf-8") as f:
            f.write("\nPipeline error: " + str(error) + "\n")
        report["error"] = str(error)
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            package_results(root, model)
        except Exception:
            pass
        update(state="partial" if sparse_ready else "failed", stage="stopped",
               message=("Sparse reconstruction is available, but the later stage failed. " if sparse_ready else "") + str(error),
               **{k: v for k, v in report.items() if k != "error"})
