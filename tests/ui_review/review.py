"""UI/UX review harness: drives the real phone and laptop pages and records screenshots + automated checks.

Not part of the unit tests (it needs a running scanner and a browser). Dev-only setup, once:
    py -3.11 -m venv .venv-review
    .venv-review/Scripts/python.exe -m pip install playwright==1.49.1
It uses the installed Microsoft Edge (channel "msedge"), so no browser download is needed.

Run against a scanner in reconstruct mode that has at least one reconstructed scan (model ready):
    .venv-review/Scripts/python.exe tests/ui_review/review.py --base http://localhost:8010 --out data/ui_review/round1

The phone is simulated: getUserMedia returns a canvas showing real photos from that scan, and
DeviceOrientation / DeviceMotion events place the "phone" at chosen angles. Every capture is a real
upload, so the server's quality checks and coverage bookkeeping run for real (it creates a scan).
Writes <out>/report.json, <out>/report.md and PNG screenshots. Scoring rubric: docs/UX_REVIEW.md.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

CHECKS_JS = r"""
(mobile) => {
  const issues = [];
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05 && !el.closest('[hidden]'); };
  const name = el => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''));
  const doc = document.scrollingElement;
  if (doc.scrollWidth > innerWidth + 1) issues.push({ check: 'horizontal-overflow', severity: 'blocker', detail: `page ${doc.scrollWidth}px wide in a ${innerWidth}px viewport` });
  // Content cut off: SVG/IMG/CANVAS drawn larger than their box, or overflow-hidden containers hiding content.
  for (const el of document.querySelectorAll('svg, img, canvas')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (el.tagName === 'svg' && !el.closest('svg:not(:scope)') && !el.getAttribute('viewBox')) {
      const w = parseFloat(el.getAttribute('width')), h = parseFloat(el.getAttribute('height'));
      if (w && (Math.abs(w - r.width) > 1 || Math.abs(h - r.height) > 1))
        issues.push({ check: 'clipped-graphic', severity: 'blocker', detail: `${name(el.parentElement)} svg drawn ${w}x${h} into ${Math.round(r.width)}x${Math.round(r.height)} without viewBox` });
    }
    if (el.tagName === 'IMG' && el.complete && el.naturalWidth === 0) issues.push({ check: 'broken-image', severity: 'major', detail: el.src });
    if (r.right > innerWidth + 1 || r.left < -1) issues.push({ check: 'offscreen-graphic', severity: 'major', detail: `${name(el)} spans ${Math.round(r.left)}..${Math.round(r.right)}` });
  }
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const s = getComputedStyle(el);
    if ((s.overflowX === 'hidden' || s.overflow === 'hidden') && el.scrollWidth > el.clientWidth + 2 && el.tagName !== 'VIDEO')
      issues.push({ check: 'hidden-overflow', severity: 'major', detail: `${name(el)} hides ${el.scrollWidth - el.clientWidth}px of content` });
    if (el.children.length === 0 && el.textContent.trim() && s.textOverflow !== 'ellipsis') {
      const r = el.getBoundingClientRect();
      if (r.right > innerWidth + 1) issues.push({ check: 'text-offscreen', severity: 'major', detail: `${name(el)}: "${el.textContent.trim().slice(0, 40)}"` });
    }
  }
  // Text contrast (WCAG 2.1 AA): 4.5:1, or 3:1 for large text. Backgrounds over video/images are skipped.
  const parse = c => { const m = c.match(/[\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const blend = (top, bottom) => { const a = top[3] ?? 1; return [0, 1, 2].map(i => top[i] * a + bottom[i] * (1 - a)); };
  function background(el) {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      const s = getComputedStyle(node), c = parse(s.backgroundColor);
      if (s.backgroundImage !== 'none' && !s.backgroundImage.startsWith('linear')) return null;
      if ((c[3] ?? 1) > 0) { layers.push(c); if ((c[3] ?? 1) >= 1) break; }
      if (node.tagName === 'BODY') break;
    }
    if (el.closest('.hud') && !layers.some(l => (l[3] ?? 1) >= .8)) return null;  // over live video
    let colour = [255, 255, 255];
    for (const layer of layers.reverse()) colour = blend(layer, colour);
    return colour;
  }
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const s = getComputedStyle(el), bg = background(el);
    if (!bg) continue;
    const fg = blend(parse(s.color), bg), size = parseFloat(s.fontSize), bold = +s.fontWeight >= 700;
    const ratio = (Math.max(lum(fg), lum(bg)) + .05) / (Math.min(lum(fg), lum(bg)) + .05);
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    const key = name(el) + ratio.toFixed(1);
    if (ratio < need && !seen.has(key)) { seen.add(key);
      issues.push({ check: 'contrast', severity: ratio < 3 ? 'major' : 'minor', detail: `${name(el)} "${el.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}:1 (needs ${need})` }); }
  }
  // Interactive elements: size and accessible names.
  for (const el of document.querySelectorAll('button, a[href], input, select, summary, label.upload, [role=button]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect(), min = mobile ? 44 : 24;
    if (el.tagName !== 'INPUT' || el.type !== 'file') {
      if (Math.min(r.width, r.height) < min - .5) issues.push({ check: 'target-size', severity: mobile ? 'major' : 'minor', detail: `${name(el)} ${Math.round(r.width)}x${Math.round(r.height)} (min ${min})` });
    }
    const label = (el.getAttribute('aria-label') || el.textContent || el.title || (el.labels && [...el.labels].map(l => l.textContent).join(' ')) || '').trim();
    if (!label && el.type !== 'file') issues.push({ check: 'no-accessible-name', severity: 'major', detail: name(el) });
  }
  for (const img of document.querySelectorAll('img')) if (visible(img) && !img.hasAttribute('alt')) issues.push({ check: 'img-alt', severity: 'minor', detail: img.src.slice(-40) });
  return issues;
}
"""

FAKE_DEVICE_JS = r"""
(photos) => {
  // Simulated rear camera: a canvas stream showing the scan's real photos; __show(i) switches photo.
  const canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 1920;
  canvas.style.cssText = 'position:fixed;left:0;top:0;width:2px;height:2px;opacity:0.01;pointer-events:none';
  const ctx = canvas.getContext('2d'); let image = null, mode = 'photo';
  const images = photos.map(src => { const i = new Image(); i.src = src; return i; });
  window.__show = index => { mode = 'photo'; image = images[index % images.length]; };
  window.__blank = () => { mode = 'blank'; };
  setInterval(() => {
    if (mode === 'blank') { ctx.fillStyle = '#777'; ctx.fillRect(0, 0, 1080, 1920); }
    else if (image?.complete) ctx.drawImage(image, 0, 0, 1080, 1920);
  }, 50);
  addEventListener('DOMContentLoaded', () => document.body.append(canvas));
  window.__show(0);
  navigator.mediaDevices.getUserMedia = async () => canvas.captureStream(15);
  // Simulated motion sensors: pitch below horizon p -> beta = 90 - p; azimuth a (walked left) -> alpha = -a.
  window.__pose = { alpha: 0, beta: 55, gamma: 0 };
  window.__aim = (azimuth, pitch) => { window.__pose = { alpha: ((-azimuth % 360) + 360) % 360, beta: 90 - pitch, gamma: 0 }; };
  setInterval(() => {
    dispatchEvent(Object.assign(new Event('deviceorientation'), window.__pose));
    dispatchEvent(Object.assign(new Event('devicemotion'), { rotationRate: { alpha: 0.5, beta: 0.5, gamma: 0.5 } }));
  }, 60);
}
"""


class Review:
    def __init__(self, out: Path):
        self.out, self.shots, self.issues, self.console, self.notes = out, [], [], [], []

    def shot(self, page, name, description, full=False):
        path = self.out / f"{len(self.shots) + 1:02d}_{name}.png"
        page.screenshot(path=str(path), full_page=full)
        self.shots.append(dict(file=path.name, state=description, viewport=page.viewport_size))

    def check(self, page, state, mobile):
        for issue in page.evaluate(CHECKS_JS, mobile):
            self.issues.append(dict(state=state, **issue))

    def watch(self, page, label):
        page.on("console", lambda m: self.console.append(f"[{label}] {m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: self.console.append(f"[{label}] pageerror: {e}"))
        page.on("response", lambda r: self.console.append(f"[{label}] HTTP {r.status} {r.url}") if r.status >= 400 and "/masks/" not in r.url else None)


def dashboard(browser, base, review: Review, scan_id):
    for width, height, label in ((1440, 900, "desktop"), (1024, 768, "laptop"), (390, 844, "narrow")):
        page = browser.new_page(viewport=dict(width=width, height=height))
        review.watch(page, f"dashboard-{label}")
        page.goto(base + "/")
        page.wait_for_selector(".scan", timeout=15000)
        page.locator(f".scan:has-text('Model ready')").first.click() if not scan_id else None
        page.wait_for_timeout(3500)
        page.evaluate("scrollTo(0, 0)")
        review.check(page, f"dashboard {label} {width}x{height}", width < 600)
        review.shot(page, f"dashboard_{label}_top", f"Dashboard {label}: first screen", full=False)
        review.shot(page, f"dashboard_{label}_full", f"Dashboard {label}: whole page with a reconstructed scan selected", full=True)
        if label == "desktop":
            page.locator("#connect").screenshot(path=str(review.out / "connect_card.png"))
            review.shots.append(dict(file="connect_card.png", state="Connect-a-phone card (QR)", viewport=page.viewport_size))
            # Keyboard: is focus visible on the first few tab stops?
            styles = []
            for _ in range(6):
                page.keyboard.press("Tab")
                styles.append(page.evaluate("(() => { const e = document.activeElement, s = getComputedStyle(e); return [e.tagName + (e.id ? '#' + e.id : ''), s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 || s.boxShadow !== 'none']; })()"))
            for element, ok in styles:
                if not ok:
                    review.issues.append(dict(state="dashboard keyboard", check="focus-visible", severity="major", detail=f"{element} shows no focus indicator"))
            review.notes.append(f"Tab order (desktop dashboard): {[s[0] for s in styles]}")
        page.close()


def phone(browser, base, review: Review, photos):
    context = browser.new_context(viewport=dict(width=390, height=844), device_scale_factor=2, is_mobile=True, has_touch=True)
    context.add_init_script(script=f"({FAKE_DEVICE_JS})({json.dumps(photos)})")
    page = context.new_page()
    review.watch(page, "phone")
    page.on("dialog", lambda dialog: (review.notes.append(f"Phone dialog: {dialog.message}"), dialog.accept()))
    page.goto(base + "/phone")
    page.wait_for_timeout(800)
    review.check(page, "phone intro", True)
    review.shot(page, "phone_intro", "Phone: start screen", full=True)
    page.click("text=No live camera? Upload photos instead")
    page.wait_for_timeout(300)
    review.shot(page, "phone_upload_open", "Phone: upload fallback expanded", full=True)
    page.click("text=No live camera? Upload photos instead")
    page.evaluate("window.__aim(0, 35)")
    page.click("#start")
    page.wait_for_timeout(2500)
    review.check(page, "phone tap step", True)
    review.shot(page, "phone_tap_step", "Phone: first step, asked to tap the object")
    # Tap the bottle (centre of the screen area showing the object in photo 0).
    page.mouse.click(215, 420)
    page.wait_for_timeout(250)
    review.shot(page, "phone_tap_feedback", "Phone: just after tapping (marker + saving)")
    page.wait_for_timeout(2500)
    review.shot(page, "phone_after_first", "Phone: after the first (tapped) photo")
    # Walk around at the middle height: auto-capture at each new side.
    captured = 1
    for step, azimuth in enumerate(range(30, 360, 30)):
        page.evaluate(f"window.__show({step + 1}); window.__aim({azimuth}, 35)")
        page.wait_for_timeout(2200)
        if step == 2:
            review.shot(page, "phone_mid_scan", "Phone: mid-scan at the middle height (auto-capture)")
    page.evaluate("window.__aim(185, 35)")
    page.wait_for_timeout(1200)
    review.check(page, "phone hud", True)
    review.shot(page, "phone_hud_guidance", "Phone: guidance after one full lap at the middle height")
    page.evaluate("window.__aim(0, 70)")  # Look down steeply: high ring
    page.wait_for_timeout(1500)
    review.shot(page, "phone_high_ring", "Phone: aiming from above (high ring)")
    page.evaluate("window.__aim(0, -30)")  # Pointing above the object (below the lowest ring) -> tilt down
    page.wait_for_timeout(1200)
    review.shot(page, "phone_tilt_cue", "Phone: phone pointed too far up -> tilt-down cue")
    page.set_viewport_size(dict(width=844, height=390))
    page.evaluate("window.__aim(100, 35)")
    page.wait_for_timeout(1200)
    review.check(page, "phone hud landscape", True)
    review.shot(page, "phone_hud_landscape", "Phone: capture HUD in landscape")
    page.set_viewport_size(dict(width=390, height=844))
    page.wait_for_timeout(500)
    # A bad photo: blank grey frame through the shutter -> rejection message.
    page.evaluate("window.__blank(); window.__aim(15, 70)")
    page.wait_for_timeout(400)
    page.click("#shutter")
    page.wait_for_timeout(1500)
    review.shot(page, "phone_rejected", "Phone: a photo rejected by the quality check")
    page.evaluate("window.__show(20)")
    finish = page.locator("#finish")
    review.notes.append(f"Finish button before finishing: '{finish.inner_text()}' enabled={finish.is_enabled()}")
    if finish.is_enabled():
        finish.click()
        page.wait_for_timeout(2500)
        review.check(page, "phone done", True)
        review.shot(page, "phone_done", "Phone: scan saved screen", full=True)
    # Landscape
    page.set_viewport_size(dict(width=844, height=390))
    page.wait_for_timeout(600)
    review.shot(page, "phone_landscape", "Phone: final screen in landscape")
    context.close()
    # Camera blocked
    context = browser.new_context(viewport=dict(width=390, height=844), is_mobile=True, has_touch=True)
    context.add_init_script(script="navigator.mediaDevices.getUserMedia = async () => { throw Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' }); };")
    page = context.new_page()
    page.goto(base + "/phone")
    page.click("#start")
    page.wait_for_timeout(800)
    review.shot(page, "phone_camera_blocked", "Phone: camera permission denied", full=True)
    context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8010")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    review = Review(args.out)
    started = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True,
                                    args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        import urllib.request
        scans = json.loads(urllib.request.urlopen(args.base + "/api/scans").read())
        ready = next((s for s in scans if (s.get("status") or {}).get("state") == "ready" and "object.glb" in s["files"]), None)
        if not ready:
            print("Needs a scan with a finished reconstruction (object.glb).", file=sys.stderr)
            return 2
        photos = [f"{args.base}/scans/{ready['id']}/images/{p['name']}" for p in ready["photos"]]
        phone(browser, args.base, review, photos)
        dashboard(browser, args.base, review, None)
        browser.close()
    counts = {}
    for issue in review.issues:
        counts[issue["severity"]] = counts.get(issue["severity"], 0) + 1
    report = dict(seconds=round(time.monotonic() - started, 1), screenshots=review.shots, issues=review.issues,
                  issue_counts=counts, console=review.console, notes=review.notes)
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# Automated UI checks ({report['seconds']} s)", "", f"Issue counts: {counts or 'none'}", "", "## Issues"]
    lines += [f"- **{i['severity']}** [{i['check']}] {i['state']}: {i['detail']}" for i in review.issues] or ["- none"]
    lines += ["", "## Console / network"] + ([f"- {c}" for c in review.console] or ["- none"])
    lines += ["", "## Notes"] + [f"- {n}" for n in review.notes]
    lines += ["", "## Screenshots"] + [f"- `{s['file']}`: {s['state']}" for s in review.shots]
    (args.out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(review.issues)} issues {counts}; {len(review.shots)} screenshots -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
