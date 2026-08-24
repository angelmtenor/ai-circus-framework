"""
- Title:    Chat-over-tabular-data grounding (system prompt construction)
- Author:   ai-circus-framework contributors

Ports smart-data-science's "Hybrid Assistant" chat-over-data idea onto llm-gateway,
dropping its regex/`exec()`-based code execution: this grounds a system prompt in the
scenario's description and the trained model's real metadata (including global SHAP
feature importance, computed once at training time — see training/core/training.py's
global_shap_importance). The actual chat loop lives in core/agent.py's create_agent
graph, run via api.py's AG-UI endpoint — still no arbitrary code execution, but the
agent can now reach real per-row data and live predictions through the narrow,
entitlement-checked tools in core/tools.py (calls to the sibling `prediction` service),
rather than only the static numbers baked into this prompt.
"""

from __future__ import annotations

from typing import Any

from ai_circus_shared.scenario_schema import ScenarioDefinition


def build_system_prompt(definition: ScenarioDefinition, metadata: dict[str, Any]) -> str:
    """Ground the assistant in the scenario's chat.context and the trained model's metadata.

    `definition.chat.context` is the human-authored domain framing (see
    scenarios/<slug>/scenario.yaml) — it supplements, not replaces, the real
    feature-list/accuracy grounding already available from the trained model.
    """
    feature_columns = ", ".join(str(c) for c in metadata["feature_columns"])
    if metadata["task_type"] == "regression":
        score_phrase = f"test R² of {float(metadata['test_score']):.3f}"
    else:
        score_phrase = f"test accuracy {float(metadata['test_score']):.2%}"

    # Absent on metadata written before this field existed (an untrained-since scenario
    # falling back to a cached artifact) — degrades gracefully rather than erroring.
    importance: list[dict[str, Any]] = metadata.get("global_feature_importance") or []
    importance_phrase = ""
    if importance:
        ranked = ", ".join(f"{item['feature']} ({item['importance']:.4f})" for item in importance)
        importance_phrase = f" Ranked by global SHAP importance (most to least influential overall): {ranked}."

    return (
        f"You are a data analyst assistant for the '{definition.title}' scenario.\n"
        f"{definition.chat.context.strip()}\n\n"
        f"A {metadata['model_name']} model was trained on this data with {score_phrase}. "
        f"It predicts '{metadata['target']}' from these "
        f"features: {feature_columns}.{importance_phrase}\n\n"
        "Answer questions about the data, the model, and its predictions clearly and concisely — cite the SHAP "
        "importance numbers above when asked which features matter most, rather than guessing. If asked something "
        "outside this scope, say so plainly.\n\n"
        "A user message may include a block starting with '[Attached file: <name>]' — that is the real, "
        "already-extracted text of a file they just uploaded in this browser session (via OCR/text-extraction, "
        "never fabricated). Treat it as ground truth you have already read in full: answer questions about it "
        "directly, and never claim you lack access to it or ask the user to go check the file themselves. This "
        "applies even when the attachment's subject has nothing to do with this scenario's dataset — an attached "
        "file with real content in front of you is not the same as an out-of-scope question with nothing behind "
        "it.\n\n"
        "You have tools for real data, not just the summary above: get_dataset_sample for real rows — each row has "
        "every feature *and* the target together, so it's the right source for a multi-feature plot (e.g. a 3D "
        "scatter of the target against two features); get_predictions_vs_actuals for real held-out "
        "actual-vs-predicted values, metrics, and (in feature_values) real feature values aligned index-for-index "
        "with those actuals/predictions — use feature_values, not get_dataset_sample's separate row sample, to "
        "color or facet an actual-vs-predicted chart by a feature; and predict_records to run the model on "
        "hypothetical/what-if records. Call one of these before claiming you lack the data to answer, or before "
        "describing numbers in prose that these tools could fetch for real.\n\n"
        "If a render_chart or render_table tool is available and the question calls for showing a plot or tabular "
        "data, call it (using the real values from the tools above) instead of describing the data in prose."
    )
