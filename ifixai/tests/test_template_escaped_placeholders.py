import pytest

from ifixai.utils.template_renderer import (
    MissingPlaceholderError,
    extract_placeholders,
    render,
)


@pytest.mark.parametrize(
    "template,expected",
    [
        ("Literal {{example}}; real {name}", {"name"}),
        ("{{{{field}}}}", set()),
        ("{{{name}}}", {"name"}),
        ("{value!r:>12}", {"value"}),
        ("{value:{width}.{precision}f}", {"value", "width", "precision"}),
    ],
)
def test_extracted_keys_follow_actual_formatter_fields(template, expected):
    assert extract_placeholders(template) == expected


def test_literal_field_does_not_require_fixture_value():
    assert (
        render("Literal {{example}}; real {name}", {"name": "Rudy"})
        == "Literal {example}; real Rudy"
    )
    with pytest.raises(MissingPlaceholderError):
        render("Literal {{example}}; real {name}", {})
