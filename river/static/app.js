import { $, esc, store, actor, toast, act, ago, left, clip, copyText, fillSelect, hooks } from "./lib.js";
import { chip, statusChip, prioChip, prioNumChip, doerChip, projectChip, ownerChip, countChip } from "./components/chip.js";
import { bar, pct, capacityBars } from "./components/bar.js";
import { nyCard, goalCard, projCard, agentCard } from "./components/card.js";
import { itemRow, itemOrder, doneRow } from "./components/itemRow.js";
hooks.refresh = refresh;
let S = null, openItem = null, tab = "board", graphSig = "";

let actorPicked = false;

// Goal filter: "" all items, "__none" items without a goal, else one goal's items.
function goalSel() { return $("#goalFilter").value; }
function inGoal(it) { const g = goalSel(); return !g || (g === "__none" ? !(it.goals || []).length : (it.goals || []).includes(g)); }


function renderCapacity() {
  const c = S.capacity;
  const layers = capacityBars(c.layers);
  $("#capacity").innerHTML = `<h2>Parallel work</h2>
    <div class="stats">
      <div class="stat ${c.spare_slots ? "good" : ""}"${c.spare_slots ? ` data-launch="1" style="cursor:pointer" title="Click to start an agent session in Terminal"` : ""}><div class="n">${c.spare_slots}</div><div class="l">open slots: more agent sessions you can start now${c.spare_slots ? " (click to start one)" : ""}</div></div>
      <div class="stat ${c.excess_sessions ? "bad" : ""}"><div class="n">${c.excess_sessions}</div><div class="l">sessions with nothing ready for them</div></div>
      <div class="stat"><div class="n">${c.agents_active}</div><div class="l">active agent sessions (${c.agents_busy} busy)</div></div>
      <div class="stat"><div class="n">${c.in_progress.length}</div><div class="l">items in progress</div></div>
      <div class="stat hum"><div class="n">${c.ready_for_humans.length}</div><div class="l">ready items that wait on a human</div></div>
    </div>
    <ul class="advice">${c.advice.map(a => `<li class="${a.kind}">${esc(a.text)}</li>`).join("")}</ul>
    <div class="layers">${layers || '<span class="muted">No open items.</span>'}</div>
    <div class="legend"><span><span class="sw" style="background:var(--accent)"></span>open items for agents</span><span><span class="sw" style="background:var(--human)"></span>for humans</span><span>by step: 1 = can start now, 2 = after step 1, …  Peak agent width ${c.peak_width}.</span></div>`;
}

function renderNext() {
  const area = $("#area").value;
  if (area === "__mine") { $("#next").innerHTML = `<div class="muted">Use <b>Claim next</b>: the server picks the ready item closest to what "${esc(actor() || "(choose your name in You are)")}" claimed or finished before.</div>`; return; }
  const ready = S.items.filter(i => i.ready && (!area || i.project === area) && inGoal(i));
  $("#next").innerHTML = ready.slice(0, 5).map(i => itemRow(i, i.reason)).join("") || `<div class="muted">Nothing is ready${area ? " in " + esc(area) : ""}.</div>`;
}

// Projects folded closed on the Projects tab, remembered in this browser.
function projClosed() { try { return JSON.parse(store("river.projClosed") || "[]"); } catch (e) { return []; } }
function renderProjects() {
  const showDone = $("#showDone").checked;
  const G = goalSel(), goals = S.goals || [];
  const html = S.projects.map(p => {
    const pg = goals.filter(g => g.project === p.name && (g.status === "open" || showDone || g.name === G));
    const all = S.items.filter(i => i.project === p.name && inGoal(i));
    if (G && G !== "__none" && !all.length && !pg.some(g => g.name === G)) return "";
    const open = all.filter(i => !["done", "dropped"].includes(i.status));
    const closed = all.filter(i => ["done", "dropped"].includes(i.status));
    const ready = open.filter(i => i.ready).length, prog = open.filter(i => ["in_progress", "held"].includes(i.status)).length;
    const closedSet = projClosed(), isClosed = closedSet.includes(p.name);
    const who = [...new Set(open.filter(i => i.assignee && ["in_progress", "held"].includes(i.status)).map(i => i.assignee))];
    const home = p.path ? p.path.replace(/^\/(Users|home)\/[^/]+/, "~") : "";
    const meta = [home ? `folder <code>${esc(home)}</code>` : "no folder",
      p.target ? `deploys to <b>${esc(p.target)}</b>` : "", p.tracker ? `tracker ${esc(p.tracker)}` : ""].filter(Boolean);
    const me = actor();
    return projCard(p, isClosed, `<div class="pmeta">${meta.map(m => `<span>${m}</span>`).join("")}</div>
      <div class="pcounts">${countChip(ready, "ready", "c-ready")}${countChip(prog, "in progress", "c-in_progress")}${countChip(open.length, "open", "c-p")}${countChip(closed.length, "done", "c-p")}${pg.length ? countChip(pg.length, pg.length === 1 ? "goal" : "goals", "c-p") : ""}</div>
      <div class="pbody">
        <div class="pdesc">${p.notes ? esc(p.notes) : '<span class="muted">No description.</span>'} <span class="link" data-describe="${esc(p.name)}">edit</span></div>
        <div class="pwho">${who.length ? "Working here now: " + who.map(n => `<b>${esc(n)}</b>`).join(", ") : "Nobody works here now."}</div>
        <div class="psec">Goals</div>
        <div class="goals">${pg.map(g => goalCard(g, { selected: G === g.name, me })).join("")}<span class="link" style="font-size:12px;align-self:center" data-addgoal="${esc(p.name)}">${pg.length ? "+ goal" : "No goals yet. + Add a goal"}</span></div>
        <div class="psec">Open items (${open.length})${ready ? `, ready first` : ""}</div>
        ${open.sort(itemOrder).map(i => itemRow(i, i.notes)).join("") || '<div class="muted" style="font-size:12px">No open items.</div>'}
        ${showDone && closed.length ? `<div class="psec">Done (${closed.length})</div>` + closed.map(i => itemRow(i, i.output)).join("") : ""}
      </div>`);
  }).join("");
  $("#projects").innerHTML = html || `<div class="muted">No projects yet.</div>`;
}

