
import click

from ifixai.core.fixture_loader import validate_fixture
from ifixai.harness.validator import LayoutValidationError, validate_layout


@click.command()
@click.argument("path", required=False)
def validate(path: str | None) -> None:
    if path is None:
        try:
            validated_ids = validate_layout()
        except LayoutValidationError as exc:
            click.echo(click.style(f"Layout validation failed: {exc}", fg="red"))
            raise SystemExit(1) from exc
        click.echo(click.style(f"Layout valid: {len(validated_ids)} tests", fg="green"))
        for test_id in validated_ids:
            click.echo(f"  - {test_id}")
        return

    errors = validate_fixture(path)

    if not errors:
        click.echo(click.style("Valid", fg="green"))
        return

    click.echo(click.style(f"Validation failed with {len(errors)} error(s):", fg="red"))
    click.echo()
    for index, error_msg in enumerate(errors, start=1):
        click.echo(f"  {index}. {error_msg}")
    click.echo()

    raise SystemExit(1)
