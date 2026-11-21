# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - The Office tab

Source: docs/scenarios/toxic_leadership.md (this repository)

A glass office tower at night, one floor per job family: Leads & Architects on top, then the
Data & AI lab, the open-plan Software Engineering floor, the DevOps & Cloud ops room and the QA
lab. Floors are sized from the data. The building has its own opinions: an "AI-FIRST ✦ 10× ✦
SYNERGY" neon on the roof, a C-suite penthouse with the lights on and nobody at the desks (the
whiteboard reads *Strategy 2026: leverage · pivot · AI-first · align*), an elevator that never
reaches the top floor, and a "We're hiring! Rockstar 10× ninjas" banner in the lobby next to
the exit.

All 4,000 reviews are scored in one fast call (probabilities only — about 4 s); a person is
SHAP-explained when you click them.

| Mode | What you see |
|---|---|
| **Model's prediction** | Probability of bad leadership, blue → grey → red. Within each floor the most likely are seated first. |
| **Reveal real ratings** | What the reviewer actually rated: bad leadership filled red, OK leadership as hollow blue rings. The reveal runs floor by floor. |
| **Model vs reality** | Only the reviews the model got wrong light up — often short, sarcastic or ranting about pay. |

<p align="center">
  <img src="../screenshots/toxic_leadership/office-reveal.png" alt="Reveal real ratings: red-filled desks for reviewers who rated leadership 1–2 stars, hollow blue rings for the rest" width="49%">
  <img src="../screenshots/toxic_leadership/office-surprises.png" alt="Model vs reality: only the reviews the model misjudged light up" width="49%">
</p>

### One review, explained

Click a desk to read the review with every word coloured by how much it pushed the probability
(red toward bad leadership, blue away), a SHAP waterfall for the whole record, and the
**deployed TF-IDF model next to the transformer challenger** on the same review.

<p align="center"><img src="../screenshots/toxic_leadership/office-review.png" alt="A Data & AI reviewer at 97%: the review text raised the probability by 29 points, the 1-star work-life and pay ratings by 22 and 17; 'away', 'management', 'no direction' and 'meetings' highlighted in red; both models agree at 97%" width="560"></p>

### What if…?

Nine fictional archetypes are ready to edit: *The LinkedIn Visionary* (a Director of AI who
has never trained a model), *The Buzzword Machine*, *The Delegator-in-Chief*, *The
Overpromising Salesperson* (sells what doesn't exist; the engineers work the weekends), *The
Rumour Mill* (covers their own misses with false stories about the team), *The
Micromanager*, *The Cornering Boss*, *The Tech Lead Who Still Codes* and *Underpaid but well
led*. Rewrite the review or change the ratings and the prediction, waterfall and word
highlights update live; **⚖ Compare with the transformer** scores the same text with the
challenger.

<p align="center"><img src="../screenshots/toxic_leadership/office-whatif.png" alt="What if: the LinkedIn Visionary persona — TF-IDF model 63%, transformer 86% — with the waterfall and highlighted words" width="520"></p>

---
