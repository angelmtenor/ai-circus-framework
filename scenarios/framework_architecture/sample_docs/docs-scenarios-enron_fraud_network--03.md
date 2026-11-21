# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - The Network tab

Source: docs/scenarios/enron_fraud_network.md (this repository)

A dark "investigation board": a force-directed map of the company's two-way e-mail ties
(1999-2002). On opening, the network unfolds from the centre and settles; after that it never
moves on its own, so the picture you learn stays put.

| On the board | Meaning |
|---|---|
| **Glowing circles** | The 143 insiders. Colour = risk tier on a validated one-hue red ramp (Background → Elevated → Review → Priority), glow ∝ score; size = total payments |
| **Violet hexagons** | Curated entities — JEDI, Chewco, LJM1, LJM2, the four Raptors, Southampton Place, Arthur Andersen, The Travel Agency in the Park |
| **Dashed violet lines** | Documented relations ("general partner of", "required reviewer of every LJM transaction", …), each with the Powers Report page it comes from |
| **Blue web** | E-mail ties; thickness and brightness by volume |
| **Small grey dots** | 100 *unscored colleagues* — Enron staff outside the pay table, anonymised, kept only because they connect scored people |
| **Outer orbit** | Insiders with no two-way e-mail tie in the corpus (many non-employee directors) |

Click a person — or search one (`/`) — and the camera eases onto their ego network; their ties
light up with messages flowing along them, and the **dossier** explains the score: SHAP
contributions rolled up into four pillars (cash pay, stock cash-out, network position, e-mail
activity), the top drivers with the person's percentile among all insiders, profile facts,
their strongest ties and every documented relation with its citation.

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-dossier.webp" alt="Andrew S. Fastow selected: his ego network lit up with message particles, dashed relations to LJM1, LJM2, Chewco and Southampton Place; the dossier shows a 31% out-of-fold score in the Elevated tier, pillar bars (cash pay +20 pts, network position −6.5 pts) and top drivers with peer percentiles" width="900">
</p>
<p align="center"><sub>The CFO who ran the partnerships scores only 31%: his own mailbox is not in the corpus. The documented relations carry what the model cannot see.</sub></p>

**Trace a path** between any two nodes — the shortest chain of e-mail ties and documented
relations, preferring the documented ones and scored people over anonymous colleagues:

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-path.webp" alt="A traced path from Kenneth L. Lay to LJM2 in three hops — Lay chaired the June 1999 Board meeting that approved LJM1, Fastow was LJM1's general partner and the managing member of LJM2's general partner — each hop citing a Powers Report page" width="900">
</p>

**Community** colours the board by the communities detected in the e-mail graph (Louvain): the
three largest in validated hues, the rest outlined in grey — every hull is labelled.

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-communities.webp" alt="Community mode: three coloured communities with translucent hulls and labels, smaller communities outlined in grey, entities drawn in ink" width="900">
</p>

The **timeline** replays the network month by month, from January 1999 to the bankruptcy —
ties fade in and out, messages flow along the active ones, and the milestones (Skilling becomes
CEO, his resignation, the Watkins letter, the Q3 2001 charges, the SEC inquiry, the CFO's
replacement, the restatement, Chapter 11, the Powers Report) pop up as they pass.

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-replay.webp" alt="The board in October 2001 with the monthly e-mail volume timeline below it: the peak of the crisis, event pins for the Q3 charges, the SEC inquiry and the CFO's replacement highlighted" width="900">
</p>
