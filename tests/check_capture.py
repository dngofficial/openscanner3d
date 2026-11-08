"""Run python -m tests.check_capture from the repository root."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from backend.app import main as server
from backend.app.config import Settings

def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()

def main():
    rng = np.random.default_rng(41)
    target = np.full((540, 960, 3), 100, np.uint8)
    for _ in range(500):
        x, y = rng.integers([10, 10], [950, 530])
        color = int(rng.integers(130, 255))
        cv2.circle(target, (int(x), int(y)), 4, (color, color, color), -1)
    with tempfile.TemporaryDirectory() as root, patch.object(server, "SCANS", Path(root)), patch.object(server, "SETTINGS", Settings()):
        with patch.object(server.cv2, "findEssentialMat", return_value=(None, None)):
            scan = server.Scan()
            scan.state = "capturing"
            for i in range(12):
                image = cv2.warpAffine(target, np.float32([[1, 0, i * 18], [0, 1, 0]]), (960, 540))
                with patch.object(server.time, "monotonic", return_value=float(i)):
                    result = scan.process(jpeg(image))
                assert result["kfs"] == i + 1, result
                overlay = result["overlay"]
                assert overlay["width"] == 960 and overlay["height"] == 540
                assert 0 < len(overlay["points"]) <= 250
                assert len(overlay["box"]) == 4
                assert all(0 <= x < 960 and 0 <= y < 540 for x, y in overlay["points"])
                if i: assert 0 < len(overlay["trails"]) <= 80
            assert result["tracked_views"] == 1 and result["pts"] == 0, result
            assert len(list(scan.img.glob("*.jpg"))) == 12
            with patch.object(server.time, "monotonic", return_value=20):
                assert scan.process(jpeg(image))["kfs"] == 12
            scan.review()
            result = scan.finish(scan.photos)
            assert result["done"] and not result["model_ready"], result
            assert "Capture-only" in result["msg"], result
        with TestClient(server.app) as client:
            assert client.get("/").status_code == 200
            assert client.get("/api/health").json()["mode"] == "capture"
            with client.websocket_connect("/ws") as ws:
                first = ws.receive_json()
                ws.send_bytes(jpeg(target))
                assert ws.receive_json()["kfs"] == 0
                ws.send_text("start_scan")
                assert ws.receive_json()["state"] == "capturing"
                ws.send_bytes(jpeg(target))
                assert ws.receive_json()["kfs"] == 1
                ws.send_text("capture")
                assert ws.receive_json()["type"] == "capture"
                ws.send_bytes(jpeg(target))
                assert ws.receive_json()["kfs"] == 2
                ws.send_text("reset")
                reset = ws.receive_json()
                assert reset["kfs"] == 0 and reset["scan_id"] != first["scan_id"]
        empty_scan = server.Scan(); empty_scan.state = "capturing"
        empty = empty_scan.process(jpeg(np.zeros((540, 960, 3), np.uint8)))
        assert empty["overlay"]["box"] is None and not empty["overlay"]["points"]
        print("Passed: changed photos saved with mapping unavailable; duplicates suppressed; Finish accepts saved photos; manual capture and reset work.")

if __name__ == "__main__":
    main()
