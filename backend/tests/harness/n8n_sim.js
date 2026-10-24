// OFFLINE n8n SUBSET RUNNER (test harness only; real n8n is not installable in this sandbox).
// Loads the REAL workflow JSON files and executes: webhook, scheduleTrigger, code, httpRequest, if, respondToWebhook (+ expressions).
// It does NOT validate n8n-specific schema details; import into real n8n for that.
const http = require('http'), fs = require('fs'), path = require('path'), crypto = require('crypto');
const DIR = process.argv[2], PORT = +(process.env.N8N_PORT || 5678);
const ENV = { ...process.env };
const wfs = fs.readdirSync(DIR).filter(f => f.endsWith('.json')).map(f => JSON.parse(fs.readFileSync(path.join(DIR, f))));
const hooks = {}; for (const wf of wfs) for (const n of wf.nodes) if (n.type === 'n8n-nodes-base.webhook') hooks[n.parameters.path] = { wf, node: n };
let execId = 1000;
function ctxFor(item, outs, items, execution) {
  const ctx = { $json: item ? item.json : undefined, $env: ENV, $execution: execution, Buffer, JSON, crypto,
    $: name => { const o = outs[name]; if (!o) throw new Error('Node not executed: ' + name); return { first: () => o[0], all: () => o, item: o[0] }; },
    $input: { first: () => items[0], all: () => items, item: items[0] } };
  return ctx;
}
function ev(str, ctx) {
  if (typeof str !== 'string' || !str.startsWith('=')) return str;
  const s = str.slice(1), run = e => new Function('ctx', 'with(ctx){return (' + e + ')}')(ctx);
  const m = s.match(/^\{\{([\s\S]*)\}\}$/);
  if (m && s.split('{{').length === 2) return run(m[1]);
  return s.replace(/\{\{([\s\S]*?)\}\}/g, (_, e) => { const v = run(e); return typeof v === 'object' ? JSON.stringify(v) : String(v); });
}
async function execNode(wf, node, items, outs, st) {
  const p = node.parameters; let branches = [items]; const t = node.type.split('.').pop();
  st.trace.push(node.name);
  if (t === 'webhook' || t === 'scheduleTrigger') { /* items already injected */ }
  else if (t === 'code') {
    const fn = new (Object.getPrototypeOf(async function () {}).constructor)('$input', '$', '$env', '$execution', 'Buffer', 'require', p.jsCode);
    const c = ctxFor(null, outs, items, st.execution);
    const r = await fn(c.$input, c.$, ENV, st.execution, Buffer, require);
    branches = [r];
  } else if (t === 'httpRequest') {
    const out = [];
    for (const it of items) {
      const c = ctxFor(it, outs, items, st.execution), url = ev(p.url, c), h = {};
      for (const x of (p.headerParameters ? p.headerParameters.parameters : [])) h[x.name] = ev(x.value, c);
      let body; if (p.sendBody) { body = ev(p.jsonBody, c); if (typeof body !== 'string') body = JSON.stringify(body); }
      const r = await fetch(url, { method: p.method, headers: h, body });
      const txt = await r.text(); let js; try { js = JSON.parse(txt); } catch (e) { js = { raw: txt }; }
      if (!r.ok) { if (node.onError === 'continueRegularOutput') { out.push({ json: { error: js, status: r.status } }); continue; } throw new Error(`${node.name}: HTTP ${r.status} ${txt.slice(0, 300)}`); }
      out.push({ json: js });
    }
    branches = [out];
  } else if (t === 'if') {
    const tr = [], fa = [];
    for (const it of items) { const c = ctxFor(it, outs, items, st.execution); (ev(p.conditions.conditions[0].leftValue, c) ? tr : fa).push(it); }
    branches = [tr, fa];
  } else if (t === 'respondToWebhook') {
    if (!st.response) { const c = ctxFor(items[0], outs, items, st.execution); let b = ev(p.responseBody, c); st.response = typeof b === 'string' ? JSON.parse(b) : b; }
    branches = [items];
  } else if (t === 'emailSend' || t === 'stickyNote') { branches = [items]; }
  else throw new Error('Unsupported node type in sim: ' + node.type);
  outs[node.name] = branches[0].length ? branches[0] : (outs[node.name] || []); st.lastNode = branches[0];
  const conn = (wf.connections[node.name] || { main: [] }).main;
  for (let b = 0; b < conn.length; b++) {
    if (!branches[b] || !branches[b].length) continue;
    for (const tgt of conn[b]) await execNode(wf, wf.nodes.find(n => n.name === tgt.node), branches[b], outs, st);
  }
}
async function runWebhook(path_, body) {
  const h = hooks[path_]; if (!h) return [404, { error: 'no workflow for path ' + path_ }];
  const st = { trace: [], response: null, execution: { id: String(++execId) } };
  try {
    await execNode(h.wf, h.node, [{ json: { body, headers: {}, query: {} } }], {}, st);
    const rm = h.node.parameters.responseMode;
    const res = st.response || (rm === 'lastNode' ? (st.lastNode[0] || {}).json : null);
    console.log(`[n8n-sim] ${h.wf.name} exec#${st.execution.id}: ${st.trace.join(' > ')}`);
    return [200, res || { ok: true }];
  } catch (e) { console.log(`[n8n-sim] ${h.wf.name} FAILED: ${e.message}`); return [500, { error: e.message }]; }
}
http.createServer((req, res) => {
  let raw = ''; req.on('data', d => raw += d); req.on('end', async () => {
    let body = {}; try { body = raw ? JSON.parse(raw) : {}; } catch (e) {}
    const m = req.url.match(/^\/webhook\/([\w-]+)/); let out = [404, { error: 'not found' }];
    if (m && req.method === 'POST') {
      if (req.headers['x-civicshield-key'] !== ENV.INTERNAL_API_KEY && false) out = [401, {}];
      out = await runWebhook(m[1], body);
    }
    res.writeHead(out[0], { 'Content-Type': 'application/json' }); res.end(JSON.stringify(out[1]));
  });
}).listen(PORT, '127.0.0.1', () => console.log('n8n-sim listening', PORT));
