import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.providers.resolver import credential_env_vars


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("external_key", [True, False])
def test_standard_judge_pairing_uses_the_selected_sut(
    tmp_path, monkeypatch, interactive, external_key
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    for provider in PROVIDER_CHOICES:
        for key in credential_env_vars(provider):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "owned-synthetic-openai-key")
    if external_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "owned-synthetic-anthropic-key")
    args = ["run", "--dry-run", "--no-telemetry"]
    if not interactive:
        args += ["--provider", "openai"]
    # Actual Click guided prompts: no deployed endpoint; pick bare OpenAI;
    # synthetic key; no custom endpoint; no model override.
    result = CliRunner().invoke(
        ifixai_cli, args, input="n\nopenai\nowned-synthetic-openai-key\nn\nn\n"
    )
    if external_key:
        assert result.exit_code == 0, result.output
        assert "auto-paired judge provider 'anthropic' (SUT='openai')" in result.output
        assert "Dry run -- no API calls will be made" in result.output
    else:
        assert result.exit_code == 2, result.output
        assert "needs a second distinct-provider credential" in result.output
        assert "auto-paired judge provider 'openai'" not in result.output
