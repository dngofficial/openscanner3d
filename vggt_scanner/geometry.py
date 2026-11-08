"""Model-input letterboxing, cross-view depth checks and colored PLY fusion."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps
from scipy.spatial import cKDTree
from depth_cloud import write_ply, sample_cloud


def prepare_inputs(paths, mask_paths, size=336, foreground=True):
    if size not in (224,336,518): raise ValueError("Use an input size of 224, 336 or 518.")
    images, masks, transforms = [], [], []
    for path, mask_path in zip(paths,mask_paths):
        with Image.open(path) as source: image=ImageOps.exif_transpose(source).convert("RGB")
        width,height=image.size
        scale=size/max(width,height)
        nw=max(14,min(size,round(width*scale/14)*14))
        nh=max(14,min(size,round(height*scale/14)*14))
        left,top=(size-nw)//2,(size-nh)//2
        rgb=np.full((size,size,3),255,np.uint8)
        rgb[top:top+nh,left:left+nw]=np.array(image.resize((nw,nh),Image.Resampling.BICUBIC))
        mask=np.zeros((size,size),bool)
        if foreground and mask_path and Path(mask_path).is_file():
            original=cv2.imread(str(mask_path),cv2.IMREAD_GRAYSCALE)
            if original is None: raise ValueError("Cannot read foreground mask.")
            resized=cv2.resize(original,(nw,nh),interpolation=cv2.INTER_NEAREST)>127
            resized=cv2.erode(resized.astype(np.uint8),np.ones((3,3),np.uint8))>0
        else: resized=np.ones((nh,nw),bool)
        mask[top:top+nh,left:left+nw]=resized
        images.append(rgb);masks.append(mask)
        transforms.append(dict(width=width,height=height,resized_width=nw,resized_height=nh,
                               left=left,top=top,scale_x=nw/width,scale_y=nh/height))
    return np.stack(images),np.stack(masks),transforms


def unproject(depth, extrinsic, intrinsic):
    height,width=depth.shape
    yy,xx=np.mgrid[:height,:width]
    z=depth
    camera=np.stack(((xx-intrinsic[0,2])*z/intrinsic[0,0],
                     (yy-intrinsic[1,2])*z/intrinsic[1,1],z),axis=-1)
    return (camera-extrinsic[:,3]) @ extrinsic[:,:3]


def consistent_support(points, own, depths, extrinsics, intrinsics, masks, confs, threshold,
                       tolerance=.12):
    support=np.ones(len(points),np.int16)
    for j in range(len(depths)):
        if j==own: continue
        cam=points @ extrinsics[j,:,:3].T + extrinsics[j,:,3]
        z=cam[:,2]
        pixels=cam @ intrinsics[j].T
        uv=np.divide(pixels[:,:2],z[:,None],out=np.full((len(points),2),-1.,float),where=z[:,None]>1e-8)
        h,w=depths[j].shape
        good=(z>1e-8)&(uv[:,0]>=0)&(uv[:,0]<w-1)&(uv[:,1]>=0)&(uv[:,1]<h-1)
        x=np.clip(uv[:,0],0,w-1).astype(np.float32).reshape(-1,1)
        y=np.clip(uv[:,1],0,h-1).astype(np.float32).reshape(-1,1)
        observed=cv2.remap(depths[j],x,y,cv2.INTER_LINEAR).ravel()
        valid_mask=cv2.remap(masks[j].astype(np.uint8),x,y,cv2.INTER_NEAREST).ravel()>0
        confidence=cv2.remap(confs[j],x,y,cv2.INTER_LINEAR).ravel()
        support+=(good&valid_mask&(confidence>=threshold)&np.isfinite(observed)&(observed>0)&
                  (np.abs(z-observed)<=tolerance*np.maximum(observed,1e-8))).astype(np.int16)
    return support


def fuse_predictions(depths, confs, extrinsics, intrinsics, images, masks, percentile=35,
                     consistency=True, cleanup=True, cancel=None, update=lambda **kw:None):
    depths=np.asarray(depths,np.float32);confs=np.asarray(confs,np.float32)
    extrinsics=np.asarray(extrinsics,np.float32);intrinsics=np.asarray(intrinsics,np.float32)
    if depths.ndim!=3 or confs.shape!=depths.shape or masks.shape!=depths.shape:
        raise ValueError("Invalid model depth/confidence/mask dimensions.")
    count,height,width=depths.shape
    if extrinsics.shape!=(count,3,4) or intrinsics.shape!=(count,3,3):
        raise ValueError("Invalid camera prediction dimensions.")
    if not np.isfinite(extrinsics).all() or not np.isfinite(intrinsics).all():
        raise ValueError("The model returned non-finite camera parameters.")
    if np.any(intrinsics[:,0,0]<=0) or np.any(intrinsics[:,1,1]<=0):
        raise ValueError("The model returned invalid focal lengths.")
    valid=np.isfinite(depths)&(depths>1e-8)&np.isfinite(confs)&masks
    if valid.sum()<100: raise ValueError("Too few valid foreground predictions. Inspect the mask or disable foreground removal.")
    threshold=float(np.percentile(confs[valid],percentile))
    all_points,all_colors=[],[]
    per_view=[]
    grid=np.zeros((height,width),bool);grid[::2,::2]=True
    for i in range(count):
        if cancel and cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
        update(stage="filtering",progress=65+int(20*(i+1)/count),message=f"Filtering view {i+1}/{count}…")
        selected=valid[i]&(confs[i]>=threshold)&grid
        world=unproject(depths[i],extrinsics[i],intrinsics[i])[selected]
        colors=images[i][selected]
        finite=np.isfinite(world).all(axis=1)
        world,colors=world[finite],colors[finite]
        initial=len(world)
        if consistency and len(world):
            keep=consistent_support(world,i,depths,extrinsics,intrinsics,masks,confs,threshold)>=2
            world,colors=world[keep],colors[keep]
        per_view.append(dict(index=i,candidate_points=initial,kept_points=len(world)))
        if len(world): all_points.append(world);all_colors.append(colors)
    if not all_points: raise ValueError("No points passed multi-view consistency. Add overlapping views or inspect the foreground masks.")
    points=np.concatenate(all_points);colors=np.concatenate(all_colors)
    if len(points)<100: raise ValueError("Fewer than 100 consistent points remain. Add better overlapping views.")
    before=len(points)
    low,high=np.percentile(points,[2,98],axis=0)
    span=max(float(np.max(high-low)),1e-6)
    bounded=np.all((points>=low-span*.5)&(points<=high+span*.5),axis=1)
    points,colors=points[bounded],colors[bounded]
    voxel=span/250
    keys=np.floor((points-low)/voxel).astype(np.int64)
    _,inverse=np.unique(keys,axis=0,return_inverse=True)
    n=np.bincount(inverse)
    points=np.column_stack([np.bincount(inverse,weights=a)/n for a in points.T])
    colors=np.column_stack([np.bincount(inverse,weights=a)/n for a in colors.T])
    if cleanup and len(points)>20:
        distance,_=cKDTree(points).query(points,k=min(9,len(points)))
        mean=distance[:,1:].mean(axis=1)
        keep=mean<=mean.mean()+2*mean.std()
        points,colors=points[keep],colors[keep]
    if len(points)>300000:
        ids=np.linspace(0,len(points)-1,300000,dtype=int);points,colors=points[ids],colors[ids]
    if len(points)<100: raise ValueError("Too few points remain after noise filtering.")
    report=dict(input_views=count,merged_points=len(points),candidate_points=before,
                confidence_percentile=percentile,confidence_threshold=threshold,
                cross_view_consistency=consistency,voxel_size=voxel,units="arbitrary model units",per_view=per_view)
    return points,colors,report


def export_cloud(root,points,colors):
    import json
    write_ply(Path(root)/"merged.ply",points,colors)
    (Path(root)/"preview.json").write_text(json.dumps(dict(points=sample_cloud(points,colors,6000),
                                                         total=len(points))),encoding="utf-8")
