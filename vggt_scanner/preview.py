"""Optional, bounded latest-frame Depth Anything preview, independent of capture."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Lock
from depth_cloud import ENGINE,local_cloud,sample_cloud,depth_picture
from vggt_backend import COMPUTE_LOCK

# Keep this lightweight model on CPU unless explicitly changed by an advanced user.
os.environ.setdefault("PREVIEW_DEVICE","cpu")


class PreviewService:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="depth-preview")
        self.lock=Lock();self.pending=None;self.running=False;self.closed=False

    def submit(self,session,number,rgb,mask):
        if ENGINE.status()["state"]!="ready": return
        with self.lock:
            if self.closed: return
            self.pending=(session,number,rgb,mask)
            if not self.running:
                self.running=True;self.pool.submit(self.pump)

    def pump(self):
        while True:
            with self.lock:
                if self.pending is None or self.closed:
                    self.running=False;return
                session,number,rgb,mask=self.pending;self.pending=None
            try:
                with COMPUTE_LOCK: inverse=ENGINE.predict(rgb)
                xyz,colors=local_cloud(inverse,rgb,mask=mask)
                result=dict(frame=number,cloud=sample_cloud(xyz,colors),depth=depth_picture(inverse),error="")
            except Exception as error:
                result=dict(frame=number,cloud=[],depth="",error=str(error))
            with session.lock: session.preview=result

    def close(self):
        with self.lock: self.closed=True;self.pending=None
        self.pool.shutdown(wait=True,cancel_futures=True)


PREVIEW=PreviewService()
