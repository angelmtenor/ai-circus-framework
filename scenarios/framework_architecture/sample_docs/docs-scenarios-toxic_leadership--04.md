# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - The Leadership check

Source: docs/scenarios/toxic_leadership.md (this repository)

Below the tower: describe what someone with power over the team's work — a manager, a
salesperson, a self-styled expert — **does** (no names) and three readers answer side by side.

- **An LLM coach** (the platform's active model, through llm-gateway) reads the description
  against a research-based rubric and answers with a verdict, a balance from toxic to great,
  every behaviour it found **quoted from your text** (each quote is checked; one that isn't
  verbatim is marked), and practical advice.
- **The TF-IDF model** and **the transformer challenger** score the same text as a review, with
  the words highlighted.

The rubric lives in the scenario YAML (`rubric_check`). The positive side follows **Google's
Project Oxygen** (coaching, empowering without micromanaging, psychological safety, clear
vision, technical skill to advise the team, strong decisions, honesty). The negative side
follows the **Toxic Leadership Scale** (Schmidt, 2008: abusive, authoritarian, narcissistic,
self-promoting, unpredictable) plus the patterns engineers report most: dishonesty and
credential inflation, blame-shifting, offloading their own job onto the team, overloading the
team, spreading falsehoods about it, no technical depth or curiosity, cornering people who
disagree, and disengagement.

<p align="center"><img src="../screenshots/toxic_leadership/leadership-check.png" alt="Leadership check: the LLM coach rates the LinkedIn Visionary description Toxic management, citing dishonesty, no technical depth, blame-shifting and credit-taking with verified quotes and advice; beside it, TF-IDF 32% and transformer 64%" width="900"></p>

It assesses the **behaviour described, never a person**: the UI says so, the prompt forbids
names and diagnoses and treats the description as data (never as instructions), and nothing
typed is stored. The two kinds of reader disagree in instructive ways. The models learned how
anonymous *reviewers* write, so a carefully worded description can score low even when the
behaviour is clearly toxic.

---
