"""Smoke check used by setup: run python -m tests.check_capture from the repository root.

Simulates a phone scan through the HTTP API (create, 12 guided photos, finish) on CPU.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app import main as server
from backend.app.config import Settings


def photo(seed):
    rng = np.random.default_rng(seed)
    image = np.full((960, 720, 3), 110, np.uint8)
    for _ in range(600):
        x, y = rng.integers([0, 0], [720, 960])
        cv2.circle(image, (int(x), int(y)), int(rng.integers(3, 12)), tuple(int(v) for v in rng.integers(20, 240, 3)), -1)
    return cv2.imencode(".jpg", image)[1].tobytes()


def main():
    with tempfile.TemporaryDirectory() as root, patch.object(server, "SCANS", Path(root)), \
            patch.object(server, "SETTINGS", Settings()):
        client = TestClient(server.app, client=("127.0.0.1", 50000))
        scan_id = client.post("/api/scans").json()["id"]
        for index in range(12):
            result = client.post(f"/api/scans/{scan_id}/photos?azimuth={index * 30}&pitch=10", content=photo(index),
                                 headers={"content-type": "image/jpeg"}).json()
            assert result["accepted"], result
        finished = client.post(f"/api/scans/{scan_id}/finish").json()
        assert finished["coverage"]["have"] == 12 and "scan.zip" in finished["files"], finished
    print("Passed: guided photos saved with angles, quality checks ran, scan finished with ZIP.")


if __name__ == "__main__":
    main()
