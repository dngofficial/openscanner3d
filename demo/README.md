# Demo validation

The existing phone recording is local input under `sample-data/`; it is not a verified reconstruction dataset or a generated model. Keep recordings and generated artifacts out of Git.

Before presenting the demo, record:

- Input provenance, capture conditions, frame selection, and permissions.
- Exact tool versions and commands for reconstruction, texturing, and export.
- Runtime, image registration results, output location, and observed defects.
- Inspection of the real GLB in both the web viewer and Blender.
- Local segmentation inference and whether masks actually affect the export.
- Offline verification with LAN connectivity retained.

Keep the input set and its successfully generated output locally as a labeled fallback. Never present a saved result as a newly completed scan. Label it in the app with:

```powershell
.venv/Scripts/python.exe -m scripts.mark_fallback SCAN_ID --label "What, how many photos, which machine, date"
```

The app then shows a **Saved fallback** badge in Results and in the viewer. On stage, start a real scan first. If it is too slow, show its live stage, then open the labelled fallback and say that it is an earlier result from the same pipeline.

## Offline rehearsal

1. Install everything (setup, `.venv-sam2`, checkpoint, COLMAP, OpenMVS), then run doctor.
2. Disconnect from the internet, keeping the Camo connection if it is needed.
3. Run capture → review → AI selection → reconstruction → viewer → download → open in Blender.
4. In the browser's network tab, confirm every request goes to `localhost`. The UI and viewer load nothing remotely, and the vendored Three.js contains only documentation links.
5. Record anything that failed offline.
