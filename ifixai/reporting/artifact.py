"""Self-contained interactive results artifact.

`render_artifact` turns a `TestRunResult` into ONE standalone HTML document — no
network calls, no CDN, no browser storage — that a Team/Enterprise Claude Code
surface can present as an interactive artifact. The results JSON stays the
source of truth for automation/CI (see `scorecard.generate_json_report`); this
is only the human-friendly view, so the orchestrator falls back to the static
`render_html` scorecard wherever artifacts aren't available.

What it shows: the overall grade + verdict, a category breakdown, compliance-
framework coverage, and a searchable / filterable list of every inspection
(pass / fail / inconclusive / error) that expands to its evidence — why it
passed or failed, the prompt used, expected vs. actual, and confidence. When a
previous run's JSON is supplied, it also shows what changed since then.
"""

from __future__ import annotations

import html
import json
from typing import Any

from ifixai.core.types import TestRunResult, TestStatus
from ifixai.providers.secrets import scrub_secrets
from ifixai.reporting.grading import (
    GRADE_BOUNDARIES as _GRADE_BOUNDARIES,
    GRADE_CLASS as _GRADE_CLASS,
)
from ifixai.reporting.regulatory import build_regulatory_summary
from ifixai.reporting.scorecard import (
    _dominant_evaluation_path,
    _format_method_mix,
    _format_run_verdict,
)


def _stability_note(overall: float | None, margin: float = 0.02) -> str:
    if overall is None:
        return "No overall score (insufficient evidence)."
    distance = min(abs(overall - b) for b in _GRADE_BOUNDARIES)
    if distance <= margin:
        return (
            f"⚠ Borderline — {distance * 100:.1f} points from a grade boundary; "
            "the letter could shift on a rerun."
        )
    return f"{distance * 100:.1f} points clear of the nearest grade boundary."


def _scrub(text: str) -> str:
    return scrub_secrets(text)


def _evidence_payload(ev) -> dict[str, Any]:
    confidence = None
    reasoning = ""
    verdict_label = ""
    if ev.judge_verdict is not None:
        confidence = ev.judge_verdict.confidence
        reasoning = ev.judge_verdict.reasoning or ""
        verdict_label = ev.judge_verdict.verdict or ""
    dims = []
    for ds in ev.dimension_scores or []:
        dims.append(
            {
                "name": ds.dimension_name,
                "passed": ds.passed,
                "confidence": ds.confidence,
                "reasoning": _scrub(ds.reasoning or ""),
                "mandatory": ds.is_mandatory,
            }
        )
    return {
        "description": _scrub(ev.description or ""),
        "prompt": _scrub(ev.prompt_sent or ""),
        "expected": _scrub(ev.expected or ev.expected_behavior or ""),
        "actual": _scrub(ev.actual_response or ev.actual or ""),
        "evaluation_result": ev.evaluation_result or "",
        "passed": ev.passed,
        "evaluation_method": ev.evaluation_method.value,
        "verdict": verdict_label,
        "confidence": confidence,
        "reasoning": _scrub(reasoning),
        "dimensions": dims,
    }


def _check_payload(br) -> dict[str, Any]:
    unscored = br.status in {TestStatus.INCONCLUSIVE, TestStatus.ERROR}
    ci = None
    if br.confidence_interval and not unscored:
        ci = [round(br.confidence_interval.lower, 2), round(br.confidence_interval.upper, 2)]
    return {
        "test_id": br.test_id,
        "name": br.name,
        "category": br.category.value,
        "status": br.status.value,
        "score": None if unscored else round(br.score, 4),
        "score_pct": "n/a" if unscored else f"{br.score * 100:.1f}%",
        "threshold_pct": f"{br.threshold * 100:.0f}%",
        "ci": ci,
        "path": _dominant_evaluation_path(br),
        "method": _format_method_mix(br),
        "evidence_count": len(br.evidence),
        "error": br.error_message or br.error or "",
        "evidence": [_evidence_payload(ev) for ev in br.evidence],
    }


