"""One background heavy job at a time, with atomic on-disk status, cancellation and restart recovery.

The job outlives the browser connection: status lives in scans/<id>/job.json and is polled over HTTP.
Stages are the pipeline's real step names; there are no invented percentages or time estimates.
"""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading

from .reconstruction import _heavy_job

STATES = ("running", "ready", "failed", "cancelled", "interrupted")
RESULT_FILES = ("textured.glb", "object.glb", "mesh.glb", "object.obj", "mesh.obj", "reviewed-images.zip",
                "colmap.log", "segmentation.log", "sparse.ply")
# Section headings COLMAP's automatic reconstructor writes to its log, mapped to readable stage names.
COLMAP_STAGES = (("feature extraction", "feature extraction"), ("feature matching", "feature matching"),
                 ("exhaustive", "feature matching"), ("sequential", "feature matching"),
                 ("mapping", "sparse mapping"), ("bundle adjustment", "sparse mapping"),
                 ("undistort", "image undistortion"), ("patch match", "dense stereo"),
                 ("patchmatch", "dense stereo"), ("fusion", "stereo fusion"), ("poisson", "meshing"),
                 ("delaunay", "meshing"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_status(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_status(folder: Path, **fields) -> dict:
    status = {**(read_status(folder) or {}), **fields, "updated": now()}
    temporary = folder / "job.json.tmp"
    temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(temporary, folder / "job.json")
    return status


def colmap_stage(log: Path) -> str | None:
    """Most recent recognised COLMAP section in the tail of its log (what the tool says it is doing)."""
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 65536))
            tail = handle.read().decode("utf-8", "replace").lower()
    except OSError:
        return None
    found = [(tail.rfind(key), label) for key, label in COLMAP_STAGES if key in tail]
    return max(found)[1] if found else None


def describe(folder: Path) -> dict:
    status = read_status(folder) or {}
    files = [name for name in RESULT_FILES if (folder / name).is_file()]
    fallback = None
    try:
        fallback = json.loads((folder / "fallback.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if status.get("state") == "running" and status.get("stage") == "COLMAP automatic reconstruction":
        status["colmap_stage"] = colmap_stage(folder / "colmap.log")
    return dict(scan_id=folder.name, files=files, fallback=fallback, **status)


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self.current = None

    def busy(self) -> bool:
        return self.current is not None or _heavy_job.locked()

    def start(self, scan_id: str, folder: Path, work) -> bool:
        """Run work(progress, cancel) in the background; False when another heavy task is active."""
        with self._lock:
            if self.busy():
                return False
            cancel = threading.Event()
            write_status(folder, state="running", stage="starting", started=now(), finished=None, message="Processing started.",
                         warnings=[], cancel_requested=False)
            thread = threading.Thread(target=self._run, args=(folder, work, cancel), daemon=True)
            self.current = dict(scan_id=scan_id, folder=folder, cancel=cancel, thread=thread)
        thread.start()
        return True

    def _run(self, folder: Path, work, cancel: threading.Event):
        try:
            result = work(lambda stage: write_status(folder, stage=stage), cancel)
            if cancel.is_set() or result.get("cancelled"):
                state = "cancelled"
            else:
                state = "ready" if result.get("model_ready") or result.get("done") else "failed"
            write_status(folder, state=state, stage="finished", finished=now(), message=result.get("msg", ""),
                         warnings=result.get("warnings", []), result={key: result.get(key) for key in
                         ("model_ready", "object_ready", "textured_ready", "isolation", "texture")})
        except Exception:
            logging.exception("Background job failed")
            write_status(folder, state="failed", stage="finished", finished=now(),
                         message="Processing failed unexpectedly; see the server console and colmap.log.")
        finally:
            with self._lock:
                self.current = None

    def cancel(self, scan_id: str) -> bool:
        with self._lock:
            current = self.current
        if not current or current["scan_id"] != scan_id:
            return False
        current["cancel"].set()  # run_logged polls this and stops the whole process tree.
        write_status(current["folder"], cancel_requested=True, message="Cancelling; stopping the running process.")
        return True

    def wait(self, timeout=None):
        current = self.current
        if current:
            current["thread"].join(timeout)


def recover(scans: Path) -> int:
    """After a restart nothing is running, so any job still marked running was interrupted."""
    count = 0
    for status_file in scans.glob("*/job.json"):
        status = read_status(status_file.parent)
        if status and status.get("state") == "running":
            write_status(status_file.parent, state="interrupted", stage="finished", finished=now(),
                         message="The server stopped while this job was running. Start it again for a clean rerun.")
            count += 1
    return count
