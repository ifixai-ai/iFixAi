# Self-check: auditing as a one-command habit

A self-check is a free, local run of the open core against your agent. It
gives a result in minutes. A self-check is **not certified and never carries a
badge**. Every result says so: the label is `self-check`, `certified` is
`false`, `badge` is `null`, and every verdict starts with "Self-check, not
certified". Certified results come only from iFixAi certified mode, which none
of the commands here can produce.

## One command

```bash
pipx run ifixai audit https://staging.example.com/v1 --yes
```

Or, with iFixAi installed (`pip install ifixai`): `ifixai audit <url> --yes`.

- The URL is an OpenAI-shaped chat endpoint (`POST <url>/chat/completions`) of
  a **test deployment**. `--yes` confirms that it has sandboxed backends and no
  real data. Without `--yes` the command asks, or refuses when nobody is at a
  terminal to answer.
- If the agent needs a key, put it in `IFIXAI_AGENT_KEY`. Do not put it in the
  URL; a URL with credentials in it is refused.
- The run uses the fast `smoke` suite, plus every finding pinned in
  `.ifixai/regressions.yaml`. Pick another suite with `--suite` (see
  `ifixai list suites`).
- The judge: with `--judge-provider` you choose it. Otherwise one is paired
  from the provider keys already in your environment, and it is never the agent
  judging itself. Those calls run on your key, at your cost. With no key at
  all, only structural checks run, and the verdict tells you how many checks it
  could not decide. `--judge-provider mock` makes no calls; use it to try the
  command out.
- Output goes to `.ifixai/self-check/`: the full report and `self-check.json`.
  `--sarif FILE` also writes SARIF 2.1.0, `--comment FILE` writes the
  pull-request comment, and `--json` prints the result.
- Exit codes: `0` nothing failed, `1` a check or a pinned regression failed,
  `2` the self-check could not reach a verdict. `--fail-on regressions` fails
  only when a pinned finding fails again; `--fail-on never` only reports.

The verdict is one sentence, filled in from counts by a template. No model
writes it:

> Self-check, not certified: 1 of 6 checks failed on 11 Nov 2026 (smoke suite): Privilege Escalation Detection.

A clean run says "no failures found". It never says "passed".

## Explain a failure, then pin it

```bash
ifixai findings explain .ifixai/self-check/<report>.json B08
ifixai findings add-regression .ifixai/self-check/<report>.json B08 --note "No admin grants on request"
```

`explain` is built from the evidence alone. It covers what the agent was
asked, what it should have done, what it did, why that counts as a failure,
and which framework controls the failure maps to. `add-regression` appends an
entry to `.ifixai/regressions.yaml`. Commit that file. From then on, every
`ifixai audit` re-runs that inspection whatever suite you choose, and fails if
it fails again.

The file can also hold `hosted:` entries. Those are findings from hosted iFixAi
audits, pinned through the hosted connector's `add-regression-test` tool. A
local self-check counts them and leaves them alone.

## Claude Code, Cursor and any MCP client

`ifixai mcp` is a local MCP server over stdio. It has four tools:
`audit_agent`, `list_findings`, `explain_failure` and `add_regression_test`.
They work on the project folder the client starts the server in.

Claude Code:

```bash
claude mcp add ifixai-local -- ifixai mcp
```

Cursor (`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json`):

```json
{ "mcpServers": { "ifixai-local": { "command": "ifixai", "args": ["mcp"] } } }
```

`audit_agent` refuses to run unless the client passes `confirm_sandboxed:
true`. The server tells the client to ask you first. The hosted iFixAi connector
is separate: it runs full audits on iFixAi's service for signed-in workspaces.

## GitHub

The self-check action lives in this repository at `actions/self-check`:

```yaml
permissions:
  contents: read
  security-events: write   # SARIF upload to code scanning
  pull-requests: write     # the verdict comment
steps:
  - uses: actions/checkout@v4
  - uses: ifixai-ai/iFixAi/actions/self-check@<ref>
    with:
      agent-url: ${{ vars.STAGING_AGENT_URL }}
      confirm-sandboxed: 'true'
      comment: 'true'
```

- Findings appear in the repository's Security tab under code scanning, in the
  `ifixai-self-check` category. Agent behaviour has no source line, so each
  alert points at line 1 of the fixture you used, `ifixai.yaml`, or
  `.ifixai/regressions.yaml`, in that order.
- With `comment: 'true'`, one comment per pull request carries the plain
  verdict and is updated on every push.
- The step fails according to `fail-on` (`any`, `regressions` or `never`). It
  fails after the upload and the comment, so you still see why.

## GitLab

Include `ci/gitlab/ifixai-self-check.gitlab-ci.yml` (or copy it in). Then set
`IFIXAI_AGENT_URL`, and set `IFIXAI_CONFIRM_SANDBOXED: "true"`. The job keeps
the report, `self-check.json`, the SARIF file and the comment as artifacts. With
`IFIXAI_MR_COMMENT_TOKEN` set, it also posts the verdict on the merge request.

## pre-commit

```yaml
- repo: <this repository>
  rev: <tag>
  hooks:
    - id: ifixai-regressions             # commit: checks the regressions file is well formed
    - id: ifixai-self-check              # push: runs the self-check
      args: [https://staging.example.com/v1, --yes]
```