def _diff_payload(result: TestRunResult, previous: dict[str, Any]) -> dict[str, Any]:
    """A plain-data diff of this run against a previous run's results JSON."""
    prev_overall = (previous.get("overall") or {}).get("score")
    prev_grade = (previous.get("overall") or {}).get("grade", "?")
    cur_overall = result.overall_score
    overall_delta = (
        round(cur_overall - prev_overall, 4)
        if (cur_overall is not None and prev_overall is not None)
        else None
    )

    prev_by_id = {t.get("test_id"): t for t in previous.get("test_results", [])}
    cur_by_id = {br.test_id: br for br in result.test_results}

    changes = []
    for tid in sorted(set(prev_by_id) | set(cur_by_id)):
        prev = prev_by_id.get(tid)
        cur = cur_by_id.get(tid)
        if cur is None:
            changes.append({"test_id": tid, "kind": "removed",
                            "prev_status": (prev or {}).get("status", "?"), "new_status": "—"})
            continue
        if prev is None:
            changes.append({"test_id": tid, "kind": "added",
                            "prev_status": "—", "new_status": cur.status.value})
            continue
        prev_status = prev.get("status", "?")
        new_status = cur.status.value
        prev_score = prev.get("score")
        new_score = None if cur.status in {TestStatus.INCONCLUSIVE, TestStatus.ERROR} else round(cur.score, 4)
        was_pass = prev_status == "pass"
        now_pass = new_status == "pass"
        if not was_pass and now_pass:
            kind = "fixed"
        elif was_pass and not now_pass:
            kind = "broken"
        elif prev_score is not None and new_score is not None and new_score - prev_score > 0.01:
            kind = "improved"
        elif prev_score is not None and new_score is not None and new_score - prev_score < -0.01:
            kind = "regressed"
        elif prev_status != new_status:
            kind = "status"
        else:
            kind = "unchanged"
        if kind != "unchanged":
            changes.append({
                "test_id": tid, "kind": kind,
                "prev_status": prev_status, "new_status": new_status,
                "prev_pct": prev.get("score_pct", "n/a"),
                "new_pct": "n/a" if new_score is None else f"{new_score * 100:.1f}%",
            })
    return {
        "overall_delta": overall_delta,
        "grade_change": f"{prev_grade} → {result.grade.value}",
        "changes": changes,
    }


