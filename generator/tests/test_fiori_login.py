from __future__ import annotations

from erp_trace_executor.tools.fiori.login import SAP_FIORI_LOGIN_URL, LoginInput


def test_login_input_defaults_to_sap_tour_url():
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    assert str(params.resolved_login_url()) == SAP_FIORI_LOGIN_URL


def test_login_input_keeps_base_url_compatibility():
    params = LoginInput.model_validate(
        {
            "base_url": "http://127.0.0.1:8000/index.html",
            "username": "buyer-a",
            "password": "secret",
        }
    )

    assert str(params.resolved_login_url()) == "http://127.0.0.1:8000/index.html"
