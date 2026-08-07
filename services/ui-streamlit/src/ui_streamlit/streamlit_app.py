"""
streamlit_app.py
-----------------

The actual Streamlit UI, rerun by `streamlit run` on every interaction (see app.py
for the launcher). Login (DEV_MODE, the shared admin key, or Logto OIDC) gates a
scenario switcher, which renders a generic tabular_ml form (driven entirely by the
selected scenario's feature_columns/feature_schema — no scenario-specific form code)
or a conversational_rag chat view. Scenario metadata/entitlements always come from
platform-registry's API, never from scenarios/*.yaml directly.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from ai_circus_shared.entitlements import PlatformRegistryClient, ScenarioSummary

from ui_streamlit import get_env_config
from ui_streamlit.core.api_client import chat, predict
from ui_streamlit.core.auth import (
    Identity,
    admin_identity,
    build_authorize_url,
    dev_identity,
    exchange_code,
    identity_from_claims,
)

st.set_page_config(page_title="ai-circus-framework", page_icon="🎪", layout="wide")

config = get_env_config()


def _login_screen() -> None:
    st.title("🎪 ai-circus-framework")

    if config.DEV_MODE.lower() == "true":
        st.warning("DEV_MODE is on — this bypasses real login. Never enable it beyond local iteration.")
        org_id = st.text_input("Org id", value=config.DEV_ORG_ID)
        roles_input = st.text_input(
            "Roles (comma-separated)", value="scenario:churn,scenario:docs_rag,scenario:ai_circus_reference"
        )
        if st.button("Log in (dev)"):
            roles = [r.strip() for r in roles_input.split(",") if r.strip()]
            st.session_state["identity"] = dev_identity(org_id, roles)
            st.rerun()
        return

    with st.expander("Admin key login"):
        st.caption("Resolves to the admin tenant, auto-entitled to every scenario. Works in any environment.")
        admin_key = st.text_input("Admin key", type="password")
        if st.button("Log in as admin") and admin_key:
            st.session_state["identity"] = admin_identity(admin_key)
            st.rerun()

    if not (
        config.LOGTO_ISSUER and config.LOGTO_CLIENT_ID and config.LOGTO_REDIRECT_URI and config.LOGTO_CLIENT_SECRET
    ):
        st.warning("LOGTO_ISSUER/LOGTO_CLIENT_ID/LOGTO_CLIENT_SECRET/LOGTO_REDIRECT_URI not set — Logto login is unavailable, but admin-key login above still works.")
        return

    query_params = st.query_params
    if "code" in query_params and "oidc_state" in st.session_state:
        tokens = exchange_code(
            issuer=config.LOGTO_ISSUER,
            client_id=config.LOGTO_CLIENT_ID,
            client_secret=config.LOGTO_CLIENT_SECRET.get_secret_value(),
            redirect_uri=config.LOGTO_REDIRECT_URI,
            code=query_params["code"],
        )
        # A real deployment should validate the returned token via
        # ai_circus_shared.auth.validate_token before trusting its claims.
        st.session_state["identity"] = identity_from_claims(tokens, str(tokens["access_token"]))
        st.query_params.clear()
        st.rerun()
        return

    url, state = build_authorize_url(
        issuer=config.LOGTO_ISSUER,
        client_id=config.LOGTO_CLIENT_ID,
        redirect_uri=config.LOGTO_REDIRECT_URI,
        resource=config.LOGTO_API_RESOURCE_INDICATOR or "",
    )
    st.session_state["oidc_state"] = state
    st.link_button("Log in", url)


def _render_chat(base_url: str, scenario: ScenarioSummary, identity: Identity, state_key: str) -> None:
    history: list[dict[str, str]] = st.session_state.setdefault(state_key, [])
    for turn in history:
        st.chat_message(turn["role"]).write(turn["content"])

    message = None
    if scenario.sample_questions and not history:
        st.caption("Try asking:")
        cols = st.columns(len(scenario.sample_questions))
        for col, question in zip(cols, scenario.sample_questions, strict=True):
            if col.button(question, key=f"{state_key}_sample_{question}"):
                message = question

    message = message or st.chat_input("Ask a question...")
    if message:
        st.chat_message("user").write(message)
        result = chat(base_url, scenario.slug, message, history, identity.access_token)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": result["reply"]})
        st.chat_message("assistant").write(result["reply"])
        sources = result.get("sources")
        if sources:
            with st.expander("Sources"):
                for source in sources:
                    st.caption(f"{source['source']} (score={source['score']:.2f})")
        elif sources is not None:
            st.caption("(answered directly, without consulting the documents)")


def _feature_input(feature: str, spec: dict[str, Any]) -> Any:
    """Render one form widget from a feature_schema entry (see libs/shared/scenario_schema.py)."""
    if spec["type"] == "numeric":
        return st.number_input(
            feature,
            min_value=float(spec["min"]),
            max_value=float(spec["max"]),
            value=float(spec["default"]),
            step=float(spec.get("step", 1.0)),
        )
    options = spec["options"]
    return st.selectbox(feature, options, index=options.index(spec["default"]))


def _render_tabular_ml(scenario: ScenarioSummary, identity: Identity) -> None:
    st.header(f"{scenario.icon} {scenario.title}")
    st.caption(scenario.description)

    feature_columns = scenario.feature_columns or []
    feature_schema = scenario.feature_schema or {}
    columns = st.columns(2)
    record: dict[str, Any] = {}
    for i, feature in enumerate(feature_columns):
        with columns[i % 2]:
            record[feature] = _feature_input(feature, feature_schema[feature])

    if st.button(f"Run {scenario.title}"):
        result = predict(config.PREDICTION_URL, scenario.slug, [record], identity.access_token)
        prediction = result["predictions"][0]
        if scenario.task_type == "regression":
            units = f" {scenario.target_units}" if scenario.target_units else ""
            st.metric("Prediction", f"{prediction['prediction']:.2f}{units}")
        else:
            st.metric("Probability", f"{prediction['prediction']:.1%}")
        st.bar_chart(prediction["contributions"])

    st.divider()
    st.subheader("💬 Ask about this data")
    _render_chat(config.ASSISTANT_URL, scenario, identity, f"{scenario.slug}_chat")


def _render_conversational_rag(scenario: ScenarioSummary, identity: Identity) -> None:
    st.header(f"{scenario.icon} {scenario.title}")
    st.caption(scenario.description)
    _render_chat(config.RAG_AGENT_URL, scenario, identity, f"{scenario.slug}_chat")


def main() -> None:
    """Render the login screen, or the scenario switcher + selected scenario's view."""
    identity: Identity | None = st.session_state.get("identity")
    if identity is None:
        _login_screen()
        return

    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    scenarios = registry.list_scenarios(org_id=identity.org_id)
    if not scenarios:
        st.info("No scenarios are assigned to your account yet. Contact your admin.")
        return

    labels = [f"{s.icon} {s.title}" for s in scenarios]
    with st.sidebar:
        st.title("🎪 ai-circus-framework")
        selected = st.radio("Scenario", labels)
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()

    if selected is None:
        return
    scenario = scenarios[labels.index(selected)]
    if scenario.kind == "tabular_ml":
        _render_tabular_ml(scenario, identity)
    else:
        _render_conversational_rag(scenario, identity)


main()
