# 🚢 Titanic Survival — an explained ML tutorial - The Voyage tab

Source: docs/scenarios/titanic.md (this repository)

A night cutaway of a four-funnel liner: decks A–G, lifeboats on the boat deck, glowing boiler
rooms, steam from the three working funnels, the ship's lights reflected on a flat-calm sea,
and the iceberg dead ahead. All 891 passengers are aboard, berthed where their class slept:
1st class high up amidships, 2nd class aft, and 3rd class (steerage) on the lowest decks at the
bow and stern. They are scored by the deployed model in one batched `/predict` call
(probabilities only, about 2.5 s); a passenger is SHAP-explained when you click them.

<p align="center"><img src="../screenshots/titanic/voyage-tab.png" alt="The Voyage tab: headline figures (891 aboard, 38% survived, model average 38%, cross-validated ROC AUC 0.885), colour-mode switch, passenger search and the ship" width="850"></p>

### Three ways to colour the ship

| Mode | What you see |
|---|---|
| **Model's prediction** | Survival probability on a red → grey → blue scale. Within each class the most likely survivors are berthed first, so every deck reads as a gradient. |
| **Reveal real fate** | What actually happened: survivors filled blue, victims as hollow red rings, so fate is never encoded by colour alone. The reveal sweeps from bow to stern, the way the ship went down. |
| **Model vs reality** | Only the model's mistakes light up: survivors it had written off (blue) and victims it expected to live (red). Everyone it got right fades into the hull. |

<p align="center">
  <img src="../screenshots/titanic/voyage-fate.png" alt="Reveal real fate: survivors as filled blue dots, victims as hollow red rings" width="49%">
  <img src="../screenshots/titanic/voyage-surprises.png" alt="Model vs reality: only the passengers the model misjudged are highlighted" width="49%">
</p>

The class cards under the ship already tell the story: the model expects 62% / 47% / 24% of 1st /
2nd / 3rd class to survive, and the real rates were 63% / 47% / 24%.

### Select a cohort

Filter chips (class, sex, title, port) and range sliders (age, fare per person) dim everyone
outside the cohort and compare its average model probability with its real survival rate. For
example, **3rd-class women** are 144 passengers: the model expects 52%, 50% really survived, and
at the 50% cut-off it gets 111 of the 144 right. You can also search any passenger by name or
`PassengerId`.

### One passenger, explained

Click anyone (or find them by name) to see their probability as a **SHAP waterfall**. It starts
from the model's average passenger and shows how much each fact about this person pushed the
probability up or down, in percentage points. The bars add up exactly to their prediction.

<p align="center"><img src="../screenshots/titanic/voyage-explain.png" alt="Laina Heikkinen, a 3rd-class woman who survived: 57% probability, raised by sex and title, lowered by ticket class" width="850"></p>

Laina Heikkinen (#3), a 26-year-old travelling alone in 3rd class, gets **57%**. Being a woman
(+14.4 pts) and a "Miss" (+12.1 pts) raise her chance; her 3rd-class ticket costs her 15.5
points. She survived.

### What if…?

**What if…? Edit this passenger** turns any passenger into a live what-if form, marked by a ★ on
the ship. You can also board one of the personas: Jack and Rose (fictional, inspired by the
1997 film), a 3rd-class mother of four, an Irish girl boarding at Queenstown, and others. The
model re-scores and re-explains every change as you type.

<p align="center"><img src="../screenshots/titanic/voyage-whatif.png" alt="What if Laina Heikkinen had travelled 1st class: 57% becomes 90% (+33.2 pts)" width="480"></p>

Move Laina to 1st class and nothing else changes: **57% → 90% (+33.2 pts)**.

---
