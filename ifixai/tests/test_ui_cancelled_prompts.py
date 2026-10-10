from types import SimpleNamespace

import click
import pytest
import questionary

from ifixai.cli import ui


@pytest.mark.parametrize('kind', ['select', 'checkbox', 'confirm', 'text'])
def test_cancelled_interactive_prompt_aborts_instead_of_accepting_default(monkeypatch, kind):
    monkeypatch.setattr(ui, 'is_interactive', lambda: True)
    monkeypatch.setattr(questionary, kind, lambda *args, **kwargs: SimpleNamespace(ask=lambda: None))
    with pytest.raises(click.Abort):
        if kind == 'select':
            ui.select('Choose provider', ['one', 'two'], default='one')
        elif kind == 'checkbox':
            ui.multiselect('Choose judges', ['one', 'two'])
        elif kind == 'confirm':
            ui.confirm('Run the paid audit now?', default=True)
        else:
            ui.text('Endpoint', default='http://localhost')


def test_explicit_negative_confirmation_is_retained(monkeypatch):
    monkeypatch.setattr(ui, 'is_interactive', lambda: True)
    monkeypatch.setattr(questionary, 'confirm', lambda *args, **kwargs: SimpleNamespace(ask=lambda: False))
    assert ui.confirm('Run now?', default=True) is False
