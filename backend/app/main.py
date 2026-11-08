"""Live 3D scan guide. Run: python -m backend.app.main -> http://localhost:8000
Pipeline: JPEG frames -> changed-view photo capture + independent SIFT/PnP live map.
On Finish: keyframes go to COLMAP (dense + Poisson mesh) -> scan/mesh.obj + scan/mesh.glb
"""
import asyncio, math, uuid, logging, time, threading
from contextlib import asynccontextmanager
import json, shutil, re, zipfile
from dataclasses import asdict
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError
from typing import Annotated, Literal, Union
from pydantic import TypeAdapter
from PIL import Image, UnidentifiedImageError
from datetime import datetime
from pathlib import Path
import cv2, numpy as np, uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import load_settings, resolve_tool
from .reconstruction import reconstruct
from .segmentation import run_segmentation, segmentation_tools
from .jobs import Jobs, describe, recover
from .images import decode_jpeg, normalize_photo, MAX_FRAME_BYTES, MAX_UPLOAD_BYTES
from .capture_quality import continuity, exposure_problem, frame_problem
from .masks import region_mask

BASE = Path(__file__).resolve().parents[2]
SCANS = BASE / "scans"
SETTINGS = load_settings()
AZ, EL_EDGES = 12, (20, 50)          # 12 azimuth sectors x 3 elevation rings
KF_DEG = 6                           # new keyframe every 6 deg of orbit
TURN_MIN_CHANGE = 6                  # mean grey-level change between saved views when turning the object
bf = cv2.BFMatcher(cv2.NORM_L2)
JOBS = Jobs()
SEGMENTING = {}                      # scan id -> cancel event for a running AI selection
SCAN_ID = r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}"


@asynccontextmanager
async def lifespan(_):
    SCANS.mkdir(exist_ok=True)
    if interrupted := recover(SCANS):
        logging.warning("Marked %d unfinished job(s) as interrupted", interrupted)
    yield
    # Do not leave a reconstruction process tree running after the server exits.
    if JOBS.current:
        JOBS.cancel(JOBS.current["scan_id"])
        JOBS.wait(20)
    for event in SEGMENTING.values():
        event.set()


app = FastAPI(lifespan=lifespan)
app.mount("/vendor", StaticFiles(directory=BASE / "frontend" / "vendor"), name="vendor")


class ConfirmSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["confirm"]
    selected: list[StrictStr] = Field(min_length=12, max_length=500)


Fraction = Annotated[float, Field(ge=0, le=1)]


class Prompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: StrictStr
    # (x, y) as fractions of the image; label 1 = object, 0 = not the object.
    points: list[tuple[Fraction, Fraction, Literal[0, 1]]] = Field(min_length=1, max_length=20)


class SegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["segment"]
    selected: list[StrictStr] = Field(min_length=2, max_length=500)
    prompts: list[Prompt] = Field(min_length=1, max_length=20)


ClientRequest = TypeAdapter(Annotated[Union[ConfirmSelection, SegmentRequest], Field(discriminator="type")])
# Recorded with each selection; machine paths (tools, checkpoints) stay out of the shared ZIP.
SHARED_SETTINGS = ("mode", "quality", "max_photos", "feature_mask_regions", "sam2_config")


def match(a, b):
    return [m[0] for m in bf.knnMatch(a, b, k=2) if len(m) == 2 and m[0].distance < .75 * m[1].distance]


