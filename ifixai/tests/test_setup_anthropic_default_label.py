import pytest

from ifixai.cli import setup_cmd

adapter = pytest.importorskip('ifixai.providers.anthropic')


def test_setup_displays_the_actual_anthropic_provider_default(monkeypatch):
    offered = []

    def select(message, choices, **kwargs):
        offered.extend(choices)
        return kwargs['default']

    monkeypatch.setattr(setup_cmd.ui, 'select', select)
    assert setup_cmd._pick_model('anthropic', role='system under test') is None
    assert offered[0] == f'Provider default ({adapter.DEFAULT_MODEL})'
