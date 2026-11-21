# 🔥 Wildfire Smoke Watch — a wall of lookout cameras that raises the alarm - What the Watch Wall shows

Source: docs/scenarios/wildfire_smoke_watch.md (this repository)

All six feeds advance on one clock (one frame per 0.7 s, so the 81-frame recording plays in about a
minute; a control has fewer frames and shows *recording ended* once it runs out). Each tick, every tile's current frame is sent to `dl-inference`'s `POST /predict` and the
tile says honestly where its number came from:

- **`live · 230 ms`** — the model really scored this frame just now.
- **`replay`** — the answer wasn't back in time, the speed is above 4×, or the service is down; the
  tile uses the model's precomputed score for that frame instead. Same model, same frame — but the
  UI never claims an inference it didn't make.

An **alert** is the alarm switching on after *N* consecutive frames at or above the threshold
(defaults: 70 %, 3 frames; both are sliders). It is timed against the recording's ground truth:
**minutes after the plume first became visible** (the detection delay). An alert up to 5 minutes
*before* that is an **early detection** — the annotation is when a person first saw the plume, so
the model may fairly see it sooner — and anything earlier is a **false alarm**; on a control, any
alert is one. Each tile ends with a one-line verdict (`✓ flagged T+11 min`, `✓ correctly quiet`, …). **🎛 Change cameras** opens a picker over all eleven held-out
lookouts (fires tagged `fire`, controls `no fire`); the default wall is four fires and two controls.
Drag the threshold and every KPI recomputes instantly; tick *Show each camera's whole
recording* to overlay the entire probability curve; click a tile to pause the wall and trace where
the model sees smoke (an occlusion heatmap — about 4 s on the CPU pod).

<p align="center">
  <img src="../screenshots/wildfire_smoke_watch/picker.webp" alt="The camera picker: eleven held-out lookouts as checkboxes, each tagged fire or no fire, six selected" width="900">
</p>

<p align="center">
  <img src="../screenshots/wildfire_smoke_watch/heatmap.webp" alt="A lookout frame of a large smoke plume with the model's occlusion heatmap glowing over the column" width="520">
</p>