class Scan:
    def __init__(s):
        s.id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        s.out = SCANS / s.id
        s.img = s.out / "images"
        s.sift = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.015, edgeThreshold=15)
        s.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        s.reset()

    def reset(s):
        s.img.mkdir(parents=True, exist_ok=True)
        s.overlay = None; s.previous_features = None
        s.import_next = False; s.frame_shape = None
        s.state = "preview"
        s.motion = "orbit"           # orbit: camera moves around a still object; turn: object turned by hand
        s.segmentation = None        # {"frames": {name: mask area}, "version": id} after AI selection
        s.accepted = False
        s.photos = []; s.last_photo = None; s.force_capture = False; s.capture_note = "Waiting for a usable frame"
        s.K = None; s.feature_mask = None; s.kfs = []; s.pts = np.zeros((0, 3)); s.ptd = np.zeros((0, 128), np.float32)
        s.bins = np.zeros((len(EL_EDGES) + 1, AZ), int); s.cur = None; s.size = 0

    # ---- geometry helpers -------------------------------------------------
    def center(s):
        return np.median(s.pts, 0) if len(s.pts) >= 30 else None

    def angles(s, R, t):
        c = s.center()
        if c is None: return None
        v = (-R.T @ t) - c; v /= np.linalg.norm(v) + 1e-9
        # world "up" = -y of the first camera (y points down in OpenCV)
        return math.atan2(v[0], v[2]), math.degrees(math.asin(np.clip(-v[1], -1, 1)))

    def bin_of(s, az, el):
        return sum(el >= e for e in EL_EDGES), int((az + math.pi) / (2 * math.pi) * AZ) % AZ

    def tri(s, ka, R, t, pa, pb, db, mask=None):
        Pa = s.K @ np.hstack([ka["R"], ka["t"][:, None]]); Pb = s.K @ np.hstack([R, t[:, None]])
        X = cv2.triangulatePoints(Pa, Pb, pa.T.astype(float), pb.T.astype(float)); 
        with np.errstate(divide="ignore", invalid="ignore"):
            X = (X[:3] / X[3]).T
        ok = np.ones(len(X), bool) if mask is None else mask.astype(bool)
        ok &= np.isfinite(X).all(axis=1)
        for Rk, tk, p in ((ka["R"], ka["t"], pa), (R, t, pb)):
            c = X @ Rk.T + tk; ok &= c[:, 2] > 0
            pr = c @ s.K.T; ok &= np.linalg.norm(pr[:, :2] / pr[:, 2:3] - p, axis=1) < 2
        s.pts = np.vstack([s.pts, X[ok]]); s.ptd = np.vstack([s.ptd, db[ok]])

    def save_view(s, xy, d, jpg, gray):
        now = time.monotonic()
        forced = s.force_capture
        s.force_capture = False
        if len(s.photos) >= SETTINGS.max_photos:
            s.capture_note = f"{SETTINGS.max_photos}-photo limit reached; finish this scan."
            return
        if s.motion == "turn":
            return s.save_turned_view(jpg, gray, now, forced)
        if s.last_photo is not None:
            if not forced and now - s.last_photo["time"] < .4:
                s.capture_note = "Waiting between captures"
                return
            valid, motion, message = continuity(s.last_photo["xy"], s.last_photo["d"], xy, d, gray.shape[1], gray.shape[0])
            if not valid:
                s.capture_note = message
                return
            if not forced and motion < 8:
                s.capture_note = f"Move a small step around the stationary object ({motion:.1f}/8 px change)"
                return
        name = f"kf_{len(s.photos):04d}.jpg"
        (s.img / name).write_bytes(jpg)
        s.photos.append(name)
        s.last_photo = dict(xy=xy, d=d, time=now)
        s.accepted = True
        s.capture_note = f"Saved photo {len(s.photos)}" + (" manually" if forced else "")

    def save_turned_view(s, jpg, gray, now, forced):
        # The background stays still while the object turns, so whole-scene continuity does not apply.
        # Save a changed view at a steady cadence; review and per-frame AI masks remove bad frames.
        thumb = cv2.resize(gray, (160, max(1, round(160 * gray.shape[0] / gray.shape[1])))).astype(np.float32)
        if s.last_photo is not None and not forced:
            if now - s.last_photo["time"] < .7:
                s.capture_note = "Waiting between captures"
                return
            change = float(np.mean(np.abs(thumb - s.last_photo["thumb"])))
            if change < TURN_MIN_CHANGE:
                s.capture_note = f"Turn the object a little more ({change:.1f}/{TURN_MIN_CHANGE} change)"
                return
        name = f"kf_{len(s.photos):04d}.jpg"
        (s.img / name).write_bytes(jpg)
        s.photos.append(name)
        s.last_photo = dict(thumb=thumb, time=now)
        s.accepted = True
        s.capture_note = f"Saved photo {len(s.photos)}" + (" manually" if forced else "")

    def add_kf(s, R, t, xy, d, jpg):
        s.kfs.append(dict(R=R, t=t, xy=xy, d=d, c=-R.T @ t))
        a = s.angles(R, t)
        if a: s.bins[s.bin_of(*a)] += 1

    def visual_features(s, xy, d, width, height):
        points = xy[np.linspace(0, len(xy) - 1, min(250, len(xy)), dtype=int)] if len(xy) else xy
        box = None
        if len(xy) >= 10:
            lo, hi = np.percentile(xy, [5, 95], axis=0)
            box = [*lo.tolist(), *hi.tolist()]
        trails = []
        if d is not None and s.previous_features is not None:
            old_xy, old_d = s.previous_features
            pairs = match(old_d, d)
            for m in pairs[:80]:
                trails.append([*old_xy[m.queryIdx].tolist(), *xy[m.trainIdx].tolist()])
        s.previous_features = (xy.copy(), d.copy()) if d is not None else None
        s.overlay = dict(width=width, height=height, points=np.round(points, 1).tolist(),
                         box=box, trails=np.round(trails, 1).tolist(), inliers=[])

    # ---- feedback ---------------------------------------------------------
    def fb(s, msg, lvl="warn", **kw):
        return dict(msg=msg, lvl=lvl, kfs=len(s.photos), state=s.state, motion=s.motion,
                    last_saved=s.photos[-1] if s.photos else None,
                    tracked_views=len(s.kfs), capture=s.capture_note,
                    tracking="Mapping" if len(s.pts) else "Initializing", pts=len(s.pts), bins=s.bins.ravel().tolist(),
                    cur=s.cur, size=round(s.size, 2), overlay=s.overlay, **kw)

    def guide(s, R, t):
        a = s.angles(R, t)
        if a is None: return s.fb("Mapping started. Keep moving slowly sideways.", "ok")
        s.cur = [a[0], a[1]]
        done = (s.bins > 0).mean()
        if done >= .8: return s.fb("Estimated tracking coverage reached. Review your photos before finishing.", "ok")
        cr, cc = s.bin_of(*a)
        best = min(((r, c) for r, c in np.argwhere(s.bins == 0)),
                   key=lambda rc: min(abs(rc[1] - cc), AZ - abs(rc[1] - cc)) + 2 * abs(rc[0] - cr))
        tr, tc = best
        d_az = ((tc - cc + AZ / 2) % AZ - AZ / 2) * 360 / AZ    # + = camera moves to its left (from start view)
        parts = []
        if abs(d_az) >= 15: parts.append(f"orbit {'left' if d_az > 0 else 'right'} about {abs(int(d_az))}°")
        if tr > cr: parts.append("raise the camera")
        if tr < cr: parts.append("lower the camera")
        return s.fb(" and ".join(parts).capitalize() + f". {int(done * 100)}% covered." if parts
                    else "Hold here and tilt slightly.", "ok")

    def distance(s, R, t, W, H):
        c = s.center()
        if c is None: return None
        near = s.pts[np.linalg.norm(s.pts - c, axis=1) < np.percentile(np.linalg.norm(s.pts - c, axis=1), 70)]
        cam = near @ R.T + t; cam = cam[cam[:, 2] > 0]
        if len(cam) < 20: return None
        p = cam @ s.K.T; p = p[:, :2] / p[:, 2:3]
        lo, hi = np.percentile(p, 10, 0), np.percentile(p, 90, 0)
        s.size = max((hi[0] - lo[0]) / W, (hi[1] - lo[1]) / H)
        return "Move closer. The object is small in frame." if s.size < .3 else \
               "Move back. The object is filling the frame." if s.size > .8 else None

    # ---- per frame --------------------------------------------------------
    def decode(s, jpg, max_bytes=MAX_FRAME_BYTES):
        full, shape = decode_jpeg(jpg, s.frame_shape, max_bytes)
        s.frame_shape = shape
        return full

    def import_photo(s, jpg):
        s.overlay = None
        if s.state not in {"preview", "paused", "review"}:
            return s.fb("Pause capture before importing photos, or start a new scan after completion.", "bad", accepted=False)
        try:
            normalized, suffix = normalize_photo(jpg)
            s.decode(normalized, MAX_UPLOAD_BYTES)
        except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            return s.fb(str(error), "bad", accepted=False)
        if len(s.photos) >= SETTINGS.max_photos:
            return s.fb("Photo limit reached; finish this scan.", "warn", accepted=False)
        name = f"kf_{len(s.photos):04d}.jpg"
        originals = s.out / "originals"
        originals.mkdir(exist_ok=True)
        (originals / f"kf_{len(s.photos):04d}{suffix}").write_bytes(jpg)
        (s.img / name).write_bytes(normalized)
        s.photos.append(name)
        s.last_photo = None
        s.state = "paused"
        s.capture_note = f"Imported photo {len(s.photos)}"
        return s.fb(s.capture_note, "ok", accepted=True)

    def process(s, jpg):
        s.accepted = False
        if s.state != "capturing":
            return s.fb("Preview only. Press Start scan to save photos.", "ok", accepted=False)
        result = s.process_active(jpg)
        # Give the capture decision priority over optional live mapping guidance.
        result.update(accepted=s.accepted, msg=s.capture_note, capture=s.capture_note)
        result["lvl"] = "ok" if s.accepted else "warn"
        return result

    def process_active(s, jpg):
        s.overlay = None
        try:
            full = s.decode(jpg)
        except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            s.capture_note = str(error)
            return s.fb(str(error), "bad")
        img = cv2.resize(full, (960, int(960 * full.shape[0] / full.shape[1])))
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); H, W = g.shape
        if s.K is None:
            s.K = np.array([[.85 * W, 0, W / 2], [0, .85 * W, H / 2], [0, 0, 1]])
            # Static overlays would otherwise produce zero-motion matches in every continuity check.
            s.feature_mask = region_mask(W, H, SETTINGS.feature_mask_regions) if SETTINGS.feature_mask_regions else None
        blur = round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1)
        brightness, contrast = round(float(g.mean()), 1), round(float(g.std()), 1)
        enhanced = s.clahe.apply(g)
        kp, d = s.sift.detectAndCompute(enhanced, s.feature_mask)
        xy = np.float32([k.pt for k in kp]).reshape(-1, 2)
        s.visual_features(xy, d, W, H)
        info = dict(blur=blur, feats=len(kp), brightness=brightness, contrast=contrast)
        if d is None or len(kp) < 40:
            msg = exposure_problem(g) or f"Only {len(kp)} trackable features (need 40). Focus on a detailed matte object; include nearby textured surroundings."
            s.capture_note = msg
            return s.fb(msg, "warn", **info)
        problem = frame_problem(g, blur, contrast)
        if problem:
            s.force_capture = False
            s.capture_note = problem
            return s.fb(problem, "warn", **info)
        info["quality"] = "Frame passed initial quality checks"
        s.save_view(xy, d, jpg, g)
        if s.motion == "turn":
            return s.fb(s.capture_note, "ok", **info)  # Camera-pose tracking assumes a still object.

        if not s.kfs:
            s.add_kf(np.eye(3), np.zeros(3), xy, d, jpg)
            return s.fb("First view saved. Slide the camera sideways, slowly.", "ok", **info)

        if len(s.pts) == 0:                                   # two-view initialisation
            k0 = s.kfs[0]; m = match(k0["d"], d)
            if len(m) < 40: return s.fb("Lost the first view. Return to where you started.", "bad", **info)
            p0 = k0["xy"][[x.queryIdx for x in m]]; p1 = xy[[x.trainIdx for x in m]]
            if np.median(np.linalg.norm(p1 - p0, axis=1)) < 12: return s.fb("Photos are being saved. Move sideways to initialize live tracking.", "warn", **info)
            E, mk = cv2.findEssentialMat(p0, p1, s.K, cv2.RANSAC, .999, 1.0)
            if E is None or E.shape != (3, 3): return s.fb("Could not initialise. Try a more textured view.", "warn", **info)
            _, R, t, mk = cv2.recoverPose(E, p0, p1, s.K, mask=mk)
            s.tri(k0, R, t.ravel(), p0, p1, d[[x.trainIdx for x in m]], mk.ravel())
            if len(s.pts) < 30: s.pts = s.pts[:0]; s.ptd = s.ptd[:0]; return s.fb("Not enough parallax. Move further sideways.", "warn", **info)
            s.add_kf(R, t.ravel(), xy, d, jpg)
            return s.fb("Map initialised. Keep orbiting the object.", "ok", **info)

        dm = match(s.ptd, d); lost = s.fb("Tracking lost. Go back to the last good position and slow down.", "bad", **info)
        if len(dm) < 30: return lost
        ok, rv, tv, inl = cv2.solvePnPRansac(s.pts[[x.queryIdx for x in dm]], xy[[x.trainIdx for x in dm]],
                                             s.K, None, reprojectionError=4, iterationsCount=200)
        if not ok or inl is None or len(inl) < 25: return lost
        s.overlay["inliers"] = np.round(xy[[dm[int(i)].trainIdx for i in inl.ravel()]][:150], 1).tolist()
        R, t = cv2.Rodrigues(rv)[0], tv.ravel(); a = s.angles(R, t)

        if a:                                                  # new keyframe if far enough from all others
            c = s.center(); v = (-R.T @ t) - c
            def sep(k):
                w = k["c"] - c; return math.degrees(math.acos(np.clip(v @ w / (np.linalg.norm(v) * np.linalg.norm(w) + 1e-9), -1, 1)))
            seps = [sep(k) for k in s.kfs]; j = int(np.argmin(seps))
            if seps[j] > KF_DEG:
                ka = s.kfs[j]; m = match(ka["d"], d)
                if len(m) >= 40:
                    s.tri(ka, R, t, ka["xy"][[x.queryIdx for x in m]], xy[[x.trainIdx for x in m]], d[[x.trainIdx for x in m]])
                s.add_kf(R, t, xy, d, jpg)

        dist = s.distance(R, t, W, H)
        r = s.guide(R, t)
        r.update(info)
        if dist: r["msg"], r["lvl"] = dist, "warn"
        return r

    # ---- finish -----------------------------------------------------------
    def review(s):
        s.state = "review"
        s.force_capture = False
        return dict(type="review", photos=list(s.photos), **s.fb("Review photos and uncheck unwanted views before confirming.", "ok"))

    def set_motion(s, motion):
        if s.photos:
            raise ValueError("Choose how you will capture before saving photos; start a new scan to change it.")
        s.motion = motion
        s.last_photo = None
        tip = ("Turn the object a little between shots and keep it in view. AI masks are required before processing."
               if motion == "turn" else "Keep the object still and move the camera around it.")
        return s.fb(tip, "ok")

    def segment(s, selected, prompts):
        if s.state != "review":
            raise ValueError("Finish capture and review the photos before running AI selection.")
        if len(set(selected)) != len(selected) or not set(selected) <= set(s.photos):
            raise ValueError("Select distinct saved photos from this scan.")
        if not {prompt["frame"] for prompt in prompts} <= set(selected):
            raise ValueError("Tap the object on a photo that is still selected.")
        selected = [name for name in s.photos if name in set(selected)]
        cancel = SEGMENTING[s.id] = threading.Event()
        try:
            result = run_segmentation(s.img, selected, prompts, s.out / "object_masks", SETTINGS, cancel)
        finally:
            SEGMENTING.pop(s.id, None)
        if result["done"]:
            s.segmentation = dict(frames=result["frames"], version=uuid.uuid4().hex[:8])
        return dict(**result, version=(s.segmentation or {}).get("version"))

    def finish(s, selected):
        if s.state != "review":
            raise ValueError("Finish capture and review the photos before confirming.")
        if len(selected) < 12 or len(set(selected)) != len(selected) or not set(selected) <= set(s.photos):
            raise ValueError("Select at least 12 distinct photos from this scan.")
        selected_set = set(selected)
        selected = [name for name in s.photos if name in selected_set]
        if s.motion == "turn" and not s.segmentation:
            raise ValueError("Photos of an object turned by hand need AI object masks. Tap the object and run AI selection first.")
        if s.segmentation:
            missing = [name for name in selected if name not in s.segmentation["frames"]]
            if missing:
                raise ValueError(f"{len(missing)} selected photos have no AI mask. Run AI selection again on this selection.")
        snapshot = s.out / "selections" / uuid.uuid4().hex
        images = snapshot / "images"
        images.mkdir(parents=True)
        for name in selected:
            shutil.copyfile(s.img / name, images / name)
        masks = None
        if s.segmentation:
            masks = snapshot / "object_masks"
            masks.mkdir()
            for name in selected:
                shutil.copyfile(s.out / "object_masks" / "masks" / f"{name}.png", masks / f"{name}.png")
            shutil.copyfile(s.out / "object_masks" / "segmentation.json", snapshot / "segmentation.json")
        settings = {key: value for key, value in asdict(SETTINGS).items() if key in SHARED_SETTINGS}
        manifest = dict(scan_id=s.id, capture_motion=s.motion, ai_object_masks=bool(masks), selected=selected,
                        excluded=[name for name in s.photos if name not in selected_set], settings=settings)
        (snapshot / "selection.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        archive = snapshot / "reviewed-images.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for name in selected:
                bundle.write(images / name, "images/" + name)
                if masks:
                    bundle.write(masks / f"{name}.png", f"object_masks/{name}.png")
            bundle.write(snapshot / "selection.json", "selection.json")
            if masks:
                bundle.write(snapshot / "segmentation.json", "segmentation.json")
        archive.replace(s.out / "reviewed-images.zip")
        with open(s.out / "sparse.ply", "w") as f:
            f.write(f"ply\nformat ascii 1.0\nelement vertex {len(s.pts)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
            f.writelines(f"{x} {y} {z}\n" for x, y, z in s.pts)
        if SETTINGS.mode != "reconstruct":
            result = reconstruct(images, s.out, SETTINGS, masks)
            s.state = "finished"
            return dict(**result, state=s.state, selected_count=len(selected))
        # Reconstruction runs as a background job so it survives a closed tab; the page polls its status.
        work = lambda progress, cancel: reconstruct(images, s.out, SETTINGS, masks, progress, cancel)
        if not JOBS.start(s.id, s.out, work):
            return dict(msg="Another heavy task is running. Wait for it or cancel it, then confirm again.", lvl="warn",
                        done=False, state=s.state, selected_count=len(selected))
        s.state = "processing"
        return dict(msg="Reconstruction started. You can close this page; reopen the result from Results.", lvl="ok",
                    done=False, job_started=True, state=s.state, selected_count=len(selected))


@app.get("/")
def index(): return FileResponse(BASE / "frontend" / "index.html")


@app.get("/camera.mjs")
def camera_module(): return FileResponse(BASE / "frontend" / "camera.mjs", media_type="text/javascript")


@app.get("/viewer.mjs")
def viewer_module(): return FileResponse(BASE / "frontend" / "viewer.mjs", media_type="text/javascript")


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    return not origin or origin in (f"http://{host}", f"https://{host}")


@app.get("/api/scans")
def list_scans():
    folders = sorted((path for path in SCANS.glob("*") if path.is_dir() and re.fullmatch(SCAN_ID, path.name)), reverse=True)
    results = [describe(folder) for folder in folders[:200]]
    return [item for item in results if item.get("state") or item["files"] != [] and set(item["files"]) - {"sparse.ply"}][:50]


@app.get("/api/scans/{scan_id}")
def scan_status(scan_id: str):
    if not re.fullmatch(SCAN_ID, scan_id) or not (SCANS / scan_id).is_dir():
        raise HTTPException(404)
    return describe(SCANS / scan_id)


@app.post("/api/scans/{scan_id}/cancel")
def cancel_scan(scan_id: str, request: Request):
    if not same_origin(request):
        raise HTTPException(403)
    if not re.fullmatch(SCAN_ID, scan_id):
        raise HTTPException(404)
    if JOBS.cancel(scan_id):
        return JSONResponse(dict(msg="Cancelling reconstruction."), status_code=202)
    if scan_id in SEGMENTING:
        SEGMENTING[scan_id].set()
        return JSONResponse(dict(msg="Cancelling AI selection."), status_code=202)
    return JSONResponse(dict(msg="Nothing is running for this scan."), status_code=409)


@app.get("/selection.mjs")
def selection_module(): return FileResponse(BASE / "frontend" / "selection.mjs", media_type="text/javascript")


@app.get("/api/health")
def health():
    return dict(status="ok", mode=SETTINGS.mode, colmap_found=bool(resolve_tool(SETTINGS.colmap)),
                textured_export=False, segmentation=segmentation_tools(SETTINGS)[2] is None, reconstruction_verified=False)


@app.get("/scans/{scan_id}/{filename}")
def download(scan_id: str, filename: str):
    if not __import__("re").fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}", scan_id):
        raise HTTPException(404)
    if filename not in {"sparse.ply", "mesh.obj", "mesh.glb", "object.obj", "object.glb", "colmap.log",
                        "segmentation.log", "reviewed-images.zip", "textured.glb"}:
        raise HTTPException(404)
    path = SCANS / scan_id / filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, filename=filename)


