# 🤝 Emergency Social Aid — rules, a model and a person

Source: docs/scenarios/prestaciones_sociales.md (this repository)

> Domain: **Public sector** · Kind: `tabular_ml` (binary classification, free text + structured data, **business rules before the model**) · Scenario: [`scenarios/prestaciones_sociales/scenario.yaml`](../../scenarios/prestaciones_sociales/scenario.yaml) · UI in **Spanish**

**Why.** Emergency social aid is public money for people who cannot cover the basics — and in Spain
its manual assessment has two well-known failure modes. **Fraud**: undeclared income, the same aid
claimed again for the same expense, money requested for something that isn't a basic need or that
the household can perfectly well afford. **Favouritism**: staff approving aid by hand for people they
know. This scenario puts every application through **the same rules and the same model**, and gives
every proposal its reasons box by box — so a grant or a denial has to be justified by the file, not
by who the applicant is, and departing from the proposal has to be argued in writing. The aid reaches
the people who actually need it; nobody is accused of anything by an algorithm (the rules and the
model only say what to check — a social worker checks it and decides).

The (fictional) Ayuntamiento de Villaclara gives *Ayudas Económicas de Emergencia Social* — up to
1,500 € for rent, utility bills, food, school supplies or medical expenses. Every application is a
basic form: **numbers** (income, household, months in the census), **categories** (employment, housing,
concept of the aid) and **three free-text boxes** (the applicant's own account, what the money is for,
the social worker's report). The scenario answers what an intelligent-document-processing desk would be
asked: *which applications can wait for a person, which can't, and which boxes explain the proposal?*

<p align="center">
  <img src="../screenshots/prestaciones_sociales/circuit.png" alt="The circuit: 600 applications flow from the registry through the business rules (76 stopped: 57 need more documents, 19 are inadmissible) and the model into three trays — grant, manual review, deny — with live threshold sliders and KPIs" width="900">
</p>
<p align="center"><sub>Every dot is an application. The rules stop what is incomplete or doesn't meet the requirements; the model sorts the rest. Drag the thresholds and the trays re-sort live.</sub></p>
