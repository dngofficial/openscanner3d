// Angle guidance from the phone's motion sensors. DOM-free so it can be tested in Node.
// Azimuth: degrees the phone's heading has turned since the scan started; positive = the user walked
// left around the object while facing it. Pitch: degrees the camera looks below the horizon.

const RAD = Math.PI / 180;

export const wrap180 = degrees => ((((degrees + 180) % 360) + 360) % 360) - 180;

// W3C DeviceOrientation angles -> direction of the rear camera (device -Z) in the earth frame.
export function cameraDirection(alpha, beta, gamma) {
  const a = alpha * RAD, b = beta * RAD, g = gamma * RAD;
  const x = -Math.sin(g) * Math.cos(a) - Math.cos(g) * Math.sin(b) * Math.sin(a);
  const y = -Math.sin(g) * Math.sin(a) + Math.cos(g) * Math.sin(b) * Math.cos(a);
  const z = -Math.cos(b) * Math.cos(g);
  let heading = Math.atan2(x, y);
  if (Math.hypot(x, y) < 0.1) {
    // Looking almost straight down: use where the top of the phone points instead.
    heading = Math.atan2(-Math.cos(b) * Math.sin(a), Math.cos(b) * Math.cos(a));
  }
  return { heading: ((heading / RAD) + 360) % 360, pitch: -Math.asin(Math.max(-1, Math.min(1, z))) / RAD };
}

export function ringOf(pitch, plan) {
  const index = plan.rings.findIndex(ring => pitch >= ring.min && pitch < ring.max);
  return index < 0 ? null : index;
}

export function sectorOf(azimuth, plan) {
  const width = 360 / plan.sectors;
  return Math.floor(((((azimuth % 360) + 360) % 360) + width / 2) / width) % plan.sectors;
}

const sectorCentre = (sector, plan) => wrap180(sector * 360 / plan.sectors);

// A further photo of a side only counts if it is a new view, not the same shot again.
export function isNewView(here, shots, plan) {
  return shots.every(shot => Math.abs(wrap180(here.azimuth - shot.azimuth)) >= plan.min_separation
    || Math.abs(here.pitch - shot.pitch) >= plan.min_separation);
}

const HEIGHT_WORDS = ['low (phone at object height)', 'middle (phone above, looking down)', 'high (looking almost straight down)'];

// What the user should do next. counts[ring][sector] come from the server; shots maps "ring:sector"
// to the poses already captured there.
export function nextStep(here, counts, shots, plan) {
  const missing = [];
  plan.rings.forEach((ring, r) => counts[r].forEach((count, s) => { if (count < ring.target) missing.push([r, s]); }));
  if (!missing.length) return { kind: 'done', text: 'All angles covered. Tap Finish.', arrow: null };
  if (!here) return { kind: 'manual', text: 'Motion sensors unavailable. Use the shutter button and cover every side.', arrow: null };
  const ring = ringOf(here.pitch, plan), sector = sectorOf(here.azimuth, plan);
  if (ring === null) {
    return here.pitch < plan.rings[0].min
      ? { kind: 'aim', text: 'Tilt the phone down toward the object.', arrow: 'down' }
      : { kind: 'aim', text: 'Tilt the phone up a little.', arrow: 'up' };
  }
  const here_shots = shots[`${ring}:${sector}`] || [];
  if (counts[ring][sector] < plan.rings[ring].target) {
    if (isNewView(here, here_shots, plan)) return { kind: 'hold', text: 'Hold still. Capturing this angle.', arrow: null, ring, sector };
    return { kind: 'aim', text: 'Shift a small step sideways for a second view of this side.', arrow: 'sideways', ring, sector };
  }
  // Nearest missing angle: walking around the same height is cheaper than changing height.
  const distance = ([r, s]) => {
    const steps = Math.abs(wrap180((s - sector) * 360 / plan.sectors)) / (360 / plan.sectors);
    return steps + 2.5 * Math.abs(r - ring);
  };
  const [targetRing, targetSector] = missing.reduce((best, candidate) => distance(candidate) < distance(best) ? candidate : best);
  const turn = wrap180(sectorCentre(targetSector, plan) - here.azimuth);
  const target = { ring: targetRing, sector: targetSector };
  if (targetSector !== sector && Math.abs(turn) >= 360 / plan.sectors / 2) {
    const degrees = Math.max(10, Math.round(Math.abs(turn) / 5) * 5);
    const side = turn > 0 ? 'left' : 'right';
    const then = targetRing !== ring ? ` Then go ${HEIGHT_WORDS[targetRing].split(' ')[0]}.` : '';
    return { kind: 'move', text: `Walk ${side} about ${degrees}° around the object, keeping it centred.${then}`, arrow: side, degrees, target };
  }
  return targetRing > ring
    ? { kind: 'move', text: `Raise the phone and look down at the object: ${HEIGHT_WORDS[targetRing]}.`, arrow: 'up', target }
    : { kind: 'move', text: `Lower the phone: ${HEIGHT_WORDS[targetRing]}.`, arrow: 'down', target };
}

// Rotation speed (deg/s) samples from devicemotion over the last moments -> steady enough to shoot.
export function isSteady(samples, now, windowMs = 400, maxRate = 25) {
  const recent = samples.filter(sample => now - sample.t <= windowMs);
  return recent.length > 0 && recent.every(sample => sample.rate <= maxRate);
}

// Quick exposure check on a small grey preview (Uint8 luminance values), matching the server's rules.
export function exposureProblem(luma) {
  let white = 0, black = 0, total = 0;
  for (const value of luma) { total += value; if (value >= 250) white++; if (value <= 5) black++; }
  const n = luma.length || 1;
  if (white / n > 0.35) return 'Too bright: move away from glare or bright light.';
  if (black / n > 0.35 || total / n < 20) return 'Too dark: add light.';
  return null;
}

// Top-down checklist dial: object in the centre (room for a progress label), outer ring = low angle, inner = high.
// Sector 0 (where the scan started) is at the bottom; walking left moves you clockwise on screen.
export function drawCoverage(ctx, size, plan, counts, here = null, target = null) {
  const c = size / 2, outer = size * 0.46, band = outer / (plan.rings.length + 1.5), width = 2 * Math.PI / plan.sectors;
  ctx.clearRect(0, 0, size, size);
  plan.rings.forEach((ring, r) => counts[r].forEach((count, s) => {
    const start = (s * 360 / plan.sectors + 90) * RAD - width / 2;
    ctx.beginPath();
    ctx.arc(c, c, outer - r * band, start, start + width);
    ctx.arc(c, c, outer - (r + 1) * band, start + width, start, true);
    ctx.closePath();
    ctx.fillStyle = count >= ring.target ? '#3fb8a0' : count > 0 ? '#d9a13a' : '#3a3f46';
    ctx.fill();
    ctx.lineWidth = target && target.ring === r && target.sector === s ? 4 : 1.5;
    ctx.strokeStyle = target && target.ring === r && target.sector === s ? '#ffffff' : '#15181c';
    ctx.stroke();
  }));
  if (here) {
    const ring = ringOf(here.pitch, plan), radius = ring === null ? outer + 6 : outer - (ring + 0.5) * band;
    const angle = (here.azimuth + 90) * RAD;
    ctx.beginPath();
    ctx.arc(c + radius * Math.cos(angle), c + radius * Math.sin(angle), Math.max(5, size / 30), 0, 2 * Math.PI);
    ctx.fillStyle = '#ffffff'; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = '#000'; ctx.stroke();
  }
}
