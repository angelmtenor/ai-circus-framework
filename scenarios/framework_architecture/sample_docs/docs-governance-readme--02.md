# Governance-ready by standards - What this is, and is not

Source: docs/governance/README.md (this repository)

| It provides | It does not provide |
|---|---|
| Declared metadata for every AI system, with a stable `ai_system_id` | A governance product, a GRC or AIMS interface, or a risk-assessment workflow |
| An inventory export (CycloneDX AI-BOM) | Automatic legal classification — risk categories are **declared by the owner**, never inferred |
| Trace attributes, decision records and events carrying ids, metrics and decisions | Compliance. It *supports* an ISO/IEC 42001 / EU AI Act management system and *provides evidence for* it; it is not one |
| A pluggable policy decision point enforced at the single model chokepoint | Prompt or personal-data capture in governance data (events, audit and records are content-free) |
| A tamper-evident admin audit trail | Integration with any particular vendor (none is named in code, config or docs) |

Everything is **off by default**. With every switch off the platform behaves exactly as it did before.
