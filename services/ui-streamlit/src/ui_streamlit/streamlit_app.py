"""
streamlit_app.py
-----------------

The actual Streamlit UI, rerun by `streamlit run` on every interaction (see app.py
for the launcher). Login (DEV_MODE or Logto OIDC) gates a scenario switcher, which
renders either the churn tabular_ml view or the docs_rag conversational view.
Scenario metadata/entitlements always come from platform-registry's API, never from
scenarios/*.yaml directly.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import streamlit as st
from ai_circus_shared.entitlements import PlatformRegistryClient

from ui_streamlit import get_env_config
from ui_streamlit.core.api_client import chat, predict
from ui_streamlit.core.auth import Identity, build_authorize_url, dev_identity, exchange_code, identity_from_claims

st.set_page_config(page_title="ai-circus-framework", page_icon="🎪", layout="wide")

config = get_env_config()


def _login_screen() -> None:
    st.title("🎪 ai-circus-framework")

    if config.DEV_MODE.lower() == "true":
        st.warning("DEV_MODE is on — this bypasses real login. Never enable it beyond local iteration.")
        org_id = st.text_input("Org id", value=config.DEV_ORG_ID)
        roles_input = st.text_input("Roles (comma-separated)", value="scenario:churn,scenario:docs_rag")
        if st.button("Log in (dev)"):
            roles = [r.strip() for r in roles_input.split(",") if r.strip()]
            st.session_state["identity"] = dev_identity(org_id, roles)
            st.rerun()
        return

    if not (
        config.LOGTO_ISSUER and config.LOGTO_CLIENT_ID and config.LOGTO_REDIRECT_URI and config.LOGTO_CLIENT_SECRET
    ):
        st.error("LOGTO_ISSUER/LOGTO_CLIENT_ID/LOGTO_CLIENT_SECRET/LOGTO_REDIRECT_URI must be set (or DEV_MODE=true).")
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


def _render_chat(base_url: str, identity: Identity, state_key: str) -> None:
    history: list[dict[str, str]] = st.session_state.setdefault(state_key, [])
    for turn in history:
        st.chat_message(turn["role"]).write(turn["content"])

    message = st.chat_input("Ask a question...")
    if message:
        st.chat_message("user").write(message)
        result = chat(base_url, message, history, identity.access_token)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": result["reply"]})
        st.chat_message("assistant").write(result["reply"])
        sources = result.get("sources")
        if sources:
            with st.expander("Sources"):
                for source in sources:
                    st.caption(f"{source['source']} (score={source['score']:.2f})")


def _render_churn(identity: Identity) -> None:
    st.header("📉 Customer Churn Prediction")
    col1, col2 = st.columns(2)
    with col1:
        credit_score = st.slider("Credit score", 300, 850, 650)
        age = st.slider("Age", 18, 92, 40)
        tenure = st.slider("Tenure (years)", 0, 10, 3)
        balance = st.number_input("Balance", 0.0, 300000.0, 50000.0)
    with col2:
        num_products = st.slider("Number of products", 1, 4, 2)
        has_cr_card = st.checkbox("Has credit card", value=True)
        is_active = st.checkbox("Is active member", value=True)
        salary = st.number_input("Estimated salary", 0.0, 250000.0, 75000.0)
    geography = st.selectbox("Geography", ["France", "Germany", "Spain"])

    if st.button("Predict churn risk"):
        record = {
            "CreditScore": credit_score,
            "Geography": geography,
            "Age": age,
            "Tenure": tenure,
            "Balance": balance,
            "NumOfProducts": num_products,
            "HasCrCard": int(has_cr_card),
            "IsActiveMember": int(is_active),
            "EstimatedSalary": salary,
        }
        result = predict(config.PREDICTION_URL, [record], identity.access_token)
        prediction = result["predictions"][0]
        st.metric("Churn probability", f"{prediction['probability']:.1%}")
        st.bar_chart(prediction["contributions"])

    st.divider()
    st.subheader("💬 Ask about this data")
    _render_chat(config.ASSISTANT_URL, identity, "churn_chat")


def _render_docs_rag(identity: Identity) -> None:
    st.header("💬 Ask Your Documents")
    _render_chat(config.RAG_AGENT_URL, identity, "docs_rag_chat")


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
        _render_churn(identity)
    else:
        _render_docs_rag(identity)


main()
