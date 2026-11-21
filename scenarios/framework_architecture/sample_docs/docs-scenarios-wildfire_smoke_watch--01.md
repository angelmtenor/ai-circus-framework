# 🔥 Wildfire Smoke Watch — a wall of lookout cameras that raises the alarm

Source: docs/scenarios/wildfire_smoke_watch.md (this repository)

> Domain: **Public sector** · Kind: `deep_learning` (image classification over frame sequences) · Scenario: [`scenarios/wildfire_smoke_watch/scenario.yaml`](../../scenarios/wildfire_smoke_watch/scenario.yaml)

Fire lookouts watch a horizon for the first thin column of smoke. This scenario replaces the
watcher with a small model and puts six real lookout cameras side by side: they replay recorded
ignitions at speed, every frame is scored by the deployed model **as it arrives**, and an alert is
raised when smoke persists for two frames in a row. The wall then measures what a real operator
would care about — how many minutes after the plume first became visible each camera spoke up, and
whether any camera cried wolf first. Some feeds are **no-fire controls** (a lookout before any fire:
every alert on one is a false alarm), and you choose which six of the eleven held-out cameras play.

<p align="center">
  <img src="../screenshots/wildfire_smoke_watch/watch-wall.webp" alt="The Watch Wall tab: six lookout camera tiles, each with a live smoke probability and a sparkline, red-framed once smoke is detected, next to an alert feed" width="900">
</p>
<p align="center"><sub>The default wall: four fires and two no-fire controls, at the end of their recordings. Images courtesy of <a href="https://www.hpwren.ucsd.edu/">HPWREN</a>, UC San Diego.</sub></p>

| | |
|---|---|
| **Question** | Is there a smoke plume anywhere in this lookout frame — and how quickly does a live alarm catch a fire that nobody has reported yet? |
| **Data** | **HPWREN Fire Ignition images Library (FIgLib)** — fixed lookout cameras in Southern California. Each recording is **81 frames, one a minute, from 40 minutes before to 40 minutes after** the plume first becomes visible; the frame file name (`<unix_ts>_<±offset seconds>.jpg`) *is* the ground truth. 25 recordings are used, one per lookout station (~1.7 GB, downloaded once, pinned by SHA-256, **never committed**). |
| **Split** | **By lookout, not by frame.** 12 lookouts train (843 frames), 2 validate/calibrate (140), and **11 are held out (757 frames) — stations the model has never seen**: 8 fires plus 3 **no-fire controls** (the pre-ignition half of a lookout, `clear_only`; the wall shows only the first 25 frames, stopping 15 minutes before the annotated start). The Watch Wall shows six at a time. The scenario's own tests refuse a YAML in which a station appears in two places. |
| **Model** | `facebook/convnextv2-nano-22k-224` fine-tuned (15.0 M parameters) at 320 px, **60 MB ONNX**, served by the same CPU-only `dl-inference` pod as the other vision scenarios — no extra container, no extra memory ceiling. |
| **Target** | `Clear` / `Smoke` per frame |
