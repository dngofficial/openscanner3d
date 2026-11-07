import assert from 'node:assert/strict';
import test from 'node:test';
import { preferredCamera, cameraConstraints, cameraError, frameSize } from '../frontend/camera.mjs';

test('Camo is preferred for either phone; explicit selection is preserved', () => {
  const devices = [{deviceId: 'built-in', label: 'Laptop'}, {deviceId: 'phone', label: 'Camo Camera'}];
  assert.equal(preferredCamera(devices).deviceId, 'phone');
  assert.equal(preferredCamera(devices, 'built-in').deviceId, 'built-in');
  assert.equal(preferredCamera([{deviceId: 'hidden', label: ''}]), null);
});
test('desktop device selection and phone rear-camera preference are separate', () => {
  assert.deepEqual(cameraConstraints('camo').video.deviceId, {exact: 'camo'});
  assert.equal(cameraConstraints('camo').video.facingMode, undefined);
  assert.deepEqual(cameraConstraints().video.facingMode, {ideal: 'environment'});
});
test('portrait and landscape frames are bounded without stretching or upscaling', () => {
  assert.deepEqual(frameSize(3840, 2160), [1280, 720]);
  assert.deepEqual(frameSize(2160, 3840), [720, 1280]);
  assert.deepEqual(frameSize(640, 480), [640, 480]);
  assert.throws(() => frameSize(0, 0));
});
test('camera failure messages explain permission, Camo, and secure origin issues', () => {
  assert.match(cameraError({name: 'NotReadableError'}), /Camo Studio/);
  assert.match(cameraError({name: 'NotAllowedError'}), /permission/);
  assert.match(cameraError({}, false), /trusted HTTPS/);
});
