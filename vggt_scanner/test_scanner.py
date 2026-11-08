from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event,RLock
import unittest
from unittest.mock import patch
import cv2
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
from geometry import prepare_inputs,unproject,fuse_predictions
from reconstruction import reconstruct,select_views
import server


class FixtureBackend:
    state="ready";status_lock=RLock()
    def status(self): return dict(installed=True,state=self.state,error="",device="fixture",model="synthetic-test")
    def predict(self,images,cancel,update):
        n,h,w,_=images.shape
        depth=np.full((n,h,w),3,np.float32)
        confidence=np.broadcast_to(np.linspace(2,6,w,dtype=np.float32),(n,h,w)).copy()
        cameras=np.tile(np.eye(4,dtype=np.float32)[:3],(n,1,1))
        cameras[:,0,3]=-np.arange(n)*.07
        K=np.tile(np.array([[w*.9,0,w/2],[0,h*.9,h/2],[0,0,1]],np.float32),(n,1,1))
        return dict(depth=depth,confidence=confidence,extrinsics=cameras,intrinsics=K)


class CollectedJobs:
    def __init__(self): self.jobs=[]
    def submit(self,fn,*args): self.jobs.append((fn,args));return object()
    def run_next(self): fn,args=self.jobs.pop(0);fn(*args)


def image_bytes(seed=0):
    image=Image.fromarray(np.random.default_rng(seed).integers(0,255,(480,640,3),dtype=np.uint8))
    output=BytesIO();image.save(output,"JPEG");return output.getvalue()


class ResourceTests(unittest.TestCase):
    def test_cpu_guard_stops_before_large_model_load(self):
        from resources import check_memory
        with patch("resources.available_memory",return_value=4*1024**3):
            with self.assertRaisesRegex(RuntimeError,"CPU memory preflight"): check_memory("cpu")
        with patch("resources.available_memory",return_value=24*1024**3):
            check_memory("cpu")

    def test_gpu_guard_handles_insufficient_vram(self):
        from resources import check_memory
        with self.assertRaisesRegex(RuntimeError,"GPU memory preflight"): check_memory("cuda",3*1024**3)
        check_memory("cuda",8*1024**3)


