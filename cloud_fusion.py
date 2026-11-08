"""Calibrate per-view inverse depth, transform by registered camera poses, voxel fuse."""
import json
from pathlib import Path

import cv2
import numpy as np

from depth_cloud import sample_cloud, write_ply
from reconstruction import Cancelled


def rotation(q):
    w, x, y, z = np.asarray(q, float) / np.linalg.norm(q)
    return np.array([[1-2*y*y-2*z*z, 2*x*y-2*w*z, 2*x*z+2*w*y],
                     [2*x*y+2*w*z, 1-2*x*x-2*z*z, 2*y*z-2*w*x],
                     [2*x*z-2*w*y, 2*y*z+2*w*x, 1-2*x*x-2*y*y]])


def read_model(folder):
    cameras, views, landmarks = {}, {}, {}
    with (folder / "cameras.txt").open() as f:
        for line in f:
            if line.startswith("#") or not line.strip(): continue
            fields = line.split()
            cameras[int(fields[0])] = (fields[1], int(fields[2]), int(fields[3]), np.array(fields[4:], float))
    with (folder / "points3D.txt").open() as f:
        for line in f:
            if line.startswith("#") or not line.strip(): continue
            fields = line.split()
            landmarks[int(fields[0])] = np.array(fields[1:4], float)
    with (folder / "images.txt").open() as f:
        while True:
            line = f.readline()
            if not line: break
            if line.startswith("#") or not line.strip(): continue
            fields = line.strip().split(maxsplit=9)
            if len(fields) != 10: raise ValueError("Invalid COLMAP image pose record.")
            observations = f.readline().split()
            if len(observations) % 3: raise ValueError("Invalid COLMAP observations.")
            xy, ids = [], []
            for i in range(0, len(observations), 3):
                point_id = int(observations[i+2])
                if point_id in landmarks:
                    xy.append([float(observations[i]), float(observations[i+1])]); ids.append(point_id)
            views[fields[9]] = dict(R=rotation(np.array(fields[1:5], float)), t=np.array(fields[5:8], float),
                                    camera=cameras[int(fields[8])], xy=np.array(xy).reshape(-1, 2), ids=ids)
    return views, landmarks


def fit_inverse_depth(predicted, z):
    predicted, z = np.asarray(predicted), np.asarray(z)
    valid = np.isfinite(predicted) & np.isfinite(z) & (z > 0)
    x, target = predicted[valid], 1 / z[valid]
    if len(x) < 25 or np.std(x) < 1e-6:
        raise ValueError("Insufficient well-distributed depth anchors (need 25).")
    threshold = max(float(np.median(target)) * .08, 1e-8)
    best = np.zeros(len(x), bool)
    rng = np.random.default_rng(17)
    for _ in range(150):
        i, j = rng.choice(len(x), 2, replace=False)
        if abs(x[j]-x[i]) < 1e-7: continue
        a = (target[j]-target[i])/(x[j]-x[i]); b = target[i]-a*x[i]
        if a <= 0: continue
        inliers = np.abs(a*x+b-target) < threshold
        if inliers.sum() > best.sum(): best = inliers
    if best.sum() < max(20, int(.45*len(x))):
        raise ValueError("Predicted depth does not agree with the registered geometry.")
    a, b = np.linalg.lstsq(np.column_stack((x[best], np.ones(best.sum()))), target[best], rcond=None)[0]
    fitted = a*x[best]+b
    relative_error = float(np.median(np.abs(fitted-target[best]) / target[best]))
    if a <= 0 or np.any(fitted <= 0) or relative_error > .2:
        raise ValueError("Depth scale calibration was unstable.")
    return float(a), float(b), dict(anchors=int(len(x)), inliers=int(best.sum()),
                                   median_inverse_depth_relative_error=relative_error)


