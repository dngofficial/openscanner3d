"""Install the pinned upstream model code without its conflicting OpenCV dependency."""
import subprocess
import sys

REVISION = "a288dd0f14786c93483e45524328726ab7b1b4ce"
URL = f"https://github.com/facebookresearch/vggt/archive/{REVISION}.zip"
if __name__ == "__main__":
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-deps", URL])
    print("VGGT source installed. Run python server.py. Weights download on first model load.")
