const { useState, useEffect, useCallback } = React; const h = (type, props, ...kids) => {
  if (props) {
    props = { ...props };
    if (typeof props.style === "string") { const o = {}; props.style.split(";").forEach(p => { const i = p.indexOf(":"); if (i > 0) o[p.slice(0, i).trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = p.slice(i + 1).trim(); }); props.style = o; }
    if ("class" in props) { props.className = props.class; delete props.class; }
  }
  return React.createElement(type, props, ...kids);
};
const html = htm.bind(h);
let TOKEN = sessionStorage.getItem("cs_token") || "";
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { method: opts.method || (opts.body ? "POST" : "GET"), headers: { ...(opts.form ? {} : { "Content-Type": "application/json" }), ...(TOKEN ? { Authorization: "Bearer " + TOKEN } : {}) },
    body: opts.form ? opts.form : opts.body ? JSON.stringify(opts.body) : undefined });
  const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j)); return j;
}
const fmt = s => s ? new Date(s).toLocaleString() : "-";
const Tag = ({ s }) => html`<span class=${"tag " + ({ resolved: "ok", verified_finding: "ok", rejected: "bad", unresolved: "bad", appealed: "warn", allegation: "", under_review: "warn", assigned: "warn" }[s] || "")}>${String(s).replace(/_/g, " ")}</span>`;
function useLoad(fn, deps = []) { const [d, setD] = useState(null); const [e, setE] = useState(""); const reload = useCallback(() => fn().then(setD).catch(x => setE(x.message)), deps); useEffect(() => { reload(); }, [reload]); return [d, e, reload]; }
const Err = ({ e }) => e ? html`<p class="err">${e}</p>` : null;

function Login({ onLogin }) {
  const [f, setF] = useState({ email: "", password: "", full_name: "" }); const [reg, setReg] = useState(false); const [e, setE] = useState("");
  const go = async () => { try { const r = await api(reg ? "/auth/register" : "/auth/login", { body: f }); TOKEN = r.token; sessionStorage.setItem("cs_token", TOKEN); onLogin(); } catch (x) { setE(x.message); } };
  const set = k => ev => setF({ ...f, [k]: ev.target.value });
  return html`<main><div class="card" style="max-width:420px;margin:40px auto"><h2>CivicShield</h2><p class="mut">A complaint supported by evidence should not disappear without a trace.</p>
    ${reg && html`<label>Full name</label><input type="text" value=${f.full_name} onInput=${set("full_name")}/>`}<label>Email</label><input type="email" value=${f.email} onInput=${set("email")}/>
    <label>Password</label><input type="password" value=${f.password} onInput=${set("password")} onKeyDown=${ev => ev.key === "Enter" && go()}/><${Err} e=${e}/>
    <p><button onClick=${go}>${reg ? "Create citizen account" : "Sign in"}</button> <button class="sec" onClick=${() => setReg(!reg)}>${reg ? "I have an account" : "Create account"}</button></p>
    <p class="mut">Demo accounts use password <code>Demo@12345</code>: priya@demo.in, ravi@demo.in, meena@demo.in (citizens); official.mun.z5@demo.gov, district.rev@demo.gov (official/supervisor); legalaid@demo.in; admin@demo.in</p></div></main>`;
}

function Dashboard({ me }) {
  const [list] = useLoad(() => api("/complaints")); const [pub] = useLoad(() => api("/public/stats"));
  if (!list || !pub) return html`<p>Loading…</p>`;
  const open = list.filter(c => !["resolved", "decided"].includes(c.status));
  return html`<h2>Welcome, ${me.full_name}</h2><div class="grid">
    <div class="card stat"><b>${list.length}</b>your cases</div><div class="card stat"><b>${open.length}</b>open</div>
    <div class="card stat"><b>${pub.complaint_volume}</b>total complaints (system)</div><div class="card stat"><b>${pub.overdue_cases}</b>overdue now</div></div>
    <div class="banner">${pub.caution}</div>`;
}

