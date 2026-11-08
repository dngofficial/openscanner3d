# Samsung S26 Ultra and iPhone capture through Camo

The partner reports that the existing iPhone/Camo workflow works. Preserve that setup. The Samsung uses the same virtual webcam interface, so the server does not need an Android-specific image protocol.

## Setup on Windows

1. Install Camo Camera on the phone and Camo Studio on the laptop using [Camo's downloads](https://camo.com/downloads).
2. Connect the phone to Camo Studio and verify its live preview **before** opening the scanner.
3. For Android, Camo supports Wi-Fi or USB. Follow its [Android setup guide](https://camo.com/support/how-to/use-android-as-webcam) and [getting-started guide](https://camo.com/support/camo/camo-getting-started); Android USB on Windows requires USB debugging according to Camo. Prefer whichever method you can verify reliably on the demo network.
4. Select a rear camera in Camo Studio. Start at a consistent 720p or 1080p output, with effects, background replacement, and automatic framing disabled. Keep the same orientation, lens, and zoom throughout a scan.
5. Start the local scanner on the laptop, open `http://localhost:8000`, and press **Start camera**. Allow browser camera permission.
6. The app prefers a device whose name contains Camo. If needed, press **Refresh cameras**, choose Camo explicitly, and check that the live preview is your phone's view.
7. Frame the stationary object before pressing **Start scan**. Use **Pause** when repositioning or handling the phone; preview continues without saving. **Finish & review** lets you exclude unsuitable shots before confirming the selection and downloading the reviewed image ZIP.

## Capturing a set that can reconstruct

The first reviewed set (77 frames of a dark power bank) covered only about a quarter orbit, so COLMAP can at best rebuild one side. For the next capture:

- **Full orbit.** Walk all the way around the object, then repeat at 2–3 heights (low oblique, about 45°, and near top-down). Unseen sides cannot be reconstructed.
- **Texture.** Dark, smooth, uniform objects give few features. Prefer a matte, patterned object, or place the object on newspaper or a printed mat so the background helps registration.
- **Fixed framing.** Turn off Camo stabilisation, auto-framing, and effects. Black wedges in frame corners mean the image is being warped, which breaks the shared-camera assumption.
- **Watermark.** The default mask removes the free-tier Camo watermark. A watermark-free output (Camo Pro) is still preferable. If the watermark moves or the resolution changes, recheck `feature_mask_regions`.

### Turning the object by hand

Pick **I turn the object by hand** before pressing Start scan. Hold the phone steadily and turn or flip the object a little between shots. Frames are saved when they are sharp and different enough from the last one; there is no camera-motion check in this mode.

- Turn slowly and pause briefly; motion blur is the most common rejection.
- Change your grip between turns, so your fingers do not hide the same part of the object in every photo.
- Use soft, even light. Lamps light a different side of the turning object each time, which can make the texture blotchy.
- In review, click the object (top *and* sides) and shift-click your hand, then run AI selection. Add clicks on a photo after a flip if the masks lose the object.

Do not open localhost on the phone for this workflow. The laptop browser receives Camo's webcam feed; the phone runs Camo Camera.

## Failure recovery

- **No Camo in the list:** first confirm the Camo Studio preview, then refresh the browser device list. Labels may remain hidden until camera permission is granted.
- **Black or busy camera:** close other programs holding the virtual camera and retry Start camera. Verify Camo is connected to the intended phone.
- **Permission denied:** allow camera access for the laptop-local site in the browser, then retry.
- **Changed orientation/resolution:** start a new scan. Old photos remain saved; the app refuses to combine mismatched working dimensions.
- **Camera unavailable:** use photo import. Use ordinary JPEG/PNG/WebP at 16 MP or less; high-resolution phone images may exceed the deliberate input budget. Original imported files are preserved separately.
- **Disconnected during capture:** saved files remain on the laptop. Reloading creates a new session; automatic session resume is not implemented.

## Device acceptance checklist

Record the phone model, OS version, laptop browser/version, Camo versions, connection method, selected lens, and delivered resolution.

- [ ] Partner iPhone still appears through Camo and saves at least 12 distinct frames.
- [ ] Galaxy S26 Ultra appears through Camo and saves at least 12 distinct frames.
- [ ] Both previews retain aspect ratio in portrait and landscape; separate scans are used after rotation.
- [ ] Switching cameras starts a new scan without deleting old photos.
- [ ] Permission denial and Camo disconnection give useful instructions.
- [ ] JPEG gallery import works with the camera stopped.
- [ ] The reviewed image ZIP can be extracted on the GPU laptop and its `images/` folder accepted by the batch command.
- [ ] Rehearse without internet after installation, using the selected Camo connection.

These are physical-device acceptance checks, not completed claims. Automated tests cover device selection and framing logic, not Camo drivers or a real Samsung camera.

## Direct phone-browser capture (later)

The app requests a rear camera when no explicit webcam is selected, but the current launcher binds only to laptop loopback. Direct LAN browser capture needs a separately configured trusted HTTPS origin, restricted network access, and phone certificate trust. [Browser camera APIs require a secure context](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia). Do not bypass certificate warnings to make camera permissions work.
