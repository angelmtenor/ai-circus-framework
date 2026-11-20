# Riverbend Water Authority: Drought Plan (illustrative)

> Fictional plan, inspired by the structure of typical drought plans, written for a reference implementation on synthetic data. It is not an operational document and declares nothing.

<!-- Generated from rulebook.yaml. Do not edit. -->

## Art. 1. Nature of this plan

**Art. 1.1** This plan is fictional and illustrative. It is inspired by the structure of typical drought plans and has no legal effect.

**Art. 1.2** The thresholds, reductions and caps below are configuration of a reference implementation, not decisions of any authority.

## Art. 2. Indicator

**Art. 2.1** The indicator is the storage of a supply system: the sum of the stored volumes of its reservoirs over the sum of their capacities, as a percentage.

**Art. 2.2** The indicator is evaluated once a day, on the latest published readings. The source publishes data unvalidated, so a reading flagged as needing review is shown next to the state, not hidden.

## Art. 3. States of drought

**Art. 3.1** Normal: system storage at or above 60 %.

**Art. 3.2** Pre-alert: system storage below 60 % and at or above 40 %.

**Art. 3.3** Alert: system storage below 40 % and at or above 25 %.

**Art. 3.4** Exceptional: system storage below 25 % and at or above 15 %.

**Art. 3.5** Emergency: system storage below 15 %.

## Art. 4. Changes of state

**Art. 4.1** The system moves to a worse state on the same day its system storage falls below the threshold of that state.

**Art. 4.2** The system moves to a better state only after 7 consecutive days with its system storage in a better state, and then to the worst of those days' states. Each further improvement needs a new run of days.

## Art. 5. Measures in state Normal

**Art. 5.1** No restrictions apply.

## Art. 6. Measures in state Pre-alert

**Art. 6.1** Awareness campaign for all sectors.

**Art. 6.2** Closer monitoring of storage and inflows.

## Art. 7. Measures in state Alert

**Art. 7.1** Agriculture: allocations reduced by 20 %.

**Art. 7.2** Industry: consumption reduced by 5 %.

**Art. 7.3** Urban supply: capped at 250 litres per person per day.

## Art. 8. Measures in state Exceptional

**Art. 8.1** Agriculture: allocations reduced by 40 %.

**Art. 8.2** Industry: consumption reduced by 15 %.

**Art. 8.3** Urban supply: capped at 230 litres per person per day.

## Art. 9. Measures in state Emergency

**Art. 9.1** Agriculture: allocations reduced by 75 %.

**Art. 9.2** Industry: consumption reduced by 25 %.

**Art. 9.3** Urban supply: capped at 200 litres per person per day.

## Art. 10. Review and human oversight

**Art. 10.1** Any operational decision that follows from a state, a forecast or a what-if requires a human decision by the responsible person.

**Art. 10.2** Forecasts and what-if results are decision support with a stated uncertainty. They never declare a state.
