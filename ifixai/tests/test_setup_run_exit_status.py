import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from ifixai.cli import setup_cmd
from ifixai.cli.setup_cmd import setup


def test_setup_propagates_run_failure(monkeypatch):
    runner = CliRunner()
    main_path = str(Path(setup_cmd.__file__).with_name('main.py'))
    root = str(Path(setup_cmd.__file__).parents[2])
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([root, os.environ.get('PYTHONPATH', '')]))
    monkeypatch.setattr(sys, 'argv', [main_path, 'setup'])
    monkeypatch.setattr(setup_cmd.ui, 'is_interactive', lambda: True)
    monkeypatch.setattr(setup_cmd.ui, 'select', lambda message, choices, **kwargs: 'mock' if 'mock' in choices else choices[0])
    monkeypatch.setattr(setup_cmd.ui, 'text', lambda *args, **kwargs: '')
    monkeypatch.setattr(setup_cmd.ui, 'confirm', lambda *args, **kwargs: True)
    run = subprocess.run
    children = []

    def launch(command):
        child = run([sys.executable, "-m", "ifixai.cli.main", *command[1:], '--definitely-invalid-option'], capture_output=True, text=True)
        children.append(child)
        return child

    monkeypatch.setattr(setup_cmd.subprocess, 'run', launch)
    with runner.isolated_filesystem():
        result = runner.invoke(setup)
    assert result.exit_code == 2, result.exception
    assert len(children) == 1
    assert children[0].returncode == 2, children[0].stdout + children[0].stderr
