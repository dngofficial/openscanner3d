# Phone setup: direct, guided capture over your Wi-Fi

The phone's own browser (Safari on iPhone, Chrome on Android) opens the scanner page served by the laptop. No app or Camo is needed. Phone browsers only allow the camera and motion sensors on a **trusted HTTPS** page, so a one-time certificate setup is required. Everything stays on your local network; nothing goes through the internet.

## One-time setup

### On the laptop

1. Install mkcert:
   ```powershell
   winget install FiloSottile.mkcert
   ```
   Then open a **new** PowerShell window.
2. Connect the laptop to the Wi-Fi the phone will use. Make sure Windows treats it as a **Private** network: Settings → Network & internet → Wi-Fi → (your network) → Private network.
3. In the repository folder, create the certificate:
   ```powershell
   .venv/Scripts/python.exe -m scripts.make_cert
   ```
   This creates a local certificate authority on the laptop (`mkcert -install`) and a certificate for the laptop's current Wi-Fi address, saved in `certs/` (ignored by Git). It also copies the **public** root certificate to `certs/phone-root-ca.pem`. Never share `rootCA-key.pem` from mkcert's folder.
4. Add to `config.local.toml`:
   ```toml
   lan = true
   ```
5. Start the scanner:
   ```powershell
   ./scripts/launch.ps1
   ```
   If Windows Firewall asks, allow Python on **Private networks only**.

### On the phone: trust the laptop's root certificate

Send `certs/phone-root-ca.pem` to the phone yourself (AirDrop, Nearby Share, USB cable, or an email to yourself). Do not download it from a website.

**iPhone (Safari):**
1. Open the file. iOS says "Profile Downloaded".
2. Settings → General → VPN & Device Management → the downloaded profile → **Install**.
3. Settings → General → About → Certificate Trust Settings → turn on **full trust** for the mkcert root. Without this step Safari still shows a warning.

**Android (Chrome, including Samsung):**
1. Settings → Security (Samsung: Security and privacy → More security settings) → Install from device storage / Encryption & credentials → **CA certificate**.
2. Pick `phone-root-ca.pem` and confirm. Android shows a general warning about installed CAs; that is expected for a local CA.

Menu names vary by Android version and manufacturer. Search Settings for "CA certificate" if you can't find it.

Never bypass a browser certificate warning instead: the camera and sensors would not work reliably, and it trains everyone to ignore warnings.

## Each scan

1. On the laptop, open `https://localhost:8443`. The page shows a QR code containing the phone link and a key, which changes every time the scanner starts.
2. Scan the QR code with the phone camera and open the link. The key is moved into a cookie and removed from the address bar.
3. Put the object on a table with room to walk around it. Stand at the side you want as its front and point the phone at it.
4. Tap **Start**, then allow camera access. On iPhone, also allow **Motion & Orientation**.
5. Keep the object inside the dashed circle and follow the instruction at the top:
   - **Walk left / right about N° around the object**: move to the next missing side.
   - **Raise the phone and look down** / **Lower the phone**: change height.
   - **Hold still. Capturing this angle.**: the photo is taken automatically when the phone is steady and the light is OK.
   - **Shift a small step sideways**: the side needs a second, slightly different view.

   A green flash plus a vibration (Android) or beep confirms a saved photo. A rejection shows the reason, such as "Blurry (sharpness 18, need 25)".
6. The ring at the bottom left is the checklist, seen from above with the object in the middle. The outer ring is low angle and the inner ring is high angle; the start side is at the bottom. Grey means missing, amber partly done, teal done, and a white outline is the next target. The white dot is you.
7. Tap **Finish** when the count reaches 60, or earlier if some angles are impossible. The laptop page then offers `scan.zip` and, in reconstruct mode, **Reconstruct**.

**Tips:**
- Keep the phone the same way up (portrait or landscape) for the whole scan.
- **Capture now** takes a photo manually at any time.
- **Undo** removes the last photo.
- If the page reloads mid-scan, tap **Continue that scan**, stand where the first photo was taken, and tap **Re-align**.

## How the angle guidance works, and its limits

The phone's gyroscope and compass give two things: how far the phone's heading has turned since Start, and how far it tilts below the horizon. As you walk around a stationary object while keeping it centred, the heading turns by the same angle you have walked. So the app maps photos to 12 sides × 3 heights without any image processing. This assumes:

- **The object stays centred.** Turning the phone without walking registers as a different side.
- **The object does not move.** This mode is for walking around a stationary object, not turning the object.
- **Readings drift slightly over time.** Phone orientation drifts a few degrees per minute, and indoor magnetic fields can disturb the compass on some Android phones. Re-align if the map clearly disagrees with where you are.

The angles saved with each photo (`poses.json` in the ZIP) are guidance data, not measured camera poses.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Phone can't open the page | Same Wi-Fi? Laptop network set to Private? Firewall allowed Python on private networks? Guest or office Wi-Fi often blocks devices from talking to each other ("client isolation"); use a phone hotspot or home router instead. |
| "Open the link or scan the QR code shown on the laptop" | The key is missing or old (the scanner restarted). Scan the QR code again. |
| Certificate warning on the phone | The root certificate isn't trusted yet (iPhone: also turn on full trust), or the laptop's address changed. Rerun `scripts.make_cert` and use the new QR code. |
| "Motion sensors unavailable" | iPhone: tap Start again and allow Motion & Orientation; if denied, enable it in Settings → Safari → Motion & Orientation Access. Use **Capture now** as a fallback. |
| Camera blocked | Allow camera access for the site in the browser settings, then tap Start again. |
| "Keep the phone in the same orientation" | You rotated the phone; turn it back to how the first photo was taken. |

## Acceptance checklist (record results)

- [ ] iPhone: certificate trusted, page opens over HTTPS, camera and motion permission work, guided scan reaches 60/60.
- [ ] Android (Samsung): same, plus vibration feedback.
- [ ] Permission denied and sensors denied each give a clear message and a usable fallback.
- [ ] Laptop restart: the old key is rejected and the new QR code works.
- [ ] Offline: internet disconnected, Wi-Fi between phone and laptop kept, full scan still works.

These are physical-device checks. The automated tests cover the guidance maths, the API, and access control with simulated sensors only.
