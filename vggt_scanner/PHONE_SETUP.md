# Direct phone capture for VGGT

The phone opens the scanner in its own browser, uses its rear camera and motion sensors,
and uploads photos over your private Wi-Fi. Reconstruction still runs with the existing
VGGT model on the laptop. Camo is not needed for this route.

From the repository root, with the VGGT environment already installed:

```powershell
cd vggt_scanner
.\.venv-vggt\Scripts\python.exe make_cert.py
.\.venv-vggt\Scripts\python.exe server.py --lan
```

`make_cert.py` needs mkcert installed first. If it is absent, install it from
[mkcert's official project](https://github.com/FiloSottile/mkcert), or use
`winget install FiloSottile.mkcert`. The helper also accepts `--mkcert C:\actual\path\mkcert.exe`.
It creates `certs/vggt-cert.pem`, `certs/vggt-key.pem` and a copy of the **public** root CA
at `certs/phone-root-ca.pem`, all under the repository root and ignored by Git.
It asks mkcert to install its local CA on the laptop. Never share the server private key
or mkcert's `rootCA-key.pem`.

The helper sets `TRUST_STORES=system` for its mkcert subprocesses, using
[mkcert's supported trust-store selection](https://github.com/FiloSottile/mkcert#supported-root-stores).
This avoids changing Java's `cacerts` when `JAVA_HOME` is set. If an earlier run reported
`keytool ... cacerts (Access is denied)` after saying the CA was already installed in the
system store, rerun the updated helper. Java trust is separate from the browser setup;
genuine system-store failures still stop certificate generation.

Install and trust the public `phone-root-ca.pem` on your phone before opening the link:

- iPhone: transfer the public certificate, install its downloaded profile in Settings,
  then enable full trust under General > About > Certificate Trust Settings.
- Android: install the public certificate as a CA certificate using the phone's certificate
  settings. Labels vary by device; use your device's certificate installation instructions.

Rerun the helper when the laptop's Wi-Fi IP changes. An existing trusted certificate can
also be supplied with `server.py --lan --cert C:\actual\cert.pem --key C:\actual\key.pem`.
Do not bypass browser certificate warnings. Camera and sensor access require a trusted
secure context: [camera API](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia),
[orientation API](https://developer.mozilla.org/en-US/docs/Web/API/DeviceOrientationEvent).

Open **https://localhost:8000 on the laptop**. Under **Scan to connect your phone**, point
the phone's camera at the QR code and open its link. It pairs into the exact laptop scan;
both devices must be on the same private Wi-Fi. The code is generated locally and is included
in the normal app dependencies. For an older environment missing QR support, install:

```powershell
.\.venv-vggt\Scripts\python.exe -m pip install -r requirements-phone.txt
```

If several laptop network addresses appear, choose the Wi-Fi address the phone can reach;
the QR code updates to that address. The expandable phone link remains available as a fallback.
The QR changes when you create a new scan, and a server restart generates a fresh pairing key.
The phone still asks for camera and motion permission after you open the link.

On Windows, the single-process launcher uses a selector event loop to avoid Python 3.12's
Proactor disconnect teardown error (`_call_connection_lost`, `WinError 10054`). Restart the
server after updating the code. This does not disable certificate validation or change the
VGGT inference path. If a phone cannot load the page, verify its root certificate trust and
that the chosen Wi-Fi address matches the certificate; a QR code does not install trust.

Allow the server through Windows Firewall on your intended private network if Windows prompts.
Do not enable public-network access, port forwarding or a tunnel. The app requires a fresh
pairing link after each server restart. The link exchanges its launch key for an HttpOnly,
Secure cookie and removes the key from the phone's address bar. Pairing links are shown only
when the dashboard is opened through loopback on the laptop.

## Capture

1. Start a new scan on the laptop for a stationary object. Keep existing rotating-item scans
   separate; switching those to phone mode is refused. Stop the laptop's webcam connection
   before switching to phone mode (New scan closes it).
2. Open that scan's phone link and tap Start scanning. Allow camera and, when prompted,
   motion/orientation access. Both sensor permission requests originate in that tap.
3. Keep the object centred and walk around it. Follow the 12-side checklist at low, middle
   and high camera elevations. The phone requests 4K video and displays the actual resolution.
   Uploaded camera frames fit within 1600 pixels on the longest side.
   Upload source bytes are kept unchanged under `originals/`; normalized RGB inputs and masks
   are stored separately under `images/` and `masks/`.
4. Automatic photos require fresh angle readings, steady rotation/acceleration and usable
   exposure. The laptop rejects dark, clipped, blurry or low-contrast guided photos with
   a reason. One upload can be pending; unsuccessful uploads do not count as saved angles.
5. Use Auto off and the shutter for manual photos. Without working sensors, the shutter
   remains usable; untracked photos do not fill the guided checklist. The thumbnail button
   deletes the last saved photo. Pause releases the camera; re-align with the first photo
   when resuming. Returning from another app also requires re-alignment.
6. After at least three saved overlapping views, Finish queues the existing VGGT final pass.
   The laptop gallery updates while the phone saves photos. The phone shows reconstruction
   status and links to the PLY and dashboard. Files can also be uploaded without camera or
   motion permissions, then submitted with Finish uploads & reconstruct.

The 36 checklist cells are capture targets, not a reconstruction guarantee. The default VGGT
budget remains **4 selected views at 224 pixels** for the 8 GB GPU. Capture additional overlapping
photos if useful, but the final pass samples only the configured view budget. Start with a few
nearby overlapping views for an initial test; a four-view pass over an entire orbit may be too
sparse. Increase the budget only when memory permits.

Sensor angles are estimates relative to the first capture orientation; drift and loss of centring
can mislabel coverage. They do not supply VGGT camera poses or measure translation/scale.
Phone mode disables rotating-item input masking: full images feed VGGT, and existing per-view
GrabCut masks can filter its output. This is classical foreground segmentation, not SAM 2.
The current VGGT output is a colored **PLY point cloud**, not a textured triangle mesh or GLB.

## Verification and remaining hardware checks

Run from `vggt_scanner`:

```powershell
.\.venv-vggt\Scripts\python.exe -m unittest discover -p "test_*.py" -v
node --test guidance.test.mjs
node test_ui.cjs
node test_phone_ui.cjs
```

Backend tests use real image decoding/storage and a synthetic reconstruction backend. JavaScript
tests simulate permission prompts, sensor readings and the camera; they do not prove hardware
sensor accuracy or pretrained reconstruction quality. No new checkpoint or inference adapter
was introduced by the phone integration.

Still test on the target iPhone/Android: trusted certificate, camera permission denial and gallery
fallback, sensor permission denial and manual shutter, portrait/landscape framing, pause/resume
alignment, a Wi-Fi interruption, and a real VGGT reconstruction of the saved phone photos. Keep
internet disconnected with LAN retained for the offline runtime check after model downloads.
