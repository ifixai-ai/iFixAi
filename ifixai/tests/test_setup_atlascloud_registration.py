from ifixai.cli.init import PROVIDER_ENV_KEYS, detect_available_providers
from ifixai.cli.model_catalog import default_model
from ifixai.cli.setup_cmd import _ALL_PROVIDERS, _PROVIDER_DESCRIPTIONS, _missing_keys


def test_setup_offers_registered_atlascloud_provider():
    assert 'atlascloud' in _ALL_PROVIDERS
    assert _PROVIDER_DESCRIPTIONS.get('atlascloud')
    assert default_model('atlascloud') == 'qwen/qwen3.5-flash'


def test_atlascloud_key_is_detected_and_missing_key_is_explained(monkeypatch):
    monkeypatch.delenv('ATLASCLOUD_API_KEY', raising=False)
    assert _missing_keys([('system', 'atlascloud')]) == [('system', 'atlascloud', 'ATLASCLOUD_API_KEY')]
    monkeypatch.setenv('ATLASCLOUD_API_KEY', 'owned-test-key')
    assert PROVIDER_ENV_KEYS['atlascloud'] == 'ATLASCLOUD_API_KEY'
    assert ('atlascloud', 'ATLASCLOUD_API_KEY') in detect_available_providers()
    assert _missing_keys([('system', 'atlascloud')]) == []
