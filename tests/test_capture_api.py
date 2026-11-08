"""Phone capture API: access control, photo checks, angle bookkeeping, finish/ZIP, reconstruction start."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import coverage, main as server, network
from backend.app.config import Settings

LAN, OUTSIDE, LOCAL = ("192.168.1.40", 50000), ("8.8.8.8", 50000), ("127.0.0.1", 50000)


def photo(seed=1, width=1080, height=1440, blur=False):
    rng = np.random.default_rng(seed)
    image = np.full((height, width, 3), 120, np.uint8)
    for _ in range(900):
        x, y = rng.integers([0, 0], [width, height])
        cv2.circle(image, (int(x), int(y)), int(rng.integers(3, 14)), tuple(int(v) for v in rng.integers(20, 240, 3)), -1)
    if blur:
        image = cv2.GaussianBlur(image, (61, 61), 20)
    return cv2.imencode(".jpg", image)[1].tobytes()


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        settings = Settings(lan=True, tls_cert=str(self.root / "lan.pem"), tls_key=str(self.root / "lan-key.pem"))
        for target, value in (("SCANS", self.root), ("SETTINGS", settings)):
            patcher = patch.object(server, target, value); patcher.start(); self.addCleanup(patcher.stop)
        self.local = TestClient(server.app, client=LOCAL)

    def phone(self):
        client = TestClient(server.app, client=LAN, base_url="https://testserver")
        client.get(f"/phone?key={network.ACCESS_KEY}", follow_redirects=False)
        return client

    def upload(self, client, scan_id, data, azimuth=None, pitch=None):
        query = "" if azimuth is None else f"?azimuth={azimuth}&pitch={pitch}"
        return client.post(f"/api/scans/{scan_id}/photos{query}", content=data, headers={"content-type": "image/jpeg"}).json()


class AccessTests(ApiTestCase):
    def test_lan_needs_the_launch_key_and_outsiders_are_refused(self):
        outsider = TestClient(server.app, client=OUTSIDE)
        self.assertEqual(outsider.get("/phone").status_code, 403)
        stranger = TestClient(server.app, client=LAN, base_url="https://testserver")
        self.assertEqual(stranger.get("/phone").status_code, 401)
        self.assertEqual(stranger.get("/phone?key=wrong").status_code, 401)
        redirect = stranger.get(f"/phone?key={network.ACCESS_KEY}", follow_redirects=False)
        self.assertEqual(redirect.status_code, 303)
        self.assertNotIn("key=", redirect.headers["location"])  # The key leaves the address bar.
        self.assertIn("httponly", redirect.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", redirect.headers["set-cookie"].lower())
        self.assertIn("secure", redirect.headers["set-cookie"].lower())
        self.assertEqual(redirect.headers["cache-control"], "no-store")
        self.assertEqual(stranger.get("/phone").status_code, 200)
        self.assertEqual(stranger.get("/api/plan").json()["sectors"], 12)

    def test_only_the_laptop_sees_the_phone_link_and_cross_site_posts_fail(self):
        phone = self.phone()
        self.assertEqual(phone.get("/api/connect").status_code, 403)
        with patch.object(network, "lan_addresses", return_value=["192.168.1.10"]):
            info = self.local.get("/api/connect").json()
        self.assertEqual(info["urls"], [f"https://192.168.1.10:8443/phone?key={network.ACCESS_KEY}"])
        self.assertIsNone(info["qr_svg"])  # No certificate yet, so no QR code that would fail on the phone.
        self.assertTrue(any("certificate" in problem for problem in info["problems"]))
        cert = self.root / "lan.pem"; cert.write_text("test"); (self.root / "lan-key.pem").write_text("test")
        with patch.object(server, "SETTINGS", Settings(lan=True, tls_cert=str(cert), tls_key=str(self.root / "lan-key.pem"))),              patch.object(network, "lan_addresses", return_value=["192.168.1.10"]):
            self.assertIn("<svg", self.local.get("/api/connect").json()["qr_svg"])
        self.assertEqual(phone.post("/api/scans", headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(phone.post("/api/scans", headers={"origin": "https://testserver"}).status_code, 200)

    def test_terminal_qr_never_blocks_startup_on_a_cp1252_console(self):
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        server.print_qr("https://192.168.1.10:8443/phone?key=x", console)  # Must not raise.
        console.seek(0)
        self.assertIn("localhost:8443", console.read())
        utf8 = io.StringIO()
        server.print_qr("https://192.168.1.10:8443/phone?key=x", utf8)
        self.assertGreater(len(utf8.getvalue().splitlines()), 10)  # A real QR drawing.

    def test_client_classification(self):
        self.assertEqual([network.client_kind(h) for h in ("127.0.0.1", "::1", "10.0.0.5", "172.20.1.1", "192.168.0.9",
                                                            "169.254.3.3", "100.64.1.1", "8.8.8.8", "testclient")],
                         ["local", "local", "lan", "lan", "lan", "outside", "outside", "outside", "outside"])


class CaptureTests(ApiTestCase):
    def test_guided_capture_records_angles_and_rejects_bad_photos(self):
        phone = self.phone()
        scan_id = phone.post("/api/scans").json()["id"]
        first = self.upload(phone, scan_id, photo(1), azimuth=-31.0, pitch=10.0)
        self.assertTrue(first["accepted"], first)
        self.assertEqual((first["ring"], first["sector"]), (0, 11))
        blurry = self.upload(phone, scan_id, photo(2, blur=True), azimuth=0, pitch=10)
        self.assertFalse(blurry["accepted"]); self.assertTrue(blurry["reason"].startswith("Blurry"))
        rotated = self.upload(phone, scan_id, photo(3, width=1440, height=1080), azimuth=0, pitch=10)
        self.assertIn("same orientation", rotated["reason"])
        self.assertIn("JPEG", self.upload(phone, scan_id, b"not an image")["reason"])
        manual = self.upload(phone, scan_id, photo(4))
        self.assertIsNone(manual["ring"])
        details = phone.get(f"/api/scans/{scan_id}").json()
        self.assertEqual(len(details["photos"]), 2)
        self.assertEqual(details["coverage"]["counts"][0][11], 1)
        self.assertEqual(details["coverage"]["unguided"], 1)
        self.assertEqual(details["size"], [1080, 1440])
        undone = phone.delete(f"/api/scans/{scan_id}/photos/last").json()
        self.assertEqual(undone["removed"], "kf_0001.jpg")
        self.assertFalse((self.root / scan_id / "images/kf_0001.jpg").exists())

    def test_imported_photos_are_normalised_and_originals_kept(self):
        phone = self.phone()
        scan_id = phone.post("/api/scans").json()["id"]
        image = Image.open(io.BytesIO(photo(5, width=1600, height=1200)))
        exif = image.getexif(); exif[274] = 6  # Stored sideways, displayed portrait.
        upload = io.BytesIO(); image.save(upload, format="JPEG", exif=exif)
        result = phone.post(f"/api/scans/{scan_id}/photos?imported=true&azimuth=10&pitch=10", content=upload.getvalue(),
                            headers={"content-type": "image/jpeg"}).json()
        self.assertTrue(result["accepted"], result)
        self.assertIsNone(result["ring"])  # Gallery photos carry no angle data, whatever the query says.
        with Image.open(self.root / scan_id / "images" / "kf_0000.jpg") as working:
            self.assertEqual(working.size, (1200, 1600))
        self.assertEqual((self.root / scan_id / "originals" / "kf_0000.jpg").read_bytes(), upload.getvalue())
        bad = phone.post(f"/api/scans/{scan_id}/photos?imported=true", content=b"nope", headers={"content-type": "image/jpeg"}).json()
        self.assertIn("readable photo", bad["reason"])

    def test_finish_writes_zip_with_poses_and_locks_the_scan(self):
        phone = self.phone()
        scan_id = phone.post("/api/scans").json()["id"]
        self.assertEqual(phone.post(f"/api/scans/{scan_id}/finish").status_code, 409)  # Too few photos.
        for index in range(12):
            self.assertTrue(self.upload(phone, scan_id, photo(index + 10), azimuth=index * 30, pitch=10)["accepted"])
        finished = phone.post(f"/api/scans/{scan_id}/finish").json()
        self.assertTrue(finished["finished"]); self.assertIn("scan.zip", finished["files"])
        self.assertEqual(finished["coverage"]["have"], 12)
        self.assertFalse(self.upload(phone, scan_id, photo(99), azimuth=0, pitch=10)["accepted"])
        with zipfile.ZipFile(self.root / scan_id / "scan.zip") as bundle:
            names = bundle.namelist()
            poses = json.loads(bundle.read("poses.json"))
        self.assertEqual(len([n for n in names if n.startswith("images/")]), 12)
        self.assertEqual([pose["sector"] for pose in poses], list(range(12)))
        self.assertEqual(self.local.get(f"/scans/{scan_id}/scan.zip").status_code, 200)
        self.assertEqual(self.local.get(f"/scans/{scan_id}/../config.local.toml").status_code, 404)
        self.assertEqual(self.local.get("/scans/not_a_scan/scan.zip").status_code, 404)
        listed = self.local.get("/api/scans").json()
        self.assertEqual(listed[0]["id"], scan_id)

    def test_reconstruct_requires_reconstruct_mode_and_runs_in_background(self):
        phone = self.phone()
        scan_id = phone.post("/api/scans").json()["id"]
        for index in range(12):
            self.upload(phone, scan_id, photo(index + 30), azimuth=index * 30, pitch=10)
        self.assertEqual(self.local.post(f"/api/scans/{scan_id}/reconstruct").status_code, 409)  # Not finished.
        self.local.post(f"/api/scans/{scan_id}/finish")
        self.assertEqual(self.local.post(f"/api/scans/{scan_id}/reconstruct").status_code, 409)  # Capture mode.
        done = dict(msg="Experimental mesh exported.", done=True, model_ready=True)
        with patch.object(server, "SETTINGS", Settings(lan=True, mode="reconstruct")), \
             patch.object(server, "reconstruct", return_value=done):
            self.assertEqual(self.local.post(f"/api/scans/{scan_id}/reconstruct").status_code, 202)
            for _ in range(50):
                status = self.local.get(f"/api/scans/{scan_id}").json()["status"]
                if status["state"] != "running":
                    break
                import time; time.sleep(.05)
        self.assertEqual(status["state"], "ready")

    def test_coverage_summary(self):
        poses = [dict(ring=0, sector=0), dict(ring=0, sector=0), dict(ring=0, sector=0), dict(ring=2, sector=5), dict(ring=None)]
        summary = coverage.summarise(poses)
        self.assertEqual(summary["have"], 2)  # Extra photos of a full side do not count twice.
        self.assertEqual(summary["wanted"], 36)
        self.assertEqual(summary["unguided"], 1)
        self.assertEqual([coverage.ring_of(p) for p in (-20, 0, 30, 60, 89)], [None, 0, 1, 2, None])


if __name__ == "__main__":
    unittest.main()
