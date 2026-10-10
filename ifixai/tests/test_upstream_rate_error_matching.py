from types import SimpleNamespace

import pytest

from ifixai.providers.base import (
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderTruncatedError,
    raise_if_choice_errored,
)


@pytest.mark.parametrize('code,message,error', [(503, 'Could not generate a response', ProviderOverloadedError), (400, 'Rejected by corporate policy', ProviderTruncatedError), (None, 'rate limit exceeded', ProviderRateLimitError), (429, 'Too many requests', ProviderRateLimitError)])
def test_upstream_error_type_does_not_match_rate_inside_other_words(code, message, error):
    choice = SimpleNamespace(finish_reason='error', error={'code': code, 'message': message})
    with pytest.raises(error):
        raise_if_choice_errored('owned', 'owned-endpoint', choice, 'partial')
