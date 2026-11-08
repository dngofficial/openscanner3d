"""Per-photo quality checks; each rejection names one cause with its measured value."""
import cv2

ANALYSIS_WIDTH = 960                 # Measure sharpness at a fixed scale so thresholds mean the same for any camera.
MIN_SHARPNESS, MIN_CONTRAST, MAX_CLIPPED, MIN_MEAN = 25, 12, .35, 20


def measurements(image) -> dict:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(cv2.resize(image, (ANALYSIS_WIDTH, max(1, round(ANALYSIS_WIDTH * height / width)))), cv2.COLOR_BGR2GRAY)
    return dict(sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()), brightness=float(gray.mean()),
                contrast=float(gray.std()), white=float((gray >= 250).mean()), black=float((gray <= 5).mean()))


def problem(values: dict) -> str | None:
    """Exposure first (judged by clipped pixels, not the mean), then sharpness, then contrast."""
    if values["white"] > MAX_CLIPPED:
        return f"Overexposed: {values['white']:.0%} of the photo is pure white. Lower exposure or angle away from glare."
    if values["black"] > MAX_CLIPPED:
        return f"Underexposed: {values['black']:.0%} of the photo is pure black. Add light."
    if values["brightness"] < MIN_MEAN:
        return f"Too dark (average brightness {values['brightness']:.0f}/255). Add light."
    if values["sharpness"] < MIN_SHARPNESS:
        return f"Blurry (sharpness {values['sharpness']:.0f}, need {MIN_SHARPNESS}). Hold still for a moment."
    if values["contrast"] < MIN_CONTRAST:
        return f"Low contrast ({values['contrast']:.0f}, need {MIN_CONTRAST}). Add side lighting or a textured surface."
    return None
