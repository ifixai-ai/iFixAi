import pytest

from ifixai.providers.base import ProviderResponseError, is_fatal_provider_error


@pytest.mark.parametrize('detail', ['Invalid max_tokens value 10401', 'Invalid seed value 4030'])
def test_digits_inside_request_values_are_not_auth_statuses(detail):
    assert not is_fatal_provider_error(ProviderResponseError(details=detail))


@pytest.mark.parametrize('detail', ['HTTP 401: rejected', 'status_code=403', 'invalid api key'])
def test_real_auth_failure_markers_remain_fatal(detail):
    assert is_fatal_provider_error(ProviderResponseError(details=detail))
