# What each scenario is built from - PCB Visual Inspection (CV Anomaly Detection) (pcb_visual_inspection, deep_learning)

Source: generated from every scenario.yaml (this repository)

Automated optical inspection for an electronics assembly line: flags printed circuit boards with any visual defect — scratches, bent or missing pins, melted components — after learning only from photos of good boards, the way a real line is set up when defects are rare and never the same twice. Each board gets a defect heatmap and its closest known-good boards, and a live inspection line shows how many boards the model can pass or reject on its own and how many still need a human inspector.

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
