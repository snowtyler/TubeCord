"""Operator dashboard: a self-contained HTML page for checking/debugging WebSub.

Served by ``main.py`` at ``/`` and ``/dashboard``. It consumes the existing
JSON endpoints (``/websub/status``, ``/health``, ``/community/status``) and the
action endpoints (``/subscribe``, ``/unsubscribe``, the ``/test-*`` injectors)
so there is no server-side templating and no external dependencies.
"""

# Note: kept as a single string so the page has zero external assets (works on
# a locked-down host with no CDN access). The version is injected at render time.
_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TubeCord — WebSub Dashboard</title>
<style>
  :root {
    --bg:#0f1115; --card:#191c22; --card2:#20242c; --line:#2b303a;
    --fg:#e6e9ef; --muted:#9aa4b2; --accent:#5865f2; --ok:#3ba55d;
    --warn:#faa61a; --bad:#ed4245; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:14px/1.5 system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
    padding:14px 20px; border-bottom:1px solid var(--line); background:var(--card); }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .ver { color:var(--muted); font:12px var(--mono); }
  header .spacer { flex:1; }
  .refresh { color:var(--muted); font-size:12px; display:flex; align-items:center; gap:6px; }
  main { padding:20px; max-width:1000px; margin:0 auto; display:grid; gap:16px; }
  .grid { display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
  .card h2 { margin:0 0 12px; font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
  .row { display:flex; justify-content:space-between; gap:12px; padding:5px 0; border-bottom:1px dashed var(--line); }
  .row:last-child { border-bottom:0; }
  .row .k { color:var(--muted); }
  .row .v { font:12.5px var(--mono); text-align:right; word-break:break-all; }
  .badge { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:600; }
  .b-ok { background:rgba(59,165,93,.15); color:var(--ok); }
  .b-bad { background:rgba(237,66,69,.15); color:var(--bad); }
  .b-warn { background:rgba(250,166,26,.15); color:var(--warn); }
  .b-muted { background:rgba(154,164,178,.15); color:var(--muted); }
  .warnbox { background:rgba(250,166,26,.08); border:1px solid var(--warn);
    border-radius:8px; padding:10px 12px; color:var(--warn); font-size:13px; }
  .warnbox.ok { background:rgba(59,165,93,.08); border-color:var(--ok); color:var(--ok); }
  .actions { display:flex; flex-wrap:wrap; gap:8px; }
  button { background:var(--card2); color:var(--fg); border:1px solid var(--line);
    padding:8px 12px; border-radius:8px; cursor:pointer; font-size:13px; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); }
  button.danger:hover { border-color:var(--bad); }
  button:disabled { opacity:.5; cursor:default; }
  pre#out { background:#0b0d11; border:1px solid var(--line); border-radius:8px;
    padding:12px; overflow:auto; max-height:320px; font:12px var(--mono);
    color:var(--muted); white-space:pre-wrap; word-break:break-word; margin:0; }
  .muted { color:var(--muted); font-size:12px; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>TubeCord · WebSub Dashboard</h1>
  <span class="ver">v__VERSION__</span>
  <span class="spacer"></span>
  <label class="refresh"><input type="checkbox" id="auto" checked> auto-refresh (10s)</label>
  <button id="refreshBtn" onclick="refreshAll()">Refresh</button>
</header>
<main>
  <div id="verdict" class="warnbox b-muted">Loading…</div>

  <div class="grid">
    <div class="card">
      <h2>Subscription</h2>
      <div id="subCard"></div>
    </div>
    <div class="card">
      <h2>Timing</h2>
      <div id="timeCard"></div>
    </div>
    <div class="card">
      <h2>Endpoint</h2>
      <div id="epCard"></div>
    </div>
    <div class="card">
      <h2>Discord &amp; Community</h2>
      <div id="dcCard"></div>
    </div>
  </div>

  <div class="card">
    <h2>Actions</h2>
    <div class="actions">
      <button class="primary" onclick="act('GET','/subscribe','Re-subscribe (with retry)?')">Subscribe</button>
      <button class="danger" onclick="act('GET','/unsubscribe','Unsubscribe from the hub? Delivery will stop until you subscribe again.')">Unsubscribe</button>
      <button onclick="act('POST','/upload/check','Poll the upload feed now for anything WebSub missed?')">Poll uploads now</button>
      <button onclick="act('POST','/community/check','Force a community-post check now?')">Force community check</button>
      <button onclick="act('POST','/test-notification','Send a TEST upload to the test channel?')">Test upload → test ch.</button>
      <button onclick="act('POST','/test-livestream','Send a TEST livestream to the test channel?')">Test livestream → test ch.</button>
      <button onclick="act('POST','/test-community','Send a TEST community post to the test channel?')">Test community → test ch.</button>
      <button onclick="loadRaw('/websub/status')">Raw /websub/status</button>
      <button onclick="loadRaw('/health')">Raw /health</button>
    </div>
    <p class="muted" style="margin:10px 0 6px">Output</p>
    <pre id="out">—</pre>
  </div>

  <p class="muted">This page reads the bot's own diagnostic endpoints. Hub-side
  truth (what YouTube actually recorded) lives on the
  <a href="https://pubsubhubbub.appspot.com/subscription-details" target="_blank" rel="noopener">hub's subscription-details page</a>.</p>
</main>

<script>
var timer = null;

function ago(sec) {
  if (sec == null) return "—";
  sec = Math.floor(sec);
  var s = sec % 60, m = Math.floor(sec/60)%60, h = Math.floor(sec/3600)%24, d = Math.floor(sec/86400);
  var parts = [];
  if (d) parts.push(d+"d"); if (h) parts.push(h+"h"); if (m && !d) parts.push(m+"m");
  if (!d && !h && !m) parts.push(s+"s");
  return parts.join(" ") + " ago";
}
function inFuture(sec) {
  if (sec == null) return "—";
  if (sec <= 0) return "expired";
  return "in " + ago(sec).replace(" ago","");
}
function badge(cls, txt) { return '<span class="badge '+cls+'">'+txt+'</span>'; }
function row(k, v) { return '<div class="row"><span class="k">'+k+'</span><span class="v">'+v+'</span></div>'; }
function esc(x){ return (x==null?"—":String(x)).replace(/[&<>]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

async function getJSON(url, opts) {
  var r = await fetch(url, opts);
  var t = await r.text();
  try { return { ok:r.ok, status:r.status, body:JSON.parse(t) }; }
  catch(e) { return { ok:r.ok, status:r.status, body:t }; }
}

async function refreshAll() {
  try {
    var s = (await getJSON('/websub/status')).body;
    var h = (await getJSON('/health')).body;
    var confirmed = !!s.subscription_confirmed;
    var expired = !!s.subscription_expired;

    // Verdict banner
    var v = document.getElementById('verdict');
    if (confirmed && !expired) { v.className = 'warnbox ok'; v.textContent = '✓ Subscription verified and live — delivery should be working.'; }
    else if ((s.warnings||[]).length) { v.className = 'warnbox'; v.innerHTML = '⚠ ' + s.warnings.map(esc).join('<br>⚠ '); }
    else { v.className = 'warnbox b-muted'; v.textContent = 'Status unknown / no verification yet.'; }

    // Subscription card
    document.getElementById('subCard').innerHTML =
      row('Accepted (active)', badge(s.subscription_active?'b-ok':'b-bad', s.subscription_active?'yes':'no')) +
      row('Verified (confirmed)', badge(confirmed?'b-ok':'b-bad', confirmed?'yes':'no')) +
      row('Expired', badge(expired?'b-bad':'b-ok', expired?'yes':'no')) +
      row('Lease', esc(s.lease_seconds)+'s');

    // Timing card
    document.getElementById('timeCard').innerHTML =
      row('Last verification', ago(s.seconds_since_verification)) +
      row('Last notification', ago(s.seconds_since_notification)) +
      row('Last subscribe req', ago(s.seconds_since_subscription)) +
      row('Expires', inFuture(s.subscription_expires_in));

    // Endpoint card
    document.getElementById('epCard').innerHTML =
      row('Callback', esc(s.callback_url)) +
      row('Channel', esc(s.channel_id)) +
      row('Hub', esc(s.hub_url));

    // Discord card
    var ds = (h && h.discord_servers) || {};
    var dcHtml = row('Upload servers', esc(ds.upload)) + row('Livestream servers', esc(ds.livestream)) + row('Community servers', esc(ds.community));
    try {
      var c = (await getJSON('/community/status')).body;
      if (c && typeof c === 'object')
        dcHtml += row('Community monitoring', badge(c.enabled?'b-ok':'b-muted', c.enabled?'on':'off')) +
                  (c.enabled ? row('Unnotified posts', esc(c.unnotified_posts)) : '');
    } catch(e){}
    try {
      var u = (await getJSON('/upload/status')).body;
      if (u && typeof u === 'object') {
        dcHtml += row('Upload poll fallback', badge(u.enabled?'b-ok':'b-muted', u.enabled?'on':'off'));
        if (u.enabled) dcHtml += row('Poll interval', esc(u.interval_minutes)+'m') +
                                 row('Last poll', u.last_check_time ? ago(Math.floor((Date.now()-Date.parse(u.last_check_time))/1000)) : '—');
      }
    } catch(e){}
    document.getElementById('dcCard').innerHTML = dcHtml;
  } catch(e) {
    document.getElementById('verdict').className = 'warnbox';
    document.getElementById('verdict').textContent = 'Failed to load status: ' + e;
  }
}

async function act(method, url, confirmMsg) {
  if (confirmMsg && !confirm(confirmMsg)) return;
  var out = document.getElementById('out');
  out.textContent = method + ' ' + url + ' …';
  try {
    var r = await getJSON(url, { method:method });
    out.textContent = method+' '+url+'  → HTTP '+r.status+'\n\n'+
      (typeof r.body==='string'? r.body : JSON.stringify(r.body, null, 2));
  } catch(e) { out.textContent = 'Error: ' + e; }
  refreshAll();
}
async function loadRaw(url) {
  var out = document.getElementById('out');
  out.textContent = 'GET '+url+' …';
  var r = await getJSON(url);
  out.textContent = 'GET '+url+'  → HTTP '+r.status+'\n\n'+JSON.stringify(r.body, null, 2);
}

function setupAuto() {
  var cb = document.getElementById('auto');
  function tick(){ if (cb.checked) refreshAll(); }
  if (timer) clearInterval(timer);
  timer = setInterval(tick, 10000);
}
document.getElementById('auto').addEventListener('change', setupAuto);
refreshAll(); setupAuto();
</script>
</body>
</html>"""


def render_dashboard(version: str) -> str:
    """Return the dashboard HTML with the running version injected."""
    return _PAGE.replace("__VERSION__", version)
