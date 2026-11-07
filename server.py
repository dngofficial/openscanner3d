"""Live 3D scan guide. Run: python server.py  ->  http://localhost:8000
Pipeline: JPEG frames -> changed-view photo capture + independent SIFT/PnP live map.
On Finish: keyframes go to COLMAP (dense + Poisson mesh) -> scan/mesh.obj + scan/mesh.glb
"""
import asyncio, math, shutil, subprocess, uuid, logging, time
from datetime import datetime
from pathlib import Path
import cv2, numpy as np, uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse

BASE = Path(__file__).resolve().parent
SCANS = BASE / "scans"
MAX_FRAME_BYTES = 5 * 1024 * 1024
AZ, EL_EDGES = 12, (20, 50)          # 12 azimuth sectors x 3 elevation rings
KF_DEG, MIN_BLUR = 6, 25             # new keyframe every 6 deg of orbit; Laplacian var floor
bf = cv2.BFMatcher(cv2.NORM_L2)
app = FastAPI()


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
        s.photos = []; s.last_photo = None; s.force_capture = False; s.capture_note = "Waiting for a usable frame"
        s.K = None; s.kfs = []; s.pts = np.zeros((0, 3)); s.ptd = np.zeros((0, 128), np.float32)
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
        if len(s.photos) >= 500:
            s.capture_note = "500-photo limit reached; finish this scan."
            return
        if s.last_photo is not None and not forced:
            if now - s.last_photo["time"] < .7:
                s.capture_note = "Waiting between captures"
                return
            matches = match(s.last_photo["d"], d)
            if len(matches) >= 15:
                pa = s.last_photo["xy"][[m.queryIdx for m in matches]]
                pb = xy[[m.trainIdx for m in matches]]
                motion = float(np.median(np.linalg.norm(pb - pa, axis=1)))
                if motion < 12:
                    s.capture_note = f"Move the camera a little farther ({motion:.1f}/12 px change)"
                    return
            else:
                # A changed viewpoint can lose overlap; save it for offline matching.
                thumb = cv2.resize(gray, (160, 120)).astype(float)
                difference = np.mean(np.abs(thumb - s.last_photo["thumb"]))
                if difference < 10:
                    s.capture_note = "View has not changed enough"
                    return
        name = f"kf_{len(s.photos):04d}.jpg"
        (s.img / name).write_bytes(jpg)
        s.photos.append(name)
        s.last_photo = dict(xy=xy, d=d, time=now,
                            thumb=cv2.resize(gray, (160, 120)).astype(float))
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
        return dict(msg=msg, lvl=lvl, kfs=len(s.photos), tracked_views=len(s.kfs), capture=s.capture_note,
                    tracking="Mapping" if len(s.pts) else "Initializing", pts=len(s.pts), bins=s.bins.ravel().tolist(),
                    cur=s.cur, size=round(s.size, 2), overlay=s.overlay, **kw)

    def guide(s, R, t):
        a = s.angles(R, t)
        if a is None: return s.fb("Mapping started. Keep moving slowly sideways.", "ok")
        s.cur = [a[0], a[1]]
        done = (s.bins > 0).mean()
        if done >= .8: return s.fb("Coverage complete. Press Finish.", "ok")
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
    def process(s, jpg):
        s.overlay = None
        if len(jpg) > MAX_FRAME_BYTES: return s.fb("Frame too large; reduce the capture resolution.", "bad")
        full = cv2.imdecode(np.frombuffer(jpg, np.uint8), 1)
        if full is None: return s.fb("Invalid image received; waiting for the next frame.", "bad")
        img = cv2.resize(full, (960, int(960 * full.shape[0] / full.shape[1])))
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); H, W = g.shape
        if s.K is None: s.K = np.array([[.85 * W, 0, W / 2], [0, .85 * W, H / 2], [0, 0, 1]])
        blur = round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1)
        brightness, contrast = round(float(g.mean()), 1), round(float(g.std()), 1)
        enhanced = s.clahe.apply(g)
        kp, d = s.sift.detectAndCompute(enhanced, None)
        xy = np.float32([k.pt for k in kp]).reshape(-1, 2)
        s.visual_features(xy, d, W, H)
        info = dict(blur=blur, feats=len(kp), brightness=brightness, contrast=contrast)
        if d is None or len(kp) < 40:
            if brightness < 35:
                msg = f"Frame is dark ({brightness}/255); increase illumination or exposure."
            elif brightness > 225:
                msg = f"Frame is overexposed ({brightness}/255); reduce exposure or glare."
            else:
                msg = f"Only {len(kp)} trackable features (need 40). Focus on a detailed matte object; include nearby textured surroundings."
            return s.fb(msg, "warn", **info)
        # Sharpness alone is not a reliable rejection test across cameras.
        info["quality"] = "Soft frame; hold steady or check focus." if blur < MIN_BLUR else "Frame usable"
        s.save_view(xy, d, jpg, g)

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
    def finish(s):
        if len(s.photos) < 12: return dict(msg=f"Only {len(s.photos)} saved photos. Capture at least 12 first.", lvl="bad")
        with open(s.out / "sparse.ply", "w") as f:
            f.write(f"ply\nformat ascii 1.0\nelement vertex {len(s.pts)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
            f.writelines(f"{x} {y} {z}\n" for x, y, z in s.pts)
        exe = shutil.which("colmap")
        if not exe: return dict(msg=f"{len(s.photos)} photos saved to the scan folder. Install COLMAP and press Finish again.", lvl="warn")
        ws = s.out / "colmap"; ws.mkdir(exist_ok=True)
        r = subprocess.run([exe, "automatic_reconstructor", "--workspace_path", str(ws), "--image_path", str(s.img),
                            "--quality", "medium", "--single_camera", "1"], capture_output=True, text=True)
        (s.out / "colmap.log").write_text(r.stdout + "\n" + r.stderr)
        if r.returncode != 0:
            return dict(msg="COLMAP failed. Check colmap.log in the scan folder.", lvl="bad")
        ply = ws / "dense/0/meshed-poisson.ply"
        if not ply.exists():
            return dict(msg="COLMAP produced no dense mesh. Dense meshing needs an NVIDIA GPU (CUDA). See scan/colmap for the sparse model.", lvl="bad")
        try:
            import trimesh
        except ImportError:
            return dict(msg="Mesh created; install trimesh to export OBJ and GLB.", lvl="warn")
        m = trimesh.load(ply); m.export(s.out / "mesh.obj"); m.export(s.out / "mesh.glb")
        return dict(msg="Done. Import mesh.glb or mesh.obj into Blender.", lvl="ok", done=True)


@app.get("/")
def index(): return FileResponse(BASE / "index.html")


@app.get("/scans/{scan_id}/{filename}")
def download(scan_id: str, filename: str):
    if not __import__("re").fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{8}", scan_id):
        raise HTTPException(404)
    if filename not in {"sparse.ply", "mesh.obj", "mesh.glb", "colmap.log"}:
        raise HTTPException(404)
    path = SCANS / scan_id / filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, filename=filename)