def camera_matrices(camera):
    model, width, height, params = camera
    if model == "SIMPLE_RADIAL":
        f, cx, cy, k = params
        K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], float)
        distortion = np.array([k, 0, 0, 0, 0], float)
    elif model == "SIMPLE_PINHOLE":
        f, cx, cy = params
        K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], float); distortion = np.zeros(5)
    elif model == "PINHOLE":
        fx, fy, cx, cy = params
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], float); distortion = np.zeros(5)
    else:
        raise ValueError(f"Unsupported fusion camera model {model}. Use SIMPLE_RADIAL.")
    return K, distortion, width, height


def align_frame(depth, rgb, view, landmarks, step=4, mask=None, restrict_anchors=False):
    K, distortion, width, height = camera_matrices(view["camera"])
    dh,dw=depth.shape
    if rgb.shape[:2] != depth.shape:
        raise ValueError("Saved depth and cloud color dimensions do not match.")
    if abs(width/height-dw/dh)>.01:
        raise ValueError("Tracking and depth image aspect ratios do not match.")
    sx,sy=dw/width,dh/height
    K=K.copy();K[0]*=sx;K[1]*=sy
    xy=view["xy"]*np.array([sx,sy])
    width,height=dw,dh
    if len(xy) < 25: raise ValueError("Not enough registered feature tracks.")
    anchor_xyz = np.array([landmarks[i] for i in view["ids"]])
    cam = anchor_xyz @ view["R"].T + view["t"]
    predictions = cv2.remap(depth, (xy[:, 0]-.5).astype(np.float32).reshape(-1,1),
                           (xy[:, 1]-.5).astype(np.float32).reshape(-1,1),
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()
    inside = (xy[:, 0] >= .5) & (xy[:, 0] < width-.5) & (xy[:, 1] >= .5) & (xy[:, 1] < height-.5)
    valid = inside & np.isfinite(cam).all(axis=1) & (cam[:, 2] > 0)
    if restrict_anchors and mask is not None:
        anchor_mask=cv2.remap(mask,(xy[:,0]-.5).astype(np.float32).reshape(-1,1),
                             (xy[:,1]-.5).astype(np.float32).reshape(-1,1),cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT).ravel()
        valid &= anchor_mask>0
    a, b, metrics = fit_inverse_depth(predictions[valid], cam[valid, 2])
    inverse = a*depth+b
    yy, xx = np.mgrid[2:height-2:step, 2:width-2:step]
    values = inverse[yy, xx].ravel()
    z = np.divide(1., values, out=np.zeros_like(values), where=values>1e-9)
    low, high = np.percentile(cam[valid, 2], [5, 95])
    good = (z > .5*low) & (z < 2*high) & np.isfinite(z)
    # Reject strong depth discontinuities that otherwise stretch across boundaries.
    gradient = np.maximum(np.abs(cv2.Sobel(depth, cv2.CV_32F, 1, 0)),
                          np.abs(cv2.Sobel(depth, cv2.CV_32F, 0, 1)))
    spread = max(float(np.percentile(depth, 95)-np.percentile(depth, 5)), 1e-6)
    good &= gradient[yy, xx].ravel() < .35*spread
    if mask is not None: good &= mask[yy,xx].ravel()>0
    pixels = np.column_stack((xx.ravel()+.5, yy.ravel()+.5)).astype(float)
    rays = cv2.undistortPoints(pixels.reshape(-1,1,2), K, distortion).reshape(-1,2)
    local = np.column_stack((rays, np.ones(len(rays)))) * z[:, None]
    # COLMAP R,t are world-to-camera; invert them for each point.
    world = (local[good]-view["t"]) @ view["R"]
    colors = rgb[yy, xx].reshape(-1,3)[good]
    metrics.update(scale=a, offset=b, generated_points=int(len(world)))
    return world, colors, metrics


def merge_live_clouds(root, model, cancel, update, max_voxels=500_000):
    root, model = Path(root), Path(model)
    views, landmarks = read_model(model)
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    xyz = np.array(list(landmarks.values()))
    span = float(np.max(np.percentile(xyz, 95, axis=0)-np.percentile(xyz, 5, axis=0)))
    voxel = max(span / 150, 1e-6)
    voxels, used, skipped, ignored = {}, [], [], 0
    photos = metadata["photos"]
    for i, photo in enumerate(photos):
        if cancel.is_set(): raise Cancelled()
        name = photo["id"] + ".jpg"
        update(stage="merge", progress=60+int(35*(i+1)/len(photos)),
               message=f"Aligning and merging cloud {i+1} / {len(photos)}…")
        file = root / "depth" / (photo["id"] + ".npz")
        try:
            if name not in views: raise ValueError("Camera pose did not register.")
            if not file.is_file(): raise ValueError("No saved depth map for this frame.")
            with np.load(file, allow_pickle=False) as data: depth = data["inverse_depth"].astype(np.float32)
            bgr = cv2.imread(str(root / "images" / name))
            if bgr is None: raise ValueError("Saved RGB frame is unavailable.")
            mask_path=root/"masks"/(name+".png")
            mask=cv2.imread(str(mask_path),cv2.IMREAD_GRAYSCALE) if mask_path.is_file() else None
            if bgr.shape[:2]!=depth.shape:
                bgr=cv2.resize(bgr,(depth.shape[1],depth.shape[0]),interpolation=cv2.INTER_AREA)
            world, rgb, metrics = align_frame(depth,bgr[:,:,::-1],views[name],landmarks,mask=mask,
                                             restrict_anchors=not photo.get("tracking_context",False))
            if len(world) < 100: raise ValueError("Too few reliable dense points after filtering.")
            keys = np.floor(world/voxel).astype(np.int64)
            keys, inverse = np.unique(keys, axis=0, return_inverse=True)
            counts = np.bincount(inverse)
            sums = np.column_stack([np.bincount(inverse, weights=values) for values in np.column_stack((world,rgb)).T])
            for key, total, count in zip(map(tuple, keys), sums, counts):
                if key in voxels: voxels[key] += np.r_[total, count, 1]
                elif len(voxels) < max_voxels: voxels[key] = np.r_[total, count, 1]
                else: ignored += int(count)
            used.append(dict(id=photo["id"], **metrics))
        except ValueError as error:
            skipped.append(dict(id=photo["id"], reason=str(error)))
    report = dict(accepted_clouds=len(used), skipped_clouds=len(skipped), skipped=skipped, calibrations=used)
    (root / "alignment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if len(used) < 2:
        raise RuntimeError("Fewer than two clouds could be aligned reliably. See alignment.json; capture more overlapping textured views.")
    merged = np.array(list(voxels.values()))
    before_filter=len(merged)
    merged=merged[merged[:,7]>=2]
    if len(merged)<100:
        raise RuntimeError("Too few points are supported by multiple views. Try slower capture with more overlap and a tighter foreground mask.")
    points = merged[:, :3] / merged[:, 6:7]
    colors = merged[:, 3:6] / merged[:, 6:7]
    from scipy.spatial import cKDTree
    distances,_=cKDTree(points).query(points,k=min(9,len(points)))
    average=distances[:,1:].mean(axis=1)
    threshold=float(average.mean()+2*average.std())
    clean=average<=threshold
    points,colors=points[clean],colors[clean]
    write_ply(root / "merged.ply", points, colors)
    (root / "merged_preview.json").write_text(json.dumps(dict(
        points=sample_cloud(points, colors, 6000), total=int(len(points)))), encoding="utf-8")
    report = dict(accepted_clouds=len(used), skipped_clouds=len(skipped), skipped=skipped,
                  merged_points=int(len(points)), voxel_size=voxel, units="arbitrary SfM units",
                  ignored_points_at_voxel_limit=ignored, filtered_voxels=before_filter-len(points), calibrations=used)
    (root / "alignment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
