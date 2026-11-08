"""Live and uploaded multi-angle images -> periodic joint VGGT reconstruction."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
import argparse
import json
from pathlib import Path
import re
import sys
from threading import Event,RLock
import time
import uuid
import cv2
from fastapi import FastAPI,HTTPException,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse,JSONResponse,RedirectResponse,Response
import numpy as np
from PIL import Image,ImageOps,UnidentifiedImageError
from pydantic import BaseModel,Field
import uvicorn
from capture import Capture
from depth_cloud import ENGINE
from preprocessing import clean_frame,feature_mask
from preview import PREVIEW
from reconstruction import reconstruct,OUTPUTS
from vggt_backend import BACKEND,COMPUTE_LOCK
from coverage_guide import plan,ring_of,sector_of,summarise
from quality import measurements,problem
from lan import ACCESS_KEY,COOKIE,client_kind,key_matches,lan_addresses,allowed_host
from urllib.parse import urlencode

BASE=Path(__file__).resolve().parent
SCANS=BASE/"scans"
SESSIONS={};REGISTRY_LOCK=RLock()
JOBS=ThreadPoolExecutor(max_workers=1,thread_name_prefix="vggt-reconstruction")
LOADERS=ThreadPoolExecutor(max_workers=2,thread_name_prefix="model-loader")
ACTIVE={"queued","running"}


class Session:
    def __init__(self,scan_id,data=None):
        self.id=scan_id;self.root=SCANS/scan_id;self.lock=RLock();self.cancel=Event()
        self.live_connected=False;self.last_start=0.;self.future=None;self.preview={}
        for folder in ("originals","images","masks","thumbs","reconstructions"): (self.root/folder).mkdir(parents=True,exist_ok=True)
        self.data=data or dict(photos=[],revision=0,processed=0,auto=False,finish_requested=False,
              options=dict(max_views=4,image_size=224,confidence=35,consistency=True,cleanup=True,foreground=True,turntable=True,interval=8),
              capture_settings=dict(mode="manual",background=True,denoise=True,roi=[.1,.08,.9,.92],preview=False),
              job=dict(state="idle",stage="capture",progress=0,message="Capture or upload overlapping views."),
              latest_result=None,final_pending=False)
        if self.data["job"]["state"] in ACTIVE:
            self.data["job"].update(state="failed",stage="interrupted",message="Server stopped during reconstruction. Saved images and previous results are kept.")
            self.data["final_pending"]=False;self.data["finish_requested"]=False
        self.persist()

    def persist(self):
        temp=self.root/"session.tmp";temp.write_text(json.dumps(self.data),encoding="utf-8")
        temp.replace(self.root/"session.json")

    def snapshot(self):
        with self.lock:
            data=json.loads(json.dumps(self.data))
            return {**data,"id":self.id,"coverage":summarise(data["photos"])}


def get_session(scan_id):
    if not re.fullmatch("[0-9a-f]{32}",scan_id): raise HTTPException(404,"Scan not found.")
    with REGISTRY_LOCK:
        if scan_id not in SESSIONS:
            file=SCANS/scan_id/"session.json"
            if not file.is_file(): raise HTTPException(404,"Scan not found.")
            SESSIONS[scan_id]=Session(scan_id,json.loads(file.read_text(encoding="utf-8")))
        return SESSIONS[scan_id]


def schedule(session,final=False):
    # Called with the session lock. Only one job per scan can be pending.
    if session.data["job"]["state"] in ACTIVE:
        if final:
            session.data["final_pending"]=True;session.data["finish_requested"]=True
            session.data["job"]["message"]="Finishing the current pass; final reconstruction will run next."
            session.persist()
        return
    if len(session.data["photos"])<3:
        raise HTTPException(400,"Select or upload at least 3 distinct overlapping views.")
    if not BACKEND.status()["installed"]:
        raise HTTPException(400,"VGGT is not installed. Install requirements.txt and run python install_vggt.py.")
    options=dict(session.data["options"]);photos=[dict(p) for p in session.data["photos"]]
    job_id=uuid.uuid4().hex;revision=session.data["revision"]
    session.cancel.clear();session.last_start=time.monotonic()
    session.data["final_pending"]=False
    session.data["job"]=dict(id=job_id,state="queued",stage="queued",progress=0,
                             message="Queued for joint multi-view reconstruction.",revision=revision,final=final)
    if final: session.data["finish_requested"]=True
    session.persist()
    session.future=JOBS.submit(run_job,session,job_id,revision,photos,options,final)


def maybe_schedule(session):
    with session.lock:
        if session.data["capture_settings"].get("mode","auto")=="manual": return
        if not session.data["auto"] or session.data["finish_requested"] or len(session.data["photos"])<3: return
        if session.data["job"]["state"] in ACTIVE: return
        if not BACKEND.status()["installed"] or BACKEND.status()["state"]=="error": return
        previous=session.data["job"].get("revision",-1)
        if session.data["revision"]<=previous: return
        if time.monotonic()-session.last_start<session.data["options"]["interval"]: return
        schedule(session)


def run_job(session,job_id,revision,photos,options,final):
    def update(**values):
        with session.lock:
            session.data["job"].update(values);session.persist()
    update(state="running",stage="inputs",message="Preparing a fixed snapshot of selected views…")
    try:
        result=reconstruct(session.root,job_id,photos,options,session.cancel,update,BACKEND,final)
        if session.cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
        result["revision"]=revision
        with session.lock:
            session.data["latest_result"]=result
            session.data["job"].update(state="completed",stage="done",progress=100,
                 message=f"{result['points']:,} points reconstructed from {len(result['frame_ids'])} of {result['captured_views']} saved views.")
            if final: session.data["finish_requested"]=True
            session.persist()
    except InterruptedError:
        update(state="cancelled",stage="cancelled",message="Cancelled. The last successful cloud is kept.")
        with session.lock:
            session.data["finish_requested"]=False;session.data["final_pending"]=False;session.persist()
    except Exception as error:
        memory_failure="memory" in str(error).lower() or "ram" in str(error).lower()
        message=str(error)+( " Automatic updates paused; reduce views/input size or free memory before retrying." if memory_failure else "")
        update(state="failed",stage="failed",message=message)
        with session.lock:
            if memory_failure:
                session.data["auto"]=False
                session.data["final_pending"]=False
            session.data["finish_requested"]=False;session.persist()
    finally:
        with session.lock:
            pending=session.data["final_pending"] and not session.cancel.is_set()
            session.data["final_pending"]=False
            if pending: schedule(session,final=True)
            else: session.persist()
        # A new snapshot is eligible later; no unbounded inference queue is created.


@asynccontextmanager
async def lifespan(app):
    yield
    with REGISTRY_LOCK:
        for session in SESSIONS.values(): session.cancel.set()
    await asyncio.to_thread(JOBS.shutdown,wait=True,cancel_futures=True)
    await asyncio.to_thread(PREVIEW.close)
    LOADERS.shutdown(wait=False,cancel_futures=True)


app=FastAPI(lifespan=lifespan)


@app.middleware("http")
async def same_origin(request:Request,call_next):
    kind=client_kind(request.client.host if request.client else None)
    if kind=="outside" or not allowed_host(request.url.hostname):
        return JSONResponse(dict(detail="Use this computer or its private LAN address."),status_code=403)
    if kind=="lan":
        if request.url.scheme!="https":
            return JSONResponse(dict(detail="Use the trusted HTTPS phone link from the laptop."),status_code=403)
        key=request.query_params.get("key")
        if request.method=="GET" and request.url.path=="/phone" and key_matches(key):
            query=dict(request.query_params);query.pop("key",None)
            response=RedirectResponse("/phone"+("?"+urlencode(query) if query else ""),status_code=303)
            response.set_cookie(COOKIE,ACCESS_KEY,httponly=True,secure=True,samesite="strict")
            response.headers["Cache-Control"]="no-store"
            response.headers["Referrer-Policy"]="no-referrer"
            return response
        if not key_matches(request.cookies.get(COOKIE)):
            return JSONResponse(dict(detail="Open the new phone link shown on the laptop to pair."),status_code=403)
    origin=request.headers.get("origin")
    if request.method in {"POST","PUT","DELETE"} and origin and origin!=str(request.base_url).rstrip("/"):
        return JSONResponse(dict(detail="Use this page from the same scanner server."),status_code=403)
    response=await call_next(request)
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Permissions-Policy"]="camera=(self), microphone=(), accelerometer=(self), gyroscope=(self), magnetometer=(self)"
    return response


@app.get("/")
def index(): return FileResponse(BASE/"index.html",headers={"Cache-Control":"no-store"})


@app.get("/phone")
def phone(): return FileResponse(BASE/"phone.html",headers={"Cache-Control":"no-store"})


@app.get("/guidance.mjs")
def guidance(): return FileResponse(BASE/"guidance.mjs",media_type="text/javascript")


@app.get("/phone.mjs")
def phone_script(): return FileResponse(BASE/"phone.mjs",media_type="text/javascript",headers={"Cache-Control":"no-store"})


@app.get("/api/plan")
def capture_plan(): return plan()


@app.get("/api/connect")
def connect_phone(request:Request,scan_id:str|None=None):
    if client_kind(request.client.host)!="local": raise HTTPException(403,"Open pairing on the laptop itself.")
    if scan_id: get_session(scan_id)
    addresses=lan_addresses()
    certificate_ips=getattr(app.state,"certificate_ips",None)
    if certificate_ips is not None:
        addresses=[address for address in addresses if address in certificate_ips]
    port=request.url.port or (443 if request.url.scheme=="https" else 80)
    query={"key":ACCESS_KEY}
    if scan_id: query["scan"]=scan_id
    links=[f"https://{address}:{port}/phone?{urlencode(query)}" for address in addresses]
    reason="" if request.url.scheme=="https" else "Restart with --lan and a trusted certificate to enable phone capture."
    if not addresses:
        reason="No current private Wi-Fi address is covered by the certificate. Rerun make_cert.py and restart." if certificate_ips is not None else "Connect the laptop and phone to the same private Wi-Fi."
    return dict(links=links if not reason else [],problem=reason)


@app.get("/api/connect/qr")
def phone_qr(request:Request,scan_id:str|None=None,index:int=0):
    connection=connect_phone(request,scan_id)
    if not 0<=index<len(connection["links"]): raise HTTPException(400,"Phone link unavailable.")
    try: import segno
    except ImportError: raise HTTPException(503,"Install segno==1.6.6 for QR codes, or use the phone link.")
    output=BytesIO();segno.make(connection["links"][index],micro=False).save(output,kind="png",scale=6)
    return Response(output.getvalue(),media_type="image/png",headers={"Cache-Control":"no-store"})


@app.get("/api/health")
def health(): return dict(vggt=BACKEND.status(),preview=ENGINE.status(),max_photos=200)


@app.post("/api/models/{model}/load")
def load_model(model:str):
    engine=BACKEND if model=="vggt" else ENGINE if model=="preview" else None
    if engine is None: raise HTTPException(404)
    with engine.status_lock:
        if engine.state not in {"ready","loading"}:
            engine.state="loading"
            def load():
                try:
                    with COMPUTE_LOCK: engine.load()
                except Exception: pass  # Engine status includes the actionable load error.
            LOADERS.submit(load)
    return engine.status()


@app.post("/api/scans")
def new_scan():
    with REGISTRY_LOCK:
        session=Session(uuid.uuid4().hex);SESSIONS[session.id]=session
    return session.snapshot()


@app.get("/api/scans/{scan_id}")
def scan_status(scan_id:str):
    session=get_session(scan_id);maybe_schedule(session)
    return session.snapshot()


class Settings(BaseModel):
    max_views:int=Field(8,ge=3,le=24)
    image_size:int=224
    confidence:int=Field(35,ge=0,le=90)
    consistency:bool=True
    cleanup:bool=True
    foreground:bool=True
    turntable:bool=False
    interval:int=Field(8,ge=5,le=120)
    auto:bool=True


@app.put("/api/scans/{scan_id}/settings")
def settings(scan_id:str,settings:Settings):
    session=get_session(scan_id)
    if settings.image_size not in (224,336,518): raise HTTPException(400,"Choose 224, 336 or 518 pixels.")
    if settings.turntable and not settings.foreground:
        raise HTTPException(400,"Rotating-item reconstruction needs Object-only cloud enabled.")
    with session.lock:
        values=settings.model_dump();session.data["auto"]=values.pop("auto")
        if session.data["capture_settings"].get("mode","auto")=="manual": session.data["auto"]=False
        session.data["options"]=values
        session.data["capture_settings"]["background"]=values["foreground"]
        session.data["revision"]+=1
        session.persist()
    maybe_schedule(session)
    return session.snapshot()


def parse_capture_settings(values,previous):
    if not isinstance(values,dict): raise ValueError("Invalid capture settings.")
    roi=values.get("roi",[.1,.08,.9,.92])
    if not isinstance(roi,list) or len(roi)!=4 or not all(isinstance(v,(int,float)) and np.isfinite(v) for v in roi) or not (0<=roi[0]<roi[2]<=1 and 0<=roi[1]<roi[3]<=1):
        raise ValueError("Invalid object region.")
    mode=values.get("mode",previous.get("mode","auto"))
    if mode not in ("manual","auto"): raise ValueError("Choose manual or automatic photo capture.")
    return dict(mode=mode,roi=roi,background=bool(values.get("background",True)),
                denoise=bool(values.get("denoise",True)),preview=bool(values.get("preview",False)))


@app.put("/api/scans/{scan_id}/capture-settings")
async def capture_settings(scan_id:str,request:Request):
    values=await request.json()
    session=get_session(scan_id)
    with session.lock:
        try: config=parse_capture_settings(values,session.data["capture_settings"])
        except ValueError as error: raise HTTPException(400,str(error)) from error
        session.data["capture_settings"]=config
        if config["mode"]=="manual": session.data["auto"]=False
        session.persist()
    return session.snapshot()


@app.post("/api/scans/{scan_id}/build")
def build(scan_id:str):
    session=get_session(scan_id)
    with session.lock: schedule(session,final=False)
    return session.snapshot()


@app.post("/api/scans/{scan_id}/finish")
def finish(scan_id:str):
    session=get_session(scan_id)
    with session.lock: schedule(session,final=True)
    return session.snapshot()


@app.post("/api/scans/{scan_id}/resume")
def resume(scan_id:str):
    session=get_session(scan_id)
    with session.lock:
        if session.data["job"]["state"] in ACTIVE: raise HTTPException(409,"Wait for or cancel the active reconstruction.")
        session.data["finish_requested"]=False;session.persist()
    return session.snapshot()


@app.post("/api/scans/{scan_id}/phone-mode")
def phone_mode(scan_id:str):
    session=get_session(scan_id)
    with session.lock:
        if session.live_connected or session.data["job"]["state"] in ACTIVE:
            raise HTTPException(409,"Disconnect laptop live capture (New scan closes it) and wait for reconstruction before switching to the phone.")
        if session.data["photos"] and session.data["options"].get("turntable"):
            raise HTTPException(409,"This scan contains rotating-item photos. Start a new scan for walking around a stationary object.")
        session.data["options"]["turntable"]=False
        session.data["phone_mode"]=True
        session.data["auto"]=False
        session.data["capture_settings"]["mode"]="manual"
        session.data["finish_requested"]=False
        session.data["revision"]+=1;session.persist()
    return session.snapshot()


@app.post("/api/scans/{scan_id}/cancel")
def cancel(scan_id:str):
    session=get_session(scan_id)
    with session.lock:
        session.data["auto"]=False;session.data["final_pending"]=False;session.cancel.set()
        if session.data["job"]["state"] in ACTIVE:
            session.data["job"]["message"]="Cancellation requested. An active model inference must return before it stops."
        else: session.data["finish_requested"]=False
        session.persist()
    return session.snapshot()


@app.get("/api/scans/{scan_id}/preview")
def depth_preview(scan_id:str):
    session=get_session(scan_id)
    with session.lock: return dict(session.preview)


def store_photo(session,image,name,pose=None,guided=False,original=None):
    rgb=np.array(image)
    bgr=rgb[:,:,::-1].copy()
    with session.lock: config=dict(session.data["capture_settings"])
    quality=measurements(bgr) if guided else None
    if quality and (reason:=problem(quality)): return dict(added=False,reason=reason)
    _,mask=clean_frame(bgr,roi=config["roi"],background=config["background"],denoise=config["denoise"])
    buf=BytesIO();image.save(buf,"JPEG",quality=95);encoded=buf.getvalue();digest=hashlib.sha256(encoded).hexdigest()
    with session.lock:
        if session.data["finish_requested"]: raise HTTPException(409,"Resume capture or create a new scan before adding images.")
        if any(p["digest"]==digest for p in session.data["photos"]): return False
        if len(session.data["photos"])>=200: raise HTTPException(400,"Maximum 200 saved views.")
        image_id=uuid.uuid4().hex
        if original:
            raw,suffix=original
            (session.root/"originals"/(image_id+suffix)).write_bytes(raw)
        (session.root/"images"/(image_id+".jpg")).write_bytes(encoded)
        cv2.imwrite(str(session.root/"masks"/(image_id+".png")),feature_mask(mask))
        thumb=image.copy();thumb.thumbnail((240,180));thumb.save(session.root/"thumbs"/(image_id+".jpg"))
        gray=cv2.cvtColor(cv2.resize(bgr,(640,round(640*bgr.shape[0]/bgr.shape[1]))),cv2.COLOR_BGR2GRAY)
        photo=dict(id=image_id,name=name,width=image.width,height=image.height,digest=digest,
                   sharpness=round(float(cv2.Laplacian(gray,cv2.CV_64F).var()),1),features=None)
        if guided:
            photo.update(source="phone",quality=quality,ring=None,sector=None)
            if pose:
                photo.update(pose,ring=ring_of(pose["pitch"]),sector=sector_of(pose["azimuth"]))
        session.data["photos"].append(photo);session.data["revision"]+=1;session.persist()
    return True


@app.post("/api/scans/{scan_id}/photos")
async def upload(scan_id:str,request:Request):
    session=get_session(scan_id);payload=bytearray()
    guided=request.query_params.get("guided")=="true"
    pose=None
    try:
        azimuth=request.query_params.get("azimuth");pitch=request.query_params.get("pitch")
        if (azimuth is None)!=(pitch is None): raise ValueError("Provide both azimuth and pitch.")
        if azimuth is not None:
            azimuth,pitch=float(azimuth),float(pitch)
            if not np.isfinite([azimuth,pitch]).all() or not (-180<=azimuth<=180 and -90<=pitch<=90):
                raise ValueError("Invalid phone orientation.")
            pose=dict(azimuth=azimuth,pitch=pitch)
    except ValueError as error: raise HTTPException(400,str(error)) from error
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload)>20*1024*1024: raise HTTPException(413,"Each image must be 20 MB or smaller.")
    try:
        with Image.open(BytesIO(payload)) as source:
            if source.format not in {"JPEG","PNG","WEBP"} or source.width*source.height>36_000_000:
                raise ValueError("Use JPEG, PNG or WebP, at most 36 megapixels.")
            suffix={"JPEG":".jpg","PNG":".png","WEBP":".webp"}[source.format]
            image=ImageOps.exif_transpose(source).convert("RGB")
            if min(image.size)<240: raise ValueError("Image sides must be at least 240 pixels.")
            image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
        added=await asyncio.to_thread(store_photo,session,image,request.query_params.get("name","Photo")[:180],pose,guided,(bytes(payload),suffix))
    except (ValueError,UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise HTTPException(400,str(error)) from error
    maybe_schedule(session)
    if isinstance(added,dict): return dict(**added,scan=session.snapshot())
    return dict(added=added,scan=session.snapshot())


@app.delete("/api/scans/{scan_id}/photos/{photo_id}")
def delete_photo(scan_id:str,photo_id:str):
    session=get_session(scan_id)
    with session.lock:
        if session.data["job"]["state"] in ACTIVE: raise HTTPException(409,"Cancel or wait for the active reconstruction before removing a view.")
        photo=next((p for p in session.data["photos"] if p["id"]==photo_id),None)
        if not photo: raise HTTPException(404)
        session.data["photos"].remove(photo);session.data["revision"]+=1;session.data["finish_requested"]=False
        for folder,suffix in (("images",".jpg"),("thumbs",".jpg"),("masks",".png")):
            (session.root/folder/(photo_id+suffix)).unlink(missing_ok=True)
        for suffix in (".jpg",".png",".webp"):
            (session.root/"originals"/(photo_id+suffix)).unlink(missing_ok=True)
        session.persist()
    return session.snapshot()


@app.get("/api/scans/{scan_id}/thumbs/{photo_id}")
def thumb(scan_id:str,photo_id:str):
    if not re.fullmatch("[0-9a-f]{32}",photo_id): raise HTTPException(404)
    path=get_session(scan_id).root/"thumbs"/(photo_id+".jpg")
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,media_type="image/jpeg")


@app.get("/api/scans/{scan_id}/images/{photo_id}")
def photo_image(scan_id:str,photo_id:str):
    if not re.fullmatch("[0-9a-f]{32}",photo_id): raise HTTPException(404)
    path=get_session(scan_id).root/"images"/(photo_id+".jpg")
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,media_type="image/jpeg")


@app.get("/api/scans/{scan_id}/results/{job_id}/{filename}")
def download(scan_id:str,job_id:str,filename:str):
    session=get_session(scan_id)
    if not re.fullmatch("[0-9a-f]{32}",job_id) or filename not in OUTPUTS: raise HTTPException(404)
    path=session.root/"reconstructions"/job_id/filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,filename=filename,headers={"Cache-Control":"no-store"})


@app.websocket("/ws/{scan_id}")
async def websocket(ws:WebSocket,scan_id:str):
    kind=client_kind(ws.client.host if ws.client else None)
    if kind=="outside" or not allowed_host(ws.url.hostname) or (kind=="lan" and
            (ws.url.scheme!="wss" or not key_matches(ws.cookies.get(COOKIE)))):
        await ws.close(code=1008);return
    origin=ws.headers.get("origin");host=ws.headers.get("host","")
    if origin and origin not in {f"http://{host}",f"https://{host}"}: await ws.close(code=1008);return
    try: session=get_session(scan_id)
    except HTTPException: await ws.close(code=1008);return
    with session.lock:
        if session.live_connected: await ws.close(code=1008);return
        session.live_connected=True
    try:
        await ws.accept();capture=await asyncio.to_thread(Capture,session,PREVIEW.submit)
        await ws.send_json(dict(type="ready"))
        while True:
            message=await ws.receive()
            if message["type"]=="websocket.disconnect": break
            if message.get("bytes") is not None:
                try:
                    response=await asyncio.to_thread(capture.process,message["bytes"])
                    maybe_schedule(session)
                except Exception as error: response=dict(type="error",message=str(error))
                await ws.send_json(response)
            elif message.get("text")=="capture":
                capture.force=True;await ws.send_json(dict(type="capture"))
            else:
                try:
                    values=json.loads(message.get("text",""))
                    if not isinstance(values,dict) or values.get("type")!="settings": raise ValueError("Invalid settings.")
                    with session.lock:
                        config=parse_capture_settings(values,session.data["capture_settings"])
                        session.data["capture_settings"]=config
                        if config["mode"]=="manual": session.data["auto"]=False
                        session.persist()
                    await ws.send_json(dict(type="settings"))
                except (ValueError,TypeError) as error: await ws.send_json(dict(type="command_error",message=str(error)))
    except WebSocketDisconnect: pass
    finally:
        with session.lock: session.live_connected=False


def run_server(**options):
    """Use socket selectors for this single-process Windows HTTP/WebSocket server.

    Python 3.12 Proactor socket teardown can raise WinError 10054 after a peer reset.
    Running serve() directly keeps Uvicorn from replacing the selected event loop.
    VGGT work runs in threads; this app does not need asyncio subprocess transports.
    """
    if sys.platform!="win32":
        return uvicorn.run(app,**options)
    previous=asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(uvicorn.Server(uvicorn.Config(app,**options)).serve())
    finally:
        asyncio.set_event_loop_policy(previous)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Local VGGT scanner with optional trusted LAN phone capture.")
    parser.add_argument("--lan",action="store_true",help="Listen on private LAN; requires a trusted TLS certificate.")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument("--cert",type=Path,default=BASE.parent/"certs/vggt-cert.pem")
    parser.add_argument("--key",type=Path,default=BASE.parent/"certs/vggt-key.pem")
    args=parser.parse_args()
    if args.lan and (not args.cert.is_file() or not args.key.is_file()):
        parser.error("LAN camera access needs trusted HTTPS. Run make_cert.py, trust its public CA on the phone, then retry.")
    if args.lan:
        import ssl
        try:
            certificate=ssl._ssl._test_decode_cert(str(args.cert.resolve()))
            app.state.certificate_ips={value for kind,value in certificate.get("subjectAltName",[]) if kind=="IP Address"}
        except (OSError,ssl.SSLError) as error:
            parser.error(f"Cannot read LAN certificate: {error}")
    tls=dict(ssl_certfile=str(args.cert),ssl_keyfile=str(args.key)) if args.lan else {}
    # Pairing URLs contain the launch key; keep them out of request access logs.
    run_server(host="0.0.0.0" if args.lan else "127.0.0.1",port=args.port,proxy_headers=False,access_log=False,**tls)
