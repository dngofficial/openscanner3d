"""Create the LAN HTTPS certificate with mkcert (https://github.com/FiloSottile/mkcert).

Phones only allow camera and motion-sensor access on a trusted HTTPS page. mkcert makes a local
certificate authority on this laptop and signs a certificate for its current Wi-Fi address(es).
Install the printed root certificate (public, no private key) on the phone once.

Usage: .venv/Scripts/python.exe -m scripts.make_cert
Rerun it whenever the laptop's Wi-Fi address changes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from backend.app.config import load_settings, project_path
from backend.app.network import lan_addresses


def find_mkcert(explicit: str | None = None) -> str | None:
    """PATH first, then winget's install folders (winget's portable installs are often not on PATH)."""
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = shutil.which("mkcert")
    if found or not os.environ.get("LOCALAPPDATA"):
        return found
    winget = Path(os.environ["LOCALAPPDATA"], "Microsoft", "WinGet")
    for pattern in ("Links/mkcert.exe", "Packages/FiloSottile.mkcert_*/mkcert*.exe"):
        matches = sorted(winget.glob(pattern))
        if matches:
            return str(matches[-1])
    return None


def run(command: list[str]) -> str:
    # Browser setup needs the system store. Avoid Java's protected cacerts when JAVA_HOME is set.
    environment = os.environ.copy()
    environment["TRUST_STORES"] = "system"
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120, env=environment)
    if result.returncode:
        sys.exit(f"{' '.join(command[:2])} failed:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mkcert", help="Path to mkcert.exe if it is not found automatically.")
    args = parser.parse_args()
    try:
        settings = load_settings()
    except (ValueError, TypeError, OSError) as error:
        parser.error(f"Configuration error: {error}")
    mkcert = find_mkcert(args.mkcert)
    if not mkcert:
        sys.exit(r"mkcert not found. Install it (winget install FiloSottile.mkcert) or pass --mkcert C:\path\to\mkcert.exe.")
    addresses = lan_addresses()
    if not addresses:
        sys.exit("No private network address found. Connect this laptop to the Wi-Fi the phone will use.")
    cert, key = project_path(settings.tls_cert), project_path(settings.tls_key)
    cert.parent.mkdir(parents=True, exist_ok=True)
    names = [*addresses, socket.gethostname(), "localhost", "127.0.0.1"]
    run([mkcert, "-install"])  # Creates/trusts the local CA on this laptop (once).
    run([mkcert, "-cert-file", str(cert), "-key-file", str(key), *names])
    cert.with_suffix(".json").write_text(json.dumps(dict(addresses=addresses, names=names), indent=2), encoding="utf-8")
    root = Path(run([mkcert, "-CAROOT"])) / "rootCA.pem"
    phone_copy = cert.parent / "phone-root-ca.pem"
    shutil.copyfile(root, phone_copy)  # Public certificate only; rootCA-key.pem stays in mkcert's folder.
    print(f"Certificate for {', '.join(names)} written to {cert.parent}.")
    print(f"Install this on the phone once: {phone_copy}")
    print("Send it to the phone yourself (AirDrop, Nearby Share, USB or your own email). Never share rootCA-key.pem.")
    print("Then see docs/PHONE_SETUP.md to trust it (iPhone needs an extra 'full trust' switch).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
