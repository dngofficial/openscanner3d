"""Synthetic geometry and live-capture integration checks; no model download needed."""
import json
from pathlib import Path
import tempfile
from threading import Event, RLock
import unittest
from unittest.mock import patch

import cv2
from fastapi.testclient import TestClient
import numpy as np

from cloud_fusion import align_frame, fit_inverse_depth, merge_live_clouds, rotation
from preprocessing import clean_frame
import server


class FakeDepth:
    state = "ready"
    status_lock = RLock()
    def predict(self, rgb):
        yy, xx = np.mgrid[:rgb.shape[0], :rgb.shape[1]]
        return (1 + xx / 1000 + yy / 2000).astype(np.float32)
    def status(self): return dict(state="ready",device="test",error="",installed=True)


class LiveTests(unittest.TestCase):
    def test_background_is_removed_before_cloud_generation(self):
        image=np.full((480,640,3),(40,140,40),np.uint8)
        cv2.ellipse(image,(320,240),(110,150),0,0,360,(20,30,220),-1)
        cleaned,mask=clean_frame(image,roi=(.25,.12,.75,.88))
        self.assertGreater(mask[240,320],0)
        self.assertEqual(mask[20,20],0)
        self.assertTrue(np.all(cleaned[20,20]==127))
        self.assertLess(float(np.mean(mask>0)),.4)

    def test_websocket_clouds_saved_and_masks_persist(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server,"SCANS",Path(directory)), patch.object(server,"ENGINE",FakeDepth()):
            server.SESSIONS.clear()
            client=TestClient(server.app)
            info=client.post("/api/scans").json();root=Path(directory)/info["id"]
            image=np.random.default_rng(2).integers(0,255,(480,640,3),dtype=np.uint8)
            encoded=cv2.imencode(".jpg",image)[1].tobytes()
            with client.websocket_connect("/ws/"+info["id"]) as ws:
                self.assertEqual(ws.receive_json()["type"],"ready")
                ws.send_text(json.dumps(dict(type="settings",background=False,denoise=True,roi=[.1,.1,.9,.9])))
                self.assertEqual(ws.receive_json()["type"],"settings")
                ws.send_bytes(encoded);frame=ws.receive_json()
                self.assertEqual(frame["type"],"frame")
                self.assertEqual(frame["saved_count"],1)
                self.assertGreater(len(frame["cloud"]),100)
                self.assertTrue(frame["mask"])
                ws.send_text("capture");self.assertEqual(ws.receive_json()["type"],"capture")
                ws.send_bytes(encoded);self.assertEqual(ws.receive_json()["saved_count"],2)
            self.assertEqual(len(list((root/"depth").glob("*.npz"))),2)
            self.assertEqual(len(list((root/"clouds").glob("*.ply"))),2)
            self.assertEqual(len(list((root/"masks").glob("*.jpg.png"))),2)
            client.close();server.SESSIONS.clear()

    def test_original_tracking_context_is_kept_but_cloud_mask_excludes_it(self):
        class RecordingDepth(FakeDepth):
            def predict(self,rgb):
                self.seen=rgb.copy()
                return super().predict(rgb)
        from video_capture import Capture
        with tempfile.TemporaryDirectory() as directory,patch.object(server,"SCANS",Path(directory)):
            session=server.Session("e"*32);engine=RecordingDepth()
            image=np.full((960,1280,3),(40,140,40),np.uint8)
            cv2.ellipse(image,(640,480),(210,300),0,0,360,(20,30,220),-1)
            capture=Capture(session,engine)
            capture.settings=dict(background=True,denoise=False,roi=[.25,.1,.75,.9])
            reply=capture.process(cv2.imencode(".jpg",image)[1].tobytes())
            photo=session.data["photos"][0];name=photo["id"]+".jpg"
            saved=cv2.imread(str(session.root/"images"/name))
            self.assertEqual(saved.shape[:2],(960,1280))
            self.assertLess(float(np.linalg.norm(saved[20,20].astype(float)-[40,140,40])),5)
            self.assertFalse(np.all(engine.seen[20,20]==127))
            self.assertEqual(engine.seen.shape[:2],(480,640))
            mask=cv2.imread(str(session.root/"masks"/(name+".png")),cv2.IMREAD_GRAYSCALE)
            self.assertEqual(mask[10,10],0)
            self.assertLess(reply["local_points"],6000)
            self.assertTrue(photo["tracking_context"])


