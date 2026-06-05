import json
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import db
from . import tags

STATIC_DIR = Path(__file__).parent / "static"


def _resolve_range(qs):
    """Turn query params (days | since/until) into (since_iso, until_iso)."""
    def _day(value, end):
        d = datetime.strptime(value, "%Y-%m-%d")
        if end:
            d = d.replace(hour=23, minute=59, second=59)
        return d.isoformat()

    since = qs["since"][0] if qs.get("since") else None
    until = qs["until"][0] if qs.get("until") else None
    until_iso = _day(until, end=True) if until else None

    if since:
        return _day(since, end=False), until_iso
    if qs.get("days"):
        days = int(qs["days"][0])
        if days > 0:
            return (datetime.now() - timedelta(days=days)).isoformat(), until_iso
    return None, until_iso


PAGE = """<!doctype html>
<html lang="en" class="h-full">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blood Pressure</title>
<script src="/static/tailwind.js"></script>
<script src="/static/chart.js"></script>
<script>
tailwind.config = { theme: { extend: { borderRadius: { DEFAULT: "2px" } } } };
</script>
<style>
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-thumb { background: #1e293b; }
  body { font-feature-settings: "tnum"; }
</style>
</head>
<body class="h-full bg-[#0a0e14] text-slate-300 text-[13px]">
<div class="mx-auto max-w-5xl px-4 py-6">
  <header class="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-4">
    <div>
      <h1 class="text-lg font-semibold tracking-tight text-slate-100">Blood Pressure</h1>
      <p id="count" class="text-xs text-slate-500"></p>
    </div>
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <select id="range" class="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-200 focus:border-slate-500 focus:outline-none">
        <option value="7">Last 7 days</option>
        <option value="30" selected>Last 30 days</option>
        <option value="90">Last 90 days</option>
        <option value="365">Last year</option>
        <option value="0">All time</option>
        <option value="custom">Custom…</option>
      </select>
      <input id="since" type="date" class="hidden rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-200 [color-scheme:dark]">
      <input id="until" type="date" class="hidden rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-200 [color-scheme:dark]">
      <select id="user" class="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-200 focus:border-slate-500 focus:outline-none">
        <option value="">All users</option>
        <option value="1">User 1</option>
        <option value="2">User 2</option>
      </select>
      <select id="tagFilter" class="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-200 focus:border-slate-500 focus:outline-none">
        <option value="">All tags</option>
      </select>
      <label class="flex items-center gap-1.5 text-slate-400 select-none">
        <input id="groupChk" type="checkbox" checked class="accent-cyan-500"> Group 5m
      </label>
      <button id="addBtn" class="rounded border border-emerald-700 bg-emerald-900/40 px-2.5 py-1.5 font-medium text-emerald-300 hover:bg-emerald-900/70">+ Add</button>
    </div>
  </header>

  <div id="stats" class="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4"></div>

  <div class="mb-5 border border-slate-800 bg-slate-900/40 p-3">
    <canvas id="chart" height="96"></canvas>
  </div>

  <div class="border border-slate-800 bg-slate-900/40">
    <table class="w-full">
      <thead class="text-left text-[11px] uppercase tracking-wide text-slate-500">
        <tr class="border-b border-slate-800">
          <th class="px-3 py-2 font-medium">Timestamp</th>
          <th class="px-3 py-2 text-right font-medium">Sys</th>
          <th class="px-3 py-2 text-right font-medium">Dia</th>
          <th class="px-3 py-2 text-right font-medium">MAP</th>
          <th class="px-3 py-2 text-right font-medium">Pulse</th>
          <th class="px-3 py-2 text-right font-medium">User</th>
          <th class="px-3 py-2 font-medium">Tags</th>
          <th class="px-3 py-2 text-right font-medium"></th>
        </tr>
      </thead>
      <tbody id="rows" class="divide-y divide-slate-800/60"></tbody>
    </table>
  </div>
</div>

<dialog id="dlg" class="m-auto w-[min(92vw,420px)] rounded border border-slate-700 bg-slate-900 p-0 text-slate-200 backdrop:bg-black/60">
  <form id="form" method="dialog" class="p-4">
    <div class="mb-3 flex items-center justify-between">
      <h2 id="dlgTitle" class="text-sm font-semibold text-slate-100">Edit record</h2>
      <span id="dlgId" class="text-xs text-slate-500"></span>
    </div>
    <div class="grid grid-cols-2 gap-3 text-xs">
      <label class="col-span-2 flex flex-col gap-1">Timestamp
        <input name="timestamp" type="datetime-local" class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200 [color-scheme:dark]">
      </label>
      <label class="flex flex-col gap-1">Systolic
        <input name="systolic" type="number" required class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">
      </label>
      <label class="flex flex-col gap-1">Diastolic
        <input name="diastolic" type="number" required class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">
      </label>
      <label class="flex flex-col gap-1">Pulse
        <input name="pulse" type="number" class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">
      </label>
      <label class="flex flex-col gap-1">User
        <input name="user_id" type="number" min="1" max="2" class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">
      </label>
      <div class="col-span-2 flex flex-col gap-1">Tags
        <div id="tagPicker" class="flex flex-wrap gap-1.5"></div>
      </div>
    </div>
    <div class="mt-5 flex items-center justify-between">
      <button type="button" id="dlgDelete" class="rounded border border-rose-800 px-2.5 py-1.5 text-xs text-rose-400 hover:bg-rose-950/60">Delete</button>
      <div class="flex gap-2">
        <button type="button" id="dlgCancel" class="rounded border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">Cancel</button>
        <button type="submit" class="rounded border border-emerald-700 bg-emerald-900/50 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-900">Save</button>
      </div>
    </div>
  </form>
</dialog>

<script>
const dot = { green: "bg-emerald-500", yellow: "bg-amber-500", red: "bg-rose-500" };
const txt = { green: "text-emerald-400", yellow: "text-amber-400", red: "text-rose-400" };

const cat = (s, d) => (s >= 140 || d >= 90) ? "red" : (s >= 130 || d >= 80) ? "yellow" : "green";

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const fmt = (ts, f = dateFmt) => { if (!ts) return "—"; const d = new Date(ts); return isNaN(d) ? ts : f.format(d); };

let chart, vocab = [], editing = null;

function buildQuery() {
  const p = new URLSearchParams();
  const u = document.getElementById("user").value;
  if (u) p.set("user", u);
  const tf = document.getElementById("tagFilter").value;
  if (tf) p.set("tag", tf);
  const range = document.getElementById("range").value;
  if (range === "custom") {
    const since = document.getElementById("since").value;
    const until = document.getElementById("until").value;
    if (since) p.set("since", since);
    if (until) p.set("until", until);
  } else {
    p.set("days", range);
  }
  const qs = p.toString();
  return qs ? "?" + qs : "";
}

async function loadTags() {
  vocab = (await (await fetch("/api/tags", { cache: "no-store" })).json()).tags;
  const sel = document.getElementById("tagFilter");
  const cur = sel.value;
  sel.innerHTML = '<option value="">All tags</option>' +
    vocab.map(t => `<option value="${t}">${t}</option>`).join("");
  sel.value = cur;
}

// Group readings within `mins` minutes; lead = lowest (sys, dia, pulse).
function groupSessions(records, mins) {
  const ms = mins * 60000;
  const ts = r => r.timestamp ? new Date(r.timestamp).getTime() : NaN;
  const dated = records.filter(r => !isNaN(ts(r))).sort((a, b) => ts(a) - ts(b));
  const undated = records.filter(r => isNaN(ts(r)));
  const raw = [];
  let cur = [];
  for (const r of dated) {
    if (cur.length && ts(r) - ts(cur[cur.length - 1]) <= ms) cur.push(r);
    else { if (cur.length) raw.push(cur); cur = [r]; }
  }
  if (cur.length) raw.push(cur);
  for (const r of undated) raw.push([r]);
  const lower = (a, b) =>
    a.systolic - b.systolic || a.diastolic - b.diastolic || (a.pulse ?? 9999) - (b.pulse ?? 9999);
  const groups = raw.map(g => ({
    lead: [...g].sort(lower)[0],
    members: [...g].sort((a, b) => ts(b) - ts(a)),
    count: g.length,
  }));
  groups.sort((a, b) => (ts(b.lead) || 0) - (ts(a.lead) || 0));
  return groups;
}

async function load() {
  const res = await fetch("/api/records" + buildQuery(), { cache: "no-store" });
  const { records, stats } = await res.json();

  document.getElementById("count").textContent = stats.count + " records";

  const card = (label, val, color) =>
    `<div class="border border-slate-800 bg-slate-900/60 px-3 py-2.5">
       <div class="text-[10px] font-medium uppercase tracking-wide text-slate-500">${label}</div>
       <div class="mt-0.5 text-lg font-semibold tabular-nums ${color || "text-slate-100"}">${val}</div>
     </div>`;
  const s = stats.systolic, d = stats.diastolic, p = stats.pulse;
  document.getElementById("stats").innerHTML = stats.count ? [
    card("Avg", `${s.avg}/${d.avg}`, txt[cat(s.avg, d.avg)]),
    card("Min", `${s.min}/${d.min}`),
    card("Max", `${s.max}/${d.max}`),
    card("Pulse avg", `${p.avg ?? "—"}`),
  ].join("") : "";

  const grouped = document.getElementById("groupChk").checked;
  const groups = grouped ? groupSessions(records, 5) : records.map(r => ({ lead: r, members: [r], count: 1 }));

  const recRow = (r, { sub = false, badge = "" } = {}) => {
    const c = cat(r.systolic, r.diastolic);
    const chips = (r.tags || []).map(t =>
      `<span class="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-cyan-300">${t}</span>`).join(" ");
    const tone = sub ? "text-slate-600" : txt[c];
    return `<tr class="hover:bg-slate-800/40 ${sub ? "bg-slate-900/30" : ""}" data-gid="${r._gid ?? ""}" ${sub ? 'data-sub="1" hidden' : ""}>
      <td class="px-3 py-2 whitespace-nowrap ${sub ? "text-slate-600 pl-8" : "text-slate-400"}">
        ${sub ? "└ " : `<span class="mr-2 inline-block h-2 w-2 rounded-full ${dot[c]}"></span>`}${fmt(r.timestamp)}${badge}
      </td>
      <td class="px-3 py-2 text-right font-medium tabular-nums ${tone}">${r.systolic}</td>
      <td class="px-3 py-2 text-right font-medium tabular-nums ${tone}">${r.diastolic}</td>
      <td class="px-3 py-2 text-right tabular-nums text-slate-600">${r.mean_ap ?? "—"}</td>
      <td class="px-3 py-2 text-right tabular-nums ${sub ? "text-slate-600" : "text-slate-400"}">${r.pulse ?? "—"}</td>
      <td class="px-3 py-2 text-right tabular-nums text-slate-600">${r.user_id ?? 1}</td>
      <td class="px-3 py-2"><div class="flex flex-wrap gap-1">${chips}</div></td>
      <td class="px-3 py-2 text-right whitespace-nowrap">
        <button onclick='openEdit(${JSON.stringify(r)})' class="text-slate-500 hover:text-slate-200">Edit</button>
      </td>
    </tr>`;
  };

  let gid = 0, html = "";
  for (const g of groups) {
    const multi = g.count > 1;
    const badge = multi
      ? ` <button onclick="toggleGroup(${gid})" class="ml-1 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-amber-300 hover:bg-slate-700">▸${g.count}</button>`
      : "";
    g.lead._gid = gid;
    html += recRow(g.lead, { badge });
    if (multi) {
      for (const m of g.members) {
        if (m === g.lead) continue;
        m._gid = gid;
        html += recRow(m, { sub: true });
      }
    }
    gid++;
  }
  document.getElementById("rows").innerHTML = html ||
    `<tr><td colspan="8" class="px-3 py-8 text-center text-slate-600">No records.</td></tr>`;

  const chrono = [...records].reverse();
  const labels = chrono.map(r => fmt(r.timestamp, dayFmt));
  if (chart) chart.destroy();
  Chart.defaults.color = "#64748b";
  Chart.defaults.borderColor = "#1e293b";
  chart = new Chart(document.getElementById("chart"), {
    type: "line",
    data: { labels, datasets: [
      { label: "Systolic",  data: chrono.map(r => r.systolic),  borderColor: "#f43f5e", backgroundColor: "#f43f5e", tension: .3, pointRadius: 0 },
      { label: "Diastolic", data: chrono.map(r => r.diastolic), borderColor: "#22d3ee", backgroundColor: "#22d3ee", tension: .3, pointRadius: 0 },
    ]},
    options: {
      responsive: true,
      scales: { x: { ticks: { display: false }, grid: { display: false } }, y: { grid: { color: "#16202e" } } },
      plugins: { legend: { labels: { boxWidth: 10, font: { size: 11 } } } },
    },
  });
}

function toggleGroup(gid) {
  document.querySelectorAll(`tr[data-sub="1"][data-gid="${gid}"]`).forEach(tr => {
    tr.hidden = !tr.hidden;
  });
}

// ── Edit / Add dialog ───────────────────────────────────────────────────────
const dlg = document.getElementById("dlg");
const form = document.getElementById("form");

function renderTagPicker(selected) {
  const set = new Set(selected || []);
  document.getElementById("tagPicker").innerHTML = vocab.length
    ? vocab.map(t => `
      <label class="cursor-pointer">
        <input type="checkbox" value="${t}" class="peer hidden" ${set.has(t) ? "checked" : ""}>
        <span class="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 peer-checked:border-cyan-500 peer-checked:bg-cyan-950/50 peer-checked:text-cyan-300">${t}</span>
      </label>`).join("")
    : `<span class="text-[11px] text-slate-600">No tags yet — add via CLI: <code>obp tags add &lt;name&gt;</code></span>`;
}

function toLocalInput(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function openEdit(r) {
  editing = r.id;
  document.getElementById("dlgTitle").textContent = "Edit record";
  document.getElementById("dlgId").textContent = "#" + r.id;
  document.getElementById("dlgDelete").style.display = "";
  form.timestamp.value = toLocalInput(r.timestamp);
  form.systolic.value = r.systolic;
  form.diastolic.value = r.diastolic;
  form.pulse.value = r.pulse ?? "";
  form.user_id.value = r.user_id ?? 1;
  renderTagPicker(r.tags);
  dlg.showModal();
}

function openAdd() {
  editing = null;
  document.getElementById("dlgTitle").textContent = "Add record";
  document.getElementById("dlgId").textContent = "";
  document.getElementById("dlgDelete").style.display = "none";
  form.reset();
  form.timestamp.value = toLocalInput(new Date().toISOString());
  form.user_id.value = 1;
  renderTagPicker([]);
  dlg.showModal();
}

function selectedTags() {
  return [...document.querySelectorAll("#tagPicker input:checked")].map(i => i.value);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    timestamp: form.timestamp.value || null,
    systolic: Number(form.systolic.value),
    diastolic: Number(form.diastolic.value),
    pulse: form.pulse.value ? Number(form.pulse.value) : null,
    user_id: Number(form.user_id.value) || 1,
    tags: selectedTags(),
  };
  const url = editing ? "/api/records/" + editing : "/api/records";
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) { alert("Save failed: " + (await res.text())); return; }
  dlg.close();
  await load();
});

document.getElementById("dlgCancel").addEventListener("click", () => dlg.close());
document.getElementById("dlgDelete").addEventListener("click", async () => {
  if (!editing || !confirm("Delete this record?")) return;
  const res = await fetch("/api/records/" + editing, { method: "DELETE" });
  if (!res.ok) { alert("Delete failed"); return; }
  dlg.close();
  await load();
});
document.getElementById("addBtn").addEventListener("click", openAdd);

// ── Filters ───────────────────────────────────────────────────────────────
const rangeSel = document.getElementById("range");
const sinceEl = document.getElementById("since");
const untilEl = document.getElementById("until");
function syncCustom() {
  const custom = rangeSel.value === "custom";
  sinceEl.classList.toggle("hidden", !custom);
  untilEl.classList.toggle("hidden", !custom);
}
rangeSel.addEventListener("change", () => { syncCustom(); load(); });
sinceEl.addEventListener("change", load);
untilEl.addEventListener("change", load);
document.getElementById("user").addEventListener("change", load);
document.getElementById("tagFilter").addEventListener("change", load);
document.getElementById("groupChk").addEventListener("change", load);

syncCustom();
loadTags().then(load);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200, no_store: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj).encode(), "application/json", status=status, no_store=True)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _record_id(self, path: str):
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "records"]:
            try:
                return int(parts[2])
            except ValueError:
                return None
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(PAGE.encode(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            name = Path(parsed.path).name  # strip dirs — no traversal
            f = STATIC_DIR / name
            if f.is_file():
                self._send(f.read_bytes(), "application/javascript")
            else:
                self.send_error(404)
            return
        if parsed.path == "/api/tags":
            self._json({"tags": tags.list_all()})
            return
        if parsed.path == "/api/records":
            qs = parse_qs(parsed.query)
            user = int(qs["user"][0]) if qs.get("user") else None
            tag = qs["tag"][0] if qs.get("tag") else None
            since, until = _resolve_range(qs)
            conn = db.init_db()
            self._json({
                "records": db.fetch_all(conn, user_id=user, since=since, until=until, tag=tag),
                "stats": db.fetch_stats(conn, user_id=user, since=since, until=until, tag=tag),
            })
            return
        self.send_error(404)

    def _clean_tags(self, raw) -> list[str]:
        """Keep only tags present in the vocabulary."""
        vocab = set(tags.list_all())
        return [t for t in (raw or []) if t in vocab]

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            data = self._body()
        except Exception:
            self._json({"error": "invalid JSON"}, status=400)
            return

        rid = self._record_id(parsed.path)
        conn = db.init_db()

        if parsed.path == "/api/records":  # create
            rec = {
                "systolic": data.get("systolic"),
                "diastolic": data.get("diastolic"),
                "pulse": data.get("pulse"),
                "timestamp": data.get("timestamp"),
                "user": data.get("user_id", 1),
                "tags": self._clean_tags(data.get("tags")),
            }
            if rec["systolic"] is None or rec["diastolic"] is None:
                self._json({"error": "systolic and diastolic required"}, status=400)
                return
            ok = db.insert_measurement(conn, rec)
            self._json({"ok": ok, "duplicate": not ok})
            return

        if rid is not None:  # update
            fields = {}
            for k in ("systolic", "diastolic", "pulse", "timestamp", "user_id"):
                if k in data:
                    fields[k] = data[k]
            if "tags" in data:
                fields["tags"] = self._clean_tags(data["tags"])
            ok = db.update_measurement(conn, rid, fields)
            self._json({"ok": ok})
            return

        self.send_error(404)

    def do_DELETE(self):
        rid = self._record_id(urlparse(self.path).path)
        if rid is None:
            self.send_error(404)
            return
        conn = db.init_db()
        self._json({"ok": db.delete_measurement(conn, rid)})

    def log_message(self, *args):
        pass


def serve(host: str, port: int):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.serve_forever()
