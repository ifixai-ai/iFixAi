import click
import pytest

from ifixai.cli.scaffold import MD_BEGIN, MD_END, _splice_agents_md, _strip_agents_md


@pytest.mark.parametrize('operation', [_splice_agents_md, _strip_agents_md])
@pytest.mark.parametrize('text', [MD_END + '\nkeep this\n' + MD_BEGIN, MD_BEGIN + MD_BEGIN + MD_END, MD_BEGIN + MD_END + MD_END])
def test_malformed_managed_regions_are_rejected(operation, text):
    with pytest.raises(click.ClickException, match='marker'):
        operation(text, 'replacement') if operation is _splice_agents_md else operation(text)


def test_valid_region_preserves_surrounding_content():
    text = 'before\n' + MD_BEGIN + '\nold\n' + MD_END + '\nafter\n'
    updated = _splice_agents_md(text, 'new')
    assert updated == 'before\n' + MD_BEGIN + '\nnew\n' + MD_END + '\nafter\n'
    assert _strip_agents_md(updated) == 'before\nafter\n'