function Report({ meta, go }) {
  const [step, setStep] = useState(1); const [f, setF] = useState({ title: "", description: "", category_id: "", jurisdiction: "", incident_date: "", location_text: "" });
  const [id, setId] = useState(null); const [ai, setAi] = useState(null); const [ok, setOk] = useState(false); const [e, setE] = useState(""); const [busy, setBusy] = useState(false); const [pendingEvidence, setPendingEvidence] = useState(0);
  const set = k => ev => setF({ ...f, [k]: ev.target.value });
  const run = fn => async () => { setE(""); setBusy(true); try { await fn(); } catch (x) { setE(x.message); } setBusy(false); };
  const create = run(async () => { const draft = { ...f, category_id: +f.category_id }; if (id) await api(`/complaints/${id}`, { body: draft, method: "PUT" }); else { const r = await api("/complaints", { body: draft }); setId(r.id); } setStep(2); });
  const analyze = run(async () => { setAi(await api(`/complaints/${id}/analyze`, { method: "POST" })); setStep(3); });
  const submit = run(async () => { const r = await api(`/complaints/${id}/submit`, { body: { approved: ok, citizen_report: ai.report } }); alert(`Submitted: ${r.code}\nn8n intake workflow: ${r.workflow}`); go("#/case/" + id); });
  const rep = ai && ai.report; const setRep = (k, v) => setAi({ ...ai, report: { ...ai.report, [k]: v } });
  return html`<h2>Report an incident</h2><p class="mut">Step ${step} of 3 — describe, attach evidence, then review the AI-assisted report before submitting.</p>
  ${step === 1 && html`<div class="card"><label>Title</label><input type="text" value=${f.title} onInput=${set("title")}/><label>Category</label><select value=${f.category_id} onChange=${set("category_id")}><option value="">Select…</option>${meta.categories.map(c => html`<option value=${c.id}>${c.department}: ${c.name}</option>`)}</select>
    <label>Jurisdiction / office area</label><select value=${f.jurisdiction} onChange=${set("jurisdiction")}><option value="">Select…</option>${meta.jurisdictions.map(j => html`<option>${j}</option>`)}</select>
    <label>Incident date</label><input type="date" value=${f.incident_date} onInput=${set("incident_date")}/><label>Location</label><input type="text" value=${f.location_text} onInput=${set("location_text")}/>
    <label>What happened? (describe facts: who, what, where, when)</label><textarea value=${f.description} onInput=${set("description")}></textarea><${Err} e=${e}/><p><button disabled=${busy} onClick=${create}>${id ? "Update draft & continue" : "Save draft & continue"}</button></p></div>`}
    ${step === 2 && html`
  <div class="card"><h3>Step 2: Evidence</h3><p class="mut">Files are validated and SHA-256 hashed by n8n, then kept private.</p>
    <div class="row"><button class="sec" onClick=${() => setStep(1)}>Back</button><button disabled=${busy || pendingEvidence > 0} onClick=${analyze}>${busy ? "n8n is analysing…" : "Run AI-assisted analysis"}</button></div>${pendingEvidence > 0 && html`<p class="mut">Upload or remove the selected files before continuing.</p>`}<${Err} e=${e}/></div>`}
  <div style=${step === 2 ? "" : "display:none"}><${EvidenceUpload} caseId=${id} onPendingChange=${setPendingEvidence}/></div>
  ${step === 3 && rep && html`<div class="card ai"><b>${ai.label}</b><p class="mut">${ai.disclaimer}</p>
    <label>Incident summary (edit freely)</label><textarea value=${rep.incident_summary} onInput=${ev => setRep("incident_summary", ev.target.value)}></textarea>
    <p><b>Location:</b> ${rep.location || "—"} · <b>Dates:</b> ${(rep.date_time.dates || []).join(", ") || "—"} · <b>Roles:</b> ${(rep.involved_roles || []).join(", ") || "—"}</p>
    <p><b>Timeline:</b></p><ul>${(rep.timeline || []).map(t => html`<li>${t.when}: ${t.event}</li>`)}</ul>
    ${rep.missing_information.length > 0 && html`<p><b>Missing information (consider adding):</b></p><ul>${rep.missing_information.map(m => html`<li>${m}</li>`)}</ul>`}
    ${rep.duplicate_warning && html`<div class="banner">${rep.duplicate_warning}</div>`}
    <label><input type="checkbox" checked=${ok} onChange=${ev => setOk(ev.target.checked)}/> I have reviewed this report, corrected anything inaccurate, and approve it for submission. I understand it describes my allegations, not proven facts.</label><${Err} e=${e}/>
    <p class="row"><button class="sec" onClick=${() => setStep(2)}>Back</button><button disabled=${!ok || busy} onClick=${submit}>Submit complaint</button></p></div>`}`;
}


