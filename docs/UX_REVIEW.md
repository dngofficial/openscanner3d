# UI/UX review rubric

Used by reviewers (people or agents) to score the phone capture page (`frontend/phone.html`) and the laptop dashboard (`frontend/index.html`). Evidence comes from `tests/ui_review/review.py`, which drives both pages in Edge with a simulated camera (real scan photos) and simulated motion sensors. Each run writes screenshots and `report.md` / `report.json` (automated checks) to `data/ui_review/<round>/`.

Users are first-timers scanning a stationary object at a hackathon demo. Phone: walk around the object while the page guides them. Laptop: connect the phone, reconstruct, inspect and download the model.

## Scenarios the evidence covers

| # | Scenario | Pass looks like |
|---|---|---|
| P1 | Phone start screen | Purpose, the 3 steps and the primary action are clear in 5 seconds; the upload fallback is findable but secondary |
| P2 | Tap-to-start | The user knows to tap the object, sees where they tapped, and can skip |
| P3 | Guided capture | The next action is always one short instruction; the progress dial and counts are understandable; auto-capture feedback is felt (flash/sound/haptics) |
| P4 | Aim errors | Too low / too high / move sideways cues are unambiguous and do not cover the object |
| P5 | Rejected photo | The reason is specific and says what to do |
| P6 | Finish | Minimum photo rule and the missing-angle warning are clear; the done screen says what happens next |
| P7 | Camera blocked / no sensors | The page explains how to recover; the upload fallback works |
| P8 | Landscape and small screens | Nothing is cut off or overlapping |
| D1 | Connect a phone | QR fully visible and scannable; link and problems readable; the fix for each problem is actionable |
| D2 | Scan list | State of each scan is obvious; the selected scan is clear |
| D3 | Scan detail | The next action (Reconstruct / Rebuild) stands out; status while running is honest; the 3D viewer is prominent and its controls are labelled |
| D4 | Results | Downloads are labelled by purpose; detail presets (low poly etc.) are understandable; mask overlays explain the ML step honestly |
| D5 | Responsive | 1440, 1024 and 390 px widths work without horizontal scroll or crushed columns |

## Scoring (each 0–10, weighted)

| Dimension | Weight | 10 means |
|---|---|---|
| Task flow and clarity | 20% | Every scenario above can be completed without reading docs; one obvious next action at every step |
| Feedback and error recovery | 15% | Every state change and failure is visible, specific and recoverable |
| Visual hierarchy and layout | 15% | Primary actions and key information dominate; consistent spacing and alignment; no crushed or empty regions |
| Responsiveness and robustness | 15% | No clipping or overflow at any tested size or orientation; no console errors |
| Accessibility | 15% | WCAG 2.1 AA contrast, ≥44 px touch targets on phone-sized layouts, visible focus, labelled controls, reduced-motion respected |
| Honesty and copy | 10% | Short, plain wording; ML and limitations described accurately; no overclaiming |
| Consistency and polish | 10% | Shared visual language between phone and laptop; states look deliberate |

**Overall** = weighted average, one decimal.

**Caps from the automated report:**
- Any `blocker` (cut-off content, horizontal page scroll, page error) caps the overall score at 7.0.
- More than 3 `major` issues cap it at 8.5.
- Each `major` issue must be fixed or explicitly justified.

Approval threshold for this round of work: **9.0 or higher from every reviewer**, with no blocker.

## Reviewer output format

```
SCORE: <overall>/10
DIMENSIONS: flow x, feedback x, hierarchy x, responsive x, a11y x, honesty x, polish x
TOP ISSUES (most impactful first, max 8): <issue> — <evidence: screenshot or file:line> — <specific fix>
APPROVE: yes|no
```
