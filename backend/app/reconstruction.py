"""Bounded adapter around the experimental COLMAP mesh pipeline, with optional OpenMVS texturing."""
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .config import Settings, resolve_tool
from .isolation import isolate_mesh
from .masks import write_colmap_masks

_heavy_job = threading.Lock()
REQUIRED_FLAGS = ("--workspace_path", "--image_path", "--quality", "--single_camera", "--mesher")
OUTPUT_STEMS = ("mesh", "object")


class Cancelled(Exception):
    """Raised after a cancellation request has stopped the running process tree."""


def colmap_help(executable: str) -> str:
    result = subprocess.run(
        [executable, "automatic_reconstructor", "-h"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or not all(flag in output for flag in REQUIRED_FLAGS):
        raise RuntimeError("Installed COLMAP does not expose the required automatic_reconstructor options. Run doctor.")
    return output


def run_logged(command: list[str], log_path: Path, timeout: int, cancel: threading.Event | None = None) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    return process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        raise subprocess.TimeoutExpired(command, timeout)
        except BaseException:
            # Stop children as well as the launcher (COLMAP can be a .bat wrapper).
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            raise


def export_validated(mesh, folder: Path, stem: str):
    import numpy as np
    import trimesh
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError(f"{stem} is not a nonempty finite triangle mesh")
    for extension in ("obj", "glb"):
        mesh.export(folder / f"{stem}.{extension}")
    if not len(trimesh.load(folder / f"{stem}.glb", force="mesh").faces):
        raise ValueError(f"Exported {stem}.glb contains no faces")


def reconstruct(images: Path, output: Path, settings: Settings, object_masks: Path | None = None,
                progress=lambda stage: None, cancel: threading.Event | None = None) -> dict:
    if settings.mode != "reconstruct":
        return dict(msg="Capture-only mode: photos saved. Run reconstruction on the partner's GPU laptop.", lvl="ok", done=True, model_ready=False)
    executable = resolve_tool(settings.colmap)
    if not executable:
        return dict(msg="COLMAP not found. Configure its path in config.local.toml and run doctor.", lvl="bad", done=False)
    if not _heavy_job.acquire(blocking=False):
        return dict(msg="Another reconstruction is running. Try Finish again when it completes.", lvl="warn", done=False)
    try:
        progress("checking COLMAP")
        help_text = colmap_help(executable)
        if object_masks and "--mask_path" not in help_text:
            return dict(msg="Installed COLMAP lacks --mask_path, so the AI object masks cannot be applied.", lvl="bad", done=False)
        if settings.feature_mask_regions and "--mask_path" not in help_text:
            return dict(msg="Installed COLMAP lacks --mask_path, so the configured watermark mask cannot be applied. "
                            "Set feature_mask_regions = [] only for unwatermarked photos.", lvl="bad", done=False)
        # Separate each attempt so a failed rerun cannot reuse a stale mesh.
        workspace = output / "runs" / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        command = [executable, "automatic_reconstructor", "--workspace_path", str(workspace),
                   "--image_path", str(images), "--quality", settings.quality,
                   "--single_camera", "1", "--mesher", "poisson"]
        if settings.feature_mask_regions or object_masks:
            progress("writing feature masks")
            # Masks only exclude features (pose estimation); dense stereo still sees the full image.
            write_colmap_masks(images, workspace / "masks", settings.feature_mask_regions, object_masks)
            command += ["--mask_path", str(workspace / "masks")]
        progress("COLMAP automatic reconstruction")
        code = run_logged(command, output / "colmap.log", settings.timeout_seconds, cancel)
        if code:
            return dict(msg=f"COLMAP exited with code {code}. See colmap.log.", lvl="bad", done=False)
        mesh_path = workspace / "dense" / "0" / "meshed-poisson.ply"
        if not mesh_path.is_file():
            return dict(msg="No dense Poisson mesh was produced. Inspect colmap.log and the installed backend's GPU support.", lvl="bad", done=False)
        import trimesh
        progress("exporting mesh")
        mesh = trimesh.load(mesh_path, force="mesh")
        export_validated(mesh, workspace, "mesh")
        isolation, isolated, warnings = None, None, []
        if object_masks:
            progress("AI isolation")
            try:
                isolated, isolation = isolate_mesh(mesh, workspace / "sparse" / "0", object_masks)
                export_validated(isolated, workspace, "object")
            except (ValueError, OSError) as error:
                isolation = None
                warnings.append(f"AI isolation failed: {error}. Only the unfiltered mesh is available.")
        texture = None
        if settings.texturing != "off":
            from .texturing import texturing_tools
            problem = texturing_tools(settings)[2]
            if problem is None:
                source = ("AI-isolated object", isolated) if isolation else ("unfiltered mesh", mesh)
                texture, warning = texture_result(executable, workspace, source, output, settings, progress, cancel)
                if warning:
                    warnings.append(warning)
            elif settings.openmvs_bin:
                warnings.append(f"Texturing skipped: {problem}")
        published = [stem for stem in OUTPUT_STEMS if stem == "mesh" or (stem == "object" and isolation)]
        for stem in published:
            for extension in ("obj", "glb"):
                os.replace(workspace / f"{stem}.{extension}", output / f"{stem}.{extension}")
        if texture:
            os.replace(workspace / "textured.glb", output / "textured.glb")
        # Never pair a new mesh with an isolated or textured result from an earlier attempt.
        stale = [] if isolation else ["object.obj", "object.glb"]
        stale += [] if texture else ["textured.glb"]
        for name in stale:
            (output / name).unlink(missing_ok=True)
        message = "Experimental mesh exported." + ("" if texture else " Vertex colours only; no UV texture.")
        if isolation:
            message += (f" AI-isolated object kept {isolation['kept_faces']} of {isolation['input_faces']} faces"
                        f" using {isolation['masked_views']} masked views; compare it with the unfiltered mesh.")
        if texture:
            message += (f" Textured {texture['source']} via OpenMVS ({texture['faces']} faces,"
                        f" {texture['texture_size'][0]}×{texture['texture_size'][1]} texture, GLB by {texture['exporter']}).")
        return dict(msg=" ".join([message, *warnings]), lvl="warn" if warnings else "ok", done=True, model_ready=True,
                    object_ready=bool(isolation), textured_ready=bool(texture), isolation=isolation, texture=texture,
                    warnings=warnings)
    except Cancelled:
        return dict(msg="Cancelled; the running process tree was stopped. Earlier results were left unchanged.",
                    lvl="warn", done=False, cancelled=True)
    except subprocess.TimeoutExpired:
        return dict(msg="Reconstruction timed out; its process tree was stopped. Photos remain available.", lvl="bad", done=False)
    finally:
        _heavy_job.release()


def texture_result(colmap, workspace, source, output, settings, progress, cancel):
    """Texture the isolated object (or the whole mesh). Returns (info, warning); failures only warn."""
    from .texturing import clean_mesh, export_textured_glb, textured_obj
    source, mesh = source
    remaining = lambda: max(10, int(settings.timeout_seconds))
    run = lambda command, log: run_logged(command, log, remaining(), cancel)
    try:
        progress("texturing (OpenMVS)")
        cleaned = clean_mesh(mesh)
        cleaned.export(workspace / "texture_input.ply")
        obj = textured_obj(colmap, workspace / "dense" / "0", workspace / "texture_input.ply", workspace / "openmvs",
                           settings, run, output)
        progress("exporting textured GLB")
        info = export_textured_glb(obj, workspace / "textured.glb", settings, run, output)
        warning = info.pop("warning", "")
        return dict(source=source, **info), warning
    except Cancelled:
        raise
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        return None, f"Texturing failed: {error}. Untextured meshes are still available."
