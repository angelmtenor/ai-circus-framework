# What each scenario is built from - Chest X-ray Pneumonia Screening (CV) (chest_xray_pneumonia, deep_learning)

Source: generated from every scenario.yaml (this repository)

Screens paediatric chest X-rays for signs of pneumonia with a modern convolutional network (ConvNeXt V2) fine-tuned on a public, expert-labelled dataset. Each read shows a heatmap of the lung regions that drove the decision plus the most similar prior studies, and a simulated reading room shows how AI worklist prioritization gets the sickest patients read first. A demonstration of medical computer vision, not a medical device.

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