def _build_payload(
    result: TestRunResult,
    *,
    live: bool,
    transport: str,
    sut_model,
    judge_model,
    honesty_note: str,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    overall = result.overall_score
    categories = []
    for cs in result.category_scores:
        ran = len(cs.test_ids)
        if ran == 0:
            continue
        categories.append({
            "category": cs.category.value,
            "score_pct": "n/a" if cs.score is None else f"{cs.score * 100:.0f}%",
            "weight": cs.weight,
            "coverage": f"{cs.test_count} of {ran} assessed",
        })

    compliance = [row for row in build_regulatory_summary(result) if row["tests_mapped"]]

    failed = [
        f"{br.test_id} ({br.score * 100:.1f}% < {br.threshold:.0%})"
        for br in sorted(result.test_results, key=lambda b: b.test_id)
        if br.status == TestStatus.FAIL
    ]

    payload: dict[str, Any] = {
        "meta": {
            "system_name": result.system_name,
            "system_version": result.system_version,
            "provider": result.provider,
            "fixture": result.fixture_name,
            "evaluation_date": result.evaluation_date.strftime("%Y-%m-%d %H:%M UTC"),
            "transport": transport,
            "live": live,
            "sut_model": str(sut_model or "(default)"),
            "judge_model": str(judge_model or "(default)"),
            "spec_version": result.specification_version,
            "honesty_note": honesty_note,
        },
        "summary": {
            "grade": result.grade.value,
            "grade_class": _GRADE_CLASS.get(result.grade.value, "inconclusive"),
            "overall_pct": "n/a (insufficient evidence)" if overall is None else f"{overall * 100:.1f}%",
            "score_before_cap": None if result.overall_score_before_cap is None
            else f"{result.overall_score_before_cap * 100:.1f}%",
            "verdict": _format_run_verdict(result),
            "strategic_pct": f"{result.strategic_score * 100:.1f}%",
            "stability": _stability_note(overall),
            "mm_passed": result.mandatory_minimums_passed,
            "mm_status": {tid: st.value for tid, st in sorted(result.mandatory_minimum_status.items())},
            "below_threshold": failed,
        },
        "categories": categories,
        "compliance": compliance,
        "checks": [_check_payload(br) for br in sorted(result.test_results, key=lambda b: b.test_id)],
        "diff": _diff_payload(result, previous) if previous else None,
    }
    return payload


def render_artifact(
    result: TestRunResult,
    *,
    live: bool,
    transport: str,
    sut_model,
    judge_model,
    honesty_note: str,
    previous: dict[str, Any] | None = None,
) -> str:
    payload = _build_payload(
        result, live=live, transport=transport, sut_model=sut_model,
        judge_model=judge_model, honesty_note=honesty_note, previous=previous,
    )
    # Embed as JSON in a non-executing script tag; escape '<' so a stray
    # "</script>" or "<" in evidence text can't break out of the tag. JSON.parse
    # reads it back unchanged.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(payload["meta"]["system_name"] or "agent")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", data)


# The whole UI is one template with embedded CSS + vanilla JS. {{ }} are literal
# braces in CSS; __TITLE__/__DATA__ are substituted above. No external assets.
_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>iFixAi — __TITLE__</title>
<style>
:root{--line:#e5e7eb;--ink:#111827;--dim:#6b7280;--bg:#fff;--card:#f9fafb;--good:#15803d;--bad:#b91c1c;--warn:#b45309;--accent:#1d4ed8}
*{box-sizing:border-box}
body{font:15px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
.wrap{max-width:980px;margin:0 auto;padding:1.5rem 1rem 4rem}
h1{font-size:1.4rem;margin:0 0 .2rem}
h2{font-size:1.05rem;margin:2rem 0 .5rem;border-bottom:2px solid var(--line);padding-bottom:.25rem}
.sub{color:var(--dim);font-weight:400;font-size:.95rem}
.gradebox{display:flex;align-items:center;gap:1.1rem;margin:1.1rem 0}
.grade{font-size:3.4rem;font-weight:800;line-height:1;padding:.2rem .8rem;border-radius:.6rem;background:var(--card)}
.pass{color:var(--good)} .fail{color:var(--bad)} .inconclusive{color:var(--warn)} .error{color:var(--bad)}
.banner{background:var(--card);border-left:4px solid var(--accent);padding:.7rem 1rem;margin:1rem 0;font-size:.9rem;border-radius:.3rem}
.banner.warn{border-left-color:var(--warn)} .banner.bad{border-left-color:var(--bad);font-weight:600}
table{border-collapse:collapse;width:100%;font-size:.92rem;margin:.3rem 0}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
thead th{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}
.controls{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.6rem 0}
input[type=search]{flex:1;min-width:220px;padding:.5rem .7rem;border:1px solid var(--line);border-radius:.4rem;font:inherit}
.chip{border:1px solid var(--line);background:var(--bg);border-radius:1rem;padding:.3rem .8rem;cursor:pointer;font-size:.85rem;color:var(--dim)}
.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.count{color:var(--dim);font-size:.85rem;margin-left:auto}
.check{border:1px solid var(--line);border-radius:.5rem;margin:.45rem 0;overflow:hidden}
.check>summary{cursor:pointer;list-style:none;padding:.6rem .8rem;display:flex;gap:.6rem;align-items:center}
.check>summary::-webkit-details-marker{display:none}
.check>summary:hover{background:var(--card)}
.tid{font-weight:700;font-variant-numeric:tabular-nums;min-width:3rem}
.cname{flex:1}
.tag{font-size:.72rem;text-transform:uppercase;letter-spacing:.03em;padding:.1rem .5rem;border-radius:.3rem;font-weight:700}
.tag.pass{background:#dcfce7;color:var(--good)} .tag.fail{background:#fee2e2;color:var(--bad)}
.tag.inconclusive{background:#fef3c7;color:var(--warn)} .tag.error{background:#fee2e2;color:var(--bad)}
.scorecol{color:var(--dim);font-variant-numeric:tabular-nums;font-size:.88rem;white-space:nowrap}
.body{padding:.4rem .9rem .9rem;border-top:1px solid var(--line);background:var(--card)}
.ev{border:1px solid var(--line);background:var(--bg);border-radius:.4rem;padding:.6rem .7rem;margin:.5rem 0}
.ev .evhead{display:flex;gap:.5rem;align-items:center;font-size:.85rem;color:var(--dim)}
.kvp{margin:.35rem 0}
.kvp b{display:block;font-size:.72rem;text-transform:uppercase;letter-spacing:.03em;color:var(--dim);margin-bottom:.1rem}
pre{white-space:pre-wrap;word-break:break-word;background:#f3f4f6;border-radius:.3rem;padding:.5rem .6rem;margin:0;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.dim{color:var(--dim)} .meta-grid{display:grid;grid-template-columns:auto 1fr;gap:.15rem 1rem;font-size:.88rem;margin:.4rem 0}
.meta-grid div:nth-child(odd){color:var(--dim)}
.empty{color:var(--dim);font-style:italic;padding:.8rem 0}
.diffkind{font-weight:700} .k-fixed,.k-improved,.k-added{color:var(--good)} .k-broken,.k-regressed,.k-removed{color:var(--bad)} .k-status{color:var(--warn)}
.bar{height:.5rem;background:#e5e7eb;border-radius:.3rem;overflow:hidden;min-width:60px;display:inline-block;width:90px;vertical-align:middle}
.bar>span{display:block;height:100%}
</style></head>
<body><div class="wrap">
<script type="application/json" id="ifixai-data">__DATA__</script>
<div id="app"></div>
<script>
const D = JSON.parse(document.getElementById('ifixai-data').textContent);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const el = (h) => { const t=document.createElement('template'); t.innerHTML=h.trim(); return t.content.firstChild; };

function header(){
  const m=D.meta, s=D.summary;
  const off = !m.live ? `<div class="banner bad">OFFLINE RUN — produced in '${esc(m.transport)}' mode from canned/recorded replies. It rehearses the pipeline and is NOT a diagnostic of a real agent.</div>`:'';
  const cap = s.score_before_cap ? ` <span class="sub">(capped from ${esc(s.score_before_cap)})</span>`:'';
  const bt = (s.below_threshold&&s.below_threshold.length)? `<div class="banner warn">⚠ ${s.below_threshold.length} inspection(s) scored below their own pass threshold: ${esc(s.below_threshold.join(', '))}. A high letter does not mean every inspection passed.</div>`:'';
  return `<h1>iFixAi Scorecard — ${esc(m.system_name)}${m.system_version?` <span class="sub">v${esc(m.system_version)}</span>`:''}</h1>
  <div class="sub">${esc(m.evaluation_date)} · ${m.live?'live':'offline'} (${esc(m.transport)})</div>
  ${off}
  <div class="gradebox"><div class="grade ${s.grade_class}">${esc(s.grade)}</div>
    <div><div style="font-size:1.1rem">Overall <strong>${esc(s.overall_pct)}</strong>${cap} · ${esc(s.verdict)}</div>
    <div class="sub">${esc(s.stability)}</div></div></div>
  ${bt}
  <div class="banner">${esc(m.honesty_note)}</div>
  <div class="meta-grid">
    <div>Provider</div><div>${esc(m.provider)}</div>
    <div>Agent under test</div><div>${esc(m.sut_model)}</div>
    <div>Judge</div><div>${esc(m.judge_model)}</div>
    <div>Fixture</div><div>${esc(m.fixture)}</div>
    <div>Strategic score</div><div>${esc(s.strategic_pct)}</div>
    <div>Mandatory minimums</div><div>${s.mm_passed?'PASS':'NOT PASSED'} — ${esc(Object.entries(s.mm_status).map(([k,v])=>k+':'+v.toUpperCase()).join('  '))}</div>
  </div>`;
}

function categories(){
  if(!D.categories.length) return '';
  const rows = D.categories.map(c=>`<tr><td>${esc(c.category)}</td><td>${esc(c.score_pct)}</td><td>${esc((c.weight*100).toFixed(0))}%</td><td>${esc(c.coverage)}</td></tr>`).join('');
  return `<h2>Category breakdown</h2><table><thead><tr><th>Category</th><th>Score</th><th>Weight</th><th>Coverage</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function compliance(){
  if(!D.compliance.length) return '';
  const rows = D.compliance.map(c=>`<tr><td>${esc(c.name)}</td><td>${esc(c.version)}</td><td>${c.tests_passing}/${c.tests_mapped}</td><td>${esc(c.coverage_pct)}</td></tr>`).join('');
  return `<h2>Compliance-framework coverage</h2><table><thead><tr><th>Framework</th><th>Version</th><th>Passing / mapped</th><th>Coverage</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function diffSection(){
  if(!D.diff) return '';
  const d=D.diff;
  const moved = d.changes.filter(c=>c.kind!=='unchanged');
  const delta = d.overall_delta==null?'—':((d.overall_delta>=0?'+':'')+(d.overall_delta*100).toFixed(1)+' pts');
  let body;
  if(!moved.length){ body=`<div class="empty">No inspection changed status or score since the previous run.</div>`; }
  else { body=`<table><thead><tr><th>ID</th><th>Change</th><th>Was</th><th>Now</th></tr></thead><tbody>`+
    moved.map(c=>`<tr><td class="tid">${esc(c.test_id)}</td><td class="diffkind k-${c.kind}">${esc(c.kind)}</td><td>${esc(c.prev_status||'')} ${esc(c.prev_pct||'')}</td><td>${esc(c.new_status||'')} ${esc(c.new_pct||'')}</td></tr>`).join('')+`</tbody></table>`; }
  return `<h2>Changes since the previous run</h2><div class="banner">Grade ${esc(d.grade_change)} · overall ${esc(delta)}</div>${body}`;
}

function evidence(ev){
  const conf = ev.confidence==null?'':` · confidence ${(ev.confidence*100).toFixed(0)}%`;
  const dims = (ev.dimensions||[]).map(dm=>`<tr><td>${dm.passed?'✓':'✗'} ${esc(dm.name)}${dm.mandatory?' <span class="dim">(mandatory)</span>':''}</td><td>${dm.confidence==null?'':(dm.confidence*100).toFixed(0)+'%'}</td><td class="dim">${esc(dm.reasoning)}</td></tr>`).join('');
  const fld=(label,val)=> val? `<div class="kvp"><b>${label}</b><pre>${esc(val)}</pre></div>`:'';
  return `<div class="ev">
    <div class="evhead"><span class="tag ${ev.passed?'pass':'fail'}">${ev.passed?'pass':'fail'}</span><span>${esc(ev.evaluation_method)}${conf}</span></div>
    ${ev.description?`<div class="kvp dim">${esc(ev.description)}</div>`:''}
    ${fld('Prompt used',ev.prompt)}
    ${fld('Expected',ev.expected)}
    ${fld('Actual',ev.actual)}
    ${ev.reasoning?fld('Why',ev.reasoning):''}
    ${ev.evaluation_result?`<div class="kvp dim">Result: ${esc(ev.evaluation_result)}</div>`:''}
    ${dims?`<table><thead><tr><th>Dimension</th><th>Conf.</th><th>Reasoning</th></tr></thead><tbody>${dims}</tbody></table>`:''}
  </div>`;
}

function checkNode(c){
  const ci = c.ci?` [${c.ci[0]}, ${c.ci[1]}]`:'';
  const score = c.score==null?'n/a':c.score_pct;
  const d = el(`<details class="check" data-id="${esc(c.test_id)}" data-status="${esc(c.status)}">
    <summary><span class="tid">${esc(c.test_id)}</span><span class="cname">${esc(c.name)} <span class="dim">· ${esc(c.category)}</span></span>
      <span class="scorecol">${esc(score)}${esc(ci)} / thr ${esc(c.threshold_pct)}</span>
      <span class="tag ${esc(c.status)}">${esc(c.status)}</span></summary>
    <div class="body"></div></details>`);
  const body = d.querySelector('.body');
  let html = `<div class="sub">Path: ${esc(c.path)} · Method: ${esc(c.method)} · ${c.evidence_count} evidence item(s)</div>`;
  if(c.error) html += `<div class="banner bad">Error: ${esc(c.error)}</div>`;
  if(c.evidence.length) html += c.evidence.map(evidence).join('');
  else html += `<div class="empty">No evidence captured for this inspection.</div>`;
  body.innerHTML = html;
  return d;
}

function checksSection(){
  const wrap = el(`<div><h2>Inspections</h2>
    <div class="controls">
      <input type="search" id="q" placeholder="Search id, name, prompt, evidence…">
      <span class="chip on" data-f="all">all</span>
      <span class="chip" data-f="pass">pass</span>
      <span class="chip" data-f="fail">fail</span>
      <span class="chip" data-f="inconclusive">inconclusive</span>
      <span class="chip" data-f="error">error</span>
      <span class="count" id="count"></span>
    </div>
    <div id="list"></div></div>`);
  const list = wrap.querySelector('#list');
  // pre-render searchable text per check (lowercased) for filtering
  const items = D.checks.map(c=>{
    const node = checkNode(c);
    const hay = (c.test_id+' '+c.name+' '+c.category+' '+c.status+' '+
      c.evidence.map(e=>(e.prompt+' '+e.actual+' '+e.expected+' '+e.reasoning+' '+e.description)).join(' ')).toLowerCase();
    return {c, node, hay};
  });
  items.forEach(it=>list.appendChild(it.node));
  let filter='all', query='';
  function apply(){
    let shown=0;
    items.forEach(it=>{
      const okF = filter==='all'||it.c.status===filter;
      const okQ = !query||it.hay.includes(query);
      const vis = okF&&okQ;
      it.node.style.display = vis?'':'none';
      if(vis) shown++;
    });
    wrap.querySelector('#count').textContent = `${shown} of ${items.length} shown`;
  }
  wrap.querySelector('#q').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();apply();});
  wrap.querySelectorAll('.chip').forEach(ch=>ch.addEventListener('click',()=>{
    wrap.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    ch.classList.add('on'); filter=ch.dataset.f; apply();
  }));
  apply();
  return wrap;
}

const app = document.getElementById('app');
app.insertAdjacentHTML('beforeend', header());
const diff = diffSection(); if(diff) app.insertAdjacentHTML('beforeend', diff);
app.insertAdjacentHTML('beforeend', categories());
app.insertAdjacentHTML('beforeend', compliance());
app.appendChild(checksSection());
</script>
</div></body></html>"""
