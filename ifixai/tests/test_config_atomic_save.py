"""Failed wizard saves must retain the previous reusable configuration."""

import errno
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ifixai.cli.config_file import RunConfig, load_config, write_config

_LIMITED_SAVE = '''
import json, resource, signal, sys
from pathlib import Path
from ifixai.cli.config_file import RunConfig, write_config
config = RunConfig(provider="mock", fixture="default", name="new configuration " * 30)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (128, 128))
try:
    write_config(config, Path(sys.argv[1]))
except OSError as exc:
    print(json.dumps({"errno": exc.errno}))
else:
    print(json.dumps({"errno": None}))
'''


def _config_paths(tmp_path, linked):
    project = tmp_path / "project"
    project.mkdir()
    requested = project / "ifixai.yaml"
    if not linked:
        return project, requested, requested
    target = tmp_path / "shared" / "real.yaml"
    target.parent.mkdir()
    intermediate = tmp_path / "shared" / "linked.yaml"
    try:
        intermediate.symlink_to(target.name)
        requested.symlink_to(intermediate)
    except OSError:
        pytest.skip("This host does not allow fixture symlinks")
    return project, requested, target


@pytest.mark.parametrize("linked", [False, True])
def test_native_failed_save_retains_config_and_can_retry(tmp_path, linked):
    pytest.importorskip("resource")
    project, requested, target = _config_paths(tmp_path, linked)
    previous = RunConfig(provider="mock", fixture="default", name="previous")
    original = previous.to_yaml().encode()
    target.write_bytes(original)
    target.chmod(0o640)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", _LIMITED_SAVE, str(project)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=True,
    )
    assert json.loads(result.stdout)["errno"] == errno.EFBIG
    assert target.read_bytes() == original
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not list(tmp_path.rglob(".ifixai-report-*.tmp"))
    if linked:
        assert requested.is_symlink()
    replacement = RunConfig(provider="mock", fixture="default", name="retry")
    assert write_config(replacement, project) == requested
    assert load_config(project) == replacement
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    if linked:
        assert requested.is_symlink()


@pytest.mark.parametrize("linked", [False, True])
def test_successful_save_keeps_requested_path_and_target_mode(tmp_path, linked):
    project, requested, target = _config_paths(tmp_path, linked)
    target.write_text("provider: mock\n", encoding="utf-8")
    target.chmod(0o600)
    config = RunConfig(provider="mock", fixture="default", name="saved")
    assert write_config(config, project) == requested
    assert load_config(project) == config
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    if linked:
        assert requested.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX umask and mode bits")
def test_new_config_keeps_normal_creation_umask(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from ifixai.cli.config_file import RunConfig, write_config
os.umask(0o077)
write_config(RunConfig(provider="mock"), Path(sys.argv[1]))
'''
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=True,
    )
    assert stat.S_IMODE((tmp_path / "ifixai.yaml").stat().st_mode) == 0o600
    assert load_config(tmp_path) == RunConfig(provider="mock")
