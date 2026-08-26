/**
 * Plain-language labels/explanations for the raw metric keys returned by prediction's
 * `/dataset/{slug}/evaluation` (see DatasetEvaluation.metrics in apiClient.ts) — used
 * by ExploreModelView's ModelPerformanceSection to render friendly StatTiles instead
 * of raw metric keys.
 */
export const METRIC_INFO: Record<string, { label: string; info: string }> = {
  mape: {
    label: "Avg. error (%)",
    info: "Mean Absolute Percentage Error — how far off predictions are, on average, as a percentage of the actual value. Lower is better. The headline metric to watch for regression scenarios.",
  },
  mae: {
    label: "Avg. error",
    info: "Mean Absolute Error — the average absolute difference between predicted and actual values, in the target's own units.",
  },
  rmse: {
    label: "RMSE",
    info: "Root Mean Squared Error — like average error, but penalizes big misses more heavily than small ones.",
  },
  r2: {
    label: "Fit quality (R²)",
    info: "The share of variation in the target the model explains, from 0 (no better than guessing the average) to 1 (perfect fit).",
  },
  accuracy: {
    label: "Accuracy",
    info: "The share of predictions that were correct.",
  },
  precision: {
    label: "Precision",
    info: "Of everything the model flagged as positive, the share that actually was.",
  },
  recall: {
    label: "Recall",
    info: "Of everything that was actually positive, the share the model caught.",
  },
  f1: {
    label: "F1 score",
    info: "A single balance of precision and recall — high only when both are high.",
  },
  roc_auc: {
    label: "Ranking quality (AUC-ROC)",
    info: "The probability the model ranks a random positive case above a random negative one. 0.5 = random guessing, 1.0 = perfect ranking. The headline metric to watch for classification scenarios.",
  },
};

export const PRIMARY_METRIC: Record<string, string> = { regression: "mape", classification: "roc_auc" };