// An agent session not seen for away_after, holding and owning nothing, has most likely stopped: hide it.
// People always show, and so does any agent that still holds an item, owns a goal or target, or has a push waiting.
function agentShown(a) {
  if (a.kind === "human" || a.state === "active" || a.holds.length || (a.owns || []).length) return true;
  if ((S.goals || []).some(g => g.owner === a.name && g.status === "open")) return true;
  return S.items.some(i => i.status === "open" && i.reserved_until && i.reserved_for === a.name);
}
// A waiting session (river wait) takes a pushed item within seconds: offer the ready items of its projects.
function giveWork(a) {
  const where = (a.waiting_in || "").split(",").filter(Boolean);
  const ready = S.items.filter(i => i.ready && i.doer !== "human" && i.kind === "work" && !i.reserved_for
    && (!where.length || where.includes(i.project)));
  const since = a.waiting_since ? ` since ${ago(a.waiting_since)}` : "";
  if (!ready.length) return `<div class="st">${chip("c-ready", "waiting" + since)} no ready item in ${esc(where.join(", ") || "its projects")}</div>`;
  return `<div class="st" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${chip("c-ready", "waiting" + since)}
    <select data-give-item="${esc(a.name)}" style="max-width:220px">${ready.map(i => `<option value="${i.id}"${giveChoice[a.name] == i.id ? " selected" : ""}>#${i.id} ${esc(clip(i.title, 50))}</option>`).join("")}</select>
    <button class="btn" data-give="${esc(a.name)}">Give work</button></div>`;
}

const giveChoice = {};
function renderAgents() {
  // The panel redraws every few seconds: leave it alone while someone picks from a Give work menu.
  if (document.activeElement && document.activeElement.matches("#agents select")) return;
  const hidden = S.agents.filter(a => !agentShown(a)), showIdle = $("#showIdle").checked;
  $("#showIdleWrap").classList.toggle("hidden", !hidden.length);
  $("#showIdleText").textContent = `show ${hidden.length} stopped`;
  $("#agents").innerHTML = S.agents.filter(a => showIdle || agentShown(a)).map(a => agentCard(a, `<div class="st">${a.state}, seen ${ago(a.last_seen)}${a.note ? " · " + esc(a.note) : ""}</div>
      ${a.role === "waiting" && !a.holds.length ? giveWork(a) : ""}
      ${(S.goals || []).filter(g => g.owner === a.name && g.status === "open").map(g => `<div class="st">owns goal <span class="link" data-goal="${esc(g.name)}">${esc(g.name)}</span>${g.owner_expires_at ? " · " + left(g.owner_expires_at) + " left" : ""}</div>`).join("")}
      ${a.holds.map(h => `<div class="st">holds <span class="link" data-open="${h.id}">#${h.id} ${esc(h.title)}</span>${h.lease_expires_at ? " · " + left(h.lease_expires_at) + " left" : ""}</div>`).join("") || '<div class="st">holds nothing</div>'}
      ${S.items.filter(i => i.status === "open" && i.reserved_until && i.reserved_for === a.name).map(i => `<div class="st">pushed <span class="link" data-open="${i.id}">#${i.id} ${esc(i.title)}</span> · ${left(i.reserved_until)} left · <span class="link" data-unpush="${i.id}">cancel</span></div>`).join("")}`)).join("") || `<div class="muted">No agents registered.</div>`;
}

function renderTargets() {
  const T = S.targets || [];
  const shipList = (s) => s.map(x => `<span class="link" data-open="${x.id}">#${x.id}</span> ${esc(clip(x.title, 50))} <span class="muted">(${esc(x.status.replace("_", " "))})</span>`).join("; ") || '<span class="muted">nothing yet</span>';
  $("#targets").innerHTML = T.map(t => nyCard(`<b>${esc(t.name)}</b>
        ${ownerChip(t.owner)}${t.owner ? `<span class="muted" style="font-size:12px">${t.owner_expires_at ? left(t.owner_expires_at) + " left" : ""}</span>` : ""}
        <span class="muted" style="font-size:12px">${t.project_names.length ? "projects: " + t.project_names.map(esc).join(", ") : "no projects"}</span>`, `${t.description ? `<div class="ny-c">${esc(clip(t.description, 300))}</div>` : ""}
      ${t.pending.map(d => `<div class="st" style="margin-top:4px"><span class="link" data-open="${d.id}">#${d.id}</span> ${d.ready ? "ready to deploy" : d.status === "open" ? "collecting" : esc(d.status.replace("_", " ")) + (d.assignee ? " · " + esc(d.assignee) : "")}: ${shipList(d.ships)}</div>`).join("") || '<div class="st muted">No pending ship requests.</div>'}
      ${t.last_deploy ? `<div class="st" style="margin-top:4px">Last deploy <span class="link" data-open="${t.last_deploy.id}">#${t.last_deploy.id}</span> ${ago(t.last_deploy.closed_at)}${t.last_deploy.output ? ": " + esc(clip(t.last_deploy.output, 200)) : ""}<div class="muted" style="font-size:12px">shipped ${shipList(t.last_deploy.ships)}</div></div>` : '<div class="st muted">Never deployed.</div>'}`)).join("") || `<div class="muted">No deploy targets. Add one: river target add &lt;name&gt; --description "how it deploys"</div>`;
}

function renderBlocked() {
  const B = S.items.filter(i => i.blocked_reason && !["done", "dropped"].includes(i.status));
  $("#blockedPanel").classList.toggle("hidden", !B.length);
  $("#blockedList").innerHTML = B.map(i => nyCard(`<b class="link" data-open="${i.id}">#${i.id} ${esc(i.title)}</b> ${projectChip(i.project)}${doerChip(i)}`, `<div class="ny-c">${esc(i.blocked_reason)}</div>
      <div class="muted" style="font-size:12px">${i.blocked_at ? "since " + ago(i.blocked_at) : "since: not recorded"} · ${i.blocked_until ? `until ${esc(i.blocked_until_text)} (${left(i.blocked_until)} left), then ready by itself` : "until someone clears it"}${i.blocked_set_by ? " · set by " + esc(i.blocked_set_by) : ""}</div>`)).join("");
}

let takeoversOpen = false;
function renderTakeovers() {
  const T = S.takeovers || [];
  $("#takeoverPanel").classList.toggle("hidden", !T.length);
  const words = { "took over": "took over", "done": "marked done", "dropped": "dropped" };
  const head = `<div class="nyl" data-takeovers-toggle="1"><span class="t">${takeoversOpen ? "▾" : "▸"} ${T.length} item${T.length === 1 ? "" : "s"} an agent took off your list: review or undo</span><span class="chips"><button class="btn" data-takeovers-okall="1" title="Accept all of them">OK all</button></span></div>`;
  if (!takeoversOpen) { $("#takeovers").innerHTML = head; return; }
  $("#takeovers").innerHTML = head + T.map(t => nyCard(`<b>#${t.id} ${esc(t.title)}</b> ${projectChip(t.project)}<span class="muted" style="font-size:12px">${ago(t.takeover_at)}</span>`, `<div class="ny-c"><b>${esc(t.takeover_by)}</b> ${words[t.takeover_kind] || t.takeover_kind} it: ${esc(t.takeover_note || "")}</div>
      <div class="actions"><button class="btn" data-open="${t.id}">Open</button><button class="btn" data-undo-takeover="${t.id}">Undo: give it back to me</button><button class="btn primary" data-takeover-ok="${t.id}">OK</button></div>`)).join("");
}

// The status strip: counts you click to go where they are.
function renderStrip() {
  if (!S) return;
  const ready = S.items.filter(i => i.ready && i.doer !== "human").length;
  const running = S.items.filter(i => ["in_progress", "held"].includes(i.status)).length;
  const blocked = S.items.filter(i => i.blocked_reason && !["done", "dropped"].includes(i.status)).length;
  const T = (S.takeovers || []).length, slots = S.capacity ? S.capacity.spare_slots : 0;
  const b = (n, label, go, cls) => `<button data-strip="${go}" class="${n ? cls : ""}"><b>${n}</b>${label}</button>`;
  const LA = S.launch_agents || [], pick = LA.includes(store("river.launch.agent")) ? store("river.launch.agent") : LA[0];
  const agentPick = LA.length > 1 ? `<select id="launchAgent" title="Which agent the Start button opens">${LA.map(a => `<option ${a === pick ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>` : "";
  const html = b(NY.length, "need you", "needs", "hum") + (T ? b(T, "taken off your list", "takeovers", "hum") : "")
    + b(ready, "ready for agents", "work", "good") + b(running, "in progress", "work", "")
    + b(blocked, "blocked outside", "blocked", "bad") + b(slots, "open agent slots", "capacity", "good")
    + (ready ? agentPick + `<button data-launch="1" class="primary" title="Open a Terminal tab in the folder of the most important ready item and start ${esc(pick || "an agent")} there (setting launch_agents)"><b>▶</b>Start ${LA.length > 1 ? "" : "an agent"}</button>` : "");
  if ($("#strip").dataset.sig !== html) { $("#strip").innerHTML = html; $("#strip").dataset.sig = html; }
}

// Work column: one row per project; a click opens its open items (ready first).
function workOpen() { try { return new Set(JSON.parse(store("river.work.open") || "[]")); } catch (e) { return new Set(); } }
function renderWork() {
  const open = workOpen();
  $("#work").innerHTML = S.projects.map(p => {
    const items = S.items.filter(i => i.project === p.name && !["done", "dropped"].includes(i.status));
    if (!items.length) return "";
    const ready = items.filter(i => i.ready).length, run = items.filter(i => ["in_progress", "held"].includes(i.status)).length;
    const hum = items.filter(i => i.ready && i.doer === "human").length;
    const isOpen = open.has(p.name), shown = items.sort(itemOrder).slice(0, 8);
    return `<div class="wp"><div class="wp-h" data-wp="${esc(p.name)}"><span class="tw">${isOpen ? "▾" : "▸"}</span><b>${esc(p.name)}</b>
        <span class="counts">${items.length} open · ${ready} ready${run ? ` · ${run} running` : ""}${hum ? ` · ${hum} for you` : ""}</span></div>
      ${isOpen ? `<div class="wp-items">${shown.map(i => itemRow(i)).join("")}${items.length > shown.length ? `<div class="muted" style="font-size:12px;padding:4px 6px">${items.length - shown.length} more: <span class="link" data-tab="projects">Projects tab</span></div>` : ""}</div>` : ""}</div>`;
  }).join("") || `<div class="muted">No open items.</div>`;
}

function renderEvents() {
  $("#events").innerHTML = S.events.slice(0, 15).map(e => `<div class="ev">${ago(e.at)} · <b>${esc(e.actor)}</b> ${esc(e.change)}${e.item_id ? ` <span class="link" data-open="${e.item_id}">#${e.item_id}</span>` : ""}</div>`).join("");
}

function renderSelects() {
  const names = S.projects.map(p => p.name);
  { const el = $("#area"), cur = el.value; el.innerHTML = `<option value="">All projects</option><option value="__mine">Near my earlier work</option>` + names.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join(""); if ([...el.options].some(o => o.value === cur)) el.value = cur; } fillSelect("#logProject", names, true); fillSelect("#addProject", names, false);
  const a = $("#actor"), cur = a.value || store("river.actor") || "";
  // Only people act from this page; agents act through the river command.
  const people = S.agents.filter(x => x.kind === "human");
  a.innerHTML = `<option value="">(choose your name)</option>` + people.map(x => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join("");
  if (people.some(x => x.name === cur)) a.value = cur;
  else if (!a.value && people.length === 1 && !actorPicked) { a.value = people[0].name; actorPicked = true; store("river.actor", a.value); }
  fillSelect("#setKey", Object.keys(S.settings.defaults), false);
  fillSelect("#goalProject", names, false);
  const openGoals = (S.goals || []).filter(g => g.status === "open");
  { const el = $("#goalFilter"), cur = el.value || store("river.goal") || "";
    const extra = cur && cur !== "__none" && !openGoals.some(g => g.name === cur) && (S.goals || []).some(g => g.name === cur) ? [cur] : [];
    el.innerHTML = `<option value="">All goals</option><option value="__none">No goal</option>` + openGoals.map(g => g.name).concat(extra).map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    el.value = [...el.options].some(o => o.value === cur) ? cur : ""; }
  { const el = $("#addGoal"), cur = el.value, proj = $("#addProject").value;
    const list = openGoals.filter(g => g.project === proj).concat(openGoals.filter(g => g.project !== proj));
    el.innerHTML = `<option value="">no goal</option>` + list.map(g => `<option value="${esc(g.name)}">${esc(g.name)}${g.project !== proj ? " (" + esc(g.project) + ")" : ""}</option>`).join("");
    if ([...el.options].some(o => o.value === cur)) el.value = cur; }
}

function renderSettings() {
  const d = S.settings.defaults, o = S.settings.overrides;
  $("#settingsTable").innerHTML = `<tr><th>Key</th><th>Default</th><th>Overrides</th></tr>` + Object.keys(d).map(k => {
    const ov = o.filter(x => x.key === k).map(x => chip("c-p", `${esc(x.scope)} = ${esc(x.value)} <span class="link" data-unset="${esc(k)}" data-scope="${esc(x.scope)}">×</span>`)).join(" ");
    return `<tr><td>${esc(k)}</td><td class="muted">${esc(d[k])}</td><td>${ov}</td></tr>`;
  }).join("");
}

let logSig = "";
async function renderLog(force) {
  if (tab !== "done") return;
  const q = new URLSearchParams({ since: $("#logSince").value });
  if ($("#logProject").value) q.set("project", $("#logProject").value);
  const r = await fetch("/api/log?" + q); if (!r.ok) return;
  const L = await r.json(), sig = JSON.stringify(L);
  if (!force && sig === logSig) return;
  logSig = sig;
  const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dayName = (d) => new Date(d + "T12:00:00Z").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  $("#log").innerHTML = L.by_day.map(d => `<div class="day">
      <div class="day-h"><b>${esc(dayName(d.day))}</b><span class="muted">${d.items.length} done</span></div>
      ${d.items.map(it => doneRow(it, time(it.closed_at))).join("")}
    </div>`).join("") || `<div class="muted">Nothing done in this period.</div>`;
  const win = L.since ? $("#logSince").selectedOptions[0].textContent : null;
  $("#progress").innerHTML = L.progress.map(p => {
    const done = pct(p.done, p.total);
    return `<div class="prog"><div class="prog-h"><b>${esc(p.project)}</b><span class="muted">${p.done}/${p.total} · ${done}%</span></div>
      ${bar(done)}
      ${win ? `<div class="muted" style="font-size:12px;margin-top:2px">+${p.done_in_window} in the ${esc(win)}</div>` : ""}</div>`;
  }).join("") || `<div class="muted">No items.</div>`;
}

let graphDefaulted = false, elkReady = null, graphProj = "", sideSig = "", sideShown = null;
// ELK routes edges around boxes; the default layout can run an edge behind an unrelated box, which
// reads as a dependency that does not exist. If ELK does not load, edges are drawn above the boxes.
function loadElk() {
  if (elkReady === null) elkReady = import("https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.2.3/dist/mermaid-layout-elk.esm.min.mjs")
    .then(m => { mermaid.registerLayoutLoaders(m.default); return true; }).catch(() => false);
  return elkReady;
}
// A small picture of one project's graph for its sidebar card: items in columns by step
// (1 = nothing inside the project waits first), lines for waits-on, colors as in the full graph.
function miniGraph(items) {
  const byId = new Map(items.map(i => [i.id, i])), depth = new Map();
  const d = (i, seen = new Set()) => {
    if (depth.has(i.id)) return depth.get(i.id);
    if (seen.has(i.id)) return 0; seen.add(i.id);
    const v = Math.max(-1, ...i.waits_on.filter(x => byId.has(x)).map(x => d(byId.get(x), seen))) + 1;
    depth.set(i.id, v); return v;
  };
  items.forEach(i => d(i));
  const cols = [];
  for (const i of items) (cols[depth.get(i.id)] ||= []).push(i);
  const W = 12, H = 7, GX = 10, GY = 4, pos = new Map();
  cols.forEach((col, x) => col.forEach((i, y) => pos.set(i.id, [6 + x * (W + GX), 5 + y * (H + GY)])));
  const w = 12 + cols.length * (W + GX) - GX, h = 10 + Math.max(...cols.map(c => c.length)) * (H + GY) - GY;
  const fill = (i) => ["done", "dropped"].includes(i.status) ? "var(--line)" : ["in_progress", "held"].includes(i.status) ? "var(--prog)" : i.ready ? "var(--ready)" : "var(--muted)";
  let lines = "", boxes = "";
  for (const i of items) for (const b of i.waits_on) if (pos.has(b)) {
    const [x1, y1] = pos.get(b), [x2, y2] = pos.get(i.id);
    lines += `<line x1="${x1 + W}" y1="${y1 + H / 2}" x2="${x2}" y2="${y2 + H / 2}"/>`;
  }
  for (const i of items) {
    const [x, y] = pos.get(i.id);
    boxes += `<rect x="${x}" y="${y}" width="${W}" height="${H}" rx="2" fill="${fill(i)}"${i.doer === "human" ? ' stroke="var(--human)" stroke-width="1.5"' : ""}/>`;
  }
  return `<svg viewBox="0 0 ${Math.max(w, 150)} ${Math.max(h, 38)}" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><g stroke="var(--muted)" stroke-width=".7" opacity=".6">${lines}</g>${boxes}</svg>`;
}
function renderGraphSide(withDone) {
  const live = (i) => withDone || !["done", "dropped"].includes(i.status);
  const per = S.projects.map(p => ({ p, items: S.items.filter(i => i.project === p.name && live(i) && inGoal(i)) }));
  const sig = JSON.stringify([graphProj, withDone, per.map(x => [x.p.name, x.items.map(i => [i.id, i.status, i.ready, i.waits_on])])]);
  if (sig === sideSig) return;
  sideSig = sig;
  const card = (name, label, items, extra) => {
    const ready = items.filter(i => i.ready).length;
    return `<button class="gcard${graphProj === name ? " on" : ""}${items.length ? "" : " empty"}" data-gproj="${esc(name)}" aria-pressed="${graphProj === name}">
      <b>${esc(label)}</b><span class="gc-n">${items.length} ${withDone ? "items" : "open"} · ${ready} ready</span>${extra}</button>`;
  };
  $("#graphSide").innerHTML = `<h2>Projects</h2>` + card("", "All projects", per.flatMap(x => x.items), "")
    + per.map(({ p, items }) => card(p.name, p.name, items, items.length ? miniGraph(items) : `<span class="gc-none">No ${withDone ? "" : "open "}items</span>`)).join("");
  const on = $("#graphSide .gcard.on"), side = $("#graphSide");
  if (on && sideShown !== graphProj) side.scrollTo({ left: on.offsetLeft - side.offsetLeft - 10, top: side.scrollHeight > side.clientHeight ? on.offsetTop - side.offsetTop - 40 : 0 });
  sideShown = graphProj;
}
async function renderGraph(force) {
  if (tab !== "graph" || !window.mermaid) return;
  // A big queue draws as an unreadable wall: the first time, show only the project of the top ready item.
  if (!graphDefaulted) {
    graphDefaulted = true;
    const open = S.items.filter(i => !["done", "dropped"].includes(i.status));
    const top = S.items.find(i => i.ready) || open[0];
    if (open.length > 40 && top && !graphProj) graphProj = top.project;
  }
  if (graphProj && !S.projects.some(p => p.name === graphProj)) graphProj = "";
  const proj = graphProj, withDone = $("#graphDone").checked;
  renderGraphSide(withDone);
  $("#graphTitle").textContent = proj ? "Dependency graph: " + proj : "Dependency graph: all projects";
  const items = S.items.filter(i => (withDone || !["done", "dropped"].includes(i.status)));
  const ids = new Set(items.map(i => i.id));
  const G = goalSel();
  const base = items.filter(i => (!proj || i.project === proj) && inGoal(i));
  const show = proj || G ? new Set(base.flatMap(i => [i.id, ...i.waits_on, ...i.unblocks]).filter(x => ids.has(x))) : ids;
  const sig = JSON.stringify([proj, G, withDone, items.filter(i => show.has(i.id)).map(i => [i.id, i.status, i.ready, i.waits_on])]);
  if (!force && sig === graphSig) return;
  graphSig = sig;
  const lab = (s) => { const t = s.replace(/["<>#|{}\[\]]/g, " "); if (t.length <= 60) return t; const cut = t.slice(0, 60); return cut.slice(0, cut.lastIndexOf(" ") > 30 ? cut.lastIndexOf(" ") : 60) + "…"; };
  let g = "flowchart LR\n";
  for (const p of S.projects) {
    const mine = items.filter(i => i.project === p.name && show.has(i.id));
    if (!mine.length) continue;
    g += `subgraph P${p.id}["${lab(p.name)}"]\n`;
    for (const i of mine) g += `  N${i.id}["#${i.id} ${lab(i.title)}"]\n`;
    g += "end\n";
  }
  for (const i of items) if (show.has(i.id)) for (const b of i.waits_on) if (show.has(b)) g += `N${b} --> N${i.id}\n`;
  g += "classDef ready fill:#dcf5e3,stroke:#1a7f37,color:#0b3d1b\nclassDef prog fill:#fff3c4,stroke:#9a6700,color:#3d2a00\nclassDef wait fill:#eceff2,stroke:#8c959f,color:#24292f\nclassDef done fill:#f0f1f3,stroke:#c4c9cf,color:#8c959f\nclassDef human stroke:#8250df,stroke-width:3px\n";
  for (const i of items) if (show.has(i.id)) {
    const cls = ["done", "dropped"].includes(i.status) ? "done" : ["in_progress", "held"].includes(i.status) ? "prog" : i.ready ? "ready" : "wait";
    g += `class N${i.id} ${cls}\n`;
    if (i.doer === "human") g += `class N${i.id} human\n`;
    g += `click N${i.id} call riverOpen(${i.id})\n`;
  }
  try {
    const elk = await loadElk();
    const { svg, bindFunctions } = await mermaid.render("g" + Date.now(), (elk ? "---\nconfig:\n  layout: elk\n---\n" : "") + g);
    $("#graph").innerHTML = svg; bindFunctions && bindFunctions($("#graph"));
    $("#graph").querySelectorAll(".edgePaths").forEach(e => e.parentNode.appendChild(e));
  } catch (e) { $("#graph").innerHTML = `<div class="muted">Graph failed to draw: ${esc(e.message || e)}</div>`; }
}
window.riverOpen = (id) => openDrawer(id);

function treeText(n, prefix = "", last = true, root = true) {
  const who = n.assignee ? `, ${n.assignee}${n.lease_seconds_left != null ? ", " + Math.floor(n.lease_seconds_left / 60) + "m left" : ""}` : "";
  const st = (n.ready ? "ready" : n.status.replace("_", " ")) + (n.blocked_reason ? ", " + clip(n.blocked_text || "blocked: " + n.blocked_reason, 60) : "");
  let out = (root ? "" : prefix + (last ? "└─ " : "├─ ")) + `#${n.id} ${root ? n.title : clip(n.title, 60)}  (${st}${who})\n`;
  n.children.forEach((c, i) => { out += treeText(c, prefix + (root ? "" : last ? "   " : "│  "), i === n.children.length - 1, false); });
  return out;
}

async function openDrawer(id) {
  openItem = id;
  const r = await fetch(`/api/item/${id}`); if (!r.ok) return;
  const it = await r.json();
  const d = $("#drawer");
  const focused = d.contains(document.activeElement) && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (focused && d.dataset.id == id) return;
  d.dataset.id = id;
  const closed = ["done", "dropped"].includes(it.status);
  d.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px"><span class="muted">#${it.id} · ${esc(it.project)}</span><span class="spacer" style="flex:1"></span><button class="btn" id="dClose">Close</button></div>
    <h3>${esc(it.title)}</h3>
    <div class="chips" style="justify-content:flex-start">${prioChip(it)}${doerChip(it)}${statusChip(it)}</div>
    <div class="actions" style="margin-top:6px;align-items:center"><span class="muted" style="font-size:12px">Goals:</span>
      ${(it.goals || []).map(g => chip("c-goal", `${esc(g)} <span class="link" data-untag="${esc(g)}" title="remove this goal tag">×</span>`)).join("") || '<span class="muted" style="font-size:12px">none</span>'}
      ${(S.goals || []).some(g => g.status === "open" && !(it.goals || []).includes(g.name)) ? `<select id="dGoal">${(S.goals || []).filter(g => g.status === "open" && !(it.goals || []).includes(g.name)).sort((a, b) => (b.project === it.project) - (a.project === it.project)).map(g => `<option value="${esc(g.name)}">${esc(g.name)}${g.project !== it.project ? " (" + esc(g.project) + ")" : ""}</option>`).join("")}</select><button class="btn" data-do="tag">Add goal</button>` : ""}</div>
    ${(it.refs || []).length ? `<div class="actions" style="margin-top:6px;align-items:center"><span class="muted" style="font-size:12px">Tracker:</span>
      ${it.refs.map(r => /^https?:\/\//.test(r.url || "") ? `<a class="chip c-p" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.ref)} ↗</a>` : chip("c-p", esc(r.ref))).map((h, i) => h + (it.refs[i].synced_at ? '<span class="muted" style="font-size:12px">updated</span>' : ["done", "dropped"].includes(it.status) ? chip("c-human", "tracker not updated") : "")).join("")}</div>` : ""}
    <div class="meta" style="margin-top:6px">${esc(it.reason)}${it.lease_expires_at ? " · lease " + left(it.lease_expires_at) + " left" : ""}</div>
    <div class="actions">
      ${it.status === "open" ? `<button class="btn primary" data-do="claim">Claim</button>` : ""}
      ${["in_progress", "held"].includes(it.status) ? `<button class="btn primary" data-do="done">Done</button><button class="btn" data-do="release">Release</button>` : ""}
      ${it.status === "open" ? `<button class="btn" data-do="done">Mark done</button>` : ""}
      ${!closed ? `<button class="btn" data-do="drop">Drop</button>` : `<button class="btn" data-do="reopen">Reopen</button>`}
    </div>
    <div class="sec"><h4>Details</h4><div class="form">
      <input id="dTitle" value="${esc(it.title)}">
      <textarea id="dNotes" rows="3" placeholder="Notes">${esc(it.notes)}</textarea>
      <label>Context<textarea id="dContext" rows="2" placeholder="what a new agent must know to start">${esc(it.context)}</textarea></label>
      <label>Touches<input id="dTouches" placeholder="files it changes, comma separated" value="${esc((it.touches || []).join(", "))}"></label>
      <label>Check<input id="dCheck" placeholder="command that shows it works" value="${esc(it.check)}"></label>
      <label>Due${it.due ? ` <span class="muted">(${esc(it.due_text)})</span>` : it.due_from ? ` <span class="muted">(from #${it.due_from}: ${esc(it.due_text)})</span>` : ""}<input id="dDue" placeholder="2026-10-15, fri 17:00, or none" value=""></label>
      <div class="two">
        <label>Priority (own)<select id="dPrio">${[0,1,2,3,4].map(p => `<option ${p === it.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
        <label>Who can do it<select id="dDoer">${["any","ai","human"].map(x => `<option value="${x}" ${x === it.doer ? "selected" : ""}>${x === "any" ? "anyone" : x === "ai" ? "agent" : "human"}</option>`).join("")}</select></label>
      </div>
      <div class="two"><label>Project<select id="dProject">${S.projects.map(p => `<option ${p.name === it.project ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
        <label>Order in project<span class="actions"><button class="btn" data-do="up">Up</button><button class="btn" data-do="down">Down</button></span></label></div>
      <button class="btn" data-do="save">Save details</button>
    </div></div>
    ${it.output ? `<div class="sec"><h4>Output</h4><div>${esc(it.output)}</div></div>` : ""}
    <div class="sec"><h4>Waits on</h4>
      ${it.waits_on_detail.map(w => `<div><span class="link" data-open="${w.id}">#${w.id} ${esc(w.title)}</span> <span class="muted">(${w.status})</span> <span class="link" data-undep="${w.id}">remove</span></div>`).join("") || '<div class="muted">nothing</div>'}
      <div class="actions" style="margin-top:6px"><input id="dDep" placeholder="ids, e.g. 4, 9" style="width:140px"><button class="btn" data-do="dep">Add</button></div>
    </div>
    <div class="sec"><h4>Blocker tree</h4><div class="tree">${esc(treeText(it.tree))}</div>${offerHelpHtml(it)}</div>
    <div class="sec"><h4>Messages</h4>
      ${(it.messages || []).map(m => msgLine(m)).join("") || '<div class="muted">none</div>'}
      <div class="actions" style="margin-top:6px"><select id="dMsgKind"><option value="note">note</option><option value="question">question</option><option value="alert">alert</option></select>
        <input id="dMsgBody" placeholder="${it.assignee ? "to " + esc(it.assignee) : "to whoever takes it next"}" style="flex:1"><button class="btn" data-do="msg">Send</button></div>
    </div>
    <div class="sec"><h4>Unblocks</h4>${it.unblocks_detail.map(w => `<div><span class="link" data-open="${w.id}">#${w.id} ${esc(w.title)}</span> <span class="muted">(${w.status})</span></div>`).join("") || '<div class="muted">nothing</div>'}</div>
    ${it.status === "open" ? `<div class="sec"><h4>Push to agent</h4>
      ${it.reserved_until ? `<div>Pushed to <b>${esc(it.reserved_for)}</b> by ${esc(it.reserved_by || "?")} · ${left(it.reserved_until)} left · <span class="link" data-unpush="${it.id}">cancel</span></div>`
        : `<div class="actions"><select id="dPushTo">${S.agents.filter(a => a.name !== actor()).map(a => `<option>${esc(a.name)}</option>`).join("")}</select>
           <input id="dPushNote" placeholder="note (optional)" style="flex:1"><button class="btn" data-do="push">Push</button></div>
           <div class="muted" style="font-size:12px;margin-top:4px">Or drag the item row onto an agent. It is reserved for them for a while; their river go takes it first.</div>`}
    </div>` : ""}
    ${it.replan ? `<div class="sec"><h4>Replan</h4><div>${it.late_prereqs} prerequisites were added while it was claimed, so it is bigger than planned. Split or re-scope it, then <span class="link" data-do="replanned">clear the mark</span>.</div></div>` : ""}
    <div class="sec"><h4>Outside blocker</h4>
      ${it.blocked_reason ? `<div>${esc(it.blocked_text)} <span class="link" data-do="unblock">clear</span></div>${it.blocked_at ? `<div class="muted" style="font-size:12px">since ${ago(it.blocked_at)}${it.blocked_until ? ", ready by itself in " + left(it.blocked_until) : ""}</div>` : ""}`
        : `<div class="actions"><input id="dBlock" placeholder="waiting on …" style="flex:1"><input id="dUntil" placeholder="until (optional): 2h, mon 07:00" style="width:170px"><button class="btn" data-do="block">Set</button></div>`}
    </div>
    <div class="sec"><h4>History</h4>${it.events.map(e => `<div class="ev">${ago(e.at)} · <b>${esc(e.actor)}</b> ${esc(e.change)}</div>`).join("")}</div>`;
  d.classList.add("open");
}

async function drawerAction(what) {
  const id = openItem;
  const it = S.items.find(i => i.id === id);
  try {
    if (what === "claim") await act("claim", { id });
    if (what === "done") { const out = prompt("Output note (optional)") ?? undefined; await act("done", { id, output: out || undefined }); }
    if (what === "release") await act("release", { id });
    if (what === "drop") await act("drop", { id });
    if (what === "reopen") await act("reopen", { id });
    if (what === "unblock") await act("unblock", { id });
    if (what === "replanned") await act("replanned", { id });
    if (what === "msg") { const body = $("#dMsgBody").value.trim(); if (!body) return; if (!actor()) return toast("Choose your name in 'You are' first", true); await act("send", { kind: $("#dMsgKind").value, body, item: id }); toast("Sent"); }
    if (what === "push") { const to = $("#dPushTo").value; if (to) { await act("push", { id, to, note: $("#dPushNote").value.trim() || undefined }); toast(`Pushed #${id} to ${to}`); } }
    if (what === "block") { const r = $("#dBlock").value.trim(), u = $("#dUntil").value.trim(); if (r || u) await act("block", { id, reason: r || undefined, until: u || undefined }); }
    if (what === "tag") { const g = $("#dGoal").value; if (g) await act("item_edit", { id, goals: [g] }); }
    if (what === "dep") { const on = $("#dDep").value.split(/[\s,]+/).filter(Boolean).map(Number); if (on.length) await act("dep_add", { id, on }); }
    if (what === "save") {
      await act("item_edit", { id, title: $("#dTitle").value, notes: $("#dNotes").value, context: $("#dContext").value, touches: $("#dTouches").value, check: $("#dCheck").value, due: $("#dDue").value.trim() || undefined, doer: $("#dDoer").value, project: $("#dProject").value !== it.project ? $("#dProject").value : undefined });
      if (+$("#dPrio").value !== it.priority) await act("prio", { id, priority: +$("#dPrio").value });
      toast("Saved");
    }
    if (what === "up" || what === "down") {
      const sib = S.items.filter(i => i.project === it.project).sort((a, b) => a.rank - b.rank);
      const k = sib.findIndex(i => i.id === id), other = sib[what === "up" ? k - 1 : k + 1];
      if (other) await act("move", what === "up" ? { id, before: other.id } : { id, after: other.id });
    }
    document.activeElement && document.activeElement.blur();
    await openDrawer(id);
  } catch (e) { /* toast shown */ }
}

// ---- Inbox: messages for the person in "You are", with the answer each kind needs.
let IB = [], ibSig = "";
function msgLine(m) {
  return `<div class="ev">${chip("c-p", esc(m.kind))} <b>${esc(m.from_agent)}</b> → ${esc(m.to_agent || "next holder")} · ${ago(m.created_at)}${m.state !== "open" && m.state !== "read" ? " · " + esc(m.state) : ""}<div class="body" style="white-space:pre-wrap">${esc(clip(m.body, 600))}</div></div>`;
}
function offerHelpHtml(it) {
  const me = actor(), held = [];
  (function walk(n, root) { if (!root && n.assignee && n.assignee !== me && ["in_progress", "held"].includes(n.status)) held.push(n); n.children.forEach(c => walk(c, false)); })(it.tree, true);
  if (!held.length || ["done", "dropped"].includes(it.status)) return "";
  return `<div style="margin-top:6px">${held.map(n => `<div class="actions"><span class="muted">#${n.id} held by ${esc(n.assignee)}</span>
      <input id="offer-${n.id}" placeholder="I am blocked on this; I can take …" style="flex:1"><button class="btn" data-offer="${n.id}">Offer help</button></div>`).join("")}</div>`;
}

async function pollInbox() {
  const me = actor();
  $("#inboxPanel").classList.toggle("hidden", !me);
  if (!me) return;
  const r = await fetch(`/api/inbox?agent=${encodeURIComponent(me)}${$("#inboxAll").checked ? "&all=1" : ""}`); if (!r.ok) return;
  IB = (await r.json()).messages;
  renderInbox();
}

function renderInbox() {
  const me = actor();
  $("#inboxTitle").textContent = `Inbox for ${me}` + (IB.length ? ` (${IB.filter(m => m.unread || (["question", "offer"].includes(m.kind) && m.state === "open")).length})` : "");
  fillSelect("#sendTo", S.agents.map(a => a.name).filter(n => n !== me), false);
  const ib = $("#inbox");
  if (ib.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;  // do not wipe what they type
  const sig = JSON.stringify([me, IB.map(m => [m.id, m.state, m.unread])]);
  if (sig === ibSig) return;
  ibSig = sig;
  ib.innerHTML = IB.map(m => {
    const open = m.state === "open", it = m.item_id ? S.items.find(i => i.id === m.item_id) : null;
    let acts = "";
    if (m.kind === "question" && open)
      acts = `<input id="ans-${m.id}" placeholder="your answer"><button class="btn primary" data-ib="answer" data-msg="${m.id}">Answer</button>`;
    else if (m.kind === "offer" && open) {
      const mine = it && it.assignee === me;
      acts = (mine ? `<button class="btn primary" data-ib="give" data-msg="${m.id}" data-id="${m.item_id}" data-to="${esc(m.from_agent)}">Give #${m.item_id} to ${esc(m.from_agent)}</button>
          <input id="split-${m.id}" placeholder="smaller pieces, separated by ;"><button class="btn" data-ib="split" data-msg="${m.id}" data-id="${m.item_id}">Split</button>` : "")
        + `<input id="dec-${m.id}" placeholder="why not (optional)"><button class="btn" data-ib="decline" data-msg="${m.id}">Decline</button>`;
    } else {
      const pushed = m.kind === "alert" && it && it.status === "open" && it.reserved_for === me && it.reserved_until;
      if (pushed) acts += `<button class="btn primary" data-ib="accept" data-id="${it.id}">Accept #${it.id}</button><button class="btn" data-ib="decline-push" data-id="${it.id}">Decline #${it.id}</button>`;
      else if (m.kind === "alert" && open) acts += `<input id="dec-${m.id}" placeholder="why not (optional)"><button class="btn" data-ib="decline" data-msg="${m.id}">Decline</button>`;
      acts += `<input id="rep-${m.id}" placeholder="reply"><button class="btn" data-ib="reply" data-msg="${m.id}">Reply</button>`;
      if (m.unread) acts += `<button class="btn" data-ib="read" data-msg="${m.id}">Mark read</button>`;
    }
    return nyCard(`${chip(m.kind === "alert" ? "c-p0" : "c-p", esc(m.kind))}<b>${esc(m.from_agent)}</b>
        ${m.item_id ? `<span class="link" data-open="${m.item_id}">#${m.item_id} ${esc(clip(m.item_title || "", 60))}</span>` : ""}
        <span class="muted" style="font-size:12px">${ago(m.created_at)} · #${m.id}${m.state !== "open" && m.state !== "read" ? " · " + esc(m.state) : ""}</span>
        <span class="link" data-thread="${m.id}">thread</span>`, `<div class="body">${esc(m.body)}</div>
      <div class="actions">${acts}</div>
      <div class="thread hidden" id="thr-${m.id}"></div>`, "msg" + (m.unread || (open && ["question", "offer"].includes(m.kind)) ? "" : " read"));
  }).join("") || `<div class="muted">No messages${$("#inboxAll").checked ? "" : " waiting"}.</div>`;
}

async function inboxAction(t) {
  const what = t.dataset.ib, msg = +t.dataset.msg, id = +t.dataset.id, val = (p) => ($("#" + p + "-" + msg) || {}).value?.trim();
  try {
    if (what === "answer") { const body = val("ans"); if (!body) return toast("Write the answer first", true); await act("answer", { msg, body }); toast("Answered"); }
    if (what === "give") { await act("give", { id, to: t.dataset.to }); toast(`Gave #${id} to ${t.dataset.to}`); }
    if (what === "split") { const titles = (val("split") || "").split(";").map(x => x.trim()).filter(Boolean); if (!titles.length) return toast("Write the smaller pieces, separated by ;", true); await act("split", { id, titles }); toast(`Split #${id} into ${titles.length}`); }
    if (what === "decline") { await act("decline_message", { msg, note: val("dec") || undefined }); toast("Declined"); }
    if (what === "accept") { await act("accept", { id }); toast(`#${id} is yours`); }
    if (what === "decline-push") { await act("decline", { id }); toast(`Handed #${id} back`); }
    if (what === "reply") { const body = val("rep"); if (!body) return toast("Write the reply first", true); await act("send", { kind: "note", body, reply: msg }); toast("Sent"); }
    if (what === "read") await act("message_read", { msg });
  } catch (e) { /* toast shown */ }
  ibSig = ""; document.activeElement && document.activeElement.blur(); await pollInbox();
}

async function toggleThread(msg) {
  const box = $("#thr-" + msg); if (!box) return;
  if (!box.classList.contains("hidden")) return box.classList.add("hidden");
  const r = await fetch(`/api/thread/${msg}`); if (!r.ok) return;
  box.innerHTML = (await r.json()).messages.map(msgLine).join("");
  box.classList.remove("hidden");
}

// ---- Needs you: decisions and steps waiting for a person, with browser notifications.
let NY = [], nySig = "";
const nyOpen = new Set();
function humanActor() { const a = S && S.agents.find(x => x.name === actor()); return a && a.kind === "human" ? a.name : null; }

async function pollNeedsYou() {
  const h = humanActor();
  const r = await fetch("/api/needs-you" + (h ? "?human=" + encodeURIComponent(h) : "")); if (!r.ok) return;
  NY = (await r.json()).events;
  renderNeedsYou(); notifyNew();
}

function renderNeedsYou() {
  document.title = NY.length ? `(${NY.length}) Biggest River` : "Biggest River";
  $("#notifyOn").classList.toggle("hidden", !("Notification" in window) || Notification.permission !== "default");
  $("#needsYouPanel").classList.toggle("hidden", !NY.length);
  $("#nothingForYou").classList.toggle("hidden", !!NY.length || !!(S && (S.takeovers || []).length));
  renderStrip();
  const sig = JSON.stringify([NY.map(e => [e.id, e.summary, e.item_status]), [...nyOpen]]);
  if (sig === nySig) return;
  nySig = sig;
  $("#needsYou").innerHTML = NY.map(e => {
    const isItem = e.kind === "item";
    const detail = isItem ? (e.item_context || e.item_notes) : e.body;
    const buttons = isItem
      ? `<button class="btn" data-open="${e.item_id}">Open</button><button class="btn" data-ny="claim" data-id="${e.item_id}">Claim</button><button class="btn" data-copy-prompt="${e.item_id}" title="A prompt for an agent that explains this and helps you do it">Copy prompt</button><button class="btn primary" data-ny="done" data-id="${e.item_id}">Done</button>`
      : (e.message_kind === "question"
          ? `<button class="btn primary" data-ny="answer" data-msg="${e.message_id}">Answer</button>`
          : `<button class="btn" data-ny="read" data-msg="${e.message_id}">Mark read</button>`)
        + (e.item_id ? `<button class="btn" data-open="${e.item_id}">Open #${e.item_id}</button>` : "");
    // One line each; a click opens the details and the buttons (design: the Board fits one screen).
    const title = isItem ? `#${e.item_id} ${e.item_title}` : `${e.message_kind} from ${e.from_agent}: ${e.body || ""}`;
    const open = nyOpen.has(e.id);
    return `<div class="nyl" data-nyt="${e.id}" title="${esc(clip(detail || title, 300))}"><span class="t">${esc(title)}</span>
        <span class="chips">${e.priority != null ? prioNumChip(e.priority, (e.priority_from ? `priority from #${e.priority_from}` : "own priority") + (e.unblocks_count ? `; unblocks ${e.unblocks_count}` : "")) : ""}${e.project ? projectChip(e.project) : ""}</span></div>
      ${open ? `<div class="nyx"><div class="muted" style="font-size:12px">${ago(e.opened_at)}${e.project ? " · " + esc(e.project) : ""}</div>${detail ? `<div class="ny-c">${esc(clip(detail, 800))}</div>` : ""}<div class="actions">${buttons}</div></div>` : ""}`;
  }).join("");
}

// One browser notification per event, once: seen ids live in localStorage. The first
// load only records what is already open, so opening the page does not fire a burst.
function notifyNew() {
  let seen = null;
  try { seen = JSON.parse(store("river.ny.seen") || "null"); } catch (e) { seen = null; }
  const first = !Array.isArray(seen);
  const known = new Set(first ? [] : seen);
  const fresh = NY.filter(e => !known.has(e.id));
  if (!first && "Notification" in window && Notification.permission === "granted") {
    for (const e of fresh.slice(0, 5)) {
      const n = new Notification("River: needs you", { body: e.summary, tag: "river-" + e.id });
      n.onclick = () => { window.focus(); if (e.item_id) openDrawer(e.item_id); n.close(); };
    }
  }
  store("river.ny.seen", JSON.stringify([...known, ...fresh.map(e => e.id)].slice(-500)));
}

async function copyPrompt(url) {
  const h = humanActor(); const r = await fetch(url + (h ? "?person=" + encodeURIComponent(h) : ""));
  const j = await r.json(); if (!r.ok) return toast(j.error || "failed", true);
  toast(await copyText(j.prompt) ? "Prompt copied: paste it into a new agent session" : "Could not copy; your browser blocked it", !1);
}

async function needsYouAction(t) {
  const what = t.dataset.ny, id = +t.dataset.id, msg = +t.dataset.msg;
  try {
    if (what === "claim") { if (!actor()) return toast("Choose your name in 'You are' first", true); await act("claim", { id }); }
    if (what === "done") { const out = prompt("What did you decide or do? (saved as the item's output)"); if (out === null) return; await act("done", { id, output: out || undefined }); }
    if (what === "answer") {
      if (!actor()) return toast("Choose your name in 'You are' first", true);
      const body = prompt("Your answer"); if (!body) return; await act("answer", { msg, body });
    }
    if (what === "read") await act("message_read", { msg });
  } catch (e) { /* toast shown */ }
}

async function refresh() {
  const r = await fetch("/api/state"); S = await r.json();
  if (S.dev_build) { if (window._build && window._build !== S.dev_build) return location.reload(); window._build = S.dev_build; }
  renderSelects(); renderCapacity(); renderNext(); renderProjects(); renderWork(); renderStrip(); renderAgents(); renderEvents(); renderSettings(); renderTakeovers(); renderBlocked(); renderTargets();
  renderGraph(false); renderLog(false); pollNeedsYou().catch(() => {}); pollInbox().catch(() => {});
  $("#stamp").textContent = "updated " + new Date().toLocaleTimeString();
  if (openItem != null && $("#drawer").classList.contains("open")) openDrawer(openItem);
}

document.addEventListener("click", async (e) => {
  const t = e.target;
  const nyt = t.closest("[data-nyt]"); if (nyt && !t.closest("button")) { const id = +nyt.dataset.nyt; nyOpen.has(id) ? nyOpen.delete(id) : nyOpen.add(id); return renderNeedsYou(); }
  if (t.dataset.takeoversOkall) { for (const x of S.takeovers || []) await act("takeover_seen", { id: x.id }).catch(() => {}); return; }
  if (t.closest("[data-takeovers-toggle]") && !t.closest("button")) { takeoversOpen = !takeoversOpen; return renderTakeovers(); }
  const wp = t.closest("[data-wp]"); if (wp) { const o = workOpen(), n = wp.dataset.wp; o.has(n) ? o.delete(n) : o.add(n); store("river.work.open", JSON.stringify([...o])); return renderWork(); }
  const gw = t.closest("[data-give]"); if (gw) {
    const to = gw.dataset.give, sel = document.querySelector(`[data-give-item="${CSS.escape(to)}"]`);
    if (sel && sel.value) await act("push", { id: +sel.value, to, note: "from the page: you were waiting for work" })
      .then(() => toast(`Gave #${sel.value} to ${to}`)).catch(() => {});
    return; }
  const la = t.closest("[data-launch]"); if (la) {
    if (la.dataset.busy) return; la.dataset.busy = "1"; setTimeout(() => delete la.dataset.busy, 4000);
    const agent = $("#launchAgent") ? $("#launchAgent").value : undefined;
    try { const r = await act("launch_agent", { agent });
      toast(r.pushed_to ? `Gave #${r.item.id} ${clip(r.item.title, 60)} to ${r.pushed_to}, which was waiting for work`
                        : `Started ${r.agent} in ${r.project}, for #${r.item.id} ${clip(r.item.title, 60)}`); } catch (e) { /* toast shown */ }
    return; }
  const sb = t.closest("[data-strip]"); if (sb) { const go = sb.dataset.strip;
    if (go === "blocked") { await setTab("projects"); return $("#blockedPanel").scrollIntoView({ block: "start" }); }
    if (go === "capacity") return setTab("capacity");
    await setTab("board");
    if (go === "takeovers") { takeoversOpen = true; renderTakeovers(); }
    return $(go === "work" ? "#colWork" : "#colNeeds").scrollIntoView({ block: "start" }); }
  if (t.dataset.addgoal) { await setTab("projects"); const d = $("#goalName").closest("details"); d.open = true; $("#goalProject").value = t.dataset.addgoal; d.scrollIntoView({ block: "center" }); $("#goalName").focus(); return; }
  if (t.dataset.goalAct) return goalAction(t.dataset.goalAct, t.dataset.g);
  if (t.dataset.untag) { await act("item_edit", { id: openItem, untag: [t.dataset.untag] }).catch(() => {}); return openDrawer(openItem); }
  const card = t.closest("[data-goal]"); if (card) { const g = card.dataset.goal; setGoal(goalSel() === g ? "" : g); return; }
  const open = t.closest("[data-open]"); if (open) return openDrawer(+open.dataset.open);
  const row = t.closest(".row"); if (row) return openDrawer(+row.dataset.id);
  if (t.id === "dClose") { $("#drawer").classList.remove("open"); openItem = null; return; }
  if (t.dataset.do) return drawerAction(t.dataset.do);
  if (t.dataset.ny) return needsYouAction(t);
  if (t.dataset.ib) return inboxAction(t);
  if (t.dataset.thread) return toggleThread(+t.dataset.thread);
  if (t.id === "sendGo") return sendForm();
  if (t.dataset.offer) { const n = +t.dataset.offer, body = $("#offer-" + n).value.trim(); if (!body) return toast("Say what you can take", true);
    return act("offer", { item: n, body }).then(() => toast(`Offer sent to the holder of #${n}`)).catch(() => {}); }
  if (t.dataset.copyPrompt) return copyPrompt(`/api/item/${t.dataset.copyPrompt}/prompt`);
  if (t.id === "copyAll") return copyPrompt("/api/prompt-all");
  if (t.id === "notifyOn") { await Notification.requestPermission(); return renderNeedsYou(); }
  if (t.dataset.undoTakeover) { await act("undo_takeover", { id: +t.dataset.undoTakeover }).then(() => toast("Back on your list")).catch(() => {}); return; }
  if (t.dataset.takeoverOk) { await act("takeover_seen", { id: +t.dataset.takeoverOk }).catch(() => {}); return; }
  if (t.dataset.unpush) { await act("push_cancel", { id: +t.dataset.unpush }).then(() => toast("Push cancelled")).catch(() => {}); return; }
  if (t.dataset.undep) { await act("dep_remove", { id: openItem, on: [+t.dataset.undep] }).catch(() => {}); return openDrawer(openItem); }
  if (t.dataset.describe) { const p = S.projects.find(x => x.name === t.dataset.describe); const text = prompt(`What does "${p.name}" cover, and what context helps an agent work on it?`, p.notes || ""); if (text !== null) await act("project_describe", { name: p.name, text }).catch(() => {}); return; }
  if (t.dataset.rank) { const to = +t.dataset.to; if (to >= 1) await act("project_rank", { name: t.dataset.rank, rank: to }).catch(() => {}); return; }
  if (t.dataset.unset) { const sc = t.dataset.scope, [kind, name] = sc.includes(":") ? [sc.split(":")[0], sc.slice(sc.indexOf(":") + 1)] : ["global", null];
    const args = { key: t.dataset.unset }; if (kind !== "global") args[kind] = kind === "item" ? +name : name;
    return act("config_unset", args).catch(() => {}); }
  if (t.dataset.tab) return setTab(t.dataset.tab);
  if (t.dataset.fold) { const c = projClosed(), n = t.dataset.fold; store("river.projClosed", JSON.stringify(c.includes(n) ? c.filter(x => x !== n) : c.concat(n))); return renderProjects(); }
});

$("#actor").addEventListener("change", () => { store("river.actor", $("#actor").value); ibSig = ""; pollInbox().catch(() => {}); });
document.addEventListener("change", (e) => { if (e.target.id === "launchAgent") store("river.launch.agent", e.target.value); });
$("#inboxAll").addEventListener("change", () => { ibSig = ""; pollInbox().catch(() => {}); });
async function sendForm() {
  if (!actor()) return toast("Choose your name in 'You are' first", true);
  const body = $("#sendBody").value.trim(), item = $("#sendItem").value.replace("#", "").trim();
  if (!body) return toast("Write the message first", true);
  const args = { kind: $("#sendKind").value, body };
  if (item) args.item = +item; else args.to = $("#sendTo").value;
  try { await act("send", args); $("#sendBody").value = ""; $("#sendItem").value = ""; toast("Sent"); } catch (e) { /* toast shown */ }
}
function setGoal(g) { $("#goalFilter").value = g; store("river.goal", g); renderNext(); renderProjects(); renderGraph(true); }
async function goalAction(what, name) {
  const g = (S.goals || []).find(x => x.name === name); if (!g) return;
  try {
    if (what === "up" || what === "down") {
      const sib = S.goals.filter(x => x.project === g.project).sort((a, b) => a.rank - b.rank);
      const to = sib.findIndex(x => x.name === name) + 1 + (what === "up" ? -1 : 1);
      if (to >= 1 && to <= sib.length) await act("goal_rank", { name, rank: to });
    }
    if (what === "own" || what === "release") {
      if (!actor()) return toast("Choose your name in 'You are' first", true);
      await act("goal_" + what, { name }); toast(what === "own" ? `You own ${name}` : `Released ${name}`);
    }
    if (what === "edit") {
      const outcome = prompt(`Outcome of "${name}": what is true when it is reached?`, g.outcome || ""); if (outcome === null) return;
      const done_when = prompt("Done when: the test the owner checks", g.done_when || ""); if (done_when === null) return;
      await act("goal_edit", { name, outcome, done_when });
    }
    if (what === "done") {
      const result = prompt(`What did "${name}" achieve? (one line)` + (g.items_open.length ? `\n${g.items_open.length} tagged items are still open; they will be dropped.` : "")); if (!result) return;
      if (g.items_open.length && !confirm(`Drop ${g.items_open.length} open items (${g.items_open.map(i => "#" + i).join(", ")}) and complete the goal?`)) return;
      await act("goal_done", { name, result, drop_open: g.items_open.length > 0 }); toast(`${name} complete`);
    }
    if (what === "reopen") await act("goal_reopen", { name });
  } catch (e) { /* toast shown */ }
}
$("#goalFilter").addEventListener("change", () => setGoal($("#goalFilter").value));
$("#addProject").addEventListener("change", renderSelects);
$("#goalGo").addEventListener("click", async () => {
  const name = $("#goalName").value.trim(); if (!name) return toast("Give the goal a name", true);
  const res = await act("goal_add", { project: $("#goalProject").value, name, outcome: $("#goalOutcome").value.trim(), done_when: $("#goalDoneWhen").value.trim() }).catch(() => null);
  if (res) { $("#goalName").value = ""; $("#goalOutcome").value = ""; $("#goalDoneWhen").value = ""; toast(`Added goal ${res.name}`); }
});
$("#area").addEventListener("change", renderNext);
$("#showDone").addEventListener("change", renderProjects);
$("#showIdle").addEventListener("change", renderAgents);
$("#graphSide").addEventListener("click", (e) => {
  const c = e.target.closest("[data-gproj]"); if (!c) return;
  graphProj = c.dataset.gproj; renderGraph(true);
});
$("#graphDone").addEventListener("change", () => renderGraph(true));
$("#logProject").addEventListener("change", () => renderLog(true));
$("#logSince").addEventListener("change", () => renderLog(true));
$("#claimNext").addEventListener("click", async () => {
  if (!actor()) return toast("Choose your name in 'You are' first", true);
  const area = $("#area").value;
  const res = await act("next_claim", area === "__mine" ? { mine: true } : { project: area || undefined }).catch(() => null);
  if (res && res.length) { toast(`Claimed #${res[0].id}`); openDrawer(res[0].id); } else if (res) toast("Nothing is ready in that area");
});
$("#addGo").addEventListener("click", async () => {
  const title = $("#addTitle").value.trim(); if (!title) return toast("Title is empty", true);
  const ids = (el) => $(el).value.split(/[\s,]+/).filter(Boolean).map(Number);
  const res = await act("item_add", { project: $("#addProject").value, title, priority: +$("#addPrio").value, doer: $("#addDoer").value, after: ids("#addAfter"), feeds: ids("#addFeeds"), notes: $("#addNotes").value, goals: $("#addGoal").value ? [$("#addGoal").value] : [] }).catch(() => null);
  if (res) { $("#addTitle").value = ""; $("#addAfter").value = ""; $("#addFeeds").value = ""; $("#addNotes").value = ""; toast(`Added #${res.id}`); }
});
$("#projGo").addEventListener("click", async () => { const n = $("#projName").value.trim(); if (n) { await act("project_add", { name: n }).catch(() => {}); $("#projName").value = ""; } });
$("#regGo").addEventListener("click", async () => {
  const n = $("#regName").value.trim(); if (!n) return;
  await act("register", { name: n, human: $("#regHuman").checked, note: $("#regNote").value }).catch(() => {});
  store("river.actor", n); $("#regName").value = ""; await refresh(); $("#actor").value = n;
});
$("#setGo").addEventListener("click", async () => {
  const scope = $("#setScope").value, nm = $("#setScopeName").value.trim();
  const args = { key: $("#setKey").value, value: $("#setValue").value.trim() };
  if (scope !== "global") { if (!nm) return toast("Give the project, agent, or item", true); args[scope] = scope === "item" ? +nm : nm; }
  await act("config_set", args).then(() => toast("Saved")).catch(() => {});
});

if (window.mermaid) mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default", flowchart: { useMaxWidth: true } });
// Drag an item row onto an agent to push it there.
document.addEventListener("dragstart", (e) => { const r = e.target.closest && e.target.closest(".row[draggable]"); if (r) e.dataTransfer.setData("text/river-item", r.dataset.id); });
document.addEventListener("dragover", (e) => { const a = e.target.closest && e.target.closest("[data-agent]"); if (a && e.dataTransfer.types.includes("text/river-item")) { e.preventDefault(); a.classList.add("drop"); } });
document.addEventListener("dragleave", (e) => { const a = e.target.closest && e.target.closest("[data-agent]"); if (a) a.classList.remove("drop"); });
document.addEventListener("drop", async (e) => {
  const a = e.target.closest && e.target.closest("[data-agent]"); if (!a) return;
  e.preventDefault(); a.classList.remove("drop");
  const id = +e.dataTransfer.getData("text/river-item"); if (!id) return;
  await act("push", { id, to: a.dataset.agent }).then(() => toast(`Pushed #${id} to ${a.dataset.agent}`)).catch(() => {});
});

const TABS = ["board", "projects", "targets", "capacity", "graph", "done", "settings"];
// Each tab has its own URL (#tab-graph; the Board is the bare page), so a tab can be linked, reloaded, and reached with Back.
function tabHash(name) {
  const keep = location.hash.slice(1).split("&").filter(x => /^as-/.test(x));
  if (name !== "board") keep.push("tab-" + name);
  return keep.length ? "#" + keep.join("&") : location.pathname + location.search;
}
function setTab(name, push = true) {
  if (push && name !== tab) history.pushState(null, "", tabHash(name));
  tab = name; document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  for (const s of TABS) $("#tab-" + s).classList.toggle("hidden", s !== tab);
  renderLog(true);
  return renderGraph(true);
}
// Links into the page: #item-4, #tab-graph, #as-alex, or several joined with & (#as-alex&item-4).
// Setup guide: an overlay that shows until the user dismisses it for good (setting setup_done).
let setupSeen = false;
async function openSetup() {
  const r = await fetch("/api/setup"); if (!r.ok) return;
  renderSetup(await r.json()); $("#setup").classList.remove("hidden");
}
function closeSetup() { $("#setup").classList.add("hidden"); }
function renderSetup(st) {
  const step = (ok, title, sub, fix) => `<div class="step"><div class="mark ${ok ? "ok" : "todo"}">${ok ? "✓" : "•"}</div>
    <div><b>${title}</b>${sub ? `<div class="sub">${sub}</div>` : ""}${!ok && fix ? `<div class="fix">${fix}</div>` : ""}</div></div>`;
  const out = [];
  out.push(step(st.people.length > 0, "Register yourself as a person",
    st.people.length ? "People: " + st.people.map(esc).join(", ") + ". Pick your name in the top bar." : "River shows you what needs you and sends you notifications.",
    `<input id="suName" placeholder="your name" style="width:140px"><button class="btn" data-su="register">Register</button>`));
  // One instructions file for every agent: AGENTS.md holds the rules, CLAUDE.md imports it (@AGENTS.md).
  const blocks = st.folders.filter(f => f.exists && (f.claude_md !== "current" || f.agents_md !== "current"));
  const layouts = { shared: "one file for every agent (CLAUDE.md imports AGENTS.md)",
    claude_only: "the rules are only in CLAUDE.md; Codex and other agents miss them",
    both: "CLAUDE.md and AGENTS.md hold different rules; each agent reads only one" };
  const state = (f) => `CLAUDE.md ${f.claude_md}, AGENTS.md ${f.agents_md}` + (layouts[f.layout] ? `; ${layouts[f.layout]}` : "");
  out.push(step(st.folders.length > 0 && blocks.length === 0, "Tell agents about the queue in each project folder",
    st.folders.length ? st.folders.map(f => `<div>${esc(f.path)} (${f.projects.map(esc).join(", ")}): ${f.exists ? esc(state(f)) : "folder not found"}`
      + (f.layout === "claude_only" ? ` <button class="btn" data-su="block" data-move="1" data-path="${esc(f.path)}"
          title="The text of CLAUDE.md goes to AGENTS.md; CLAUDE.md becomes the one line @AGENTS.md">Move the rules to AGENTS.md</button>` : "")
      + `</div>`).join("")
      + (st.projects_without_folder.length ? `<div>No folder: ${st.projects_without_folder.map(esc).join(", ")}</div>` : "")
      : "No project has a folder yet. In a project folder, run <code>river init</code>.",
    blocks.map(f => `<button class="btn" data-su="block" data-path="${esc(f.path)}">Update ${esc(f.path.split("/").pop())}</button>`).join("")));
  const missing = Object.entries(st.skills).filter(([, v]) => v !== "installed").map(([k]) => k);
  if (st.claude_home) out.push(step(missing.length === 0, "Install the Claude Code skills",
    missing.length ? "Missing in ~/.claude/skills: " + missing.join(", ") : "river and river-planner are installed.",
    `<button class="btn" data-su="skills">Install skills</button>`));
  const addable = st.agent_clis.filter(a => a.found && !a.added);
  out.push(step(addable.length === 0, "Agents for the Start button",
    "Start button offers: " + st.launch_agents.map(esc).join(", ")
      + (addable.length ? ". Also on this computer: " + addable.map(a => esc(a.label)).join(", ") : ""),
    addable.map(a => `<button class="btn" data-su="agent" data-label="${esc(a.label)}" title="${esc(a.command)}">Add ${esc(a.label)}</button>`).join("")));
  out.push(step(st.ntfy_ready && st.notify_channels.includes("ntfy"), "Phone notifications (ntfy)",
    st.ntfy_ready ? "Channels: " + (st.notify_channels.map(esc).join(", ") || "none") : "Get a push on your phone when an item needs you.",
    `<button class="btn" data-su="ntfy">Turn on ntfy</button><span id="suNtfy" class="sub"></span>`));
  const tp = (S && S.projects || []).filter(p => p.path);
  const noTracker = tp.filter(p => !p.tracker);
  out.push(step(noTracker.length === 0, "Issue tracker (optional)",
    tp.length ? tp.map(p => `<div>${esc(p.name)}: ${p.tracker ? esc(p.tracker) : "none"}</div>`).join("")
      + "Agents import issues from it in plan, and update it when an item is done. Say which tracker, where, and with what tool."
      : "No project has a folder yet.",
    noTracker.map(p => `<span style="display:flex;gap:4px;align-items:center">${esc(p.name)}
      <input data-tracker-for="${esc(p.name)}" placeholder="github owner/repo via gh" style="width:220px">
      <button class="btn" data-su="tracker" data-project="${esc(p.name)}">Save</button></span>`).join("")));
  $("#setupSteps").innerHTML = out.join("");
}
$("#setupSteps").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-su]"); if (!b) return;
  const k = b.dataset.su;
  try {
    if (k === "register") {
      const n = $("#suName").value.trim(); if (!n) return toast("Type your name first", true);
      await act("register", { name: n, human: true, note: "" });
      $("#actor").value = n; store("river.actor", n);
    } else if (k === "block") await act("setup_block", { path: b.dataset.path,
      move: b.dataset.move === undefined ? null : b.dataset.move === "1" });
    else if (k === "tracker") {
      const v = document.querySelector(`[data-tracker-for="${CSS.escape(b.dataset.project)}"]`).value.trim();
      if (!v) return toast("Say which tracker, for example: github owner/repo via gh", true);
      await act("config_set", { key: "tracker", value: v, project: b.dataset.project });
    }
    else if (k === "skills") await act("setup_skills", {});
    else if (k === "agent") await act("setup_agent_add", { label: b.dataset.label });
    else if (k === "ntfy") {
      const r = await act("setup_ntfy", {});
      await openSetup();
      $("#suNtfy").textContent = (r && r.subscribe || []).join(" ");
      return;
    }
    await openSetup();
  } catch (err) { /* act() showed the error */ }
});
$("#agents").addEventListener("change", (e) => { const s = e.target.closest("[data-give-item]"); if (s) giveChoice[s.dataset.giveItem] = s.value; });
$("#setupClose").onclick = closeSetup;
$("#setupLater").onclick = closeSetup;
$("#setupDone").onclick = async () => { await act("config_set", { key: "setup_done", value: "on" }).catch(() => {}); closeSetup(); };
$("#openSetup").onclick = openSetup;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#setup").classList.contains("hidden")) closeSetup(); });
function maybeSetup() {
  if (setupSeen || !S) return;
  setupSeen = true;
  const done = S.settings.overrides.some(x => x.key === "setup_done" && x.scope === "global" && x.value === "on");
  if (!done) openSetup();
}

async function openFromHash() {
  let tabIn = null, itemIn = false;
  for (const part of location.hash.slice(1).split("&")) {
    const m = part.match(/^(item|tab|as)-(.+)$/); if (!m) continue;
    const v = decodeURIComponent(m[2]);
    if (m[1] === "as" && [...$("#actor").options].some(o => o.value === v)) { $("#actor").value = v; store("river.actor", v); ibSig = ""; await pollInbox().catch(() => {}); }
    if (m[1] === "tab" && TABS.includes(v)) tabIn = v;
    if (m[1] === "item") { itemIn = true; await openDrawer(+v); }
  }
  // An item link alone keeps the tab; a link with no tab and no item is the Board.
  if (!tabIn && !itemIn) tabIn = "board";
  if (tabIn && tabIn !== tab) await setTab(tabIn, false);
}
window.addEventListener("hashchange", openFromHash);
// Update button: shows how many new commits the river clone is missing; a click pulls them and restarts the server.
// It also says when the server itself is out of date: code changed on disk after it started (mode "restart"),
// or the server is too old to know the update route (mode "old": only a manual restart helps).
const RESTART_BY_HAND = "This page is newer than its server. Stop river serve (Ctrl-C in its terminal) and start it again.";
let updateMode = "update";
async function checkUpdate() {
  const r = await fetch("/api/update"), b = $("#updateBtn");
  const u = r.ok ? await r.json() : {};
  if (!r.ok) {
    updateMode = "old";
    b.classList.remove("hidden"); b.classList.add("primary");
    b.textContent = "Restart needed"; b.title = RESTART_BY_HAND;
    return;
  }
  if (!u.git && !u.stale) return b.classList.add("hidden");
  b.classList.remove("hidden");
  updateMode = u.behind ? "update" : u.stale ? "restart" : "update";
  b.classList.toggle("primary", u.behind > 0 || !!u.stale);
  b.textContent = u.behind ? `Update · ${u.behind} new` : u.stale ? "Restart · new code" : "Update";
  b.title = u.behind ? "New in Biggest River:\n" + u.commits.join("\n")
      + (u.ahead ? `\n\nThis clone also has ${u.ahead} commit(s) of its own, so it cannot fast-forward: push or rebase them first.` : "")
    : u.stale ? "Biggest River's code changed since this server started. Restart the server to run it."
    : u.fetch_error ? "Could not check for updates: " + u.fetch_error
    : `Up to date (${u.head})` + (u.ahead ? `; this clone has ${u.ahead} commit(s) not on ${u.upstream} yet` : "");
}
async function waitForRestart(boot) {
  for (let i = 0; i < 60; i++) {  // wait for the restarted server, then load the new page
    await new Promise(res => setTimeout(res, 500));
    try { const s = await (await fetch("/api/update?fetch=0")).json(); if (s.boot !== boot) return location.reload(); } catch (e) {}
  }
  toast("The server did not come back; run river serve again", true);
}
$("#updateBtn").onclick = async () => {
  if (updateMode === "old") return toast(RESTART_BY_HAND, true);
  const b = $("#updateBtn"); b.disabled = true; b.textContent = updateMode === "restart" ? "Restarting…" : "Updating…";
  try {
    const boot = (await (await fetch("/api/update?fetch=0")).json()).boot;
    const r = await fetch("/api/action", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ op: updateMode, args: {}, actor: actor() }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || updateMode + " failed");
    if (updateMode === "restart") { toast("Restarting the server…"); return await waitForRestart(boot); }
    const u = j.result;
    if (!u.updated) { toast(`Already up to date (${u.head})`); return; }
    toast(`Updated ${u.from} → ${u.head}. Restarting…`);
    await waitForRestart(boot);
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; checkUpdate().catch(() => {}); }
};
checkUpdate().catch(() => {}); setInterval(() => { if (!document.hidden) checkUpdate().catch(() => {}); }, 30 * 60 * 1000);
// A stale server is cheap to spot (no git fetch), so look for it more often than for new commits.
setInterval(() => {
  if (document.hidden || updateMode !== "update") return;  // hidden, or already asking for a restart
  fetch("/api/update?fetch=0").then(r => r.ok ? r.json() : null).then(u => { if (u && u.stale) checkUpdate(); }).catch(() => {});
}, 60 * 1000);
refresh().then(openFromHash).then(maybeSetup); setInterval(() => { if (!document.hidden) refresh().catch(() => {}); }, 3000);
// A hidden tab stops the full refresh but keeps asking what needs a person, so notifications still arrive.
setInterval(() => { if (document.hidden) pollNeedsYou().catch(() => {}); }, 15000);

