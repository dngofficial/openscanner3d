import assert from 'node:assert/strict';
import test from 'node:test';
import { cameraDirection, wrap180, ringOf, sectorOf, nextStep, isNewView, isSteady, exposureProblem } from '../frontend/guidance.mjs';

const plan = { sectors: 12, min_separation: 10, rings: [
  { name: 'low', min: -15, max: 25, target: 2 }, { name: 'mid', min: 25, max: 50, target: 2 }, { name: 'high', min: 50, max: 86, target: 1 }] };
const empty = () => plan.rings.map(() => new Array(12).fill(0));
const close = (actual, expected, tolerance = 0.5) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} vs ${expected}`);

test('rear camera direction from W3C orientation angles', () => {
  // Phone upright facing the user: the camera looks horizontally along the heading.
  close(cameraDirection(0, 90, 0).pitch, 0);
  close(cameraDirection(0, 90, 0).heading, 0);
  // Lying flat, screen up: the camera looks straight down.
  close(cameraDirection(0, 0, 0).pitch, 90);
  // Tilted 60° from flat: looking 30° below the horizon.
  close(cameraDirection(0, 60, 0).pitch, 30);
  // Turning the phone 90° counter-clockwise (alpha +90) turns the view to heading 270 (west).
  close(cameraDirection(90, 90, 0).heading, 270);
  // Holding it in landscape (gamma 90) must not change where the camera points.
  close(cameraDirection(0, 90, 90).pitch, 0, 1);
});

test('angle bins', () => {
  assert.equal(wrap180(190), -170);
  assert.deepEqual([ringOf(0, plan), ringOf(30, plan), ringOf(70, plan), ringOf(-40, plan), ringOf(89, plan)], [0, 1, 2, null, null]);
  assert.deepEqual([sectorOf(0, plan), sectorOf(14, plan), sectorOf(16, plan), sectorOf(-16, plan), sectorOf(350, plan)], [0, 0, 1, 11, 0]);
});

test('hold at a missing angle, then walk toward the nearest gap on the same height', () => {
  const counts = empty();
  assert.equal(nextStep({ azimuth: 0, pitch: 10 }, counts, {}, plan).kind, 'hold');
  counts[0][0] = 2;
  counts[0][1] = 2; counts[0][2] = 2;           // Gaps remain on both sides; the right side is closer.
  const step = nextStep({ azimuth: 0, pitch: 10 }, counts, {}, plan);
  assert.equal(step.arrow, 'right');
  assert.deepEqual(step.target, { ring: 0, sector: 11 });
  assert.match(step.text, /Walk right about 30°/);
});

test('changing height once the current ring is complete', () => {
  const counts = empty();
  counts[0].fill(2);
  const step = nextStep({ azimuth: 0, pitch: 10 }, counts, {}, plan);
  assert.equal(step.arrow, 'up');
  assert.deepEqual(step.target, { ring: 1, sector: 0 });
  plan.rings.forEach((ring, r) => counts[r].fill(ring.target));
  assert.equal(nextStep({ azimuth: 0, pitch: 10 }, counts, {}, plan).kind, 'done');
});

test('a second photo of a side needs a slightly different view', () => {
  const counts = empty(); counts[0][0] = 1;
  const shots = { '0:0': [{ azimuth: 2, pitch: 10 }] };
  assert.equal(nextStep({ azimuth: 4, pitch: 12 }, counts, shots, plan).arrow, 'sideways');
  assert.equal(nextStep({ azimuth: -9, pitch: 10 }, counts, shots, plan).kind, 'hold');
  assert.equal(isNewView({ azimuth: 2, pitch: 22 }, shots['0:0'], plan), true);
});

test('aiming, manual fallback, steadiness and exposure', () => {
  assert.equal(nextStep({ azimuth: 0, pitch: -40 }, empty(), {}, plan).arrow, 'down');
  assert.equal(nextStep(null, empty(), {}, plan).kind, 'manual');
  assert.equal(isSteady([{ t: 900, rate: 5 }, { t: 1000, rate: 10 }], 1000), true);
  assert.equal(isSteady([{ t: 900, rate: 40 }, { t: 1000, rate: 10 }], 1000), false);
  assert.equal(isSteady([], 1000), false);
  assert.match(exposureProblem(new Uint8Array(100).fill(255)), /bright/);
  assert.match(exposureProblem(new Uint8Array(100).fill(3)), /dark/);
  assert.equal(exposureProblem(new Uint8Array(100).fill(120)), null);
});
