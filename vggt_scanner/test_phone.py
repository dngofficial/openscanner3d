"""Phone HTTP integration with actual decoding/storage and synthetic VGGT predictions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import server
from test_scanner import FixtureBackend, CollectedJobs, image_bytes


class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(server, "SCANS", Path(self.temp.name)),
                        patch.object(server, "BACKEND", FixtureBackend()),
                        patch.object(server, "JOBS", CollectedJobs()),
                        patch("server.lan_addresses", return_value=["192.168.1.10"]),
                        patch("lan.lan_addresses", return_value=["192.168.1.10"])]
        for p in self.patches: p.start()
        server.SESSIONS.clear()
        self.local = TestClient(server.app, base_url="https://localhost", client=("127.0.0.1", 50000))
        self.phone = TestClient(server.app, base_url="https://192.168.1.10", client=("192.168.1.20", 50000))
        self.scan = self.local.post("/api/scans").json()
        self.path = "/api/scans/" + self.scan["id"]

    def tearDown(self):
        self.local.close(); self.phone.close(); server.SESSIONS.clear()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def pair(self):
        link = self.local.get("/api/connect", params={"scan_id": self.scan["id"]}).json()["links"][0]
        response = self.phone.get(link, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("key=", response.headers["location"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_pairing_private_network_origin_and_websocket_access(self):
        self.assertEqual(self.phone.get("/api/health").status_code, 403)
        self.assertEqual(self.phone.get("/phone?key=wrong").status_code, 403)
        with self.assertRaises(WebSocketDisconnect):
            with self.phone.websocket_connect("wss://192.168.1.10/ws/" + self.scan["id"]): pass
        self.pair()
        self.assertEqual(self.phone.get("/api/health").status_code, 200)
        self.assertEqual(self.phone.get("/api/connect").status_code, 403)
        self.assertEqual(self.phone.post("/api/scans", headers={"Origin": "https://evil.example"}).status_code, 403)
        with self.phone.websocket_connect("wss://192.168.1.10/ws/" + self.scan["id"], headers={"Origin": "https://192.168.1.10"}) as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
        unsafe = TestClient(server.app, base_url="http://192.168.1.10", client=("192.168.1.20", 1))
        try: self.assertEqual(unsafe.get("/phone", params={"key": server.ACCESS_KEY}).status_code, 403)
        finally: unsafe.close()

    def test_external_address_and_rebinding_host_are_refused(self):
        for address, host in (("8.8.8.8", "localhost"), ("127.0.0.1", "evil.example")):
            client = TestClient(server.app, base_url="https://" + host, client=(address, 1))
            try: self.assertEqual(client.get("/api/health").status_code, 403)
            finally: client.close()

    def test_page_scripts_and_certificate_address_mismatch(self):
        for path in ("/", "/phone", "/guidance.mjs", "/phone.mjs"):
            self.assertEqual(self.local.get(path).status_code, 200)
        self.assertIn('src="/phone.mjs"', self.local.get("/phone").text)
        with patch.object(server.app.state, "certificate_ips", {"192.168.1.99"}, create=True):
            result = self.local.get("/api/connect").json()
            self.assertFalse(result["links"])
            self.assertIn("certificate", result["problem"])

    def test_qr_is_local_and_optional(self):
        import importlib.util
        response = self.local.get("/api/connect/qr", params={"scan_id": self.scan["id"]})
        if importlib.util.find_spec("segno"):
            self.assertEqual(response.status_code, 200)
            import cv2
            import numpy as np
            image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_GRAYSCALE)
            decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
            expected = self.local.get("/api/connect", params={"scan_id": self.scan["id"]}).json()["links"][0]
            self.assertEqual(decoded, expected)
            self.assertEqual(response.headers["content-type"], "image/png")
        else:
            self.assertEqual(response.status_code, 503)
            self.assertIn("phone link", response.json()["detail"])
        self.assertTrue(self.local.get("/api/connect").json()["links"])
        self.pair()
        self.assertEqual(self.phone.get("/api/connect/qr").status_code, 403)

    def test_phone_photos_coverage_undo_and_vggt_finish(self):
        self.pair()
        result = self.phone.post(self.path + "/phone-mode").json()
        self.assertFalse(result["options"]["turntable"])
        self.assertFalse(result["auto"])
        self.phone.put(self.path + "/capture-settings", json={"mode": "manual", "background": False, "denoise": False})
        for i in range(3):
            response = self.phone.post(self.path + "/photos", content=image_bytes(i),
                                       params={"guided": "true", "azimuth": i * 30, "pitch": 10})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["added"])
        state = self.local.get(self.path).json()
        self.assertEqual(state["coverage"]["have"], 3)
        photo = state["photos"][-1]
        self.assertEqual(photo["source"], "phone")
        self.assertEqual(photo["sector"], 2)
        self.assertIn("sharpness", photo["quality"])
        original = server.get_session(self.scan["id"]).root / "originals" / (photo["id"] + ".jpg")
        self.assertEqual(original.read_bytes(), image_bytes(2))
        self.assertEqual(self.phone.get(self.path + "/images/" + photo["id"]).status_code, 200)
        result = self.phone.delete(self.path + "/photos/" + photo["id"]).json()
        self.assertEqual(result["coverage"]["have"], 2)
        self.assertFalse(original.exists())
        self.phone.post(self.path + "/photos", content=image_bytes(4), params={"guided": "true", "azimuth": 60, "pitch": 10})
        self.assertEqual(self.phone.post(self.path + "/finish").status_code, 200)
        server.JOBS.run_next()
        state = self.local.get(self.path).json()
        self.assertEqual(state["job"]["state"], "completed")
        self.assertEqual(self.phone.get(self.path + "/results/" + state["latest_result"]["id"] + "/merged.ply").status_code, 200)
        self.assertEqual(self.phone.post(self.path + "/photos", content=image_bytes(8)).status_code, 409)
        self.assertEqual(self.phone.post(self.path + "/phone-mode").status_code, 200)

    def test_bad_angles_and_dark_photo_do_not_change_coverage(self):
        self.local.post(self.path + "/phone-mode")
        for values in ({"azimuth": "nan", "pitch": 10}, {"azimuth": 20}, {"azimuth": 200, "pitch": 10}):
            self.assertEqual(self.local.post(self.path + "/photos", content=image_bytes(), params=values).status_code, 400)
        from PIL import Image
        from io import BytesIO
        output = BytesIO(); Image.new("RGB", (640, 480), "black").save(output, "JPEG")
        response = self.local.post(self.path + "/photos", content=output.getvalue(), params={"guided": "true"}).json()
        self.assertFalse(response["added"])
        self.assertIn("Underexposed", response["reason"])
        self.assertEqual(response["scan"]["coverage"]["have"], 0)
        self.assertFalse(response["scan"]["photos"])

    def test_phone_mode_refuses_existing_turntable_input_and_running_job(self):
        self.local.post(self.path + "/photos", content=image_bytes())
        self.assertEqual(self.local.post(self.path + "/phone-mode").status_code, 409)
        session = server.get_session(self.scan["id"])
        session.data["options"]["turntable"] = False
        session.data["job"]["state"] = "running"
        self.assertEqual(self.local.post(self.path + "/phone-mode").status_code, 409)


if __name__ == "__main__": unittest.main()
