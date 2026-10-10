import click
from click.testing import CliRunner

from ifixai.quick_build import collect_quick_build_context


@click.command()
def collect():
    context = collect_quick_build_context()
    click.echo(f"TOOLS={context.tool_names};ROLES={context.role_names}")


def test_repeated_empty_tool_input_is_reprompted():
    result = CliRunner().invoke(collect, input=",\n,\nread\nuser\n")
    assert result.exit_code == 0, result.output
    assert "TOOLS=['read'];ROLES=['user']" in result.output


def test_empty_role_input_is_reprompted():
    result = CliRunner().invoke(collect, input="read\n,\nuser\n")
    assert result.exit_code == 0, result.output
    assert "TOOLS=['read'];ROLES=['user']" in result.output
