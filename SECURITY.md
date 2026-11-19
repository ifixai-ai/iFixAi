# Security Policy

## Reporting a vulnerability

Please report suspected security vulnerabilities by email to **info@ime.life**. Do not open a public GitHub issue for a security-sensitive report.

Include in your report: a description of the issue, reproduction steps or proof-of-concept, the affected version or commit, and your assessment of impact. PGP encryption is not required.

We aim to acknowledge receipt within **7 business days** and to provide a remediation plan or explicit triage disposition within **30 business days** for reports rated critical and **90 days** for everything else. If the issue requires coordinated disclosure, we will agree on a date with you before publication.

## Scope

### In scope

- The `ifixai` Python package as published from this repository.
- The `ifixai` CLI entrypoint.
- The packaged fixtures under `ifixai/fixtures/`.

### Out of scope

- User-supplied fixtures and prompts. The project does not validate the content of arbitrary user fixtures beyond the JSON-schema structural checks in `ifixai/fixtures/schema.json`; treat your own fixtures as trusted input.
- Third-party LLM provider endpoints. Bugs in OpenAI / Anthropic / Gemini / etc. are their responsibility.
- The user's own API keys and credentials. The project reads these from environment variables and never persists them; if you commit a key to your own repository, rotate it immediately.
- Packages that depend on `ifixai`. Report via those projects' own channels.

## Secret handling

`ifixai/providers/secrets.py::scrub_secrets` redacts recognisable secret patterns from provider HTTP error logs before they are emitted — defence in depth for the one path where a request or response body could echo a credential. It is **not** applied to scorecards or resume checkpoints, which capture full model inputs and outputs verbatim (see the warning below); run manifests carry only run metadata, never model I/O, so no credential reaches them by construction. This is defence in depth, not a replacement for hygiene:

- **Never commit `.env` or any file containing API keys.** `.gitignore` excludes `.env` at the repo root; keep it that way. Use environment variables exclusively.
- **Prefer short-lived tokens over long-lived API keys** where the provider supports them.
- **Rotate any key that was exposed anywhere, including local shells on shared machines, log files, or screen-shares.** Rotation must be immediate — the blast radius of a leaked key is higher than the friction of rotating it.
- **Do not paste scorecard JSON into third-party web tools** (diagram renderers, pastebins, gists) without first confirming no inspection response contains sensitive material. Scorecards capture full model inputs and outputs; those may include content users did not intend to publish.
- **Resume checkpoints hold the same full inputs and outputs.** Every run writes `runs/<run_id>/checkpoint.json` (under `--reliability-out`) after each inspection, so `--resume <run_id>` can finish an interrupted run without re-billing the inspections already done. The file is written owner-only (`0600`) and stays on disk after the run finishes. Treat it like a scorecard: delete stale checkpoints, and do not run on a shared multi-user machine with untrusted co-tenants.

## Coding agent settings the CLI can change

Only when you pick **Continue with a free Pro audit** and then **Add it for me** in a terminal, `ifixai run` adds iFixAi's remote MCP server (`https://mcp.ifixai.ai/mcp`, named `ifixai`) to the coding agents it finds. It never does this in CI, without a terminal, or with `--no-promo`. The full list of agents, commands and files is in [docs/cli.md](docs/cli.md#free-pro-audit-offer).

- **Commands it runs:** each agent's own `mcp add` command from your `PATH` (for example `claude`, `gemini`, `copilot`), as a fixed argument list, never through a shell, with stdin closed and a 60-second limit. Their output is not shown or stored.
- **Files it writes:** Codex's `config.toml`, opencode's global `opencode.json` (or `opencode.jsonc`, `config.json`), and the JSON settings files of Devin (CLI and Desktop), Cursor, VS Code Copilot, Kiro, Junie, Warp and Antigravity under your home folder. A file is written only when the agent's own folder already exists. Only the `ifixai` entry is added; an existing `ifixai` entry and files that do not parse are left untouched.
- **Backup and atomic write:** before a file changes, it is copied to `<file>.ifixai-backup` beside it. The new content goes to a temporary file in the same folder and is renamed into place, keeping the old file's permissions, so the agent never reads a half-written file. To undo, restore the backup or remove the `ifixai` entry.
- **No credentials:** nothing written contains a key or token. Signing in to the server happens later, in your coding agent.

## Credential redaction coverage

The scrubber (`ifixai/providers/secrets.py::scrub_secrets`) is parametrically verified against every provider registered in `ifixai.providers.resolver.REGISTERED_PROVIDERS`. Each registered provider has at least one credential shape that is matched and replaced with a provider-tagged redaction token:

