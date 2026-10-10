import pytest
from click.testing import CliRunner

from ifixai.cli import setup_cmd
from ifixai.cli.config_file import load_config
from ifixai.cli.init import detect_available_providers


@pytest.mark.parametrize('provider,primary,alias', [('gemini', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'), ('huggingface', 'HF_TOKEN', 'HUGGINGFACE_API_TOKEN')])
def test_setup_recognizes_and_saves_supported_credential_alias(monkeypatch, tmp_path, provider, primary, alias):
    monkeypatch.delenv(primary, raising=False)
    monkeypatch.setenv(alias, 'owned-alias-key')
    assert (provider, alias) in detect_available_providers()
    assert setup_cmd._missing_keys([('system', provider)]) == []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup_cmd.ui, 'is_interactive', lambda: True)
    monkeypatch.setattr(setup_cmd.ui, 'select', lambda message, choices, **kwargs: provider if message.startswith('What is the system') else choices[0])
    monkeypatch.setattr(setup_cmd.ui, 'multiselect', lambda *args, **kwargs: [])
    monkeypatch.setattr(setup_cmd.ui, 'text', lambda *args, **kwargs: '')
    monkeypatch.setattr(setup_cmd.ui, 'confirm', lambda message, **kwargs: message.startswith('Save to'))
    result = CliRunner().invoke(setup_cmd.setup)
    assert result.exit_code == 0, result.exception
    assert load_config(tmp_path).api_key_env == alias
