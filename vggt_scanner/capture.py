"""Cheap live selection; model inference never blocks this capture path."""
import base64
import hashlib
import time
import uuid
import cv2
import numpy as np
from preprocessing import clean_frame,feature_mask


class Capture:
    def __init__(self,session,preview_submit):
        self.session=session;self.preview_submit=preview_submit;self.force=False;self.last=None
        self.sift=cv2.SIFT_create(nfeatures=1200,contrastThreshold=.02)
        self.matcher=cv2.BFMatcher(cv2.NORM_L2)
        if session.data["photos"]:
            last=session.root/"images"/(session.data["photos"][-1]["id"]+".jpg")
            image=cv2.imread(str(last))
            if image is not None: self.last=self.features(image);self.last["time"]=0

    def features(self,bgr):
        work=cv2.resize(bgr,(640,round(640*bgr.shape[0]/bgr.shape[1])),interpolation=cv2.INTER_AREA)
        gray=cv2.cvtColor(work,cv2.COLOR_BGR2GRAY)
        keypoints,d=self.sift.detectAndCompute(gray,None)
        return dict(xy=np.float32([p.pt for p in keypoints]).reshape(-1,2),d=d,time=time.monotonic(),
                    thumb=cv2.resize(gray,(120,90)).astype(float),
                    sharpness=round(float(cv2.Laplacian(gray,cv2.CV_64F).var()),1))

    def novel(self,current):
        if self.last is None or self.force: return True
        if current["time"]-self.last["time"]<.8: return False
        if current["d"] is not None and self.last["d"] is not None:
            pairs=[m[0] for m in self.matcher.knnMatch(self.last["d"],current["d"],k=2)
                   if len(m)==2 and m[0].distance<.75*m[1].distance]
            if len(pairs)>=12:
                a=self.last["xy"][[m.queryIdx for m in pairs]];b=current["xy"][[m.trainIdx for m in pairs]]
                return np.median(np.linalg.norm(a-b,axis=1))>=12
        return np.mean(np.abs(current["thumb"]-self.last["thumb"]))>=10

    def process(self,payload):
        if len(payload)>5*1024*1024: raise ValueError("Frame exceeds 5 MB.")
        raw=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
        if raw is None: raise ValueError("Invalid camera frame.")
        height,width=raw.shape[:2]
        if height/width<.2 or height/width>2: raise ValueError("Unsupported camera aspect ratio.")
        if width>1280: raw=cv2.resize(raw,(1280,round(1280*height/width)),interpolation=cv2.INTER_AREA)
        work=cv2.resize(raw,(640,round(640*raw.shape[0]/raw.shape[1])),interpolation=cv2.INTER_AREA)
        with self.session.lock: settings=dict(self.session.data["capture_settings"])
        _,mask=clean_frame(work,roi=settings["roi"],background=settings["background"],denoise=settings["denoise"])
        current=self.features(work)
        foreground=float(np.mean(mask>0))
        requested=self.force;self.force=False
        manual=settings.get("mode","auto")=="manual"
        saved=False;note="Rotate the item, move your hands away, then click Take photo." if manual else "Waiting for a changed view."
        with self.session.lock:
            if self.session.data["finish_requested"]: raise ValueError("Finish was requested. Start a new scan or resume capture.")
            self.session.data["processed"]+=1
            frame_number=self.session.data["processed"]
            photos=self.session.data["photos"]
            if len(photos)>=200: note="200-view limit reached. Finish the reconstruction."
            elif manual and not requested: pass
            elif foreground<.01 and not requested: note="Foreground mask is empty. Adjust the rectangle or disable background removal."
            elif current["sharpness"]<8 and not requested: note="Skipped a very soft frame; hold steady briefly."
            elif requested or self.novel(current):
                image_id=uuid.uuid4().hex;encoded=cv2.imencode(".jpg",raw,[cv2.IMWRITE_JPEG_QUALITY,95])[1].tobytes()
                (self.session.root/"images"/(image_id+".jpg")).write_bytes(encoded)
                cv2.imwrite(str(self.session.root/"masks"/(image_id+".png")),feature_mask(mask))
                thumb=cv2.resize(raw,(240,round(240*raw.shape[0]/raw.shape[1])))
                cv2.imwrite(str(self.session.root/"thumbs"/(image_id+".jpg")),thumb)
                photos.append(dict(id=image_id,name=f"View {len(photos)+1}",width=raw.shape[1],height=raw.shape[0],
                                   sharpness=current["sharpness"],features=len(current["xy"]),
                                   source="manual" if requested else "automatic",
                                   digest=hashlib.sha256(encoded).hexdigest()))
                self.session.data["revision"]+=1
                self.last=current;saved=True;note="Photo saved. Rotate the item before taking the next photo." if requested else "Selected a new keyframe."
                if foreground<.01: note+=" Foreground mask is empty; adjust the rectangle and retake this photo."
                if current["sharpness"]<8: note+=" This photo looks soft; consider retaking it."
            self.session.persist()
            count=len(photos)
        if settings.get("preview"):
            model_input=cv2.bilateralFilter(work,5,25,25) if settings["denoise"] else work
            self.preview_submit(self.session,frame_number,model_input[:,:,::-1].copy(),feature_mask(mask))
        encoded_mask=cv2.imencode(".jpg",cv2.applyColorMap(mask,cv2.COLORMAP_VIRIDIS))[1].tobytes()
        return dict(type="frame",saved=saved,saved_count=count,processed=frame_number,
                    features=len(current["xy"]),sharpness=current["sharpness"],foreground=round(foreground,3),
                    mask=base64.b64encode(encoded_mask).decode(),note=note,limit_reached=count>=200)
