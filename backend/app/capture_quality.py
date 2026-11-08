"""Conservative scene-continuity heuristics, not object coverage estimation."""
import cv2
import numpy as np

MIN_SHARPNESS, MIN_CONTRAST, MAX_CLIPPED, MIN_MEAN = 25, 12, .35, 20


def exposure_problem(gray):
    """Judge exposure by clipped pixels, not the mean: a white table or dark object can still be well exposed."""
    bright, dark, mean = (gray >= 250).mean(), (gray <= 5).mean(), gray.mean()
    if bright > MAX_CLIPPED:
        return f"Overexposed: {bright:.0%} of the frame is pure white. Lower exposure or angle away from glare."
    if dark > MAX_CLIPPED:
        return f"Underexposed: {dark:.0%} of the frame is pure black. Add light or raise exposure."
    if mean < MIN_MEAN:
        return f"Too dark (average brightness {mean:.0f}/255). Add light or raise exposure."
    return None


def frame_problem(gray, sharpness, contrast):
    """Return the single most relevant reason to reject a frame, or None."""
    exposure = exposure_problem(gray)
    if exposure:
        return exposure
    if sharpness < MIN_SHARPNESS:
        return f"Blurry (sharpness {sharpness:.0f}, need {MIN_SHARPNESS}). Hold steady or wait for focus."
    if contrast < MIN_CONTRAST:
        return f"Low contrast ({contrast:.0f}, need {MIN_CONTRAST}). Add side lighting or a textured surface."
    return None


# Measured on the reviewed 77-frame Camo set: these accept 93/80/59% of real saved-frame pairs 2/3/4 steps
# apart (the homography check accepted 69/38/27%) and 0 of 150 mirrored, wrong-scene pairs.
MATCH_RATIO, MIN_MATCHES, MIN_INLIERS, MIN_INLIER_FRACTION = .8, 20, 18, .45
MIN_SPAN, MIN_AREA, MAX_MOTION = .15, .08, .35


def continuity(previous_xy, previous_descriptors, xy, descriptors, width, height):
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(previous_descriptors, descriptors, k=2)
    matches = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < MATCH_RATIO * pair[1].distance]
    # Keep mutual best matches only; repeated textures must not create many correspondences to one feature.
    backward = {pair[0].queryIdx: pair[0].trainIdx for pair in matcher.knnMatch(descriptors, previous_descriptors, k=1) if pair}
    matches = [match for match in matches if backward.get(match.trainIdx) == match.queryIdx]
    if len(matches) < MIN_MATCHES:
        return False, 0.0, f"Weak scene continuity ({len(matches)}/{MIN_MATCHES} matches). Move back toward the last saved view."
    before = np.float32([previous_xy[m.queryIdx] for m in matches])
    after = np.float32([xy[m.trainIdx] for m in matches])
    # Epipolar geometry holds for any static 3D scene; a homography only fits flat scenes, so it
    # rejected genuine views of a 3D object with parallax.
    _, mask = cv2.findFundamentalMat(before, after, cv2.USAC_MAGSAC, 1.5, .999)
    if mask is None:
        return False, 0.0, "Could not verify scene continuity. Move back toward the last saved view."
    inliers = mask.ravel().astype(bool)
    if inliers.sum() < MIN_INLIERS or inliers.mean() < MIN_INLIER_FRACTION:
        return False, 0.0, (f"Inconsistent feature matches ({inliers.sum()} consistent of {len(matches)}). "
                            "Slow down and return toward the last saved view.")
    # Reject support confined to a watermark or tiny textured patch.
    for points in (before[inliers], after[inliers]):
        span = np.ptp(points, axis=0) / [width, height]
        if min(span) < MIN_SPAN or np.prod(span) < MIN_AREA:
            return False, 0.0, "Matching features cover too little of the scene. Keep more textured surroundings visible."
    motion = float(np.median(np.linalg.norm(after[inliers] - before[inliers], axis=1)))
    if motion > MAX_MOTION * min(width, height):
        return False, motion, "View changed too far. Move back toward the last saved view and take smaller steps."
    return True, motion, "Scene continuity check passed (not object-specific overlap)."
