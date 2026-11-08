"""Local object scanner: the phone captures guided photos directly; the laptop stores and processes them.

Run: python -m backend.app.main
  lan = false: http://localhost:8000 on the laptop only (photo checks, dashboard, reconstruction).
  lan = true:  https://<laptop LAN address>:8443 for the phone (camera and motion sensors need HTTPS).
"""
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid
import zipfile

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import uvicorn

from . import coverage, network, quality
from .config import DETAIL, load_settings, project_path, resolve_tool
from .images import MAX_UPLOAD_BYTES, decode_jpeg, normalize_photo
from .reconstruction import PREVIEWS, _heavy_job, rebuild_object, reconstruct

BASE = Path(__file__).resolve().parents[2]
SCANS = BASE / "scans"
SETTINGS = load_settings()
SCAN_ID = r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}"
PHOTO = r"kf_[0-9]{4}\.jpg"
MIN_PHOTOS = 12
PAGES = {"/": "index.html", "/phone": "phone.html"}
MODULES = {"guidance.mjs", "viewer.mjs"}
DOWNLOADS = {"scan.zip", "object.glb", "object_hires.glb", "object.obj", "object.mtl", "object_albedo.png",
             "mesh.glb", "mesh.obj", "colmap.log"}
_write_lock = threading.Lock()
app = FastAPI()
app.mount("/vendor", StaticFiles(directory=BASE / "frontend" / "vendor"), name="vendor")  # Three.js, served locally.
phone_seen = 0.0  # monotonic time of the last request from a phone holding the key


