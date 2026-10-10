import pytest

from ifixai.providers.base import ProviderEmptyContentError, ProviderResponseError
from ifixai.providers.minimax import _extract_response_text


@pytest.mark.parametrize('style,data', [('messages', {}), ('messages', {'content': 'invalid'}), ('chat_completions', {}), ('chat_completions', {'choices': []}), ('chat_completions', {'choices': [None]}), ('chat_completions', {'choices': [{'message': None}]}), ('chat_completions', {'choices': [{'message': {'content': 123}}]})])
def test_malformed_minimax_response_is_an_error_not_completed_empty_output(style, data):
    with pytest.raises(ProviderResponseError) as caught:
        _extract_response_text(data, style, 'owned-endpoint')
    assert type(caught.value) is ProviderResponseError


@pytest.mark.parametrize('style,data', [('messages', {'content': []}), ('chat_completions', {'choices': [{'message': {'content': ''}}]}), ('chat_completions', {'choices': [{'message': {'content': None}}]})])
def test_completed_empty_output_retains_empty_content_error(style, data):
    with pytest.raises(ProviderEmptyContentError):
        _extract_response_text(data, style, 'owned-endpoint')
