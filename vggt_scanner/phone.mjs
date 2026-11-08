import { cameraDirection, wrap180, nextStep, isSteady, exposureProblem, drawCoverage } from '/guidance.mjs';
const $ = id => document.getElementById(id), video = $('video');
const STORE = 'vggt-phone-scan';
const linked = new URLSearchParams(location.search).get('scan');
const probe = document.createElement('canvas'); probe.width = 48; probe.height = 36;
const probeContext = probe.getContext('2d', { willReadFrequently: true });
let plan, scan = null, counts, shots = {}, orientation = null, heading0 = null, motion = [];
let stream = null, inFlight = false, uploading = false, finishing = false, running = false;
let lastCapture = 0, holdSince = null, sticky = null, audio = null, wakeLock = null, loop = null;
let autoCapture = true, soundOn = true, exposure = null, ticks = 0;
let permissionGeneration = 0;
function say(text, level = 'warn') { $('instruction').textContent = text; $('instruction').className = level; }
function alert(text, level = 'bad', ms = 2600) { sticky = { text, level, until: performance.now() + ms }; say(text, level); }
async function api(path, options = {}) {
  const response = await fetch(path, options), body = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  return body;
}
const base = () => `/api/scans/${scan.id}`;
function restore(result) {
  scan = result; counts = result.coverage.counts; shots = {};
  for (const photo of scan.photos) if (photo.ring != null) {
    (shots[`${photo.ring}:${photo.sector}`] ||= []).push({ azimuth: photo.azimuth, pitch: photo.pitch });
  }
  localStorage.setItem(STORE, scan.id); showProgress();
  const photo = scan.photos.at(-1);
  $('lastShot').disabled = !photo || inFlight;
  $('lastShot').style.background = photo ? `#000 center / cover url("${base()}/thumbs/${photo.id}")` : '#000';
}
function showProgress() {
  if (!scan || !plan) return;
  const { have, wanted } = scan.coverage;
  $('percent').textContent = `${Math.round(100 * have / wanted)}%`;
  $('rings').textContent = plan.rings.map((r, i) => `${r.name} ${counts[i].filter(c => c >= r.target).length}/${plan.sectors}`).join(' · ');
  $('finish').disabled = scan.photos.length < 3 || inFlight || finishing;
  $('finish').textContent = `Finish · ${scan.photos.length} photos`;
  $('shutter').disabled = inFlight || finishing;
  $('pause').disabled = inFlight || finishing;
  $('lastShot').disabled = !scan.photos.length || inFlight || finishing;
}
window.addEventListener('deviceorientation', e => {
  if (![e.alpha, e.beta, e.gamma].every(Number.isFinite)) return;
  orientation = { alpha: e.alpha, beta: e.beta, gamma: e.gamma, t: performance.now() };
});
window.addEventListener('devicemotion', e => {
  const r = e.rotationRate, a = e.acceleration, now = performance.now();
  if (!r || ![r.alpha, r.beta, r.gamma].every(Number.isFinite)) return;
  const moving = a && [a.x, a.y, a.z].every(Number.isFinite) ? Math.hypot(a.x, a.y, a.z) : 0;
  motion.push({ t: now, rate: Math.hypot(r.alpha, r.beta, r.gamma), acceleration: moving });
  motion = motion.filter(s => now - s.t < 1000);
});
function headingNow() {
  return orientation ? cameraDirection(orientation.alpha, orientation.beta, orientation.gamma).heading : null;
}
function here() {
  if (!orientation || performance.now() - orientation.t > 1500 || heading0 === null) return null;
  const d = cameraDirection(orientation.alpha, orientation.beta, orientation.gamma);
  return { azimuth: wrap180(d.heading - heading0), pitch: d.pitch };
}
async function permissions() {
  const generation = ++permissionGeneration;
  orientation = null; motion = []; heading0 = null;
  // Invoke both requests in this tap's activation, before awaiting either prompt.
  const requests = [window.DeviceOrientationEvent, window.DeviceMotionEvent].map(Sensor => {
    try { return typeof Sensor?.requestPermission === 'function' ? Sensor.requestPermission() : Promise.resolve('unsupported'); }
    catch { return Promise.resolve('denied'); }
  });
  await Promise.allSettled(requests);
  if (!navigator.mediaDevices?.getUserMedia) throw Error('Live capture needs the trusted HTTPS link from the laptop. Photo upload is available below.');
  const camera = await navigator.mediaDevices.getUserMedia({ audio: false, video: {
    facingMode: { ideal: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 }
  } });
  if (generation !== permissionGeneration) { camera.getTracks().forEach(t => t.stop()); throw Error('Capture paused. Tap Start again.'); }
  stream = camera; video.srcObject = stream; await video.play();
  try { const Audio = window.AudioContext || window.webkitAudioContext; if (Audio) { audio = new Audio(); await audio.resume(); } } catch {}
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch {}
  if (generation !== permissionGeneration) { stop(); throw Error('Capture paused. Tap Start again.'); }
  $('resolution').textContent = `${video.videoWidth} × ${video.videoHeight}`;
}
async function begin(resumeId) {
  if ($('start').disabled) return;
  $('start').disabled = true; $('resume').disabled = true; $('introError').textContent = '';
  try {
    await permissions(); plan ||= await api('/api/plan');
    const result = resumeId ? await api(`/api/scans/${encodeURIComponent(resumeId)}`) : await api('/api/scans', { method: 'POST' });
    scan = result;
    restore(await api(base() + '/phone-mode', { method: 'POST' }));
    if (scan.photos.length && resumeId) {
      $('reference').src = `${base()}/images/${scan.photos[0].id}`;
      $('intro').hidden = true; $('realign').hidden = false;
    } else startScanning();
  } catch (e) {
    stop(); $('introError').textContent = e.name === 'NotAllowedError'
      ? 'Camera access was blocked. Allow camera access in this site’s browser settings, or upload photos below.' : e.message;
  } finally { $('start').disabled = false; $('resume').disabled = false; }
}
function startScanning() {
  heading0 = headingNow(); holdSince = null; running = true;
  $('intro').hidden = true; $('realign').hidden = true; $('hud').hidden = false;
  clearInterval(loop); loop = setInterval(tick, 150); showProgress(); tick();
}
function checkExposure() {
  if (!video.videoWidth) return null;
  probeContext.drawImage(video, 0, 0, 48, 36);
  const pixels = probeContext.getImageData(0, 0, 48, 36).data, luma = new Uint8Array(pixels.length / 4);
  for (let i = 0; i < luma.length; i++) luma[i] = .299 * pixels[4*i] + .587 * pixels[4*i+1] + .114 * pixels[4*i+2];
  return exposureProblem(luma);
}
function chip(id, text, level) { $(id).textContent = text; $(id).className = `chip ${level}`; }
function tick() {
  if (!running) return;
  const now = performance.now();
  if (heading0 === null && orientation) heading0 = headingNow();
  const position = here(), step = nextStep(position, counts, shots, plan);
  if (++ticks % 3 === 0) exposure = checkExposure();
  const recent = motion.filter(s => now - s.t < 400);
  const steady = isSteady(motion, now) && recent.every(s => s.acceleration < 1.5);
  chip('chipLight', exposure ? 'Check light' : 'Light OK', exposure ? 'bad' : 'ok');
  chip('chipSteady', recent.length ? (steady ? 'Steady' : 'Moving') : 'No motion data', steady ? 'ok' : 'warn');
  chip('chipAngles', position ? 'Angles tracked' : 'Manual shutter', position ? 'ok' : 'warn');
  const cues = { left: ['cueLeft'], right: ['cueRight'], up: ['cueUp'], down: ['cueDown'], sideways: ['cueLeft', 'cueRight'] };
  for (const id of ['cueLeft', 'cueRight', 'cueUp', 'cueDown']) $(id).hidden = !(cues[step.arrow] || []).includes(id);
  drawCoverage($('map').getContext('2d'), 380, plan, counts, position, step.target || (step.kind === 'hold' ? step : null));
  const ready = position && step.kind === 'hold' && steady && !exposure && !inFlight;
  if (ready) holdSince ??= now; else holdSince = null;
  if (sticky && now < sticky.until) say(sticky.text, sticky.level);
  else if (inFlight) say('Saving photo…', 'ok');
  else if (exposure) say(exposure, 'bad');
  else if (!position) say('Motion sensors unavailable or paused. Use the shutter and cover every side.');
  else if (!recent.length) say('Motion readings unavailable. Tap the shutter at each angle.');
  else if (step.kind === 'hold') say(autoCapture ? (steady ? 'Hold still…' : 'Stop moving for a moment.') : 'Good angle. Tap the shutter.', steady ? 'ok' : 'warn');
  else say(step.text, step.kind === 'done' ? 'ok' : 'warn');
  if (autoCapture && ready && now - holdSince >= 400 && now - lastCapture > 1000) capture(position);
}
function feedback() {
  $('flash').classList.add('on'); setTimeout(() => $('flash').classList.remove('on'), 70);
  try { navigator.vibrate?.(60); } catch {}
  if (audio && soundOn) try {
    const tone = audio.createOscillator(), gain = audio.createGain(); gain.gain.value = .06;
    tone.frequency.value = 880; tone.connect(gain).connect(audio.destination); tone.start(); tone.stop(audio.currentTime + .09);
  } catch {}
}
async function capture(position) {
  if (!running || inFlight || finishing || video.readyState < 2) return;
  inFlight = true; lastCapture = performance.now(); showProgress();
  try {
    const canvas = document.createElement('canvas');
    // Bound encoded frame memory while preserving aspect ratio and source orientation.
    const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .93));
    if (!blob) throw Error('Camera could not encode this photo.');
    const query = new URLSearchParams({ guided: 'true', name: `Phone angle ${scan.photos.length + 1}` });
    if (position) { query.set('azimuth', position.azimuth.toFixed(1)); query.set('pitch', position.pitch.toFixed(1)); }
    const result = await api(`${base()}/photos?${query}`, { method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob });
    restore(result.scan);
    if (result.added) { feedback(); alert('Photo saved.', 'ok', 800); }
    else alert(result.reason || 'Duplicate photo. Move a little farther for the next view.');
  } catch (e) { alert(`Photo not saved: ${e.message}`, 'bad', 4000); }
  finally { inFlight = false; lastCapture = performance.now(); holdSince = null; showProgress(); }
}
function stop() {
  permissionGeneration++; running = false; clearInterval(loop); holdSince = null;
  stream?.getTracks().forEach(t => t.stop()); stream = null; video.srcObject = null;
  wakeLock?.release?.(); wakeLock = null; audio?.close?.(); audio = null;
}
$('start').onclick = () => begin(linked);
$('resume').onclick = () => begin(linked || localStorage.getItem(STORE));
$('realignNow').onclick = async () => {
  $('realignNow').disabled = true;
  try { if (!stream) await permissions(); startScanning(); }
  catch (e) { $('intro').hidden = false; $('introError').textContent = e.message; }
  finally { $('realignNow').disabled = false; }
};
$('shutter').onclick = () => capture(here());
$('auto').onclick = () => { autoCapture = !autoCapture; $('auto').setAttribute('aria-pressed', String(autoCapture)); $('auto').textContent = autoCapture ? 'Auto on' : 'Auto off'; };
$('sound').onclick = () => { soundOn = !soundOn; $('sound').setAttribute('aria-pressed', String(soundOn)); $('sound').textContent = soundOn ? 'Sound on' : 'Sound off'; };
$('pause').onclick = () => {
  if (inFlight) return;
  stop(); $('hud').hidden = true; $('realign').hidden = false;
  if (scan.photos.length) $('reference').src = `${base()}/images/${scan.photos[0].id}`;
};
$('lastShot').onclick = async () => {
  if (inFlight || finishing || !scan.photos.length) return;
  inFlight = true; showProgress();
  try { restore(await api(`${base()}/photos/${scan.photos.at(-1).id}`, { method: 'DELETE' })); alert('Last photo removed.', 'warn'); }
  catch (e) { alert(e.message); }
  finally { inFlight = false; showProgress(); }
};
$('finish').onclick = async () => {
  if (inFlight || finishing || scan.photos.length < 3) return;
  finishing = true; showProgress();
  try {
    const result = await api(base() + '/finish', { method: 'POST' }); stop(); restore(result); showDone();
  } catch (e) { alert(e.message); }
  finally { finishing = false; showProgress(); }
};
function showDone() {
  $('hud').hidden = true; $('intro').hidden = true; $('done').hidden = false;
  $('doneText').textContent = `${scan.photos.length} photos saved · ${scan.coverage.have}/${scan.coverage.wanted} guided angles. VGGT uses up to ${scan.options.max_views} photos per final pass.`;
  drawCoverage($('doneMap').getContext('2d'), 400, plan, counts);
  $('donePercent').textContent = `${Math.round(100 * scan.coverage.have / scan.coverage.wanted)}%`;
  $('laptop').href = '/?scan=' + scan.id;
  pollResult();
}
async function pollResult() {
  try {
    restore(await api(base())); $('jobStatus').textContent = scan.job.message;
    const result = scan.latest_result;
    if (result) { $('resultDownload').hidden = false; $('resultDownload').href = `${base()}/results/${result.id}/merged.ply`; }
    if (['running', 'queued'].includes(scan.job.state)) setTimeout(pollResult, 2000);
  } catch (e) { $('jobStatus').textContent = e.message; }
}
$('again').onclick = () => { localStorage.removeItem(STORE); location.href = '/phone'; };
$('files').onchange = async event => {
  const files = [...event.target.files]; event.target.value = '';
  if (!files.length || uploading) return;
  uploading = true; $('files').disabled = true; $('start').disabled = true; $('resume').disabled = true;
  const failures = [];
  try {
    plan ||= await api('/api/plan');
    if (!scan) {
      scan = linked ? await api(`/api/scans/${encodeURIComponent(linked)}`) : await api('/api/scans', { method: 'POST' });
      restore(await api(base() + '/phone-mode', { method: 'POST' }));
    }
    for (const [i, file] of files.entries()) {
      $('uploadStatus').textContent = `Uploading ${i + 1}/${files.length}…`;
      if (file.size > 20 * 1024 * 1024) { failures.push(`${file.name}: over 20 MB`); continue; }
      try { const r = await api(`${base()}/photos?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file }); restore(r.scan); }
      catch (e) { failures.push(`${file.name}: ${e.message}`); }
    }
    $('uploadStatus').textContent = `${scan.photos.length} saved. ${failures.join(' · ')}`;
    $('uploadFinish').hidden = scan.photos.length < 3;
  } catch (e) { $('uploadStatus').textContent = e.message; }
  finally { uploading = false; $('files').disabled = false; $('start').disabled = false; $('resume').disabled = false; }
};
$('uploadFinish').onclick = async () => {
  if (uploading || finishing) return;
  finishing = true; $('uploadFinish').disabled = true;
  try { restore(await api(base() + '/finish', { method: 'POST' })); showDone(); }
  catch (e) { $('uploadStatus').textContent = e.message; }
  finally { finishing = false; $('uploadFinish').disabled = false; }
};
window.addEventListener('pagehide', stop);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && (running || stream)) {
    stop(); $('hud').hidden = true; $('realign').hidden = false;
    if (scan?.photos.length) $('reference').src = `${base()}/images/${scan.photos[0].id}`;
  }
});
(async () => {
  try {
    plan = await api('/api/plan');
    const id = linked || localStorage.getItem(STORE);
    if (id) { restore(await api(`/api/scans/${encodeURIComponent(id)}`)); $('resumeBox').hidden = false; }
  } catch (e) { $('introError').textContent = e.message; }
})();
