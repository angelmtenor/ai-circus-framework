# 💸 Follow the Money — transaction monitoring for a bank consortium - What a score is — and is not

Source: docs/scenarios/aml_money_trail.md (this repository)

A score is *resemblance to the behaviour of holders involved in laundering*. It is a case for an
analyst to review, never a conclusion: receivers can be unwitting, and everything here is
simulated. Three properties of the data are stated on the tab and in the chat context:

1. **Generator artefacts.** ~87 % of laundering transfers go by ACH against 12 % of all transfers,
   laundering never changes currency, and the schemes follow eight textbook shapes. The AUC is a
   property of this simulation, not of what a real bank would achieve.
2. **A partial view.** The consortium sees only transfers that touch a member bank. 440 more holders
   launder only through transfers it cannot see and are labelled clean.
3. **A small base rate.** 3.7 % positives: a 20 % probability is already a strong outlier, so the
   review tiers are set from the out-of-fold scores, not from 50 %.
