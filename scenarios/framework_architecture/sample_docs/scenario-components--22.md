# What each scenario is built from - Machined Screw Inspection (CV Anomaly Detection — hard) (screw_visual_inspection, deep_learning)

Source: generated from every scenario.yaml (this repository)

End-of-line inspection for a turned-parts line: a camera photographs every machined steel screw lying at a random position and angle on the inspection table, and flags any with a damaged thread flank or tip, a scratched head or neck, or a deformed front — after learning only from photos of good screws, the way a real line is set up when defects are rare and never the same twice. Much harder than the PCB line: no fixture aligns the part, the thread is a fine repeating texture in which one chipped flank looks almost normal, and most defects cover a tiny fraction of the photo. Each screw gets a defect heatmap and its closest known-good screws, and a live inspection line shows how many screws the model can pass or scrap on its own and how many still need a human inspector.

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
