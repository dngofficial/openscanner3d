"""Run the real model on a small set: python smoke_test.py angle1.jpg angle2.jpg angle3.jpg"""
import argparse
from pathlib import Path
from threading import Event
import uuid
from PIL import Image,ImageOps
from reconstruction import reconstruct
from vggt_backend import BACKEND


def main():
    parser=argparse.ArgumentParser(description="Run real VGGT reconstruction and colored PLY export.")
    parser.add_argument("images",nargs="+")
    parser.add_argument("--output",default="smoke_output")
    parser.add_argument("--size",type=int,choices=(224,336,518),default=224)
    args=parser.parse_args()
    if len(args.images)<3: parser.error("Provide at least 3 overlapping images.")
    root=Path(args.output).resolve()
    (root/"images").mkdir(parents=True,exist_ok=True);(root/"masks").mkdir(exist_ok=True)
    photos=[]
    for path in args.images:
        image_id=uuid.uuid4().hex
        with Image.open(path) as source: image=ImageOps.exif_transpose(source).convert("RGB")
        image.save(root/"images"/(image_id+".jpg"),quality=95)
        photos.append(dict(id=image_id,name=Path(path).name))
    options=dict(max_views=min(12,len(photos)),image_size=args.size,confidence=35,
                 consistency=True,cleanup=True,foreground=False)
    result=reconstruct(root,uuid.uuid4().hex,photos,options,Event(),
                       lambda **state:print(state.get("message",state),flush=True),BACKEND,final=True)
    print("PLY:",root/"reconstructions"/result["id"]/"merged.ply")
    print("Points:",result["points"],"seconds:",result["seconds"])


if __name__=="__main__": main()
