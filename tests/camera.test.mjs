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

import { promptPoint, segmentRequest } from '../frontend/selection.mjs';

test('Object taps become clamped image fractions with object/not-object labels', () => {
  assert.deepEqual(promptPoint(240, 135, 480, 270), [0.5, 0.5, 1]);
  assert.deepEqual(promptPoint(-5, 300, 480, 270, true), [0, 1, 0]);
});

test('Segmentation requests keep frame order and ignore deselected or negative-only prompts', () => {
  const order = ['kf_0000.jpg', 'kf_0001.jpg', 'kf_0002.jpg'];
  const prompts = new Map([['kf_0002.jpg', [[0.5, 0.5, 1]]], ['kf_0001.jpg', [[0.1, 0.1, 1]]]]);
  const request = segmentRequest(order, new Set(['kf_0002.jpg', 'kf_0000.jpg']), prompts);
  assert.deepEqual(request, {type: 'segment', selected: ['kf_0000.jpg', 'kf_0002.jpg'],
    prompts: [{frame: 'kf_0002.jpg', points: [[0.5, 0.5, 1]]}]});
  assert.equal(segmentRequest(order, new Set(order), new Map([['kf_0000.jpg', [[0.2, 0.2, 0]]]])), null);
});
