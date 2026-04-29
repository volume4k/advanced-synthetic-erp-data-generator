from __future__ import annotations

import pytest

from erp_trace_executor.credentials import CredentialLookupError, EnvCredentialStore, load_env_credentials


def test_load_env_credentials_resolves_password_by_username(tmp_path):
    env_path = tmp_path / "sap.env"
    env_path.write_text(
        "\n".join(
            [
                "SAP_USER_1_UN=LEARN-901",
                "SAP_USER_1_PW=secret-901",
                "SAP_USER_2_UN=LEARN-902",
                "SAP_USER_2_PW=secret-902",
            ]
        ),
        encoding="utf-8",
    )

    store = load_env_credentials(env_path)

    assert store.password_for_username("LEARN-902") == "secret-902"


def test_load_env_credentials_ignores_comments_and_quotes(tmp_path):
    env_path = tmp_path / "sap.env"
    env_path.write_text(
        "\n".join(
            [
                "# SAP credentials",
                "SAP_USER_1_UN='LEARN-901'",
                'SAP_USER_1_PW="secret-901"',
            ]
        ),
        encoding="utf-8",
    )

    store = load_env_credentials(env_path)

    assert store.password_for_username("LEARN-901") == "secret-901"


def test_env_credentials_report_missing_username():
    store = EnvCredentialStore({"LEARN-901": "secret-901"})

    with pytest.raises(CredentialLookupError, match="LEARN-902"):
        store.password_for_username("LEARN-902")
