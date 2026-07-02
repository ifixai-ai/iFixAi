# Thin PowerShell shim: locate a Python 3 and hand off to the cross-platform
# provisioner, hooks/bootstrap.py (the real logic lives there). This is the
# native-Windows path — when Git Bash isn't installed, your agent's Bash tool and
# hooks run under PowerShell, so the skill's Step 0 provisions with:
#   powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}\hooks\bootstrap.ps1"
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'bootstrap.py'

foreach ($py in 'py', 'python', 'python3') {
  $cmd = Get-Command $py -ErrorAction SilentlyContinue
  # Skip the Microsoft Store App-Execution-Alias stubs in WindowsApps: they exist
  # on a Python-less box, print "Python was not found…", and do nothing — so fall
  # through to the actionable error below instead of treating them as a real Python.
  if ($cmd -and $cmd.Source -notlike '*\WindowsApps\*') {
    # The `py` launcher needs -3 to guarantee a Python 3 interpreter.
    if ($py -eq 'py') { & $cmd.Source -3 $script @args } else { & $cmd.Source $script @args }
    exit $LASTEXITCODE
  }
}

[Console]::Error.WriteLine('iFixAi: no Python 3 on PATH (tried py, python, python3) — the engine needs Python 3.10+.')
exit 1
