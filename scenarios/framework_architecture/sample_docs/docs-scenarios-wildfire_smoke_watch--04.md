# 🔥 Wildfire Smoke Watch — a wall of lookout cameras that raises the alarm - How it fits the framework

Source: docs/scenarios/wildfire_smoke_watch.md (this repository)

No scenario-specific code. A new **data source type**, `http_frame_sequences`, reads per-camera
`.tgz` archives (each digest-checked, cached in the tenant's SeaweedFS bucket under `raw/`); the
trainer publishes the held-out frames **in recording order** with each frame's camera (`group`) and
frame offset in seconds (`seq`); and the generic `camera_wall` tab in `ui-react` renders any
scenario with that shape. `dl-inference` warms a `camera_wall` model first at start-up, because it
fires a request per camera per tick. See [`scripts/prepare_figlib_sequences.py`](../../scripts/prepare_figlib_sequences.py)
to choose recordings and regenerate the pinned `source:` block.

```bash
make dl-train SCENARIOS=wildfire_smoke_watch   # ~3 min on a laptop GPU; downloads the 25 archives first
make k3s-dl-build k3s-dl-up                    # the optional deep-learning overlay (dl-inference)
```
