# Reservoirs & Drought Watch: what is built, how it works, what is not - What is left

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

1. **Assistant on the tenant's artifacts.** Point the drought tools at the forecast the pipeline trained for the
   caller's org (and recompute the outlook from it) instead of the committed files; and, if wanted, a what-if
   re-forecast for the quantile model on the board.
2. ~~**UI:** the data-quality view, the drought state on the map, and the forecast's drivers on the board.~~ Built in
   the drought control room.
3. **A live evaluation** of the golden prompts against a real model, using `untraceable()` on the answers.
