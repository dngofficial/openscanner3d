"""Live JPEG frames -> inferred depth -> local clouds -> changed-view keyframes."""
import hashlib
import time
import uuid

import cv2
import numpy as np

from depth_cloud import depth_picture, local_cloud, sample_cloud, write_ply
from preprocessing import clean_frame, feature_mask


class Capture:
    def __init__(self, session, engine, limit=200):
        self.session, self.engine, self.limit = session, engine, limit
        self.sift = cv2.SIFT_create(nfeatures=1500, contrastThreshold=.02)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.last = None
        self.force = False
        self.settings = session.data.get("capture_settings",dict(background=True,denoise=True,roi=[.1,.08,.9,.92]))
        photos = session.data["photos"]
        if photos:
            image = cv2.imread(str(session.root / "images" / (photos[-1]["id"] + ".jpg")))
            if image is not None:
                self.last = self.features(image[:, :, ::-1])
                self.last["time"] = 0

    def features(self, rgb, mask=None):
        if rgb.shape[1] != 640:
            size=(640,round(rgb.shape[0]*640/rgb.shape[1]))
            rgb=cv2.resize(rgb,size,interpolation=cv2.INTER_AREA)
            if mask is not None: mask=cv2.resize(mask,size,interpolation=cv2.INTER_NEAREST)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        keypoints, descriptors = self.sift.detectAndCompute(gray, mask)
        return dict(xy=np.float32([k.pt for k in keypoints]).reshape(-1,2), d=descriptors,
                    thumb=cv2.resize(gray, (120,90)).astype(float), time=time.monotonic())

    def should_save(self, current):
        if self.force or self.last is None: return True
        if current["time"]-self.last["time"] < 1.: return False
        if self.last["d"] is not None and current["d"] is not None:
            matches = [m[0] for m in self.matcher.knnMatch(self.last["d"], current["d"], k=2)
                       if len(m)==2 and m[0].distance < .75*m[1].distance]
            if len(matches) >= 15:
                pa = self.last["xy"][[m.queryIdx for m in matches]]
                pb = current["xy"][[m.trainIdx for m in matches]]
                return float(np.median(np.linalg.norm(pb-pa, axis=1))) >= 12
        return float(np.mean(np.abs(current["thumb"]-self.last["thumb"]))) >= 10

    def process(self, payload):
        if len(payload) > 5*1024*1024: raise ValueError("Frame is too large.")
        bgr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None: raise ValueError("Cannot decode camera frame.")
        height, width = bgr.shape[:2]
        target = (640, round(640*height/width))
        if target[1] < 120 or target[1] > 960: raise ValueError("Unsupported camera aspect ratio.")
        tracking_bgr = bgr
        if width > 1280:
            tracking_bgr = cv2.resize(bgr,(1280,round(1280*height/width)),interpolation=cv2.INTER_AREA)
        working_bgr = cv2.resize(bgr, target, interpolation=cv2.INTER_AREA)
        _, mask = clean_frame(working_bgr, **self.settings)
        # Preserve context for pose tracking and depth; apply the foreground mask to geometry.
        model_bgr = cv2.bilateralFilter(working_bgr,5,25,25) if self.settings["denoise"] else working_bgr
        rgb = working_bgr[:, :, ::-1].copy()
        foreground = float(np.mean(mask>0))
        mask_image = cv2.applyColorMap(mask, cv2.COLORMAP_VIRIDIS)
        import base64
        mask_preview = base64.b64encode(cv2.imencode(".jpg",mask_image)[1]).decode()
        if foreground < .01:
            return dict(type="frame",cloud=[],local_points=0,depth="",mask=mask_preview,
                        processed=self.session.data.get("processed_frames",0),
                        saved_count=len(self.session.data["photos"]),saved=False,features=0,seconds=0,
                        foreground=foreground,note="No foreground found. Adjust the object rectangle or disable background removal.",limit_reached=False)
        started = time.monotonic()
        inverse = self.engine.predict(model_bgr[:, :, ::-1].copy())
        xyz, colors = local_cloud(inverse, rgb, mask=feature_mask(mask))
        current = self.features(rgb)
        saved = False
        note = "Live cloud updated; waiting for a changed camera view."
        with self.session.lock:
            if self.session.data["state"] in {"queued","running"}:
                raise ValueError("Alignment is running; pause camera capture.")
            photos = self.session.data["photos"]
            self.session.data["processed_frames"] = self.session.data.get("processed_frames",0)+1
            if len(photos) < self.limit and self.should_save(current):
                photo_id = uuid.uuid4().hex
                image_path = self.session.root / "images" / (photo_id + ".jpg")
                for directory in ("depth","clouds","thumbs","masks"):
                    (self.session.root/directory).mkdir(exist_ok=True)
                encoded = cv2.imencode(".jpg", tracking_bgr, [cv2.IMWRITE_JPEG_QUALITY,95])[1]
                try:
                    image_path.write_bytes(encoded.tobytes())
                    cv2.imwrite(str(self.session.root / "masks" / (photo_id + ".jpg.png")), feature_mask(mask))
                    np.savez_compressed(self.session.root / "depth" / (photo_id + ".npz"), inverse_depth=inverse)
                    write_ply(self.session.root / "clouds" / (photo_id + ".ply"), xyz, colors)
                    thumb = cv2.resize(working_bgr, (320, max(1, round(320*working_bgr.shape[0]/working_bgr.shape[1]))))
                    (self.session.root / "thumbs" / (photo_id + ".jpg")).write_bytes(cv2.imencode(".jpg",thumb)[1].tobytes())
                except Exception:
                    image_path.unlink(missing_ok=True)
                    for directory, suffix in (("depth",".npz"),("clouds",".ply"),("thumbs",".jpg"),("masks",".jpg.png")):
                        (self.session.root/directory/(photo_id+suffix)).unlink(missing_ok=True)
                    raise
                for output in ("merged.ply","merged_preview.json","preview.json","results.zip","alignment.json",
                               "sparse.ply","dense.ply","mesh.ply","mesh.obj","mesh.glb","report.json","reconstruction.log","diagnostics.json"):
                    (self.session.root/output).unlink(missing_ok=True)
                photos.append(dict(id=photo_id, name=f"Cloud {len(photos)+1}", width=tracking_bgr.shape[1], height=tracking_bgr.shape[0],
                                   depth_width=rgb.shape[1], depth_height=rgb.shape[0], tracking_context=True,
                                   features=len(current["xy"]), warnings=[], digest=hashlib.sha256(encoded).hexdigest(), foreground=foreground))
                self.last = current
                saved = True
                note = "Saved RGB frame, depth map and local point cloud."
                self.session.data.update(state="collecting", stage="capture", progress=0,
                                         registered_images=0, points=0, accepted_clouds=0, skipped_clouds=0, merged_points=0, message=note)
                self.force = False
            elif len(photos) >= self.limit:
                note = "200 saved-cloud limit reached. Pause and align the saved clouds."
                self.force = False
            self.session.persist()
            total = self.session.data["processed_frames"]
            saved_count = len(photos)
        return dict(type="frame", cloud=sample_cloud(xyz, colors), local_points=len(xyz),
                    depth=depth_picture(inverse), mask=mask_preview,foreground=round(foreground,3),processed=total, saved_count=saved_count, saved=saved,
                    features=len(current["xy"]), note=note, seconds=round(time.monotonic()-started,2),
                    limit_reached=saved_count>=self.limit)
