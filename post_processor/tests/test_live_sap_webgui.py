from __future__ import annotations

from pathlib import Path

import pytest

from erp_sap_export.artifacts import load_env_file
from erp_sap_export.se16 import Se16Client, WebGuiCredentials, webgui_url_from_login_url


@pytest.mark.live_sap
def test_live_webgui_probe_reaches_se16() -> None:
    env = load_env_file(Path("configuration/.env"))
    credentials = WebGuiCredentials(
        username=env["SAP_USER_1_UN"],
        password=env["SAP_USER_1_PW"],
        webgui_url=webgui_url_from_login_url(env["SAP_URL"]),
    )

    result = Se16Client(credentials).probe(["CDHDR", "CDPOS"])

    assert result["webgui"] is True
    assert result["se16"] is True
    assert result["tables"]["CDHDR"]["selection_screen"] is True
    assert result["tables"]["CDPOS"]["selection_screen"] is True
