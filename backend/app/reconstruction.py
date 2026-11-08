"""Bounded adapter around the existing, experimental COLMAP mesh pipeline."""
import os
from pathlib import Path
import subprocess
import threading
import uuid

from .config import Settings, resolve_tool
from .silhouette import reconstruct_object

_heavy_job = threading.Lock()
REQUIRED_FLAGS = ("--workspace_path", "--image_path", "--quality", "--single_camera", "--mesher")
OBJECT_FILES = ("object.glb", "object_hires.glb", "object.obj", "object.mtl", "object_albedo.png")


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


def reconstruct(images: Path, output: Path, settings: Settings) -> dict:
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
        code = run_logged(command, output / "colmap.log", settings.timeout_seconds)
        if code:
            return dict(msg=f"COLMAP exited with code {code}. See colmap.log.", lvl="bad", done=False)
        mesh_path = workspace / "dense" / "0" / "meshed-poisson.ply"
        if not mesh_path.is_file():
            return dict(msg="No dense Poisson mesh was produced. Inspect colmap.log and the installed backend's GPU support.", lvl="bad", done=False)
        import numpy as np
        import trimesh
        mesh = trimesh.load(mesh_path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
            raise ValueError("COLMAP output is not a nonempty finite triangle mesh")
        for extension in ("obj", "glb"):
            destination = workspace / f"mesh.{extension}"
            mesh.export(destination)
        reloaded = trimesh.load(workspace / "mesh.glb", force="mesh")
        if not len(reloaded.faces):
            raise ValueError("Exported GLB contains no faces")
        for extension in ("obj", "glb"):
            os.replace(workspace / f"mesh.{extension}", output / f"mesh.{extension}")
        message = "Scene mesh exported (COLMAP dense + Poisson)."
        for name in OBJECT_FILES:
            (output / name).unlink(missing_ok=True)
        try:
            # Plain objects give dense stereo little to work with; their outlines still define the shape.
            info = reconstruct_object(workspace, images, workspace)
            for name in OBJECT_FILES:
                if (workspace / name).is_file():
                    os.replace(workspace / name, output / name)
            message += (f" Object from {info['photos']} outlines: object_hires.glb ({info['faces']} faces, vertex colours)."
                        + (" It touches the search box edge, so part may be cut off." if info["clipped"] else ""))
            if info.get("game_asset"):
                message += f" Game asset: object.glb ({info['game_asset']['triangles']} triangles, photo-baked texture)."
            if info.get("warnings"):
                message += " " + " ".join(info["warnings"])
            return dict(msg=message, lvl="warn" if info["clipped"] or info.get("warnings") else "ok",
                        done=True, model_ready=True, object=info)
        except (ValueError, OSError, np.linalg.LinAlgError) as error:
            for name in OBJECT_FILES:
                (output / name).unlink(missing_ok=True)  # Never pair a new scene with an old object.
            return dict(msg=f"{message} Object reconstruction failed: {error}.", lvl="warn", done=True, model_ready=True)
    except subprocess.TimeoutExpired:
        return dict(msg="Reconstruction timed out; its process tree was stopped. Photos remain available.", lvl="bad", done=False)
    finally:
        _heavy_job.release()