| Provider | Credential shape matched | Redaction token |
|---|---|---|
| `openai` | `sk-[A-Za-z0-9_-]{20,}` | `***REDACTED_OPENAI_KEY***` |
| `minimax` | `sk-cp-[A-Za-z0-9_-]+` | `***REDACTED_MINIMAX_KEY***` |
| any (JWT-shaped bearer tokens, incl. `minimax`) | three-segment JWT beginning with `eyJ` | `***REDACTED_JWT***` |
| `atlascloud` | `(ak|apikey)-[A-Za-z0-9_-]{20,}` | `***REDACTED_ATLASCLOUD_KEY***` |
| `openrouter` | `sk-or-[A-Za-z0-9_-]{20,}` (matched before `openai`) | `***REDACTED_OPENROUTER_KEY***` |
| `orcarouter` | `sk-orca-[A-Za-z0-9_-]{20,}` (matched before `openai`) | `***REDACTED_ORCAROUTER_KEY***` |
| `requesty` | `rqsty-[A-Za-z0-9+/=_-]{20,}` | `***REDACTED_REQUESTY_KEY***` |
| `cloudflare` | `cfut_`, `cfat_` or `cfk_` followed by `[A-Za-z0-9_-]{40,}`; tokens in the older unprefixed format have no fixed shape and are redacted only inside an `Authorization: Bearer` header | `***REDACTED_CLOUDFLARE_TOKEN***` |
| `vercel` | `vck_[A-Za-z0-9_-]{20,}` | `***REDACTED_VERCEL_KEY***` |
| `anthropic` | `sk-ant-[A-Za-z0-9_-]{20,}`, `anthropic_[A-Za-z0-9_-]{20,}` | `***REDACTED_ANTHROPIC_KEY***` |
| `gemini` | `AIzaSy[0-9A-Za-z_-]{33}` | `***REDACTED_GEMINI_KEY***` |
| `azure` | 32-char hex (`\b[a-fA-F0-9]{32}\b`) | `***REDACTED_AZURE_KEY***` |
| `bedrock` | `AKIA[0-9A-Z]{16}` (access key), `FwoGZXIvYXdz…` (session token) | `***REDACTED_AWS_KEY***`, `***REDACTED_BEDROCK_SESSION***` |
| `huggingface` | `hf_[A-Za-z0-9]{20,}` | `***REDACTED_HUGGINGFACE_KEY***` |
| `langchain` | No fixed key shape; caught by the generic bearer-token fallback | `***REDACTED_BEARER_TOKEN***` |
| `http` | No fixed key shape; caught by the generic bearer-token / `X-API-Key` fallback | `***REDACTED_BEARER_TOKEN***`, `***REDACTED_API_KEY***` |

Generic `Authorization: Bearer <token>` and `X-API-Key: <value>` headers are scrubbed case-insensitively regardless of provider. Providers using custom auth schemes that do not match one of the shapes above fall back to the bearer-token regex when the value appears in a standard HTTP header.

## Telemetry

iFixAi sends pseudonymous run telemetry — from the CLI, the plugin, and scaffolded agent skills alike, since all three run the same engine — so we can count how many people use it and whether they return. It is consent-first: disclosed in plain language on first run, off automatically in CI, and every opt-out is honored *before* any identifier is created or any event is sent.

**What is sent:** one `ifixai_started` event per run, plus `ifixai_completed` when a run produces a report, `ifixai_offer_shown` when the terminal shows the free Pro audit offer, and `ifixai_offer_accepted` when you pick it:

| Field | Example | Why |
|---|---|---|
| install id | random `uuid4`, stored at `${XDG_CONFIG_HOME:-~/.config}/ifixai/install-id` (`0600`) | unique installs + retention |
| event | `ifixai_started` / `ifixai_completed` / `ifixai_offer_shown` / `ifixai_offer_accepted` | run count + completion rate + how often the offer is seen and picked |
| where | `start` / `end`, on the two offer events only | where the offer was seen or picked |
| version | `3.0.2` | adoption per release |
| os | `Darwin` / `Linux` / `Windows` (from `platform.system()`) | platform mix |
| surface | `cli` / `plugin` / `skill` | separate the run interfaces |
| install_source | `pip` / `repo` | read downloads and runs as one funnel |
| timestamp | ISO-8601 UTC | retention across days |

**Never sent:** file contents, findings, grades, prompts, file/repo paths, hostnames, usernames, environment values, or your IP address. The collector (PostHog, US region) is configured to discard the request IP at ingestion, and every event also carries `$ip: null` and `$geoip_disable: true`. Inspect the exact payload anytime with `ifixai run --print-telemetry`.

**Opt out** — any one of these disables telemetry (checked before anything is created or sent):

- `--no-telemetry` (this run only)
- `IFIXAI_TELEMETRY=0` (also `false` / `no` / `off`)
- `DO_NOT_TRACK=1` (presence-based — any value)
- a file at `${XDG_CONFIG_HOME:-~/.config}/ifixai/telemetry-opt-out`
- automatically in CI (`CI`, or a known vendor variable such as `GITHUB_ACTIONS`)

**Data protection (GDPR / UK GDPR).** The install id is a persistent identifier, so this telemetry is *pseudonymous personal data*, not anonymous. Lawful basis: legitimate interest (Art 6(1)(f)) in understanding adoption and retention; the processing is minimal (eight fields, no model I/O, only classifications and never the paths they were derived from), IP-stripped, opt-out-respecting, and never used for profiling or advertising. **Retention: events are kept indefinitely**; we do not auto-delete. **Erasure:** run `ifixai run --show-id` to read your install id, then email **info@ime.life** to have its events deleted; deleting the local `install-id` file stops future linkage but does not remove rows already collected. Data controller: iFixAi (**info@ime.life**).

## Coordinated disclosure

We prefer coordinated disclosure. If you give us at least the window described under *Reporting a vulnerability* above before publishing, we will credit you in the release notes of the fixing version — unless you request otherwise.

## What we will not do

- Issue a bug bounty. The project is open source, operated as a community resource.
- Accept reports about behaviours that are explicitly documented as intentional in `README.md` or `docs/`.
- Accept reports whose sole impact is self-DoS via pathological fixture content.

## Versions

Security fixes are issued only for the most recent minor version. Users on older minor versions should upgrade.