@app.get("/scans/{scan_id}/images/{filename}")
def saved_photo(scan_id: str, filename: str):
    if not re.fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}", scan_id) or not re.fullmatch(r"kf_[0-9]{4}\.jpg", filename):
        raise HTTPException(404)
    path = SCANS / scan_id / "images" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.get("/scans/{scan_id}/object_masks/{filename}")
def mask_preview(scan_id: str, filename: str):
    if not re.fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}", scan_id) or not re.fullmatch(r"kf_[0-9]{4}\.jpg", filename):
        raise HTTPException(404)
    path = SCANS / scan_id / "object_masks" / "previews" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.websocket("/ws")
async def ws_(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    if origin and origin not in (f"http://{host}", f"https://{host}"):
        await ws.close(code=1008); return
    await ws.accept(); s = Scan()
    await ws.send_json({"type": "ready", "scan_id": s.id, "mode": SETTINGS.mode, "state": s.state, "motion": s.motion,
                        "segmentation_problem": segmentation_tools(SETTINGS)[2],
                        "msg": f"Scanner ready ({SETTINGS.mode} mode). Start the camera."})
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect": break
            selection = request = None
            if (m.get("text") or "").startswith("{"):
                try:
                    request = ClientRequest.validate_json(m["text"])
                except ValidationError:
                    await ws.send_json(dict(type="error", state=s.state, msg="Invalid request. Select at least 12 photos, and tap the object on a selected photo.", lvl="bad"))
                    continue
                if isinstance(request, ConfirmSelection):
                    selection = request.selected
                    if s.state != "review" or len(set(selection)) != len(selection) or not set(selection) <= set(s.photos):
                        await ws.send_json(dict(type="error", state=s.state, msg="Review this scan and select distinct saved photos only.", lvl="bad"))
                        continue
                    m["text"] = "confirm"
                else:
                    m["text"] = "segment"
            if m.get("bytes") is not None:
                try:
                    processor = s.import_photo if s.import_next else s.process
                    s.import_next = False
                    result = await asyncio.to_thread(processor, m["bytes"])
                except (cv2.error, ValueError, FloatingPointError):
                    logging.exception("Frame processing failed")
                    result = s.fb("Could not process this frame; try moving slowly.", "bad")
                result["type"] = "frame"
                await ws.send_json(result)
            elif m.get("text") == "reset":
                s = Scan()
                await ws.send_json({"type": "reset", "scan_id": s.id, **s.fb("New scan started.", "ok")})
            elif m.get("text") == "capture":
                s.force_capture = s.state == "capturing"
                await ws.send_json({"type": "capture", **s.fb("Manual capture requested; waiting for a usable frame.", "ok")})
            elif m.get("text") in {"start_scan", "pause"}:
                action = m["text"]
                if s.state == "finished":
                    await ws.send_json(dict(type="error", state=s.state, msg="Start a new scan after finishing.", lvl="warn"))
                    continue
                s.state = "capturing" if action == "start_scan" else "paused"
                s.force_capture = False
                s.capture_note = "Capturing. Keep the object stationary." if s.state == "capturing" else "Paused. No camera frames will be saved."
                await ws.send_json(dict(type="state", **s.fb(s.capture_note, "ok")))
            elif m.get("text") in {"motion:orbit", "motion:turn"}:
                try:
                    await ws.send_json(dict(type="motion", **s.set_motion(m["text"].split(":")[1])))
                except ValueError as error:
                    await ws.send_json(dict(type="error", state=s.state, msg=str(error), lvl="warn"))
            elif m.get("text") == "segment" and isinstance(request, SegmentRequest):
                await ws.send_json({"type": "segmenting", "msg": "Running AI object selection on the selected photos. This can take a few minutes on a CPU.", "lvl": "ok"})
                try:
                    result = await asyncio.to_thread(s.segment, request.selected, [p.model_dump() for p in request.prompts])
                except ValueError as error:
                    result = dict(msg=str(error), lvl="bad", done=False)
                except Exception:
                    logging.exception("Segmentation failed")
                    result = dict(msg="AI selection failed; check segmentation.log.", lvl="bad", done=False)
                await ws.send_json(dict(type="segmented", state=s.state, **result))
            elif m.get("text") == "import":
                s.import_next = True
                await ws.send_json({"type": "import", "msg": "Importing photo…"})
            elif m.get("text") == "cancel_import":
                s.import_next = False
            elif m.get("text") == "finish":
                await ws.send_json(s.review())
            elif m.get("text") == "confirm" and selection is not None:
                message = "Saving capture." if SETTINGS.mode == "capture" else "Building experimental reconstruction. This can take several minutes."
                await ws.send_json({"type": "busy", "msg": message, "lvl": "ok"})
                try:
                    result = await asyncio.to_thread(s.finish, selection)
                except ValueError as error:
                    result = {"msg": str(error), "lvl": "bad", "state": s.state}
                except Exception:
                    logging.exception("Reconstruction failed")
                    result = {"msg": "Processing failed; check the server console.", "lvl": "bad", "state": s.state}
                files = ["reviewed-images.zip", "sparse.ply", "colmap.log"]
                if result.get("model_ready"):
                    files.extend(["mesh.obj", "mesh.glb"])
                if result.get("object_ready"):
                    files.extend(["object.glb", "object.obj"])
                if result.get("textured_ready"):
                    files.append("textured.glb")
                result.update(type="finish", scan_id=s.id,
                              files=[f for f in files if (s.out / f).is_file()])
                await ws.send_json(result)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, ws_max_size=MAX_UPLOAD_BYTES, ws_max_queue=2)
