"""``ifixai install`` — scaffold the ``/ifixai`` slash command into coding agents.

One host-neutral operator playbook (``ifixai/scaffold/playbook.md``) is fanned out
into each agent's native command file, spec-kit style. The generated body tells the
agent to act as the operator and run the existing ``ifixai run`` CLI; no server, no
new protocol.

The command name defaults to ``ifixai-skill`` (avoiding the Claude Code marketplace
plugin, which already owns ``/ifixai``). Pass ``--name ifixai`` to get the bare form.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import click

PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "scaffold" / "playbook.md"

DESCRIPTION = "Run iFixAi's operational-misalignment diagnostic on your own agent."
DEFAULT_COMMAND_NAME = "ifixai-skill"
# A command name becomes a file name and (for Zed) a directory name, so keep it to
# safe characters and reject anything that could traverse the path.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Markers wrap the managed region so re-runs replace cleanly and --revert can find
# (and only touch) what we wrote. Markdown uses HTML comments; TOML uses # comments.
MD_BEGIN = "<!-- IFIXAI:BEGIN (managed by `ifixai install`; edits here are overwritten) -->"
MD_END = "<!-- IFIXAI:END -->"
TOML_BEGIN = "# IFIXAI:BEGIN (managed by `ifixai install`; edits here are overwritten)"
TOML_END = "# IFIXAI:END"


@dataclass(frozen=True)
class AgentTarget:
    slug: str
    label: str
    scope: str  # "project" (under --dir / cwd) or "user" (under home)
    rel_path: str  # path relative to the scope root; "{name}" is the command name
    fmt: str  # "markdown" | "toml" | "agents_md"
    frontmatter: str = ""  # markdown YAML frontmatter ("{name}" substituted), no newlines around it


# The verified per-agent entry points (native command path per agent).
REGISTRY: tuple[AgentTarget, ...] = (
    AgentTarget(
        "claude", "Claude Code", "project", ".claude/commands/{name}.md", "markdown",
        frontmatter=f"description: {DESCRIPTION}",
    ),
    AgentTarget("cursor", "Cursor", "project", ".cursor/commands/{name}.md", "markdown"),
    AgentTarget("codex", "Codex CLI", "user", ".codex/prompts/{name}.md", "markdown"),
    AgentTarget(
        "vscode", "VS Code / Copilot", "project", ".github/prompts/{name}.prompt.md",
        "markdown", frontmatter=f"mode: agent\ndescription: {DESCRIPTION}",
    ),
    AgentTarget("windsurf", "Windsurf", "project", ".windsurf/workflows/{name}.md", "markdown"),
    AgentTarget("cline", "Cline", "project", ".clinerules/workflows/{name}.md", "markdown"),
    AgentTarget(
        "continue", "Continue", "project", ".continue/prompts/{name}.md", "markdown",
        frontmatter=f"name: {{name}}\ndescription: {DESCRIPTION}\ninvokable: true",
    ),
    AgentTarget("gemini", "Gemini CLI", "project", ".gemini/commands/{name}.toml", "toml"),
    AgentTarget(
        "zed", "Zed", "project", ".agents/skills/{name}/SKILL.md", "markdown",
        frontmatter=f"name: {{name}}\ndescription: {DESCRIPTION}",
    ),
    AgentTarget("agents", "AGENTS.md (universal bridge)", "project", "AGENTS.md", "agents_md"),
)

REGISTRY_BY_SLUG: dict[str, AgentTarget] = {t.slug: t for t in REGISTRY}


def _home_root() -> Path:
    """Root for user-scoped agents. ``IFIXAI_SCAFFOLD_HOME`` overrides for tests."""
    override = os.environ.get("IFIXAI_SCAFFOLD_HOME")
    return Path(override) if override else Path.home()


def _target_path(target: AgentTarget, project_root: Path, name: str) -> Path:
    root = project_root if target.scope == "project" else _home_root()
    return root / target.rel_path.replace("{name}", name)


def _load_playbook() -> str:
    if not PLAYBOOK_PATH.is_file():
        raise click.ClickException(
            f"Operator playbook missing at {PLAYBOOK_PATH}. The package is "
            "incomplete; reinstall ifixai."
        )
    body = PLAYBOOK_PATH.read_text(encoding="utf-8").strip()
    # The managed-block markers delimit our region via str.split; a marker inside
    # the body would corrupt the splice (and a user's AGENTS.md). The body is ours,
    # so this guards against a future edit, not untrusted input.
    for marker in (MD_BEGIN, MD_END, TOML_BEGIN, TOML_END):
        if marker in body:
            raise click.ClickException(
                f"Operator playbook contains a managed-block marker ({marker!r}); "
                "remove it so the scaffolder can wrap the body cleanly."
            )
    return body


def _render(target: AgentTarget, body: str, name: str) -> str:
    """Render the full file content for one agent from the shared playbook body."""
    if target.fmt == "toml":
        # Gemini custom command: the playbook is the model-facing prompt.
        quoted = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return (
            f"{TOML_BEGIN}\n"
            f'description = "{DESCRIPTION}"\n'
            f'prompt = """\n{quoted}\n"""\n'
            f"{TOML_END}\n"
        )
    # markdown (dedicated file) and agents_md share the same managed block shape.
    block = f"{MD_BEGIN}\n{body}\n{MD_END}\n"
    if target.fmt == "agents_md":
        return block
    front = (
        f"---\n{target.frontmatter.replace('{name}', name)}\n---\n\n"
        if target.frontmatter
        else ""
    )
    return f"{front}{block}"


def _is_managed(text: str, target: AgentTarget) -> bool:
    begin = TOML_BEGIN if target.fmt == "toml" else MD_BEGIN
    return begin in text


def _splice_agents_md(existing: str, body: str) -> str:
    """Insert or replace our marker block inside an existing AGENTS.md, leaving the
    rest of the user's file untouched."""
    has_begin, has_end = MD_BEGIN in existing, MD_END in existing
    if has_begin != has_end:
        raise click.ClickException(
            "AGENTS.md has an unbalanced iFixAi marker block (one of IFIXAI:BEGIN/END "
            "is missing). Fix or remove it by hand, then re-run."
        )
    block = f"{MD_BEGIN}\n{body}\n{MD_END}"
    if has_begin and has_end:
        pre = existing.split(MD_BEGIN, 1)[0]
        post = existing.split(MD_END, 1)[1]
        return f"{pre.rstrip()}\n\n{block}\n{post.lstrip()}".strip() + "\n"
    sep = "" if not existing.strip() else existing.rstrip() + "\n\n"
    return f"{sep}{block}\n"


