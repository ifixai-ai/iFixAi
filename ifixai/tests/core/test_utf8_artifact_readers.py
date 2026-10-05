import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ifixai.core.fixture_loader import load_fixture, resolve_fixture_path


def _read_in_non_utf8_process(operation, argument):
    script = """
import json, locale, sys
from ifixai.core.fixture_loader import load_fixture, validate_fixture
from ifixai.rules.loader import RuleLoader, load_inspection_definition
encoding = locale.getpreferredencoding(False)
if encoding.lower().replace('-', '') == 'utf8':
    print(json.dumps({'utf8_locale': True}))
    raise SystemExit(0)
operation, argument = sys.argv[1:]
if operation == 'fixture':
    fixture = load_fixture(argument)
    assert not validate_fixture(argument)
    result = fixture.metadata.name
elif operation == 'plan':
    result = RuleLoader().load_rules(argument).model_dump(mode='json')
else:
    result = load_inspection_definition(argument).model_dump(mode='json')
print(json.dumps({'encoding': encoding, 'result': result}, ensure_ascii=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, operation, argument],
        cwd=Path(__file__).parents[3],
        env={
            **os.environ,
            "LC_ALL": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "IFIXAI_TELEMETRY": "0",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    if data.get("utf8_locale"):
        pytest.skip("this platform cannot select a non-UTF-8 locale")
    return data["result"]


@pytest.mark.parametrize("name", ["healthcare", "openclaw_strict"])
def test_bundled_utf8_fixtures_load_outside_utf8_locale(name):
    expected = load_fixture(name).metadata.name
    assert (
        _read_in_non_utf8_process("fixture", str(resolve_fixture_path(name)))
        == expected
    )


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_utf8_fixture_text_is_preserved_outside_utf8_locale(tmp_path, suffix):
    raw = yaml.safe_load(resolve_fixture_path("healthcare").read_text(encoding="utf-8"))
    raw["metadata"]["name"] = "Café — 医療"
    path = tmp_path / f"fixture{suffix}"
    text = (
        yaml.safe_dump(raw, allow_unicode=True)
        if suffix == ".yaml"
        else json.dumps(raw, ensure_ascii=False)
    )
    path.write_text(text, encoding="utf-8")
    assert _read_in_non_utf8_process("fixture", str(path)) == raw["metadata"]["name"]


@pytest.mark.parametrize("operation", ["plan", "definition"])
def test_bundled_utf8_rule_text_is_preserved_outside_utf8_locale(operation):
    from ifixai.rules.loader import RuleLoader

    expected = RuleLoader().load_rules("B12").model_dump(mode="json")
    assert _read_in_non_utf8_process(operation, "B12") == expected