@app.websocket("/ws")
async def ws_(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    if origin and origin not in (f"http://{host}", f"https://{host}"):
        await ws.close(code=1008); return
    await ws.accept(); s = Scan()
    await ws.send_json({"type": "ready", "scan_id": s.id, "msg": "Scanner ready. Start the camera."})
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect": break
            if m.get("bytes") is not None:
                try:
                    result = await asyncio.to_thread(s.process, m["bytes"])
                except (cv2.error, ValueError, FloatingPointError):
                    logging.exception("Frame processing failed")
                    result = s.fb("Could not process this frame; try moving slowly.", "bad")
                result["type"] = "frame"
                await ws.send_json(result)
            elif m.get("text") == "reset":
                s = Scan()
                await ws.send_json({"type": "reset", "scan_id": s.id, **s.fb("New scan started.", "ok")})
            elif m.get("text") == "capture":
                s.force_capture = True
                await ws.send_json({"type": "capture", **s.fb("Manual capture requested; waiting for a usable frame.", "ok")})
            elif m.get("text") == "finish":
                await ws.send_json({"type": "busy", "msg": "Building reconstruction. This can take several minutes.", "lvl": "ok"})
                try:
                    result = await asyncio.to_thread(s.finish)
                except Exception:
                    logging.exception("Reconstruction failed")
                    result = {"msg": "Reconstruction failed; check the server console.", "lvl": "bad"}
                result.update(type="finish", scan_id=s.id,
                              files=[f for f in ("sparse.ply", "mesh.obj", "mesh.glb", "colmap.log") if (s.out / f).is_file()])
                await ws.send_json(result)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