def _strip_agents_md(existing: str) -> str:
    if MD_BEGIN not in existing or MD_END not in existing:
        return existing
    pre = existing.split(MD_BEGIN, 1)[0]
    post = existing.split(MD_END, 1)[1]
    return (pre.rstrip() + "\n" + post.lstrip()).strip() + "\n" if (pre.strip() or post.strip()) else ""


def _resolve_agents(agents: str | None, project_root: Path, name: str) -> list[AgentTarget]:
    if agents:
        if agents.strip().lower() == "all":
            return list(REGISTRY)
        chosen: list[AgentTarget] = []
        for raw in agents.split(","):
            slug = raw.strip().lower()
            if not slug:
                continue
            if slug not in REGISTRY_BY_SLUG:
                raise click.ClickException(
                    f"Unknown agent '{slug}'. Choices: "
                    f"{', '.join(t.slug for t in REGISTRY)}, all."
                )
            chosen.append(REGISTRY_BY_SLUG[slug])
        return chosen
    # No --agents: auto-detect by the presence of each agent's parent directory.
    detected = [
        t for t in REGISTRY
        if t.slug != "agents" and _target_path(t, project_root, name).parent.is_dir()
    ]
    return detected


@click.command()
@click.option(
    "--agents",
    default=None,
    help="Comma-separated agent slugs or 'all'. If omitted, auto-detects installed "
    "agents in the project. Slugs: " + ", ".join(t.slug for t in REGISTRY) + ".",
)
@click.option(
    "--name",
    default=DEFAULT_COMMAND_NAME,
    help=f"Slash-command name to scaffold (default: {DEFAULT_COMMAND_NAME}). The default "
    "avoids the Claude Code marketplace plugin's /ifixai; pass --name ifixai for the bare name.",
)
@click.option(
    "--dir", "project_dir", default=".", type=click.Path(file_okay=False),
    help="Project root for project-scoped command files (default: current directory).",
)
@click.option("--revert", is_flag=True, default=False, help="Remove what `ifixai install` wrote (restoring any .bak).")
@click.option("--list", "list_only", is_flag=True, default=False, help="List supported agents and their target paths, then exit.")
@click.option("--force", is_flag=True, default=False, help="Overwrite a pre-existing non-managed file without saving a .bak.")
def install(agents: str | None, name: str, project_dir: str, revert: bool, list_only: bool, force: bool) -> None:
    """Scaffold the /<name> slash command into your coding agents (default: /ifixai-skill)."""
    if not _NAME_RE.match(name):
        raise click.ClickException(
            f"Invalid --name '{name}'. Use letters, digits, '.', '_' or '-' "
            "(must start with a letter or digit)."
        )
    project_root = Path(project_dir).resolve()

    if list_only:
        click.echo(click.style(f"ifixai install — supported agents (command: /{name})", bold=True))
        for t in REGISTRY:
            click.echo(f"  {t.slug:10s} {t.label:28s} {_target_path(t, project_root, name)}")
        return

    targets = _resolve_agents(agents, project_root, name)
    if not targets:
        click.echo(click.style("No agents selected and none auto-detected.", fg="yellow"))
        click.echo("Pass --agents (e.g. --agents cursor,codex or --agents all), or --list.")
        return

    if revert:
        _do_revert(targets, project_root, name)
        return

    body = _load_playbook()
    written = 0
    for t in targets:
        path = _target_path(t, project_root, name)
        path.parent.mkdir(parents=True, exist_ok=True)

        if t.fmt == "agents_md":
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(_splice_agents_md(existing, body), encoding="utf-8")
            click.echo(f"  {click.style('updated', fg='green')} {t.label}: {path}")
            written += 1
            continue

        if path.is_file():
            existing = path.read_text(encoding="utf-8")
            if not _is_managed(existing, t) and not force:
                bak = path.with_suffix(path.suffix + ".bak")
                if bak.exists():
                    click.echo(f"  {click.style('kept', fg='yellow')} existing backup {bak.name} (not overwritten)")
                else:
                    bak.write_text(existing, encoding="utf-8")
                    click.echo(f"  {click.style('backed up', fg='cyan')} existing file -> {bak.name}")
        path.write_text(_render(t, body, name), encoding="utf-8")
        click.echo(f"  {click.style('wrote', fg='green')} {t.label}: {path}")
        written += 1

    click.echo()
    click.echo(click.style(f"Done. {written} file(s) written.", bold=True))
    click.echo(f"Open your agent and run /{name} to start a diagnostic.")


def _do_revert(targets: list[AgentTarget], project_root: Path, name: str) -> None:
    removed = 0
    for t in targets:
        path = _target_path(t, project_root, name)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")

        if t.fmt == "agents_md":
            stripped = _strip_agents_md(text)
            if stripped.strip():
                path.write_text(stripped, encoding="utf-8")
            else:
                path.unlink()
            click.echo(f"  {click.style('reverted', fg='yellow')} {t.label}: {path}")
            removed += 1
            continue

        if not _is_managed(text, t):
            click.echo(f"  {click.style('skipped', fg='yellow')} {t.label} (not managed by ifixai): {path}")
            continue
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.is_file():
            path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink()
            click.echo(f"  {click.style('restored', fg='green')} {t.label} from .bak: {path}")
        else:
            path.unlink()
            click.echo(f"  {click.style('removed', fg='yellow')} {t.label}: {path}")
        removed += 1

    click.echo()
    click.echo(click.style(f"Reverted {removed} file(s).", bold=True))
