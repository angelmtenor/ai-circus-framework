# Reservoirs & Drought Watch: what is built, how it works, what is not - What is left

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

1. ~~**Assistant on the tenant's artifacts.**~~ Built: the drought tools, the control room and the bulletin use the
   forecast trained for the caller's organisation and recompute the outlook from it (`DroughtToolkit.with_forecast`).
   Still open, if wanted: a what-if re-forecast for the quantile model on the board.
2. ~~**UI:** the data-quality view, the drought state on the map, and the forecast's drivers on the board.~~ Built in
   the drought control room.
3. **A live evaluation** of the golden prompts against a real model, using `untraceable()` on the answers.
