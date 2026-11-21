# What each scenario is built from - Wildfire Smoke Watch (CV) (wildfire_smoke_watch, deep_learning)

Source: generated from every scenario.yaml (this repository)

A wall of fire-lookout cameras that watches for the first smoke of a wildfire. Mountain-top cameras from Southern California's HPWREN network replay real recordings, one frame a minute from forty minutes before a plume appears to forty minutes after, and a ConvNeXt V2 network fine-tuned on twelve other lookouts scores every frame as it arrives. An alert fires when smoke persists for consecutive frames, and the wall reports how many minutes after the plume first became visible each camera raised it — and whether any camera cried wolf first. Some feeds are controls — a lookout before any fire, where every alert is a false alarm — and the viewer picks which six of the eleven held-out cameras play. The test cameras were never seen in training, so the numbers are what a newly installed camera would get. Hard on purpose: a fresh plume is a few pixels against haze, cloud and glare, and the first minutes are genuinely ambiguous.

Components it uses:
- Kubernetes (k3s) (infra): Runs every pod this scenario needs.
- GPU node (infra): Training runs on the host GPU when there is one.
- SeaweedFS (data): The images or texts and the exported ONNX model.
- PostgreSQL (data): Holds tenants, entitlements and chat history.
- dl-training (data): Fine-tunes the model and exports it to ONNX.
- ui-react (consumption): Draws the workspace from this scenario.yaml: Overview, data, model and showpiece tabs.
- dl-inference (consumption): Scores every input with ONNX Runtime.
- Traefik (gateway): Routes the browser's calls to each service by hostname.
- Keycloak (gateway): Signs the user in; the tenant is their organization.
- platform-registry (gateway): Serves this scenario and checks the tenant's entitlement on every call.
