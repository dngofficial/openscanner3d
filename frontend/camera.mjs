export function preferredCamera(devices, currentId = '') {
  return devices.find(device => device.deviceId === currentId)
    || devices.find(device => /camo/i.test(device.label))
    || null;
}

export function cameraConstraints(deviceId) {
  return { audio: false, video: {
    ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }),
    width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 },
  } };
}

export function frameSize(width, height, maxEdge = 1280) {
  if (!(width > 0 && height > 0)) throw new Error('Camera has not delivered a frame yet.');
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return [Math.max(1, Math.round(width * scale)), Math.max(1, Math.round(height * scale))];
}

export function cameraError(error, secure = true) {
  if (!secure) return 'Live camera needs localhost on the laptop or trusted HTTPS on the phone. You can still import photos.';
  if (error.name === 'NotAllowedError') return 'Camera permission was denied. Allow camera access for this site, then press Start camera.';
  if (error.name === 'NotReadableError') return 'Camera is busy or unavailable. Check the preview in Camo Studio and close other apps using the camera.';
  if (error.name === 'NotFoundError') return 'No camera found. Connect Camo Camera to Camo Studio, then press Refresh cameras.';
  return 'Camera unavailable: ' + error.message + '. You can import photos instead.';
}
