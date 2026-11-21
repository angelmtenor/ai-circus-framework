# 🔥 Wildfire Smoke Watch — a wall of lookout cameras that raises the alarm - What it gets right, and what it doesn't

Source: docs/scenarios/wildfire_smoke_watch.md (this repository)

Measured on the eleven unseen lookouts, **every frame included** — even the first minutes after
ignition, when the plume is a few pixels and no model (or person) can tell:

| | |
|---|---|
| Frame accuracy / macro-F1 | **89.3 %** / 0.890 |
| AUROC | **0.914** |
| Default wall (4 fires + 2 controls), 70 %, 3 frames | **4 / 4 fires detected**, mean **5.2 min** after the plume first appeared; **both controls silent; 0 false alarms**, 1 early detection (`syp-n`, 4 min before its annotation) |
| Same wall at the old 50 %, 2 frames | the same fires, but **4 false alarms** on `syp-n` before its ignition |
| Calibration | ECE 0.076 — the temperature fit on two validation lookouts made it *worse* than the raw model (0.045); read the probabilities as a ranking, not as frequencies |

Things to know about those wall numbers:

- **The alert rule was tuned on what you see.** I chose 70 % / 3 frames *after* watching `syp-n` raise
  false alarms at 50 % / 2, on the same held-out cameras the wall shows. So the wall is optimistic;
  an honest estimate needs a threshold fixed on the two validation lookouts, or a fresh set of cameras.
- **`syp-n` is a hard lookout.** A white cumulus-like puff sits on its left horizon for the whole
  recording (already there at −36 min) and a bird crosses the sky at −22 min, where the model hits 99 %.
  The model does see real haze rising from about −7 min, a few minutes before the annotation.
- **`ml-n` is not a clean control after −15 min.** Its smoke probability jumps to 83 % at −14 min and
  98 % at −1 min: a real plume was visible before the annotated start. The wall therefore stops every
  control 15 minutes before the annotation (`control_margin_seconds`), so none can reach a fire.
- **They move between training runs.** GPU training is not bit-for-bit repeatable. An earlier run of
  the same recipe, on a different six-fire wall, detected 6 / 6 with no false alarms and a 10.2 min mean.
- **A camera already ringing at ignition counts as detected at once**, and an early alert counts as a
  detection with a negative delay, which pulls the mean down.

Why the model is not better, and why that is the honest answer:

- **The first minutes are ambiguous by construction.** Offset 0 is when a human annotator first
  saw the plume. Frames in the next 10 minutes are left out of training and validation (teaching a
  network to "see" smoke that is a handful of pixels teaches it to hallucinate), but they stay in
  the wall and in the test score — so the delay you see is the real one.
- **The wall's fires differ in difficulty.** `pi-w` and `bh-w` have faint plumes; `dwpgm-s`, `lp-n`, `syp-n` and `ws-n` are unmistakable. Swap them in with the picker to see the model struggle.
- **Twelve training lookouts is a small world.** Haze, low sun, cloud and dust differ per mountain
  top. More recordings would help; the YAML lists them, no code changes.
- **The data had to be chosen by eye.** In many FIgLib recordings the plume stays a few dozen pixels
  even after 40 minutes, which a 320 px whole-frame classifier cannot resolve. Most of the 25
  recordings used are ones where a plume is clear by roughly the 25-minute mark. A production system
  would use full-resolution tiles (the SmokeyNet approach) and a much larger model.
