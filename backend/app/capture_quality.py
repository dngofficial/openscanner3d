"""Conservative scene-continuity heuristics, not object coverage estimation."""
import cv2
import numpy as np


def continuity(previous_xy, previous_descriptors, xy, descriptors, width, height):
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(previous_descriptors, descriptors, k=2)
    matches = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
    # Repeated textures must not create many correspondences to one feature.
    unique = {}
    for match in sorted(matches, key=lambda item: item.distance):
        unique.setdefault(match.trainIdx, match)
    matches = list(unique.values())
    if len(matches) < 30:
        return False, 0.0, "Weak scene continuity. Move back toward the last saved view."
    before = np.float32([previous_xy[m.queryIdx] for m in matches])
    after = np.float32([xy[m.trainIdx] for m in matches])
    _, mask = cv2.findHomography(before, after, cv2.RANSAC, 3.0)
    if mask is None:
        return False, 0.0, "Could not verify scene continuity. Move back toward the last saved view."
    inliers = mask.ravel().astype(bool)
    if inliers.sum() < 25 or inliers.mean() < .4:
        return False, 0.0, "Inconsistent feature matches. Slow down and return toward the last saved view."
    # Reject support confined to a watermark or tiny textured patch.
    for points in (before[inliers], after[inliers]):
        span = np.ptp(points, axis=0) / [width, height]
        if min(span) < .2 or np.prod(span) < .12:
            return False, 0.0, "Matching features cover too little of the scene. Keep more textured surroundings visible."
    motion = float(np.median(np.linalg.norm(after[inliers] - before[inliers], axis=1)))
    if motion > .18 * min(width, height):
        return False, motion, "View changed too far. Move back toward the last saved view and take smaller steps."
    return True, motion, "Scene continuity check passed (not object-specific overlap)."