# ---- access control ------------------------------------------------------
@app.middleware("http")
async def guard(request: Request, call_next):
    kind = network.client_kind(request.client.host if request.client else None)
    if kind == "outside":
        return PlainTextResponse("This scanner only answers devices on the local network.", status_code=403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin and origin not in (f"https://{request.headers.get('host')}", f"http://{request.headers.get('host')}"):
            return PlainTextResponse("Cross-site request refused.", status_code=403)
    if kind == "lan":
        offered = request.query_params.get("key")
        if network.key_matches(offered):
            # Move the key from the URL into a cookie, then reload without it.
            response = RedirectResponse(request.url.remove_query_params("key"), status_code=303)
            # QR scanners can start a cross-site navigation; Lax permits its top-level GET redirect.
            response.set_cookie(network.COOKIE, network.ACCESS_KEY, httponly=True, secure=True, samesite="lax")
            response.headers["Cache-Control"] = "no-store"
            return response
        if not network.key_matches(request.cookies.get(network.COOKIE)):
            return PlainTextResponse("Open the link or scan the QR code shown on the laptop's scanner page.", status_code=401)
        global phone_seen
        phone_seen = time.monotonic()
    return await call_next(request)


# ---- pages ---------------------------------------------------------------
@app.get("/")
@app.get("/phone")
def page(request: Request):
    return FileResponse(BASE / "frontend" / PAGES[request.url.path], headers={"Cache-Control": "no-store"})


@app.get("/{module}.mjs")
def module(module: str):
    if f"{module}.mjs" not in MODULES:
        raise HTTPException(404)
    return FileResponse(BASE / "frontend" / f"{module}.mjs", media_type="text/javascript")


@app.get("/api/plan")
def capture_plan():
    return coverage.plan()


@app.get("/api/connect")
def connect(request: Request):
    """Phone links for the laptop screen. Only the laptop itself may read the key."""
    if network.client_kind(request.client.host if request.client else None) != "local":
        raise HTTPException(403)
    addresses = network.lan_addresses()
    urls = [f"https://{address}:{SETTINGS.port}/phone?key={network.ACCESS_KEY}" for address in addresses]
    certified = network.certificate_addresses(SETTINGS)
    problems = []
    if not SETTINGS.lan:
        problems.append('LAN mode is off. Set lan = true in config.local.toml and restart, so the phone can connect.')
    if not project_path(SETTINGS.tls_cert).is_file() or not project_path(SETTINGS.tls_key).is_file():
        problems.append("No HTTPS certificate yet. Run: .venv/Scripts/python.exe -m scripts.make_cert")
    elif certified is not None and addresses and addresses[0] not in certified:
        problems.append(f"This laptop's address {addresses[0]} is not in the certificate ({', '.join(certified)}). "
                        "Rerun scripts.make_cert after changing networks.")
    if not addresses:
        problems.append("No private network address found. Connect the laptop to the same Wi-Fi as the phone.")
    svg = None
    if urls and SETTINGS.lan and project_path(SETTINGS.tls_cert).is_file():  # Never show a link that cannot work.
        import segno
        svg = segno.make(urls[0], error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")
    seen = round(time.monotonic() - phone_seen) if phone_seen else None
    return dict(lan=SETTINGS.lan, urls=urls, qr_svg=svg, problems=problems, mode=SETTINGS.mode, phone_seen_seconds=seen)


# ---- scans ---------------------------------------------------------------
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def folder(scan_id: str) -> Path:
    if not re.fullmatch(SCAN_ID, scan_id) or not (SCANS / scan_id / "scan.json").is_file():
        raise HTTPException(404, "Unknown scan")
    return SCANS / scan_id


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def describe(scan: Path) -> dict:
    meta, poses = read(scan / "scan.json", {}), read(scan / "poses.json", [])
    obj = read(scan / "object.json", None)
    masks = read(scan / PREVIEWS / "masks.json", None)
    return dict(id=scan.name, **meta, photos=poses, coverage=coverage.summarise(poses),
                status=read(scan / "status.json", None),
                files=[name for name in sorted(DOWNLOADS) if (scan / name).is_file()],
                object=obj and dict(detail=obj.get("detail"), triangles=(obj.get("game_asset") or {}).get("triangles"),
                                    texture=(obj.get("game_asset") or {}).get("texture_size"), clipped=obj.get("clipped")),
                masks=masks, rebuildable=(scan / "run.json").is_file())


@app.post("/api/scans")
def create_scan():
    scan_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    (SCANS / scan_id / "images").mkdir(parents=True)
    write(SCANS / scan_id / "poses.json", [])
    write(SCANS / scan_id / "scan.json", dict(created=now(), finished=False, size=None))
    return dict(id=scan_id, plan=coverage.plan())


@app.get("/api/scans")
def list_scans():
    scans = sorted((path for path in SCANS.glob("*") if re.fullmatch(SCAN_ID, path.name) and (path / "scan.json").is_file()),
                   reverse=True)[:50]
    return [describe(scan) for scan in scans]


@app.get("/api/scans/{scan_id}")
def scan_details(scan_id: str):
    return describe(folder(scan_id))


def add_photo(scan: Path, data: bytes, azimuth: float | None, pitch: float | None, imported: bool = False,
              tap: tuple[float, float] | None = None) -> dict:
    """Check and store one photo. Imported gallery photos (the no-live-camera fallback) are kept as uploaded in
    originals/ and stored upright, at most 3200 px, as the working JPEG; they carry no angle data."""
    original = None
    if imported:
        try:
            original = data
            data, suffix = normalize_photo(data)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            return dict(accepted=False, reason="Not a readable photo. Upload JPEG, PNG, or WebP files.")
        except ValueError as error:
            return dict(accepted=False, reason=str(error))
        azimuth = pitch = None
    with _write_lock:
        meta, poses = read(scan / "scan.json", {}), read(scan / "poses.json", [])
        if meta.get("finished"):
            return dict(accepted=False, reason="This scan is finished. Start a new scan to add photos.")
        if len(poses) >= SETTINGS.max_photos:
            return dict(accepted=False, reason=f"{SETTINGS.max_photos}-photo limit reached. Tap Finish.")
        try:
            image, size = decode_jpeg(data, tuple(meta["size"]) if meta.get("size") else None, MAX_UPLOAD_BYTES)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            return dict(accepted=False, reason="Expected a JPEG photo from the phone camera.")
        except ValueError as error:
            reason = str(error)
            if "orientation changed" in reason:
                reason = "Keep the phone in the same orientation (portrait or landscape) as the first photo."
            return dict(accepted=False, reason=reason)
        values = quality.measurements(image)
        problem = quality.problem(values)
        if problem:
            return dict(accepted=False, reason=problem, quality=values)
        name = f"kf_{len(poses):04d}.jpg"
        (scan / "images" / name).write_bytes(data)
        if original is not None:
            (scan / "originals").mkdir(exist_ok=True)
            (scan / "originals" / f"kf_{len(poses):04d}{suffix}").write_bytes(original)
        ring = coverage.ring_of(pitch) if pitch is not None and azimuth is not None else None
        poses.append(dict(name=name, azimuth=azimuth, pitch=pitch, ring=ring,
                          sector=coverage.sector_of(azimuth) if ring is not None else None,
                          imported=imported, time=now(), quality={key: round(value, 3) for key, value in values.items()}))
        if tap and not any(pose.get("tap") for pose in poses):
            poses[-1]["tap"] = [round(tap[0], 4), round(tap[1], 4)]  # Where the object is, as image fractions.
        write(scan / "poses.json", poses)
        if not meta.get("size"):
            write(scan / "scan.json", {**meta, "size": list(size)})
        return dict(accepted=True, name=name, ring=poses[-1]["ring"], sector=poses[-1]["sector"],
                    coverage=coverage.summarise(poses))


@app.post("/api/scans/{scan_id}/photos")
async def upload_photo(scan_id: str, request: Request,
                       azimuth: float | None = Query(None, ge=-720, le=720), pitch: float | None = Query(None, ge=-90, le=90),
                       imported: bool = Query(False),
                       tap_x: float | None = Query(None, ge=0, le=1), tap_y: float | None = Query(None, ge=0, le=1)):
    scan = folder(scan_id)
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Photo exceeds 25 MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Photo exceeds 25 MB")
    tap = (tap_x, tap_y) if tap_x is not None and tap_y is not None else None
    return await asyncio.to_thread(add_photo, scan, bytes(data), azimuth, pitch, imported, tap)


@app.delete("/api/scans/{scan_id}/photos/last")
def remove_last(scan_id: str):
    scan = folder(scan_id)
    with _write_lock:
        meta, poses = read(scan / "scan.json", {}), read(scan / "poses.json", [])
        if meta.get("finished") or not poses:
            raise HTTPException(409, "Nothing to undo")
        removed = poses.pop()
        (scan / "images" / removed["name"]).unlink(missing_ok=True)
        write(scan / "poses.json", poses)
        return dict(removed=removed["name"], coverage=coverage.summarise(poses))


@app.post("/api/scans/{scan_id}/finish")
def finish(scan_id: str):
    scan = folder(scan_id)
    with _write_lock:
        meta, poses = read(scan / "scan.json", {}), read(scan / "poses.json", [])
        if len(poses) < MIN_PHOTOS:
            raise HTTPException(409, f"Only {len(poses)} photos; take at least {MIN_PHOTOS} before finishing.")
        meta = {**meta, "finished": True, "finished_at": now()}
        write(scan / "scan.json", meta)
        temporary = scan / "scan.zip.tmp"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
            for pose in poses:
                bundle.write(scan / "images" / pose["name"], "images/" + pose["name"])
            bundle.writestr("poses.json", json.dumps(poses, indent=2))
            bundle.writestr("scan.json", json.dumps(meta, indent=2))
        os.replace(temporary, scan / "scan.zip")
    return describe(scan)


@app.post("/api/scans/{scan_id}/reconstruct")
def start_reconstruction(scan_id: str):
    scan = folder(scan_id)
    if not read(scan / "scan.json", {}).get("finished"):
        raise HTTPException(409, "Finish the scan first.")
    if SETTINGS.mode != "reconstruct":
        raise HTTPException(409, "This laptop is in capture mode. Download scan.zip and reconstruct on the GPU laptop.")
    if _heavy_job.locked():
        raise HTTPException(409, "Another reconstruction is running.")
    write(scan / "status.json", dict(state="running", started=now(), message="COLMAP reconstruction running."))

    def run():
        try:
            result = reconstruct(scan / "images", scan, SETTINGS)
            state = "ready" if result.get("model_ready") else "failed"
            write(scan / "status.json", dict(state=state, finished=now(), message=result.get("msg", "")))
        except Exception:
            logging.exception("Reconstruction failed")
            write(scan / "status.json", dict(state="failed", finished=now(), message="Reconstruction failed; see the server console."))
    threading.Thread(target=run, daemon=True).start()
    return JSONResponse(dict(state="running"), status_code=202)


@app.post("/api/scans/{scan_id}/object")
def start_object_rebuild(scan_id: str, detail: str = Query(..., pattern="^(low|standard|high)$")):
    """Rebuild only the object model from the existing COLMAP run, e.g. as a low-poly asset."""
    scan = folder(scan_id)
    if SETTINGS.mode != "reconstruct":
        raise HTTPException(409, "This laptop is in capture mode.")
    if not (scan / "run.json").is_file():
        raise HTTPException(409, "Reconstruct the scan first.")
    if _heavy_job.locked():
        raise HTTPException(409, "Another reconstruction is running.")
    triangles, texture = DETAIL[detail]
    write(scan / "status.json", dict(state="running", started=now(),
                                     message=f"Rebuilding the object: {detail} detail ({triangles} triangles, {texture} px texture)."))

    def run():
        try:
            result = rebuild_object(scan, SETTINGS, detail)
            write(scan / "status.json", dict(state="ready" if result.get("done") else "failed", finished=now(),
                                             message=result.get("msg", "")))
        except Exception:
            logging.exception("Object rebuild failed")
            write(scan / "status.json", dict(state="failed", finished=now(), message="Object rebuild failed; see the server console."))
    threading.Thread(target=run, daemon=True).start()
    return JSONResponse(dict(state="running"), status_code=202)


@app.get("/scans/{scan_id}/masks/{name}")
def mask_preview(scan_id: str, name: str):
    if not re.fullmatch(PHOTO, name):
        raise HTTPException(404)
    path = folder(scan_id) / PREVIEWS / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/scans/{scan_id}/images/{name}")
def photo(scan_id: str, name: str):
    if not re.fullmatch(PHOTO, name):
        raise HTTPException(404)
    path = folder(scan_id) / "images" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.get("/scans/{scan_id}/{name}")
def download(scan_id: str, name: str):
    path = folder(scan_id) / name
    if name not in DOWNLOADS or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=f"{scan_id}-{name}")


@app.get("/api/health")
def health():
    return dict(status="ok", mode=SETTINGS.mode, lan=SETTINGS.lan, colmap_found=bool(resolve_tool(SETTINGS.colmap)),
                detail=SETTINGS.detail, details={key: dict(triangles=t, texture=x) for key, (t, x) in DETAIL.items()})


def recover():
    """Nothing runs across restarts, so a reconstruction still marked running was interrupted."""
    for status in SCANS.glob("*/status.json"):
        if read(status, {}).get("state") == "running":
            write(status, dict(state="interrupted", finished=now(), message="The server stopped during reconstruction."))


def print_qr(url: str, out=None):
    """Draw the phone link as a terminal QR code when the console can show it.

    Windows consoles often use cp1252, which has no block characters; there the dashboard shows the QR
    code instead. Never let this stop the server from starting.
    """
    import io
    import segno
    out = out or sys.stdout
    drawing = io.StringIO()
    segno.make(url, error="m").terminal(out=drawing, compact=True)
    try:
        out.write(drawing.getvalue())
        out.flush()
    except (UnicodeEncodeError, OSError):
        print("(Open https://localhost:%d on this laptop for the QR code.)" % SETTINGS.port, file=out)


def serve():
    SCANS.mkdir(exist_ok=True)
    recover()
    if not SETTINGS.lan:
        print("Scanner on http://localhost:8000 (this laptop only). Set lan = true to connect a phone.")
        uvicorn.run(app, host="127.0.0.1", port=8000)
        return
    cert, key = project_path(SETTINGS.tls_cert), project_path(SETTINGS.tls_key)
    if not cert.is_file() or not key.is_file():
        sys.exit("LAN mode needs an HTTPS certificate. Run: .venv/Scripts/python.exe -m scripts.make_cert")
    addresses = network.lan_addresses()
    print(f"Scanner on https://localhost:{SETTINGS.port} (laptop dashboard shows the phone QR code).")
    if addresses:
        url = f"https://{addresses[0]}:{SETTINGS.port}/phone?key={network.ACCESS_KEY}"
        print(f"Phone link (same Wi-Fi): {url}")
        print_qr(url)
    # Listen on all interfaces; the middleware still refuses non-private clients and requests without the key.
    uvicorn.run(app, host="0.0.0.0", port=SETTINGS.port, ssl_certfile=str(cert), ssl_keyfile=str(key))


if __name__ == "__main__":
    serve()
