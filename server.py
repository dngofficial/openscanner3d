"""Live camera to local clouds, followed by global alignment and fusion. Run python server.py and open localhost:8000."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from threading import Event, RLock
import uuid

import cv2
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np
from pydantic import BaseModel
import uvicorn

from reconstruction import find_colmap, reconstruct
from depth_cloud import ENGINE
from video_capture import Capture

BASE = Path(__file__).resolve().parent
SCANS = BASE / "scans"
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PHOTOS = 200
MAX_PIXELS = 36_000_000
ACTIVE = {"queued", "running"}
OUTPUTS = {"sparse.ply", "dense.ply", "mesh.ply", "mesh.obj", "mesh.glb",
           "results.zip", "preview.json", "report.json", "reconstruction.log",
           "merged.ply", "merged_preview.json", "alignment.json"}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reconstruction")
DEPTH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="depth-loader")
SESSIONS = {}
REGISTRY_LOCK = RLock()


class Session:
    def __init__(self, scan_id, data=None):
        self.id = scan_id
        self.root = SCANS / scan_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "images").mkdir(exist_ok=True)
        (self.root / "thumbs").mkdir(exist_ok=True)
        self.lock = RLock()
        self.cancel = Event()
        self.live_connected = False
        self.data = data or dict(state="collecting", stage="capture", progress=0,
                                 message="Start live capture, then finish to align saved clouds.", photos=[],
                                 registered_images=0, points=0)
        if data and data["state"] in ACTIVE:
            self.data.update(state="failed", stage="interrupted",
                             message="The server stopped during alignment. Captured data is kept; start alignment again.")
        self.persist()

    def persist(self):
        temp = self.root / "session.tmp"
        temp.write_text(json.dumps(self.data), encoding="utf-8")
        temp.replace(self.root / "session.json")

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            self.persist()

    def snapshot(self):
        with self.lock:
            return dict(id=self.id, **self.data,
                        outputs=[name for name in sorted(OUTPUTS) if (self.root / name).is_file()])


def get_session(scan_id):
    if not re.fullmatch(r"[0-9a-f]{32}", scan_id):
        raise HTTPException(404, "Scan not found.")
    with REGISTRY_LOCK:
        if scan_id not in SESSIONS:
            file = SCANS / scan_id / "session.json"
            if not file.is_file():
                raise HTTPException(404, "Scan not found.")
            SESSIONS[scan_id] = Session(scan_id, json.loads(file.read_text(encoding="utf-8")))
        return SESSIONS[scan_id]


def ensure_editable(session):
    if session.data["state"] in ACTIVE:
        raise HTTPException(409, "Wait for reconstruction to finish or cancel it first.")


def invalidate_outputs(session):
    for name in OUTPUTS:
        (session.root / name).unlink(missing_ok=True)
    session.data.update(state="collecting", stage="photos", progress=0,
                        registered_images=0, points=0, accepted_clouds=0, skipped_clouds=0, merged_points=0, message="Photo set updated. Ready to reconstruct.")


def normalize_photo(payload):
    try:
        with Image.open(BytesIO(payload)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use JPEG, PNG or WebP photos. Convert HEIC/RAW images first.")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Photo exceeds the 36-megapixel limit; resize it first.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if min(image.size) < 240:
                raise ValueError("Photo is too small; each side must be at least 240 pixels.")
            image.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
            encoded = BytesIO()
            image.save(encoded, "JPEG", quality=95)
            thumbnail = image.copy()
            thumbnail.thumbnail((320, 240), Image.Resampling.LANCZOS)
            thumb_bytes = BytesIO()
            thumbnail.save(thumb_bytes, "JPEG", quality=85)
            gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
            scale = min(1, 960 / max(gray.shape))
            gray = cv2.resize(gray, None, fx=scale, fy=scale)
            sharpness = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1)
            brightness = round(float(gray.mean()), 1)
            detector = cv2.SIFT_create(nfeatures=2000, contrastThreshold=.02)
            keypoints = detector.detect(gray, None)
            features = len(keypoints)
            xy = np.array([k.pt for k in keypoints], dtype=float).reshape(-1, 2)
            bounds = None
            if len(xy) >= 10:
                bounds = np.percentile(xy, [5, 95], axis=0) / [gray.shape[1], gray.shape[0]]
                bounds = np.round(bounds.ravel(), 4).tolist()
            sample = xy[np.linspace(0, len(xy) - 1, min(80, len(xy)), dtype=int)] if len(xy) else xy
            sample = np.round(sample / [gray.shape[1], gray.shape[0]], 4).tolist()
            warnings = []
            if brightness < 35: warnings.append("Dark image")
            if brightness > 225: warnings.append("Very bright image")
            if sharpness < 25: warnings.append("Possibly soft or blurry")
            if features < 80: warnings.append("Limited surface detail")
            return encoded.getvalue(), thumb_bytes.getvalue(), dict(
                width=image.width, height=image.height, brightness=brightness,
                sharpness=sharpness, features=features, warnings=warnings, feature_points=sample, feature_box=bounds)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Cannot read this photo. Use a valid JPEG, PNG or WebP.") from error


@asynccontextmanager
async def lifespan(app):
    yield
    with REGISTRY_LOCK:
        for session in SESSIONS.values():
            session.cancel.set()
    await asyncio.to_thread(POOL.shutdown, wait=True, cancel_futures=True)
    DEPTH_POOL.shutdown(wait=False, cancel_futures=True)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    if request.method in {"POST", "DELETE", "PUT"}:
        origin = request.headers.get("origin")
        if origin and origin not in {str(request.base_url).rstrip("/")}:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Use this page from the same localhost server."}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(BASE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"colmap": bool(find_colmap()), "max_photos": MAX_PHOTOS, "max_upload_mb": 20, "depth": ENGINE.status()}


@app.post("/api/scans")
def create_scan():
    with REGISTRY_LOCK:
        session = Session(uuid.uuid4().hex)
        SESSIONS[session.id] = session
    return session.snapshot()


@app.get("/api/scans/{scan_id}")
def status(scan_id: str):
    return get_session(scan_id).snapshot()


@app.post("/api/scans/{scan_id}/photos")
async def upload(scan_id: str, request: Request):
    session = get_session(scan_id)
    with session.lock:
        ensure_editable(session)
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_UPLOAD:
            raise HTTPException(413, "Each photo must be 20 MB or smaller.")
    name = request.query_params.get("name", "Photo")[:180]
    try:
        image, thumbnail, info = await asyncio.to_thread(normalize_photo, bytes(payload))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    digest = hashlib.sha256(image).hexdigest()
    with session.lock:
        ensure_editable(session)
        for photo in session.data["photos"]:
            if photo["digest"] == digest:
                return {"duplicate": True, "photo": photo}
        if len(session.data["photos"]) >= MAX_PHOTOS:
            raise HTTPException(400, f"Maximum {MAX_PHOTOS} photos per scan.")
        photo_id = uuid.uuid4().hex
        (session.root / "images" / (photo_id + ".jpg")).write_bytes(image)
        (session.root / "thumbs" / (photo_id + ".jpg")).write_bytes(thumbnail)
        photo = dict(id=photo_id, name=name, digest=digest, **info)
        session.data["photos"].append(photo)
        invalidate_outputs(session)
        session.persist()
    return {"duplicate": False, "photo": photo}


@app.delete("/api/scans/{scan_id}/photos/{photo_id}")
def remove_photo(scan_id: str, photo_id: str):
    session = get_session(scan_id)
    with session.lock:
        ensure_editable(session)
        existing = next((p for p in session.data["photos"] if p["id"] == photo_id), None)
        if not existing:
            raise HTTPException(404, "Photo not found.")
        session.data["photos"].remove(existing)
        (session.root / "images" / (photo_id + ".jpg")).unlink(missing_ok=True)
        (session.root / "thumbs" / (photo_id + ".jpg")).unlink(missing_ok=True)
        (session.root / "depth" / (photo_id + ".npz")).unlink(missing_ok=True)
        (session.root / "clouds" / (photo_id + ".ply")).unlink(missing_ok=True)
        (session.root / "masks" / (photo_id + ".jpg.png")).unlink(missing_ok=True)
        invalidate_outputs(session)
        session.persist()
    return session.snapshot()


@app.get("/api/scans/{scan_id}/thumbs/{photo_id}")
def thumbnail(scan_id: str, photo_id: str):
    session = get_session(scan_id)
    if not re.fullmatch(r"[0-9a-f]{32}", photo_id):
        raise HTTPException(404)
    path = session.root / "thumbs" / (photo_id + ".jpg")
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/scans/{scan_id}/photos/{photo_id}/image")
def photo_image(scan_id: str, photo_id: str):
    session = get_session(scan_id)
    if not re.fullmatch(r"[0-9a-f]{32}", photo_id):
        raise HTTPException(404)
    path = session.root / "images" / (photo_id + ".jpg")
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


class BuildOptions(BaseModel):
    dense: bool = True
    shared_camera: bool = False
    merge_live: bool = False


def run_job(session, options):
    if session.cancel.is_set():
        session.update(state="cancelled", stage="cancelled", message="Reconstruction cancelled.")
        return
    session.update(state="running", message="Starting reconstruction…")
    reconstruct(session.root, options.dense, options.shared_camera, session.cancel, session.update, merge_live=options.merge_live)


@app.post("/api/scans/{scan_id}/build")
def build(scan_id: str, options: BuildOptions):
    session = get_session(scan_id)
    with session.lock:
        ensure_editable(session)
        if len(session.data["photos"]) < 3:
            raise HTTPException(400, "Save at least 3 overlapping camera views. 20–80 is a better starting set.")
        if options.shared_camera and len({(p["width"], p["height"]) for p in session.data["photos"]}) > 1:
            raise HTTPException(400, "Shared camera requires matching image dimensions. Uncheck it for this photo set.")
        if not find_colmap():
            raise HTTPException(400, "COLMAP is missing. Install it, add it to PATH or set COLMAP_EXE, then restart this server.")
        invalidate_outputs(session)
        session.cancel.clear()
        session.update(state="queued", stage="queued", progress=0, message="Queued for reconstruction…")
        POOL.submit(run_job, session, options)
    return session.snapshot()


@app.post("/api/scans/{scan_id}/cancel")
def cancel(scan_id: str):
    session = get_session(scan_id)
    with session.lock:
        if session.data["state"] in ACTIVE:
            session.cancel.set()
            session.update(message="Cancelling; waiting for the current process to stop…")
    return session.snapshot()


@app.get("/api/scans/{scan_id}/files/{filename}")
def result_file(scan_id: str, filename: str):
    session = get_session(scan_id)
    if filename not in OUTPUTS:
        raise HTTPException(404)
    path = session.root / filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, filename=filename, headers={"Cache-Control": "no-store"})


@app.post("/api/depth/load")
def load_depth():
    with ENGINE.status_lock:
        if ENGINE.state not in {"ready", "loading"}:
            ENGINE.state = "loading"
            DEPTH_POOL.submit(ENGINE.load)
    return ENGINE.status()


@app.get("/api/depth")
def depth_status():
    return ENGINE.status()


@app.websocket("/ws/{scan_id}")
async def live_feed(ws: WebSocket, scan_id: str):
    origin = ws.headers.get("origin")
    host = ws.headers.get("host", "")
    if origin and origin not in {f"http://{host}", f"https://{host}"}:
        await ws.close(code=1008); return
    try:
        session = get_session(scan_id)
    except HTTPException:
        await ws.close(code=1008); return
    with session.lock:
        if session.live_connected or session.data["state"] in ACTIVE:
            await ws.close(code=1008); return
        session.live_connected = True
    try:
        await ws.accept()
        capture = await asyncio.to_thread(Capture, session, ENGINE)
        await ws.send_json({"type":"ready", "saved_count":len(session.data["photos"]),
                            "processed":session.data.get("processed_frames",0)})
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect": break
            if message.get("bytes") is not None:
                try:
                    reply = await asyncio.to_thread(capture.process, message["bytes"])
                except Exception as error:
                    reply = dict(type="error", message=str(error))
                await ws.send_json(reply)
            elif message.get("text", "").startswith("{"):
                try:
                    settings=json.loads(message["text"])
                    if settings.get("type")!="settings": raise ValueError("Unknown command")
                    roi=settings.get("roi",[.1,.08,.9,.92])
                    if len(roi)!=4 or not all(isinstance(v,(int,float)) and np.isfinite(v) for v in roi):
                        raise ValueError("Invalid object rectangle.")
                    if not (0<=roi[0]<roi[2]<=1 and 0<=roi[1]<roi[3]<=1):
                        raise ValueError("Invalid object rectangle.")
                    capture.settings=dict(background=bool(settings.get("background",True)),
                                          denoise=bool(settings.get("denoise",True)),roi=roi)
                    with session.lock:
                        session.data["capture_settings"]=capture.settings
                        session.persist()
                    await ws.send_json({"type":"settings","message":"Background cleanup settings updated."})
                except (ValueError,TypeError) as error:
                    await ws.send_json({"type":"command_error","message":str(error)})
            elif message.get("text") == "capture":
                capture.force = True
                await ws.send_json({"type":"capture", "message":"The next processed frame will be saved."})
    except WebSocketDisconnect:
        pass
    finally:
        with session.lock: session.live_connected = False


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