class FusionTests(unittest.TestCase):
    def test_inverse_fit_handles_outliers_and_bad_anchors(self):
        x=np.linspace(.1,2,100);inverse=.3*x+.2
        z=1/inverse
        z[::7]=50
        a,b,stats=fit_inverse_depth(x,z)
        self.assertAlmostEqual(a,.3,places=6);self.assertAlmostEqual(b,.2,places=6)
        self.assertGreater(stats["inliers"],80)
        with self.assertRaises(ValueError): fit_inverse_depth(x[:10],z[:10])
        with self.assertRaises(ValueError): fit_inverse_depth(x,np.ones(100))

    def plane(self, center):
        f=540.;width,height=640,480
        yy,xx=np.mgrid[:height,:width]
        rayx=(xx+.5-width/2)/f;rayy=(yy+.5-height/2)/f
        z=(3+.2*center)/(1-.2*rayx-.15*rayy)
        return ((1/z-.05)/.4).astype(np.float32)

    def fixture(self, center=0):
        xs,ys=np.meshgrid(np.linspace(70,570,14),np.linspace(60,420,12))
        rayx=(xs.ravel()-320)/540;rayy=(ys.ravel()-240)/540
        z=3/(1-.2*rayx-.15*rayy)
        xyz=np.column_stack((rayx*z,rayy*z,z))
        landmarks={i+1:point for i,point in enumerate(xyz)}
        cam=xyz-[center,0,0]
        xy=cam[:,:2]/cam[:,2:3]*540+[320,240]
        view=dict(R=np.eye(3),t=np.array([-center,0,0]),xy=xy,ids=list(landmarks),
                  camera=("SIMPLE_RADIAL",640,480,np.array([540,320,240,0])))
        return view,landmarks

    def test_pose_transform_and_depth_calibration(self):
        view,landmarks=self.fixture(.08)
        depth=self.plane(.08);rgb=np.full((480,640,3),120,np.uint8)
        world,_,stats=align_frame(depth,rgb,view,landmarks)
        residual=np.abs(world[:,2]-(3+.2*world[:,0]+.15*world[:,1]))
        self.assertLess(np.median(residual),1e-4)
        self.assertAlmostEqual(stats["scale"],.4,places=3)
        self.assertAlmostEqual(stats["offset"],.05,places=3)
        q=[np.cos(np.pi/4),0,0,np.sin(np.pi/4)]
        np.testing.assert_allclose(rotation(q)@[1,0,0],[0,1,0],atol=1e-8)

    def test_camera_intrinsics_scale_to_depth_resolution(self):
        view,landmarks=self.fixture(.08)
        view["xy"]=view["xy"]*2
        view["camera"]=("SIMPLE_RADIAL",1280,960,np.array([1080,640,480,0]))
        world,_,_=align_frame(self.plane(.08),np.full((480,640,3),120,np.uint8),view,landmarks)
        residual=np.abs(world[:,2]-(3+.2*world[:,0]+.15*world[:,1]))
        self.assertLess(np.median(residual),1e-4)

    def test_three_clouds_merge_and_outlier_noise_is_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/"model";model.mkdir()
            for folder in ("images","depth","masks"): (root/folder).mkdir()
            photos=[dict(id=f"frame{i}") for i in range(3)]
            (root/"session.json").write_text(json.dumps(dict(photos=photos)))
            (model/"cameras.txt").write_text("1 SIMPLE_RADIAL 640 480 540 320 240 0\n")
            _,landmarks=self.fixture()
            (model/"points3D.txt").write_text("".join(f"{i} {' '.join(map(str,p))} 100 120 130 0\n" for i,p in landmarks.items()))
            text=""
            for i,center in enumerate((0,.08,-.08)):
                view,_=self.fixture(center);name=photos[i]["id"]+".jpg"
                text+=f"{i+1} 1 0 0 0 {-center} 0 0 1 {name}\n"
                text+=" ".join(f"{xy[0]} {xy[1]} {point_id}" for xy,point_id in zip(view["xy"],view["ids"]))+"\n"
                cv2.imwrite(str(root/"images"/name),np.full((480,640,3),120,np.uint8))
                np.savez_compressed(root/"depth"/(photos[i]["id"]+".npz"),inverse_depth=self.plane(center))
                cv2.imwrite(str(root/"masks"/(name+".png")),np.full((480,640),255,np.uint8))
            (model/"images.txt").write_text(text)
            result=merge_live_clouds(root,model,Event(),lambda **state:None)
            self.assertEqual(result["accepted_clouds"],3)
            self.assertGreater(result["merged_points"],100)
            self.assertGreater(result["filtered_voxels"],0)
            self.assertTrue((root/"merged.ply").is_file())
            preview=json.loads((root/"merged_preview.json").read_text())
            self.assertEqual(preview["total"],result["merged_points"])


if __name__=="__main__":
    unittest.main()
