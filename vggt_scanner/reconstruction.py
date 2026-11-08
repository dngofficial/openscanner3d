"""One joint multi-view snapshot -> one coherent, immutable reconstruction."""
import json
from pathlib import Path
import time
import traceback
import zipfile
import numpy as np
from geometry import prepare_inputs,fuse_predictions,export_cloud

OUTPUTS={"merged.ply","preview.json","report.json","cameras.json","predictions.npz","reconstruction.log","results.zip"}


def select_views(photos,limit):
    if len(photos)<=limit: return list(photos)
    # Preserve chronology and coverage of the entire recording, including its ends.
    return [photos[int(i)] for i in np.linspace(0,len(photos)-1,limit,dtype=int)]


def reconstruct(root,job_id,photos,options,cancel,update,backend,final=False):
    root=Path(root);output=root/"reconstructions"/job_id
    output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    selected=select_views(photos,options["max_views"] if final else min(8,options["max_views"]))
    log=output/"reconstruction.log"
    report=dict(engine="VGGT",final=final,captured_views=len(photos),selected_views=len(selected),
                selected_ids=[p["id"] for p in selected],model=backend.status()["model"])
    try:
        if cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
        update(stage="inputs",progress=10,message=f"Preparing {len(selected)} overlapping views…")
        paths=[root/"images"/(p["id"]+".jpg") for p in selected]
        masks=[root/"masks"/(p["id"]+".png") for p in selected]
        images,foreground,transforms=prepare_inputs(paths,masks,options["image_size"],options["foreground"])
        if cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
        update(stage="model",progress=20,message="Loading or waiting for the VGGT model…")
        predictions=backend.predict(images,cancel,update)
        points,colors,stats=fuse_predictions(
            predictions["depth"],predictions["confidence"],predictions["extrinsics"],predictions["intrinsics"],
            images,foreground,percentile=options["confidence"],consistency=options["consistency"],
            cleanup=options["cleanup"],cancel=cancel,update=update)
        report.update(stats)
        update(stage="export",progress=90,message="Writing the merged colored PLY…")
        export_cloud(output,points,colors)
        cameras=[]
        for photo,transform,E,K in zip(selected,transforms,predictions["extrinsics"],predictions["intrinsics"]):
            original=K.copy();original[0,0]/=transform["scale_x"];original[1,1]/=transform["scale_y"]
            original[0,2]=(K[0,2]-transform["left"])/transform["scale_x"]
            original[1,2]=(K[1,2]-transform["top"])/transform["scale_y"]
            cameras.append(dict(id=photo["id"],world_to_camera=E.tolist(),model_intrinsics=K.tolist(),
                                original_image_intrinsics=original.tolist(),letterbox=transform))
        (output/"cameras.json").write_text(json.dumps(dict(convention="OpenCV world-to-camera; scale arbitrary",cameras=cameras),indent=2))
        np.savez_compressed(output/"predictions.npz",**predictions)
        report["seconds"]=round(time.monotonic()-started,2)
        (output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        log.write_text(json.dumps(report,indent=2),encoding="utf-8")
        if cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
        with zipfile.ZipFile(output/"results.zip","w",zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(OUTPUTS-{"results.zip"}):
                if (output/name).is_file(): archive.write(output/name,name)
        return dict(id=job_id,frame_ids=report["selected_ids"],captured_views=len(photos),final=final,
                    points=report["merged_points"],seconds=report["seconds"],
                    outputs=sorted(name for name in OUTPUTS if (output/name).is_file()))
    except Exception as error:
        log.write_text(traceback.format_exc(),encoding="utf-8")
        report.update(error=str(error),seconds=round(time.monotonic()-started,2))
        (output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        raise
