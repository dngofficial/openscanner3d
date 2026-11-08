"""Per-photo quality checks; each rejection names one cause in plain words (measured values are stored with the photo)."""
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
        return "Too bright: angle the phone away from glare or bright light."
    if values["black"] > MAX_CLIPPED:
        return "Too dark: turn on more light."
    if values["brightness"] < MIN_MEAN:
        return "Too dark: turn on more light."
    if values["sharpness"] < MIN_SHARPNESS:
        return "Blurry: hold the phone still for a second."
    if values["contrast"] < MIN_CONTRAST:
        return "Too flat: add light from the side, or put the object on a less plain surface."
    return None
