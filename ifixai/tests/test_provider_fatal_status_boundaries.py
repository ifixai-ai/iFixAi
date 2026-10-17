import pytest

from ifixai.providers.base import (
    ProviderResponseError,
    friendly_provider_message,
    is_fatal_provider_error,
)


@pytest.mark.parametrize('detail', ['Invalid max_tokens value 10401', 'Invalid seed value 4030'])
def test_digits_inside_request_values_are_not_auth_statuses(detail):
    assert not is_fatal_provider_error(ProviderResponseError(details=detail))


@pytest.mark.parametrize('detail', ['HTTP 401: rejected', 'status_code=403', 'invalid api key'])
def test_real_auth_failure_markers_remain_fatal(detail):
    assert is_fatal_provider_error(ProviderResponseError(details=detail))


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1/billing-assistant",
    "http://127.0.0.1/credit-advisor",
    "http://127.0.0.1/quota-monitor",
    "http://127.0.0.1/authentication-help",
])
def test_endpoint_names_do_not_make_a_malformed_reply_fatal(endpoint):
    error = ProviderResponseError(provider="http", endpoint=endpoint, details="Malformed reply")
    assert not is_fatal_provider_error(error)


@pytest.mark.parametrize("detail", ["HTTP 400: request id 9c3a401e", "judge at http://127.0.0.1:8401 failed", "request returned 403 characters"])
def test_status_digits_in_request_identity_do_not_reject_key_or_show_auth_banner(detail):
    assert not is_fatal_provider_error(ProviderResponseError(details=detail))
    message = friendly_provider_message(detail)
    assert message is None or not any(text in message.lower() for text in ["key was rejected", "access was forbidden"])
