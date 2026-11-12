"""Jinja2 templates for the local dashboard (autoescaped; no JavaScript, no external assets)."""

BASE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ title }} · Retract</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font:14px/1.45 system-ui,sans-serif;margin:0;color:#1d2433;background:#f6f7f9}
 header{background:#1d2433;color:#fff;padding:10px 24px;display:flex;gap:24px;align-items:center}
 header a{color:#cfd6e4;text-decoration:none} header a:hover{color:#fff}
 header strong{color:#fff;margin-right:12px}
 main{max-width:1180px;margin:20px auto;padding:0 24px}
 h1{font-size:20px;margin:8px 0 16px} h2{font-size:16px;margin:24px 0 8px}
 table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #dde1e8}
 th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #eceff3;vertical-align:top}
 th{background:#f0f2f5;font-weight:600} tr:last-child td{border-bottom:0}
 code{font:12px ui-monospace,monospace} .muted{color:#6b7385}
 .badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#e6e9ef}
 .active,.pass,.completed,.finalized,.retraction{background:#dff3e4;color:#1e6b34}
 .quarantined,.contained,.containment,.held,.not_run{background:#fff1d6;color:#8a5a00}
 .retracted,.tombstoned,.failed,.fail,.pending{background:#fbe1e1;color:#9b1c1c}
 .note{background:#fff;border:1px solid #dde1e8;border-left:4px solid #3b6fd8;padding:10px 14px}
 .warn{border-left-color:#c27c0e} .err{border-left-color:#c62828}
 form.inline{display:inline} button{font:inherit;padding:6px 14px;border-radius:6px;
  border:1px solid #2f5fc4;background:#3b6fd8;color:#fff;cursor:pointer}
 button.danger{background:#c62828;border-color:#a51f1f} select,input{font:inherit;padding:5px}
 ul.tree{list-style:none;padding-left:18px;border-left:1px dashed #c9cfda;margin:4px 0}
 ul.tree>li{margin:4px 0} .kv td:first-child{width:200px;color:#6b7385}
</style></head><body>
<header><strong>Retract</strong>
 <a href="/">Records</a><a href="/sources">Sources</a><a href="/ops">Operations</a>
 <a href="/retract">Retract a source</a></header>
<main>{% if message %}<p class="note {{ message_kind }}">{{ message }}</p>{% endif %}
{% block body %}{% endblock %}</main></body></html>"""

RECORDS = """{% extends "base" %}{% block body %}
<h1>Records <span class="muted">({{ rows|length }})</span></h1>
<p class="muted">Every record in the lineage store. Agents read only <em>active</em> records with
no hold; everything else is hidden by the guarded read path.</p>
<table><tr><th>record</th><th>agent / tier</th><th>status</th><th>labels (sources)</th>
<th>content</th></tr>
{% for r in rows %}<tr>
<td><a href="/records/{{ r.record_id }}"><code>{{ r.record_id[:8] }}</code></a>
 {% if r.derived %}<span class="muted">derived</span>{% endif %}</td>
<td>{{ r.agent }}<br><span class="muted">{{ r.tier }}</span></td>
<td><span class="badge {{ r.status }}">{{ r.status }}</span>
 {% if r.held %}<span class="badge held">held</span>{% endif %}</td>
<td>{% for s in r.labels %}<code>{{ s }}</code><br>{% endfor %}</td>
<td>{% if r.content is none %}<span class="muted">(purged)</span>
 {% else %}{{ r.content|truncate(110) }}{% endif %}</td>
</tr>{% endfor %}</table>{% endblock %}"""

TREE = """{% macro node(n) %}<li>
<a href="/records/{{ n.record.record_id }}"><code>{{ n.record.record_id[:8] }}</code></a>
<span class="badge {{ n.record.status }}">{{ n.record.status }}</span>
{% if n.kind %}<span class="muted">via {{ n.kind }}</span>{% endif %}
{{ n.record.agent }} · <span class="muted">{{ n.record.tier }}</span>
{% if n.seen %}<span class="muted">(shown above)</span>
{% elif not n.parents %}<br><span class="muted">primary, owned by</span>
 {% for s in n.sources %}<code>{{ s }}</code> {% endfor %}
 — {% if n.record.content is none %}<span class="muted">(purged)</span>
 {% else %}“{{ n.record.content|truncate(90) }}”{% endif %}
{% else %}<ul class="tree">{% for p in n.parents %}{{ node(p) }}{% endfor %}</ul>{% endif %}
</li>{% endmacro %}"""

RECORD = """{% extends "base" %}{% import "tree" as t %}{% block body %}
<h1>Record <code>{{ rec.record_id }}</code></h1>
<table class="kv">
<tr><td>status</td><td><span class="badge {{ rec.status }}">{{ rec.status }}</span>
 {% if held %}<span class="badge held">held by
 <a href="/ops/{{ held }}"><code>{{ held[:8] }}</code></a></span>{% endif %}</td></tr>
<tr><td>agent / tier</td><td>{{ rec.agent }} · {{ rec.tier }}</td></tr>
<tr><td>labels (sources)</td><td>{% for s in rec.labels|sort %}<code>{{ s }}</code>
 <a href="/retract?source={{ s|urlencode }}">retract…</a><br>{% endfor %}</td></tr>
{% if fn %}<tr><td>derived by</td><td><code>{{ fn }}</code></td></tr>{% endif %}
{% if supersedes %}<tr><td>replaces</td><td><a href="/records/{{ supersedes }}">
 <code>{{ supersedes[:8] }}</code></a></td></tr>{% endif %}
{% for r in replaced_by %}<tr><td>replaced by</td><td><a href="/records/{{ r }}">
 <code>{{ r[:8] }}</code></a></td></tr>{% endfor %}
<tr><td>content</td><td>{% if rec.content is none %}<span class="muted">(purged)</span>
 {% else %}{{ rec.content }}{% endif %}</td></tr>
</table>
<h2>Why does the agent know this?</h2>
<p class="muted">Lineage back to the primary records and the sources that own them.
<em>input</em> = declared input, <em>read</em> = read inside the same <code>rt.step</code>.</p>
<ul class="tree">{{ t.node(tree) }}</ul>
<h2>What was derived from it</h2>
{% if children %}<table><tr><th>record</th><th>edge</th><th>agent / tier</th><th>status</th></tr>
{% for c, kind in children %}<tr><td><a href="/records/{{ c.record_id }}">
<code>{{ c.record_id[:8] }}</code></a></td><td>{{ kind }}</td>
<td>{{ c.agent }} · {{ c.tier }}</td>
<td><span class="badge {{ c.status }}">{{ c.status }}</span></td></tr>{% endfor %}</table>
{% else %}<p class="muted">Nothing.</p>{% endif %}
{% endblock %}"""

SOURCES = """{% extends "base" %}{% block body %}
<h1>Sources</h1>
<table><tr><th>source</th><th>primary records</th><th>live records it reaches</th><th>state</th>
<th></th></tr>
{% for s in rows %}<tr><td><code>{{ s.source }}</code></td><td>{{ s.primaries }}</td>
<td>{{ s.closure }}</td><td>{% if s.op %}
 <span class="badge {{ s.op_state }}">{{ s.op_state }}</span>
 <a href="/ops/{{ s.op }}"><code>{{ s.op[:8] }}</code></a>
 {% else %}<span class="badge active">live</span>{% endif %}</td>
<td>{% if not s.op %}<a href="/retract?source={{ s.source|urlencode }}">preview retraction</a>
 {% endif %}</td></tr>{% endfor %}</table>{% endblock %}"""

RETRACT = """{% extends "base" %}{% block body %}
<h1>Retract a source</h1>
<form method="get" action="/retract"><input name="source" size="36" value="{{ source }}"
 placeholder="e.g. user:u_priya"> <select name="mode">{% for m in modes %}
 <option value="{{ m }}" {% if m == mode %}selected{% endif %}>{{ m }}</option>{% endfor %}
 </select> <button>Preview</button></form>
{% if plan %}
<h2>Preview: {{ plan.blast_radius }}</h2>
{% if plan.noop_op_id %}<p class="note">Already retracted by
 <a href="/ops/{{ plan.noop_op_id }}"><code>{{ plan.noop_op_id }}</code></a>.</p>
{% else %}
<table><tr><th>#</th><th>record</th><th>agent / tier</th><th>action</th><th>reason</th></tr>
{% for a in plan.actions %}<tr><td>{{ loop.index }}</td><td><a href="/records/{{ a.record_id }}">
<code>{{ a.record_id[:8] }}</code></a></td><td>{{ a.agent }} · {{ a.tier }}</td>
<td><span class="badge">{{ a.action }}</span></td><td class="muted">{{ a.reason }}</td></tr>
{% endfor %}</table>
<p class="muted">Closure hash <code>{{ plan.closure_hash[:16] }}</code>. If the closure changes
before you confirm, nothing is touched and you get a fresh preview.</p>
<form method="post" action="/retract"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="hidden" name="source" value="{{ source }}">
<input type="hidden" name="mode" value="{{ mode }}">
<input type="hidden" name="closure_hash" value="{{ plan.closure_hash }}">
<button class="danger">Confirm retraction ({{ mode }})</button></form>
{% endif %}{% endif %}{% endblock %}"""

OPS = """{% extends "base" %}{% block body %}
<h1>Operations</h1>
<table><tr><th>op</th><th>source</th><th>mode</th><th>state</th><th>outcome</th><th>error</th>
<th>created</th></tr>
{% for o in rows %}<tr><td><a href="/ops/{{ o.op_id }}"><code>{{ o.op_id[:8] }}</code></a></td>
<td><code>{{ o.source }}</code></td><td>{{ o.mode }}</td>
<td><span class="badge {{ o.state }}">{{ o.state }}</span></td>
<td>{% if o.outcome %}<span class="badge {{ o.outcome }}">{{ o.outcome }}</span>{% endif %}</td>
<td class="muted">{{ (o.error or "")|truncate(80) }}</td><td class="muted">{{ o.created_at }}</td>
</tr>{% else %}<tr><td colspan="7" class="muted">No operations yet.</td></tr>{% endfor %}
</table>{% endblock %}"""

OP = """{% extends "base" %}{% block body %}
<h1>Operation <code>{{ op.op_id }}</code></h1>
<table class="kv">
<tr><td>source</td><td><code>{{ source }}</code></td></tr>
<tr><td>mode</td><td>{{ op.mode }}</td></tr>
<tr><td>state</td><td><span class="badge {{ op.state }}">{{ op.state }}</span></td></tr>
{% if op.error %}<tr><td>error</td><td>{{ op.error }}</td></tr>{% endif %}
</table>
{% if op.state != "finalized" %}
<p class="note warn">This operation did not finish. Its records stay held, so agents cannot
read them, until it is resumed. Resuming continues from the journal: finished actions are not
repeated, and writes that already reached a store are adopted, not duplicated.</p>
<form method="post" action="/ops/{{ op.op_id }}/resume"><input type="hidden" name="csrf"
 value="{{ csrf }}"><button>Resume operation</button></form>
{% endif %}
{% if receipt %}
<h2>Receipt</h2>
<table class="kv">
<tr><td>outcome</td><td><span class="badge {{ receipt.outcome }}">{{ receipt.outcome }}</span>
</td></tr>
<tr><td>receipt type</td><td><span class="badge {{ receipt.receipt_type }}">
{{ receipt.receipt_type }}</span></td></tr>
<tr><td>signature and chain</td><td>integrity {{ "ok" if report.integrity else "FAILED" }} ·
 chain link {{ "ok" if report.chain_link else "FAILED" }} · execution
 {{ "complete" if report.execution_complete else "INCOMPLETE" }}</td></tr>
</table>
<h2>Verification (three dimensions, reported separately)</h2>
<table><tr><th>dimension</th><th>status</th><th>detail</th></tr>
<tr><td>1. structural</td><td><span class="badge {{ structural.status }}">
{{ structural.status }}</span></td><td class="muted">{{ structural_detail }}</td></tr>
<tr><td>2. retrieval residue</td><td><span class="badge {{ retrieval.status }}">
{{ retrieval.status }}</span></td><td class="muted">{{ retrieval_detail }}</td></tr>
<tr><td>3. answer contamination</td><td><span class="badge not_run">not measured</span></td>
<td class="muted">Needs a fact oracle; measured in the experiments, never claimed by the SDK.
</td></tr></table>
<p><a href="/ops/{{ op.op_id }}/receipt.json">Export receipt (JSON)</a></p>
{% endif %}
<h2>Actions</h2>
<table><tr><th>#</th><th>record</th><th>planned</th><th>reason</th><th>state</th><th>effect</th>
<th>replacement</th><th>detail</th></tr>
{% for a in actions %}<tr><td>{{ a.seq }}</td><td><a href="/records/{{ a.record_id }}">
<code>{{ a.record_id[:8] }}</code></a></td><td>{{ a.action }}</td>
<td class="muted">{{ a.reason }}</td><td><span class="badge {{ a.state }}">{{ a.state }}</span></td>
<td>{% if a.effect %}<span class="badge">{{ a.effect }}</span>{% endif %}</td>
<td>{% if a.new_record_id %}<a href="/records/{{ a.new_record_id }}">
<code>{{ a.new_record_id[:8] }}</code></a>{% endif %}</td>
<td class="muted">{{ a.detail or "" }}</td></tr>{% endfor %}</table>
{% endblock %}"""

ERROR = """{% extends "base" %}{% block body %}<h1>{{ heading }}</h1>{% endblock %}"""

TEMPLATES = {
    "base": BASE,
    "tree": TREE,
    "records": RECORDS,
    "record": RECORD,
    "sources": SOURCES,
    "retract": RETRACT,
    "ops": OPS,
    "op": OP,
    "error": ERROR,
}
