from ifixai.cli import ui


def test_missing_input_stream_uses_noninteractive_fallback(monkeypatch):
    monkeypatch.setattr(ui, 'supports_color', lambda: True)
    monkeypatch.setattr(ui, '_questionary_available', lambda: True)
    monkeypatch.setattr(ui.sys, 'stdin', None)
    assert ui.is_interactive() is False
    assert ui.select('Pick', ['one', 'two'], default='two') == 'two'
