import pytest

from ifixai.cli.init import detect_available_providers


@pytest.mark.parametrize('secret,present', [(None, False), ('', False), ('owned-secret', True)])
def test_init_requires_both_aws_credential_variables(monkeypatch, secret, present):
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'owned-id')
    if secret is None:
        monkeypatch.delenv('AWS_SECRET_ACCESS_KEY', raising=False)
    else:
        monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', secret)
    assert (('bedrock', 'AWS_ACCESS_KEY_ID') in detect_available_providers()) is present