class GeometryTests(unittest.TestCase):
    def test_rotating_item_masks_inputs_without_changing_framing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"image.png";mask_path=Path(folder)/"mask.png"
            Image.new("RGB",(400,300),(30,80,120)).save(path)
            mask=np.zeros((300,400),np.uint8);mask[60:240,100:300]=255
            cv2.imwrite(str(mask_path),mask)
            full,_,transforms=prepare_inputs([path],[mask_path],224,True)
            isolated,masks,isolated_transforms=prepare_inputs([path],[mask_path],224,True,isolate=True)
            np.testing.assert_array_equal(isolated[~masks],255)
            np.testing.assert_array_equal(isolated[masks],full[masks])
            self.assertEqual(transforms,isolated_transforms)
            cv2.imwrite(str(mask_path),np.zeros_like(mask))
            with self.assertRaisesRegex(ValueError,"empty foreground"):
                prepare_inputs([path],[mask_path],224,True,isolate=True)
            with self.assertRaisesRegex(ValueError,"mask for every"):
                prepare_inputs([path],[None],224,True,isolate=True)

    def test_letterbox_preserves_aspect_and_masks_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"image.jpg";Image.new("RGB",(800,400),(120,80,30)).save(path)
            images,masks,transforms=prepare_inputs([path],[None],224,False)
            self.assertEqual(images.shape,(1,224,224,3))
            self.assertFalse(masks[0,0,0]);self.assertTrue(masks[0,112,112])
            self.assertEqual(transforms[0]["top"],56)

    def test_joint_cloud_and_cross_view_filter(self):
        images=np.full((3,112,112,3),140,np.uint8);masks=np.ones((3,112,112),bool)
        p=FixtureBackend().predict(images,Event(),lambda **kw:None)
        p["depth"][0,20:40,20:40]=30
        xyz,rgb,report=fuse_predictions(p["depth"],p["confidence"],p["extrinsics"],p["intrinsics"],images,masks)
        self.assertGreater(len(xyz),100)
        self.assertLess(np.max(xyz[:,2]),4)
        self.assertEqual(rgb.shape,xyz.shape)
        self.assertTrue(report["cross_view_consistency"])

    def test_pose_inverse_is_used(self):
        E=np.c_[np.eye(3),[-2,0,0]]
        K=np.eye(3)
        points=unproject(np.ones((2,2)),E,K)
        np.testing.assert_allclose(points[0,0],[2,0,1])

    def test_uniform_selection_covers_recording(self):
        photos=[dict(id=i) for i in range(100)]
        selected=select_views(photos,8)
        self.assertEqual(len(selected),8);self.assertEqual(selected[0]["id"],0);self.assertEqual(selected[-1]["id"],99)

    def test_empty_foreground_rejects_instead_of_exporting_background(self):
        images=np.zeros((3,28,28,3),np.uint8);p=FixtureBackend().predict(images,Event(),lambda **kw:None)
        with self.assertRaisesRegex(ValueError,"foreground"):
            fuse_predictions(p["depth"],p["confidence"],p["extrinsics"],p["intrinsics"],images,np.zeros((3,28,28),bool))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.storage=patch.object(server,"SCANS",Path(self.temp.name));self.storage.start()
        self.backend=patch.object(server,"BACKEND",FixtureBackend());self.backend.start()
        self.queue=CollectedJobs();self.worker=patch.object(server,"JOBS",self.queue);self.worker.start()
        server.SESSIONS.clear();self.client=TestClient(server.app, base_url="http://localhost", client=("127.0.0.1", 50000))
        self.scan=self.client.post("/api/scans").json();self.path="/api/scans/"+self.scan["id"]
        self.client.put(self.path+"/settings",json=dict(auto=False,foreground=False,image_size=224,max_views=6))
        self.client.put(self.path+"/capture-settings",json=dict(mode="auto",roi=[.1,.1,.9,.9],background=False,preview=False,denoise=False))
    def tearDown(self):
        self.client.close();self.storage.stop();self.backend.stop();self.worker.stop();server.SESSIONS.clear();self.temp.cleanup()
    def upload(self,n): return self.client.post(self.path+"/photos",content=image_bytes(n),params=dict(name=f"angle {n}.jpg"))
    def add_views(self):
        for i in range(3): self.assertEqual(self.upload(i).status_code,200)

    def test_validation_duplicate_and_origin(self):
        self.assertEqual(self.client.post(self.path+"/photos",content=b"invalid").status_code,400)
        self.assertEqual(self.client.post(self.path+"/build").status_code,400)
        self.assertEqual(self.client.post("/api/scans",headers=dict(Origin="https://other.example")).status_code,403)
        self.assertTrue(self.upload(0).json()["added"]);self.assertFalse(self.upload(0).json()["added"])
        self.assertEqual(self.client.get(self.path+"/results/"+("a"*32)+"/session.json").status_code,404)

    def test_manual_capture_only_saves_requested_photos_and_finishes(self):
        config=dict(type="settings",mode="manual",roi=[.1,.1,.9,.9],background=False,preview=False,denoise=False)
        with self.client.websocket_connect("ws://localhost/ws/"+self.scan["id"]) as ws:
            self.assertEqual(ws.receive_json()["type"],"ready")
            ws.send_text(json.dumps(config));self.assertEqual(ws.receive_json()["type"],"settings")
            for i in range(3):
                ws.send_bytes(image_bytes(i));self.assertFalse(ws.receive_json()["saved"])
            for i in range(3):
                ws.send_text("capture");self.assertEqual(ws.receive_json()["type"],"capture")
                ws.send_bytes(image_bytes(i));self.assertTrue(ws.receive_json()["saved"])
                ws.send_bytes(image_bytes(i+10));self.assertFalse(ws.receive_json()["saved"])
        snapshot=self.client.get(self.path).json()
        self.assertEqual(len(snapshot["photos"]),3);self.assertFalse(self.queue.jobs)
        self.assertTrue(all(p["source"]=="manual" for p in snapshot["photos"]))
        self.client.put(self.path+"/settings",json=dict(auto=True,foreground=False,image_size=224,max_views=4))
        self.assertFalse(self.client.get(self.path).json()["auto"]);self.assertFalse(self.queue.jobs)
        self.assertEqual(self.client.post(self.path+"/finish").status_code,200)
        self.queue.run_next();snapshot=self.client.get(self.path).json()
        self.assertTrue(snapshot["latest_result"]["final"])
        response=self.client.get(self.path+"/results/"+snapshot["latest_result"]["id"]+"/merged.ply")
        self.assertEqual(response.status_code,200);self.assertTrue(response.content.startswith(b"ply\n"))

    def test_manual_soft_frame_is_saved_once_and_mode_is_validated(self):
        self.assertEqual(self.client.put(self.path+"/capture-settings",json=dict(mode="invalid")).status_code,400)
        self.assertEqual(self.client.put(self.path+"/settings",json=dict(turntable=True,foreground=False)).status_code,400)
        with self.client.websocket_connect("ws://localhost/ws/"+self.scan["id"]) as ws:
            ws.receive_json()
            ws.send_text(json.dumps(dict(type="settings",mode="manual",background=False,denoise=False)))
            self.assertEqual(ws.receive_json()["type"],"settings")
            buf=BytesIO();Image.new("RGB",(640,480),(100,100,100)).save(buf,"JPEG")
            ws.send_text("capture");ws.receive_json()
            with patch("capture.clean_frame",return_value=(np.zeros((480,640,3),np.uint8),np.zeros((480,640),np.uint8))):
                ws.send_bytes(buf.getvalue());result=ws.receive_json()
            self.assertTrue(result["saved"]);self.assertIn("soft",result["note"]);self.assertIn("empty",result["note"])
            ws.send_bytes(buf.getvalue());self.assertFalse(ws.receive_json()["saved"])
        self.assertEqual(len(self.client.get(self.path).json()["photos"]),1)

    def test_new_scan_manual_defaults_and_old_scan_capture_compatibility(self):
        new=self.client.post("/api/scans").json()
        self.assertEqual(new["capture_settings"]["mode"],"manual")
        self.assertFalse(new["auto"]);self.assertTrue(new["options"]["turntable"])
        self.assertEqual(new["options"]["max_views"],4)
        old=dict(self.client.get(self.path).json());old.pop("id")
        old["capture_settings"].pop("mode");old["options"].pop("turntable",None)
        from capture import Capture
        session=server.Session(self.scan["id"],old)
        result=Capture(session,lambda *args:None).process(image_bytes(90))
        self.assertTrue(result["saved"])

    def test_capture_continues_during_background_job(self):
        self.add_views();self.client.post(self.path+"/build")
        with self.client.websocket_connect("ws://localhost/ws/"+self.scan["id"]) as ws:
            self.assertEqual(ws.receive_json()["type"],"ready")
            ws.send_text(json.dumps(dict(type="settings",roi=[.1,.1,.9,.9],background=False,preview=False,denoise=False)))
            self.assertEqual(ws.receive_json()["type"],"settings")
            ws.send_bytes(image_bytes(7));response=ws.receive_json()
            self.assertEqual(response["type"],"frame");self.assertTrue(response["saved"])
        snapshot=self.client.get(self.path).json()
        self.assertEqual(len(snapshot["photos"]),4);self.assertEqual(snapshot["job"]["state"],"queued")
        self.queue.run_next()
        snapshot=self.client.get(self.path).json()
        self.assertEqual(snapshot["latest_result"]["captured_views"],3)
        self.assertLess(snapshot["latest_result"]["revision"],snapshot["revision"])
        output=self.client.get(self.path+"/results/"+snapshot["latest_result"]["id"]+"/merged.ply")
        self.assertEqual(output.status_code,200);self.assertTrue(output.content.startswith(b"ply\n"))

    def test_finish_queues_latest_snapshot_after_current_pass(self):
        self.add_views();self.client.post(self.path+"/build")
        self.upload(4)
        response=self.client.post(self.path+"/finish").json()
        self.assertTrue(response["final_pending"])
        self.assertEqual(len(self.queue.jobs),1)
        self.queue.run_next();self.assertEqual(len(self.queue.jobs),1)
        self.queue.run_next()
        data=self.client.get(self.path).json()
        self.assertTrue(data["latest_result"]["final"])
        self.assertEqual(data["latest_result"]["captured_views"],4)
        self.assertTrue(data["finish_requested"])

    def test_failure_and_cancellation_preserve_good_result(self):
        self.add_views();self.client.post(self.path+"/build");self.queue.run_next()
        good=self.client.get(self.path).json()["latest_result"]["id"]
        self.client.post(self.path+"/build")
        with patch.object(server.BACKEND,"predict",side_effect=RuntimeError("fixture memory error")): self.queue.run_next()
        result=self.client.get(self.path).json()
        self.assertEqual(result["job"]["state"],"failed");self.assertEqual(result["latest_result"]["id"],good)
        self.client.post(self.path+"/build");self.client.post(self.path+"/cancel");self.queue.run_next()
        result=self.client.get(self.path).json()
        self.assertEqual(result["job"]["state"],"cancelled");self.assertEqual(result["latest_result"]["id"],good)

    def test_auto_jobs_are_coalesced(self):
        self.add_views()
        self.client.put(self.path+"/settings",json=dict(auto=True,foreground=False,image_size=224,max_views=6,interval=5))
        for _ in range(10): self.client.get(self.path)
        self.assertEqual(len(self.queue.jobs),1)
        for i in range(4,7): self.upload(i)
        self.assertEqual(len(self.queue.jobs),1)


if __name__=="__main__": unittest.main()
