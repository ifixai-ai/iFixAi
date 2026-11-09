"""Private, standalone reports of model reachability and tested policy repairs."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from ifixai.policy_lab.integrity import validate_exploration_bundle
from ifixai.policy_lab.models import ExplorationBundle, Witness
from ifixai.policy_lab.repair import RepairReport, validate_repair_report


def compare_policies(original: ExplorationBundle, candidate: ExplorationBundle) -> dict[str, Any]:
    original = validate_exploration_bundle(original)
    candidate = validate_exploration_bundle(candidate)
    if original.report.model_digest != candidate.report.model_digest:
        raise ValueError("policy comparison requires the exact same business model")
    if original.report.config_digest != candidate.report.config_digest:
        raise ValueError("policy comparison requires the exact same search limits")
    return {"original_bundle_digest": original.integrity_digest,
            "original_policy_digest": original.report.policy_digest,
            "candidate_policy_digest": candidate.report.policy_digest,
            "model_digest": candidate.report.model_digest,
            "policy_changed": original.report.policy_digest != candidate.report.policy_digest,
            "original_decision": original.report.decision, "candidate_decision": candidate.report.decision,
            "properties": {key: {"before": status, "after": candidate.report.property_status[key]}
                           for key, status in original.report.property_status.items()},
            "goals": {key: {"before": status, "after": candidate.report.goal_status[key]}
                      for key, status in original.report.goal_status.items()},
            "limitation": "Rechecks an explicit model under another policy; does not apply changes to a deployed system."}


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def render_policy_lab_html(bundle: ExplorationBundle, *, comparison: dict[str, Any] | None = None, repair: RepairReport | None = None) -> str:
    escape = html.escape
    report = bundle.report

    def witness_card(witness: Witness, ordinal: int) -> str:
        steps = []
        for step in witness.steps:
            changes = ", ".join(f"{key}: {str(step.before.facts[key]).lower()} → {str(value).lower()}"
                                for key, value in step.after.facts.items() if step.before.facts[key] != value)
            ledger = ", ".join(f"{key}={value}" for key, value in step.after.budgets.items())
            steps.append(f'<li><b>{escape(step.action_id)}</b><p>{escape(step.actor_id)} → {escape(step.tool)} · {escape(step.resource)}</p><p class="change">{escape(changes) or "Facts unchanged"}</p><small>Permission rules: {escape(str(step.permission_indices))} · cost {step.cost_units} units · {escape(ledger) or "No budget pool configured"}</small></li>')
        no_steps = '<li>Zero-step witness: the initial state already satisfies the objective.</li>'
        terminal = ", ".join(f"{key}={str(value).lower()}" for key, value in witness.terminal.facts.items())
        return f'<article class="witness" id="witness-{ordinal}"><h3>{escape(witness.kind)} · {escape(witness.objective_id)}</h3><p>{len(witness.steps)} steps · shortest discovered path</p><ol class="path">{"".join(steps) or no_steps}</ol><details><summary>Terminal fact snapshot</summary><p>{escape(terminal)}</p></details></article>'

    witnesses = [*report.safety_witnesses, *report.goal_witnesses]
    witness_links = {(w.kind, w.objective_id): f"#witness-{index}" for index, w in enumerate(witnesses)}
    properties = []
    for prop in bundle.model.properties:
        href = witness_links.get(("safety", prop.property_id))
        link = f'<a href="{href}">Counterexample</a>' if href else "—"
        properties.append(f'<tr><td><b>{escape(prop.property_id)}</b><br>{escape(prop.label)}</td><td>{escape(report.property_status[prop.property_id])}</td><td>{link}</td></tr>')
    goals = []
    for goal in bundle.model.goals:
        href = witness_links.get(("goal", goal.goal_id))
        link = f'<a href="{href}">Completion path</a>' if href else "—"
        goals.append(f'<tr><td><b>{escape(goal.goal_id)}</b><br>{escape(goal.label)}</td><td>{escape(report.goal_status[goal.goal_id])}</td><td>{link}</td></tr>')
    scenario = f'<section><h2>Policy comparison</h2><pre>{escape(_json(comparison))}</pre></section>' if comparison else ""
    repair_section = ""
    if repair:
        candidate_rows = "".join(f'<tr><td>{escape(str(item.removed_rule_indices))}</td><td>{escape(item.report.decision)}</td><td>{str(item.report.frontier_exhausted).lower()}</td></tr>' for item in repair.candidates)
        selected = ""
        if repair.selected_bundle:
            selection = repair.selected_bundle
            selected = f'<p class="result">Selected policy: {selection.report.decision} · all {len(selection.report.goal_status)} declared goals REACHABLE · frontier exhausted</p><p>Rule indices refer to the original ordered permission list. Candidate bundle is retained in selected-bundle.json.</p>'
        repair_section = f'<section><h2>Goal-preserving permission repair</h2><p class="result">{escape(repair.status)} · {len(repair.candidates)} candidates checked</p>{selected}<p>Minimum removal cardinality proven: {str(repair.minimality_proven).lower()}. Search allowance: {repair.max_removals} removals, {repair.max_candidates} candidates, {repair.transition_budget} transition examinations.</p><table><thead><tr><th>Removed rule indices</th><th>Decision</th><th>Complete frontier</th></tr></thead><tbody>{candidate_rows}</tbody></table><p>These are evaluated model changes. No deployed permission has been changed.</p></section>'
    gaps = [*report.cutoff_reasons, *report.coverage_issues]
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>iFixAi · Business Policy Lab</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0b1423;color:#e6edf5;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:48px 24px}}h1{{font-size:42px;line-height:1.15}}h2{{margin-top:0}}small,.eyebrow{{color:#a8b8cd}}section{{padding:24px;margin:20px 0;background:#122239;border:1px solid #294461;border-radius:14px}}.result{{font-weight:700;color:#8ee2cc}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #294461;vertical-align:top;overflow-wrap:anywhere}}a{{color:#91caff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}}.path{{list-style:none;padding:0}}.path li{{border-left:3px solid #5cc3ab;padding:12px 18px;margin:10px 0;background:#0c1a2d}}.change{{color:#a0dfbb}}article{{padding:12px 0;margin:14px 0}}article:target{{outline:2px solid #91caff;padding:16px;border-radius:8px}}input{{width:100%;padding:12px;background:#0b1423;color:#fff;border:1px solid #7394b5}}details p{{overflow-wrap:anywhere}}
</style></head><body><main><p class="eyebrow">IFIXAI / PROSPECTIVE BUSINESS ASSURANCE</p><h1>Business Policy Lab</h1><p class="result">{escape(report.decision)}</p><p>{escape(bundle.model.name)} · policy {escape(bundle.policy.name)}</p><p>Searches explicit business contracts for harmful compositions of individually permitted actions. Conclusions apply to this declared model; implementation behavior is verified separately.</p>
<section class="grid"><div><b>{report.states_discovered} distinct states</b><p>{report.states_expanded} fully expanded</p></div><div><b>{report.transitions_examined} transition examinations</b><p>{report.transitions_enabled} enabled edges</p></div><div><b>{len(report.safety_witnesses)} harm witnesses</b><p>{len(report.goal_witnesses)} legitimate completion witnesses</p></div></section>
<section><h2>Safety properties</h2><table><thead><tr><th>Forbidden condition</th><th>Model verdict</th><th>Evidence</th></tr></thead><tbody>{''.join(properties)}</tbody></table></section>
<section><h2>Legitimate workflows</h2><p>Removing every permission is not an accepted repair. Each declared completion goal must remain reachable.</p><table><thead><tr><th>Goal</th><th>Reachability</th><th>Evidence</th></tr></thead><tbody>{''.join(goals)}</tbody></table></section>
{repair_section}{scenario}<section><h2>Shortest discovered paths</h2><input id="search" aria-label="Search witness paths" placeholder="Filter by actor, tool, action or fact"><div id="witnesses">{''.join(witness_card(w,index) for index,w in enumerate(witnesses))}</div></section>
<section><h2>Exploration coverage</h2><p>Frontier exhausted: <b>{str(report.frontier_exhausted).lower()}</b>. Limits: depth {bundle.config.max_depth}, states {bundle.config.max_states}, examinations {bundle.config.max_transitions}.</p><ul>{''.join('<li>'+escape(gap)+'</li>' for gap in gaps) or '<li>No retained search cutoff or coverage issue.</li>'}</ul><p>Actions not observed enabled: {escape(', '.join(report.not_observed_action_ids)) or 'none'}.</p></section>
<section><h2>Portable evidence and boundaries</h2><pre>Bundle SHA-256: {bundle.integrity_digest}\nModel SHA-256: {report.model_digest}\nPolicy SHA-256: {report.policy_digest}</pre><ul>{''.join('<li>'+escape(limit)+'</li>' for limit in report.limitations)}</ul><p>Effects, costs and actors are explicit declarations. A checksum provides integrity, not truth or completeness of the business model.</p></section></main><script>const search=document.getElementById('search');const paths=Array.from(document.querySelectorAll('.witness'));search.addEventListener('input',()=>{{const q=search.value.toLowerCase();paths.forEach(path=>path.hidden=!path.textContent.toLowerCase().includes(q));}});function revealWitness(hash){{const target=document.getElementById(hash.slice(1));if(target&&target.classList.contains('witness')){{target.hidden=false;target.scrollIntoView();}}}}window.addEventListener('hashchange',()=>revealWitness(location.hash));document.querySelectorAll('a[href^="#witness-"]').forEach(link=>link.addEventListener('click',()=>revealWitness(link.getAttribute('href'))));revealWitness(location.hash);</script></body></html>'''


def render_policy_lab_markdown(bundle: ExplorationBundle, *, comparison: dict[str, Any] | None = None, repair: RepairReport | None = None) -> str:
    def safe(value: str) -> str:
        for char in ("\\", "`", "*", "_", "[", "]", "<", ">", "|", "#"):
            value = value.replace(char, "\\" + char)
        return value.replace("\n", " ").replace("\r", " ")

    report = bundle.report
    decision_label = "Baseline decision" if repair is not None else "Decision"
    lines = ["# Business Policy Lab", "", f"{decision_label}: **{report.decision}**", "",
             f"Model: {safe(bundle.model.name)}. Policy: {safe(bundle.policy.name)}.", "",
             f"States: {report.states_discovered}. Examinations: {report.transitions_examined}. Complete frontier: {report.frontier_exhausted}.", "",
             "## Safety and completion", ""]
    lines.extend(f"- Property {safe(key)}: {status}" for key, status in report.property_status.items())
    lines.extend(f"- Goal {safe(key)}: {status}" for key, status in report.goal_status.items())
    for witness in (*report.safety_witnesses, *report.goal_witnesses):
        lines.extend(["", f"## {safe(witness.kind)}: {safe(witness.objective_id)}", ""])
        lines.extend(f"{step.index}. {safe(step.actor_id)} → {safe(step.action_id)} ({safe(step.tool)})." for step in witness.steps)
        if not witness.steps:
            lines.append("Initial state already matches the objective.")
    if repair is not None:
        lines.extend(["", "## Goal-preserving permission repair", "", f"Repair: **{repair.status}**", "",
                      f"Minimum removal cardinality proven: {str(repair.minimality_proven).lower()}.",
                      f"Search allowance: {repair.max_removals} removals, {repair.max_candidates} candidates, {repair.transition_budget} transition examinations.",
                      f"Transition examinations used: {repair.transitions_examined}.", ""])
        for candidate in repair.candidates:
            lines.append(f"- Removed original rule indices {candidate.removed_rule_indices}: {candidate.report.decision}; frontier exhausted: {str(candidate.report.frontier_exhausted).lower()}.")
        if repair.selected_bundle is not None:
            selected = repair.selected_bundle
            lines.extend(["", f"Selected policy decision: **{selected.report.decision}**", ""])
            lines.extend(f"- Goal {safe(key)}: {status}" for key, status in selected.report.goal_status.items())
            lines.extend(["", f"Selected evidence: `selected-bundle.json` (SHA-256: `{selected.integrity_digest}`)."])
        lines.extend(f"- Search cutoff: {safe(reason)}" for reason in repair.cutoff_reasons)
        lines.extend(["", "No deployed permissions have been changed."])
    if comparison is not None:
        lines.extend(["", "## Policy comparison", "",
                      f"{safe(str(comparison['original_decision']))} → {safe(str(comparison['candidate_decision']))}",
                      f"Original bundle SHA-256: {safe(str(comparison['original_bundle_digest']))}", ""])
        for family in ("properties", "goals"):
            for key, change in comparison[family].items():
                lines.append(f"- {safe(key)}: {safe(str(change['before']))} → {safe(str(change['after']))}")
        lines.extend(["", safe(str(comparison["limitation"]))])
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {safe(limit)}" for limit in report.limitations)
    lines.extend(["", f"Bundle SHA-256: `{bundle.integrity_digest}`", ""])
    return "\n".join(lines)


def write_policy_lab_reports(bundle: ExplorationBundle, output: str | Path, *, comparison: dict[str, Any] | None = None, repair: RepairReport | None = None, metadata: dict[str, Any] | None = None) -> Path:
    # Frozen Pydantic models still contain mutable dictionaries. Recompute the
    # retained evidence before making a durable report or creating its directory.
    bundle = validate_exploration_bundle(bundle)
    if repair is not None:
        repair = validate_repair_report(bundle, repair)
    destination = Path(output)
    destination.mkdir(mode=0o700, exist_ok=False)
    files = {"bundle.json": _json(bundle.model_dump(mode="json")), "report.json": _json(bundle.report.model_dump(mode="json")),
             "report.html": render_policy_lab_html(bundle, comparison=comparison, repair=repair),
             "report.md": render_policy_lab_markdown(bundle, comparison=comparison, repair=repair)}
    if comparison is not None:
        files["comparison.json"] = _json(comparison)
    if repair is not None:
        files["repair.json"] = _json(repair.model_dump(mode="json"))
        if repair.selected_bundle is not None:
            files["selected-bundle.json"] = _json(repair.selected_bundle.model_dump(mode="json"))
    if metadata is not None:
        files["metadata.json"] = _json(metadata)
    for name, content in files.items():
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    return destination
