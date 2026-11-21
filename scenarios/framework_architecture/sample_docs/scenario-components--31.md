# What each scenario is built from - Patient Symptom Triage (NLP) (symptom_triage, deep_learning)

Source: generated from every scenario.yaml (this repository)

Reads a patient's own free-text description of their symptoms and suggests the most likely of 22 common conditions, using a clinical language model (BioClinical ModernBERT) fine-tuned on public data. Every suggestion is explained word by word and backed by the most similar training cases, and a live triage board shows how such a model could route incoming messages to the right care pathway — sending anything it isn't confident about to a nurse. A demonstration of clinical NLP, not a medical device.

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
