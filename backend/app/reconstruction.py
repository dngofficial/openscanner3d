"""Bounded adapter around the existing, experimental COLMAP mesh pipeline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

from .ai_masks import make_segmenter
from .config import DETAIL, Settings, resolve_tool
from .silhouette import reconstruct_object

_heavy_job = threading.Lock()
REQUIRED_FLAGS = ("--workspace_path", "--image_path", "--quality", "--single_camera", "--mesher")
OBJECT_FILES = ("object.glb", "object_hires.glb", "object.obj", "object.mtl", "object_albedo.png")
PREVIEWS = "mask_previews"


def object_step(workspace: Path, images: Path, output: Path, settings: Settings, detail: str, stage=None) -> dict:
    """Object model from an existing COLMAP run, built in a staging folder and moved into output only on success."""
    triangles, texture = DETAIL[detail]
    staging = workspace / f"object_{uuid.uuid4().hex[:8]}"
    info = reconstruct_object(workspace, images, staging, target_triangles=triangles, texture_size=texture,
                              segment=make_segmenter(settings, workspace), mask_cache=workspace / "object_masks",
                              previews=staging / PREVIEWS, stage=stage)
    if not (staging / "object_hires.glb").is_file():
        raise ValueError("No object mesh was written")
    for name in OBJECT_FILES:
        if (staging / name).is_file():
            os.replace(staging / name, output / name)
        else:
            (output / name).unlink(missing_ok=True)  # e.g. a failed game asset: never keep an older one beside it.
    if (staging / PREVIEWS).is_dir():
        # File by file: a preview another program has open (Windows) must not fail a finished model.
        try:
            (output / PREVIEWS).mkdir(exist_ok=True)
            fresh = {path.name for path in (staging / PREVIEWS).iterdir()}
            for path in (output / PREVIEWS).iterdir():
                if path.name not in fresh:
                    path.unlink(missing_ok=True)
            for name in fresh:
                os.replace(staging / PREVIEWS / name, output / PREVIEWS / name)
        except OSError as error:
            info.setdefault("warnings", []).append(f"Outline previews not updated ({error.strerror or error}).")
    info["detail"] = detail
    (output / "object.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    shutil.rmtree(staging, ignore_errors=True)
    return info


def describe_object(info: dict) -> str:
    masks = info.get("masks") or {}
    outline = f"SAM 2 outlines in {masks['sam2']} of {info['photos']} photos" if masks.get("sam2") else "basic outlines"
    game = info.get("game_asset")
    if game:
        message = (f"Object ready: a {game['triangles']:,}-triangle game asset with a photo texture "
                   f"({info.get('detail', 'standard')} detail), plus a {info['faces']:,}-triangle high-detail version. "
                   f"Built from {info['photos']} photos using {outline}.")
    else:
        message = f"Object ready (high-detail version only, {info['faces']:,} triangles), built from {info['photos']} photos using {outline}."
    if info["clipped"]:
        message += " It touches the edge of the search area, so part of it may be cut off."
    if info.get("warnings"):
        message += " Note: " + " ".join(info["warnings"])
    return message


def newest_run(scan: Path) -> Path | None:
    try:
        run = scan / "runs" / json.loads((scan / "run.json").read_text(encoding="utf-8"))["run"]
        if (run / "sparse" / "0" / "images.bin").is_file():
            return run
    except (OSError, ValueError, KeyError, TypeError):
        pass
    runs = [run for run in (scan / "runs").glob("*") if (run / "sparse" / "0" / "images.bin").is_file()]
    return max(runs, key=lambda run: run.stat().st_mtime) if runs else None


def rebuild_object(scan: Path, settings: Settings, detail: str, stage=None) -> dict:
    """Rerun only the object step (e.g. a lower triangle budget). The previous object stays if this fails."""
    if not _heavy_job.acquire(blocking=False):
        return dict(msg="Another reconstruction is running.", lvl="warn", done=False)
    try:
        workspace = newest_run(scan)
        if workspace is None:
            return dict(msg="No finished COLMAP run for this scan. Reconstruct it first.", lvl="bad", done=False)
        info = object_step(workspace, scan / "images", scan, settings, detail, stage)
        return dict(msg=describe_object(info), lvl="warn" if info["clipped"] or info.get("warnings") else "ok",
                    done=True, model_ready=True, object=info)
    except (ValueError, OSError) as error:
        return dict(msg=f"Object rebuild failed: {error}. The previous object files are unchanged.", lvl="bad",
                    done=False, model_ready=True)
    finally:
        _heavy_job.release()


def colmap_help(executable: str) -> str:
    result = subprocess.run(
        [executable, "automatic_reconstructor", "-h"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or not all(flag in output for flag in REQUIRED_FLAGS):
        raise RuntimeError("Installed COLMAP does not expose the required automatic_reconstructor options. Run doctor.")
    return output


def run_logged(command: list[str], log_path: Path, timeout: int) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            # Stop children as well as the launcher (COLMAP can be a .bat wrapper).
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            raise


def export_scene_mesh(workspace: Path) -> str | None:
    """Write workspace/mesh.{obj,glb} from COLMAP's Poisson mesh. Returns why it could not, or None.

    Plain, low-texture objects often leave dense stereo with almost no points, so Poisson writes an empty
    or degenerate mesh. That is not fatal: the object step only needs the sparse camera poses.
    """
    import numpy as np
    import trimesh
    mesh_path = workspace / "dense" / "0" / "meshed-poisson.ply"
    if not mesh_path.is_file():
        return "COLMAP wrote no dense Poisson mesh (check colmap.log and the build's CUDA support)."
    try:
        mesh = trimesh.load(mesh_path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
            return "COLMAP's dense Poisson mesh is empty (too little surface texture for dense stereo)."
        for extension in ("obj", "glb"):
            mesh.export(workspace / f"mesh.{extension}")
        if not len(trimesh.load(workspace / "mesh.glb", force="mesh").faces):
            return "The exported scene GLB contains no faces."
    except Exception as error:  # A truncated or empty PLY can fail in many ways inside the parser.
        return f"COLMAP's dense Poisson mesh could not be read ({type(error).__name__})."
    return None


def reconstruct(images: Path, output: Path, settings: Settings, stage=None) -> dict:
    """stage(text): optional callback told about each real step as it starts (no invented percentages)."""
    stage = stage or (lambda text: None)
    if settings.mode != "reconstruct":
        return dict(msg="Capture-only mode: photos saved. Run reconstruction on the partner's GPU laptop.", lvl="ok", done=True, model_ready=False)
    executable = resolve_tool(settings.colmap)
    if not executable:
        return dict(msg="COLMAP not found. Configure its path in config.local.toml and run doctor.", lvl="bad", done=False)
    if not _heavy_job.acquire(blocking=False):
        return dict(msg="Another reconstruction is running. Try Finish again when it completes.", lvl="warn", done=False)
    try:
        colmap_help(executable)
        # Separate each attempt so a failed rerun cannot reuse a stale mesh.
        workspace = output / "runs" / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        command = [executable, "automatic_reconstructor", "--workspace_path", str(workspace),
                   "--image_path", str(images), "--quality", settings.quality,
                   "--single_camera", "1", "--mesher", "poisson"]
        stage("Step 1 of 2: COLMAP is finding the camera positions and the scene's 3D points. This takes several minutes.")
        code = run_logged(command, output / "colmap.log", settings.timeout_seconds)
        if code:
            return dict(msg=f"COLMAP exited with code {code}. See colmap.log.", lvl="bad", done=False)
        import numpy as np
        scene_error = export_scene_mesh(workspace)
        if scene_error and not (workspace / "sparse" / "0" / "images.bin").is_file():
            return dict(msg=f"{scene_error} COLMAP also produced no camera poses (sparse/0). See colmap.log.",
                        lvl="bad", done=False)
        if not scene_error:
            # The scene stands on its own, so it replaces older outputs before the object step runs.
            for extension in ("obj", "glb"):
                os.replace(workspace / f"mesh.{extension}", output / f"mesh.{extension}")
            (output / "run.json").write_text(json.dumps(dict(run=workspace.name)), encoding="utf-8")
            for name in (*OBJECT_FILES, "object.json"):
                (output / name).unlink(missing_ok=True)
            shutil.rmtree(output / PREVIEWS, ignore_errors=True)
        message = f"Scene mesh skipped: {scene_error}" if scene_error else "Scene mesh built (COLMAP)."
        try:
            # Plain objects give dense stereo little to work with; their outlines still define the shape.
            info = object_step(workspace, images, output, settings, settings.detail, stage)
        except (ValueError, OSError, np.linalg.LinAlgError) as error:
            if scene_error:  # Nothing new to show: leave any earlier outputs exactly as they were.
                return dict(msg=f"{message} Object reconstruction failed: {error}.", lvl="bad", done=False)
            for name in (*OBJECT_FILES, "object.json"):
                (output / name).unlink(missing_ok=True)  # Never pair a new scene with an old object.
            return dict(msg=f"{message} Object reconstruction failed: {error}.", lvl="warn", done=True, model_ready=True)
        if scene_error:
            for extension in ("obj", "glb"):
                (output / f"mesh.{extension}").unlink(missing_ok=True)  # Never pair a new object with an old scene.
            (output / "run.json").write_text(json.dumps(dict(run=workspace.name)), encoding="utf-8")
        return dict(msg=f"{message} {describe_object(info)}",
                    lvl="warn" if scene_error or info["clipped"] or info.get("warnings") else "ok",
                    done=True, model_ready=True, object=info)
    except subprocess.TimeoutExpired:
        return dict(msg="Reconstruction timed out; its process tree was stopped. Photos remain available.", lvl="bad", done=False)
    finally:
        _heavy_job.release()