function EvidenceUpload({ caseId, reload, buttonLabel, onPendingChange }) {
  const [entries, setEntries] = useState([]); const [busy, setBusy] = useState(false); const nextKey = React.useRef(0);

  const handleFileChange = (e) => {
    const selected = Array.from(e.target.files || []);
    setEntries(current => [...current, ...selected.map(file => ({ key: ++nextKey.current, file, status: "Waiting" }))]);
    e.target.value = "";
  };

  const updateEntry = (key, update) => setEntries(current => current.map(entry => entry.key === key ? { ...entry, ...update } : entry));
  const uploadFile = async (entry) => {
    updateEntry(entry.key, { status: "Uploading", error: "" });
    try {
      const formData = new FormData();
      formData.append('file', entry.file);
      const data = await api(`/complaints/${caseId}/evidence`, { form: formData, method: "POST" });
      updateEntry(entry.key, { status: "Registered", evidenceId: data.evidence_id, sha256: data.sha256 });
      return true;
    } catch (err) {
      updateEntry(entry.key, { status: "Failed", error: err.message });
      return false;
    }
  };

  const uploadFiles = async () => {
    if (busy || !caseId) return;
    setBusy(true);
    for (const entry of entries) if (!["Registered", "Uploading"].includes(entry.status)) await uploadFile(entry);
    setBusy(false); if (reload) reload();
  };

  const retryFile = async (entry) => {
    setBusy(true); await uploadFile(entry); setBusy(false); if (reload) reload();
  };

  const removeFile = key => setEntries(current => current.filter(entry => entry.key !== key));
  const pending = entries.filter(entry => entry.status !== "Registered");
  useEffect(() => { if (onPendingChange) onPendingChange(pending.length); }, [pending.length]);

  if (!caseId) return null;
  return html`<div class="card evidence-upload"><h3>Add evidence</h3><p class="mut">JPG, PNG, PDF, MP3, MP4, or TXT up to 5 MB. ZIP and other archive files are not accepted.</p>
    <input type="file" multiple accept=".jpg,.jpeg,.png,.pdf,.mp3,.mp4,.txt" onChange=${handleFileChange} id="evidence-upload-input" class="visually-hidden"/>
    <label htmlFor="evidence-upload-input" class="file-picker">+ ${buttonLabel || "Choose files"}</label>
    ${entries.length ? html`<div class="evidence-list" role="list">${entries.map(entry => {
      const file = entry.file; const statusClass = entry.status === "Registered" ? "ok" : entry.status === "Failed" ? "bad" : "";
      const marker = entry.status === "Registered" ? "✓" : entry.status === "Failed" ? "!" : "•";
      return html`<div class="evidence-row" role="listitem"><div class="evidence-entry-main">
        <div class="evidence-file-line"><span class=${"tag evidence-status " + statusClass}>${marker} ${entry.status}</span><strong class="evidence-name" title=${file.name}>${file.name}</strong></div>
        <span class="mut">${file.type || "Unknown type"} · ${(file.size / 1024).toFixed(1)} KB</span>
        ${entry.sha256 && html`<code>SHA-256 ${entry.sha256}</code>`}${entry.error && html`<span class="err" role="alert">${entry.error}</span>`}
      </div><div class="row evidence-actions">
        ${entry.status === "Failed" && html`<button class="sec" disabled=${busy} onClick=${() => retryFile(entry)}>Retry</button>`}
        ${!["Registered", "Uploading"].includes(entry.status) && html`<button class="sec remove-upload" disabled=${busy} onClick=${() => removeFile(entry.key)}>Remove from upload</button>`}
      </div></div>`;
    })}</div>` : html`<p class="mut">No files selected.</p>`}
    ${pending.length > 0 && html`<p><button disabled=${busy} onClick=${uploadFiles}>${busy ? "Uploading…" : `Upload ${pending.length} file${pending.length === 1 ? "" : "s"}`}</button></p>`}
    ${entries.length > 0 && pending.length === 0 && html`<p class="upload-success" role="status">${entries.length} ${entries.length === 1 ? "file" : "files"} registered successfully.</p>`}
  </div>`;
}

