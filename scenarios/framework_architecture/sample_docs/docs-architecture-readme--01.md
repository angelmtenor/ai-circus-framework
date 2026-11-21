# Architecture guide

Source: docs/architecture/README.md (this repository)

How the framework is built, for someone seeing it for the first time: the layers from the cluster up, one request
followed end to end, and how a YAML file becomes an app. Every component's one-line description, how to switch it on
and its design decisions are in [components.md](components.md); both pages and the diagram are generated from one
catalog, so they cannot disagree with each other or with the app's **Architecture** page.

<p align="center">
  <img src="architecture.svg" alt="The platform by layer: gateways and governance at the top, then consumption (AI, BI and GenAI), data and infrastructure, with observability alongside" width="1100">
</p>
