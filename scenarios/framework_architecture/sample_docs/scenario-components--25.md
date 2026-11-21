# What each scenario is built from - Solar Cell EL Inspection (CV) (solar_cell_inspection, deep_learning)

Source: generated from every scenario.yaml (this repository)

Quality control for photovoltaic module production. In an electroluminescence (EL) test the module is powered in the dark and photographed in near-infrared: healthy silicon glows, while micro-cracks, broken fingers and disconnected areas stay dark — the X-ray of solar manufacturing. A ConvNeXt V2 network fine-tuned on expert-graded cells from 44 real mono- and polycrystalline modules classifies each cell as functional or defective. Hard on purpose: many cracks are hairline, polycrystalline grain looks like damage, and the experts themselves rated a quarter of the defective cells only "possibly" defective. Each cell gets a heatmap of the area that drove the decision and its most similar known cells, and a live grading line shows how many cells the model can pass or reject on its own and how many still go to a human.

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