function CaseList({ me, filter }) {
  const [list, e] = useLoad(() => api("/complaints"));
  const [flash] = useState(() => sessionStorage.getItem("cs_flash") || "");
  useEffect(() => { if (flash) sessionStorage.removeItem("cs_flash"); }, []);
  if (!list) return html`<${Err} e=${e}/>`;
  const rows = list.filter(filter || (() => true));
  return html`<h2>${me.role === "citizen" ? "My complaints" : "Cases"}</h2>${flash && html`<p class="banner" role="status">${flash}</p>`}<div class="card"><table><tr><th>ID</th><th>Title</th><th>Status</th><th>Stage</th><th>Authority</th><th>Ack due</th></tr>
  ${rows.map(c => html`<tr><td><a href=${"#/case/" + c.id}>${c.code || "draft"}</a></td><td>${c.title}${c.has_duplicates && me.role !== "citizen" ? " ⚠ possible duplicate" : ""}</td><td><${Tag} s=${c.status}/></td><td><${Tag} s=${c.stage}/></td><td>${c.authority || "-"}</td><td>${fmt(c.ack_deadline)}</td></tr>`)}</table>${!rows.length && html`<p class="mut">Nothing here.</p>`}</div>`;
}

function Case({ id, me }) {
  const [c, e, reload] = useLoad(() => api("/complaints/" + id), [id]); const [aud, setAud] = useState(null); const [sum, setSum] = useState(null); const [f, setF] = useState({}); const [msg, setMsg] = useState("");
  const [peers] = useLoad(() => ["official", "supervisor"].includes(me.role) ? api("/authorities/peers") : Promise.resolve([]));
  if (!c) return html`<${Err} e=${e}/>`;
  const act = (path, body, after) => async () => { setMsg(""); try { await api(`/complaints/${id}/${path}`, { body }); setF({}); reload(); after && after(); } catch (x) { setMsg(x.message); } };
  const mine = ["official", "supervisor"].includes(me.role) && c.current_authority_id === me.authority_id;
  const ownDraft = me.role === "citizen" && c.citizen_id === me.id && c.status === "draft";
  const deleteDraft = async () => {
    if (!window.confirm("Remove this draft from your list? Its audit history will be retained.")) return;
    setMsg("");
    try { await api(`/complaints/${id}`, { method: "DELETE" }); sessionStorage.setItem("cs_flash", "Draft deleted successfully."); location.hash = "#/cases"; }
    catch (x) { setMsg(x.message); }
  };
  const dl = async eid => { const r = await fetch(`/api/evidence/${eid}/download`, { headers: { Authorization: "Bearer " + TOKEN } }); if (!r.ok) return alert("Not permitted"); window.open(URL.createObjectURL(await r.blob())); };
  const rep = c.citizen_report || (c.ai_analysis && c.ai_analysis.report) || {}; const lastDec = c.decisions[c.decisions.length - 1];
  const events = [...c.status_history.map(h => ({ t: h.created_at, x: `${h.to_status} — ${h.reason || ""} (${String(h.actor).startsWith("system") ? "system" : "user " + h.actor})` })), ...c.escalations.map(x => ({ t: x.created_at, x: "ESCALATION: " + x.reason }))].sort((a, b) => a.t < b.t ? -1 : 1);
  return html`<p class="row"><a href="#/cases">← back</a>${ownDraft && html`<button class="bad" onClick=${deleteDraft}>Delete Draft</button>`}</p><h2>${c.code || "Draft"} <${Tag} s=${c.status}/> <${Tag} s=${c.stage}/></h2><h3>${c.title}</h3>
  <div class="banner">Stage "${c.stage.replace("_", " ")}" is a procedural label. Allegations are unverified unless the stage is <b>verified finding</b>. Overdue deadlines never imply guilt.</div>
  <div class="card"><b>Current authority:</b> ${c.current_authority || "not yet assigned"}<br/><b>Acknowledge by:</b> ${fmt(c.ack_deadline)} · <b>Resolve by:</b> ${fmt(c.resolution_deadline)}<br/><b>Citizen:</b> ${c.citizen.name} ${c.citizen.phone || ""}
  <br/><b>Original description (immutable, SHA-256 </b><code>${c.original_hash.slice(0, 16)}…</code><b>):</b><p>${c.original_description}</p></div>
  ${c.ai_analysis && html`<div class="card ai"><b>${c.ai_analysis.label}</b><p>${rep.incident_summary}</p>${(rep.missing_information || []).length ? html`<p class="mut">Missing: ${rep.missing_information.join("; ")}</p>` : ""}</div>`}
  ${c.duplicate_flags && c.duplicate_flags.length > 0 && html`<div class="card"><b>Possible duplicates (flagged for human review — not rejected)</b><ul>${c.duplicate_flags.map(d => html`<li>${d.code || d.note} ${d.reasons ? "— " + d.reasons.join(", ") : ""}</li>`)}</ul></div>`}
  ${me.role === "citizen" && c.citizen_id === me.id && c.status !== "draft" && html`<${EvidenceUpload} caseId=${id} reload=${reload} buttonLabel="Add Evidence"/>`}
  <div class="card"><h3>Evidence</h3>${c.evidence.map(x => html`<p>📎 ${x.original_filename} <span class="mut">(${x.size} B)</span> <code>SHA-256 ${x.sha256}</code> ${c.can_download_evidence && html`<button class="sec" onClick=${() => dl(x.id)}>open</button>`}</p>`)}${!c.evidence.length && html`<span class="mut">No evidence attached.</span>`}</div>
  <div class="card"><h3>Case timeline</h3><div class="tl">${events.map(v => html`<div><span class="mut">${fmt(v.t)}</span><br/>${v.x}</div>`)}</div></div>
  <div class="card"><h3>Official responses & decisions</h3>${c.responses.map(r => html`<p><b>${r.kind}</b> <span class="mut">${fmt(r.created_at)}</span><br/>${r.body}</p>`)}${c.decisions.map(d => html`<p><b>Decision #${d.id}: ${d.outcome}</b> ${d.supersedes_id ? `(supersedes #${d.supersedes_id}; original preserved)` : ""}<br/>${d.reasoning}</p>`)}
  ${c.appeals.map(a => html`<p><b>Appeal #${a.id}</b> <${Tag} s=${a.status}/> ${a.outcome || ""}<br/>${a.grounds}</p>`)}</div>
  <div class="card"><h3>Tamper-evident audit trail</h3><p>${c.audit_chain.valid ? html`<span class="tag ok">hash chain intact (${c.audit_chain.events} events)</span>` : html`<span class="tag bad">CHAIN BROKEN at ${c.audit_chain.broken_at}</span>`}
  <button class="sec" onClick=${async () => setAud(await api(`/complaints/${id}/audit`))}>show events</button> <button class="sec" onClick=${async () => setSum(await api(`/complaints/${id}/summary`))}>case summary (n8n)</button></p>
  ${aud && html`<table><tr><th>When</th><th>Event</th><th>Hash (prev → current)</th></tr>${aud.events.map(v => html`<tr><td>${fmt(v.timestamp)}</td><td>${v.event_type}</td><td><code>${v.previous_hash.slice(0, 8)} → ${v.current_hash.slice(0, 8)}</code></td></tr>`)}</table>`}
  ${sum && html`<pre style="white-space:pre-wrap;font-size:12px">${JSON.stringify(sum, null, 2)}</pre>`}</div>
  ${me.role === "citizen" && html`<div class="card"><h3>Your actions</h3>
    ${c.status === "info_requested" && html`<label>Reply to the information request</label><textarea value=${f.reply || ""} onInput=${ev => setF({ ...f, reply: ev.target.value })}></textarea><button onClick=${act("reply", { body: f.reply })}>Send reply</button>`}
    ${c.status === "decided" && lastDec && ["rejected", "partially_upheld"].includes(lastDec.outcome) && !c.appeals.some(a => a.decision_id === lastDec.id) && html`<label>Appeal the decision — your grounds</label><textarea value=${f.grounds || ""} onInput=${ev => setF({ ...f, grounds: ev.target.value })}></textarea><button onClick=${act("appeal", { grounds: f.grounds })}>Submit appeal</button>`}
    <p><label><input type="checkbox" checked=${!!c.legal_share} onChange=${act("legal-share", { share: !c.legal_share })}/> Share a masked case summary (no evidence files) with legal-aid providers</label></p>
    ${c.legal_assistance ? html`<${Legal} l=${c.legal_assistance}/>` : html`<button class="sec" onClick=${act("legal-help/request", {})}>Show legal-assistance options</button>`}</div>`}
  ${mine && html`<div class="card"><h3>Authority actions</h3>
    <div class="row"><button class="sec" onClick=${act("acknowledge", { note: "Complaint acknowledged." })} disabled=${c.status !== "assigned"}>Acknowledge</button></div>
    <label>Response / correction text</label><textarea value=${f.body || ""} onInput=${ev => setF({ ...f, body: ev.target.value })}></textarea>
    <div class="row"><button onClick=${act("respond", { body: f.body })}>Send response</button><button class="sec" onClick=${act("request-info", { body: f.body })}>Request information (pauses clock)</button></div>
    <label>Decision</label><div class="row"><select value=${f.outcome || ""} onChange=${ev => setF({ ...f, outcome: ev.target.value })}><option value="">Outcome…</option><option>upheld</option><option>partially_upheld</option><option>rejected</option><option>resolved</option></select>
    <input type="text" placeholder="Reasoning (required)" style="flex:1" value=${f.reasoning || ""} onInput=${ev => setF({ ...f, reasoning: ev.target.value })}/><button onClick=${act("decision", { outcome: f.outcome, reasoning: f.reasoning })}>Record decision</button></div>
    ${peers && peers.length > 0 && html`<label>Transfer to peer authority</label><div class="row"><select onChange=${ev => setF({ ...f, to: +ev.target.value })}><option value="">Select…</option>${peers.map(p => html`<option value=${p.id}>${p.name}</option>`)}</select><button class="sec" onClick=${act("transfer", { to_authority_id: f.to, reason: f.body })}>Transfer (uses response text as reason)</button></div>`}
    <p class="mut">Officials cannot delete the complaint, evidence, decisions or audit events; corrections are added as new records.</p></div>`}
  ${me.role === "legal_aid" && c.legal_assistance && html`<${Legal} l=${c.legal_assistance}/>`}<${Err} e=${msg}/>`;
}

function Legal({ l }) {
  return html`<div class="card"><h3>Legal help</h3><div class="banner"><b>${l.disclaimer}</b></div><h4>General procedural options</h4><ul>${l.options.map(o => html`<li><b>${o.option}</b> — ${o.what} <span class="mut">(${o.typical_forum})</span></li>`)}</ul>
  <h4>Legal-aid directory (free & low-cost first)</h4><table>${l.providers.map(p => html`<tr><td>${p.name}<br/><span class="mut">${p.services}</span></td><td><${Tag} s=${p.cost}/></td><td>${p.eligibility}<br/>${p.contact}</td></tr>`)}</table>
  <h4>Checklist</h4><ul>${l.checklist.map(x => html`<li>${x}</li>`)}</ul></div>`;
}

function Notifications() {
  const [n, e, reload] = useLoad(() => api("/notifications")); if (!n) return null;
  return html`<h2>Notifications</h2>${n.map(x => html`<div class="card" style=${x.is_read ? "opacity:.65" : ""}><b>${x.title}</b> <span class="tag">${x.channel.replace("_", " ")}</span> <span class="mut">${fmt(x.created_at)}</span><br/>${x.message} ${x.complaint_id && html`<a href=${"#/case/" + x.complaint_id}>open</a>`} ${!x.is_read && html`<button class="sec" onClick=${async () => { await api(`/notifications/${x.id}/read`, { method: "POST" }); reload(); }}>mark read</button>`}</div>`)}`;
}

function Appeals({ me }) {
  const [a, e, reload] = useLoad(() => api("/appeals")); const [f, setF] = useState({}); const [m, setM] = useState("");
  if (!a) return html`<${Err} e=${e}/>`;
  return html`<h2>Appeals</h2>${a.map(x => html`<div class="card"><b>${x.code}</b> ${x.title || ""} <${Tag} s=${x.status}/> ${x.outcome || ""}<br/>${x.grounds}<br/><span class="mut">Review deadline ${fmt(x.deadline)} · <a href=${"#/case/" + x.complaint_id}>open case</a></span>
  ${me.role === "supervisor" && x.status === "under_review" && html`<div class="row"><select onChange=${ev => setF({ ...f, [x.id]: { ...f[x.id], o: ev.target.value } })}><option value="">Outcome…</option><option>overturned</option><option>modified</option><option>upheld</option></select>
  <input type="text" placeholder="Reasoning" style="flex:1" onInput=${ev => setF({ ...f, [x.id]: { ...f[x.id], r: ev.target.value } })}/><button onClick=${async () => { try { await api(`/appeals/${x.id}/decide`, { body: { outcome: (f[x.id] || {}).o, reasoning: (f[x.id] || {}).r } }); reload(); } catch (z) { setM(z.message); } }}>Decide appeal</button></div>`}</div>`)}${!a.length && html`<p class="mut">No appeals.</p>`}<${Err} e=${m}/>`;
}

function Supervisor() {
  const [ov, , r1] = useLoad(() => api("/supervisor/overdue")); const [es] = useLoad(() => api("/supervisor/escalated")); const [an] = useLoad(() => api("/supervisor/analytics")); const [msg, setMsg] = useState("");
  const pct = v => v == null ? "—" : Math.round(v * 100) + "%";
  return html`<h2>Oversight</h2><div class="card"><h3>Overdue cases</h3><p class="mut">Overdue means a procedural deadline passed. It is not a finding of misconduct.</p>${ov && html`<table>${ov.map(o => html`<tr><td><a href=${"#/case/" + o.complaint_id}>${o.code}</a></td><td>${o.title}</td><td>${o.deadline_type} due ${fmt(o.deadline)}</td><td>${o.can_escalate ? "→ " + o.next_authority.name : "top of hierarchy"}</td></tr>`)}</table>`}
  <button onClick=${async () => { try { setMsg(JSON.stringify(await api("/admin/run-deadline-monitor", { method: "POST" })).slice(0, 300)); r1(); } catch (x) { setMsg(x.message); } }}>Run n8n deadline monitor now</button> <span class="mut">${msg}</span></div>
  <div class="card"><h3>Escalations</h3>${es && html`<table>${es.map(x => html`<tr><td><a href=${"#/case/" + x.complaint_id}>${x.code}</a></td><td>${fmt(x.created_at)}</td><td>${x.reason}</td></tr>`)}</table>`}</div>
  ${an && html`<div class="card"><h3>Department performance & accountability analytics</h3><div class="banner">${an.overall.caution}</div><table><tr><th>Department</th><th>Volume</th><th>Response rate</th><th>Avg response (h)</th><th>Resolution rate</th><th>Overdue</th><th>Verified findings</th><th>Appeals</th></tr>
  ${an.departments.map(d => html`<tr><td>${d.department}</td><td>${d.complaint_volume}</td><td>${pct(d.response_rate)}</td><td>${d.avg_response_hours ?? "—"}</td><td>${pct(d.resolution_rate)}</td><td>${d.overdue_cases}</td><td>${d.verified_findings}</td><td>${JSON.stringify(d.appeal_outcomes)}</td></tr>`)}</table>
  <p class="mut">Stages: ${Object.entries(an.overall.by_stage).map(([k, v]) => k.replace("_", " ") + " " + v).join(" · ")}</p></div>`}`;
}

function Admin() {
  const [tab, setTab] = useState("users"); const [u] = useLoad(() => api("/admin/users")); const [cfg] = useLoad(() => api("/admin/config")); const [au] = useLoad(() => api("/admin/audit")); const [runs, , rr] = useLoad(() => api("/admin/workflow-runs"));
  return html`<h2>Administration</h2><div class="row">${["users", "config", "audit", "workflows"].map(t => html`<button class=${tab === t ? "" : "sec"} onClick=${() => setTab(t)}>${t}</button>`)}</div>
  ${tab === "users" && u && html`<div class="card"><table>${u.map(x => html`<tr><td>${x.email}</td><td>${x.full_name}</td><td>${x.role}</td><td>${x.active ? "active" : "disabled"}</td></tr>`)}</table></div>`}
  ${tab === "config" && cfg && html`<div class="card"><h3>Escalation rules (hours)</h3><table><tr><th>Level</th><th>Ack</th><th>Review</th><th>Resolution</th><th>Appeal</th></tr>${cfg.rules.map(r => html`<tr><td>${r.level}</td><td>${r.ack_hours}</td><td>${r.review_hours}</td><td>${r.resolution_hours}</td><td>${r.appeal_hours}</td></tr>`)}</table>
  <h3>Authority hierarchy</h3><table>${cfg.authorities.map(a => html`<tr><td>${a.name}</td><td>L${a.level} ${a.level_name}</td><td>${a.jurisdiction}</td><td>escalates to #${a.parent_authority_id || "— (top)"}</td></tr>`)}</table>
  <h3>Categories</h3><p>${cfg.categories.map(c => c.name).join(" · ")}</p><h3>Legal aid</h3><p>${cfg.legal_aid.map(l => l.name + " (" + l.cost + ")").join(" · ")}</p></div>`}
  ${tab === "audit" && au && html`<div class="card"><h3>Hash-chain verification</h3><table>${au.chains.map(c => html`<tr><td>case ${c.complaint_id}</td><td>${c.valid ? "✔ intact" : "✖ " + c.reason}</td><td>${c.events} events</td></tr>`)}</table><h3>Recent events (metadata withheld)</h3><table>${au.recent.map(e => html`<tr><td>${fmt(e.timestamp)}</td><td>${e.event_type}</td><td>${e.actor_id}</td><td><code>${e.current_hash.slice(0, 10)}</code></td></tr>`)}</table></div>`}
  ${tab === "workflows" && runs && html`<div class="card"><h3>n8n workflow executions started by the backend</h3><table><tr><th>When</th><th>Workflow</th><th>Case</th><th>Status</th><th></th></tr>${runs.map(r => html`<tr><td>${fmt(r.created_at)}</td><td>${r.workflow}</td><td>${r.complaint_id || ""}</td><td><${Tag} s=${r.status === "completed" ? "resolved" : r.status === "pending" ? "unresolved" : "assigned"}/> ${r.error || ""}</td><td>${r.status === "pending" && html`<button class="sec" onClick=${async () => { await api(`/admin/workflow-runs/${r.id}/retry`, { method: "POST" }); rr(); }}>retry</button>`}</td></tr>`)}</table></div>`}`;
}

function Legalaid() { const [d] = useLoad(() => api("/legal-aid")); return d && html`<h2>Legal-aid directory</h2><div class="card"><table>${d.map(p => html`<tr><td>${p.name}<br/><span class="mut">${p.services}</span></td><td><${Tag} s=${p.cost}/></td><td>${p.region}</td><td>${p.eligibility}</td></tr>`)}</table></div>`; }

const NAV = { citizen: [["", "Dashboard"], ["report", "Report Incident"], ["cases", "My Complaints"], ["notifications", "Notifications"], ["appeals", "Appeals"], ["legal", "Legal Help"]],
  official: [["", "Dashboard"], ["cases", "Assigned Cases"], ["notifications", "Notifications"]], supervisor: [["", "Dashboard"], ["cases", "Cases"], ["oversight", "Escalated / Overdue / Analytics"], ["appeals", "Appeals"], ["notifications", "Notifications"]],
  legal_aid: [["cases", "Shared Cases"], ["legal", "Directory"]], admin: [["", "Dashboard"], ["admin", "Admin"], ["oversight", "Oversight"], ["cases", "All Cases"], ["notifications", "Notifications"]] };
function App() {
  const [me, setMe] = useState(null); const [meta, setMeta] = useState(null); const [h, setH] = useState(location.hash || "#/");
  const [theme, setTheme] = useState(() => localStorage.getItem("cs_theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  const load = () => TOKEN ? api("/me").then(setMe).catch(() => { TOKEN = ""; sessionStorage.removeItem("cs_token"); setMe(null); }) : setMe(null);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("cs_theme", theme); }, [theme]);
  useEffect(() => { load(); api("/meta").then(setMeta); const f = () => setH(location.hash || "#/"); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  if (!me) return html`<${Login} onLogin=${load}/>`; if (!meta) return null;
  const [, route, arg] = h.split("/"); const go = x => { location.hash = x; };
  let page;
  if (route === "case") page = html`<${Case} id=${+arg} me=${me}/>`; else if (route === "report") page = html`<${Report} meta=${meta} go=${go}/>`; else if (route === "cases") page = html`<${CaseList} me=${me}/>`;
  else if (route === "notifications") page = html`<${Notifications}/>`; else if (route === "appeals") page = html`<${Appeals} me=${me}/>`; else if (route === "oversight") page = html`<${Supervisor}/>`;
  else if (route === "admin") page = html`<${Admin}/>`; else if (route === "legal") page = me.role === "citizen" ? html`<${CaseList} me=${me} filter=${() => true}/>` : html`<${Legalaid}/>`; else page = me.role === "legal_aid" ? html`<${CaseList} me=${me}/>` : html`<${Dashboard} me=${me}/>`;
  return html`<header><b>🛡 CivicShield</b><nav>${NAV[me.role].map(([r, l]) => html`<a class=${route === r || (!route && !r) ? "on" : ""} onClick=${() => go("#/" + r)}>${l}</a>`)}</nav><span>${me.full_name} (${me.role})</span><button class="sec" aria-pressed=${theme === "dark"} onClick=${() => setTheme(theme === "dark" ? "light" : "dark")}>${theme === "dark" ? "Light mode" : "Dark mode"}</button><button class="sec" style="color:#fff;border-color:#fff" onClick=${() => { TOKEN = ""; sessionStorage.removeItem("cs_token"); setMe(null); }}>Sign out</button></header><main>${page}</main>`;
}
ReactDOM.createRoot(document.getElementById("root")).render(html`<${App}/>`);

