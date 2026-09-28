// Chips: the small colored labels on items, goals, projects, and messages.
import { esc, left } from "../lib.js";

// One chip. cls picks the color (c-ready, c-p, c-p0, c-goal, ...); html is already escaped;
// title is plain text; attrs adds raw attributes.
export function chip(cls, html, title, attrs = "") {
  return `<span class="chip ${cls}"${title != null ? ` title="${esc(title)}"` : ""}${attrs}>${html}</span>`;
}

export function statusChip(it) {
  const pushed = it.status === "open" && it.reserved_until ? chip("c-human", "pushed · " + esc(it.reserved_for), "pushed by " + (it.reserved_by || "?")) : "";
  return pushed + baseStatusChip(it);
}
function baseStatusChip(it) {
  if (it.status === "open") {
    if (it.ready) return chip("c-ready", "ready");
    if (it.blocked_reason) return chip("c-blocked", it.blocked_until ? "blocked · " + left(it.blocked_until) : "blocked", it.blocked_text);
    return chip("c-waiting", "waits on " + it.open_blockers.map(b => "#" + b).join(","));
  }
  const who = it.assignee ? " · " + esc(it.assignee) : "";
  return chip("c-" + it.status, it.status.replace("_", " ") + who);
}
export function prioChip(it) {
  const inh = it.priority_from != null ? ` ← #${it.priority_from}` : "";
  return prioNumChip(it.effective_priority, "own priority P" + it.priority, inh);
}
// A priority number alone, red at P0.
export function prioNumChip(p, title, suffix = "") { return chip(p === 0 ? "c-p0" : "c-p", "P" + p + suffix, title); }
export function dueChip(it) {
  if (!it.effective_due || ["done", "dropped"].includes(it.status)) return "";
  const cls = it.due_state === "overdue" ? "c-p0" : it.due_state === "soon" ? "c-blocked" : "c-p";
  const word = it.due_state === "overdue" ? "overdue" : "due " + left(it.effective_due);
  return chip(cls, word, "due " + it.due_text + (it.due_from ? " (from #" + it.due_from + ")" : ""));
}
export function replanChip(it) {
  return it.replan && !["done", "dropped"].includes(it.status) ? chip("c-blocked", "replan", `${it.late_prereqs} prerequisites added while claimed: plan it again`) : "";
}
// "human" or "agent": who does an item (doer ai|human), or what an agent row is (kind human|ai).
export function personChip(kind) { return kind === "human" ? chip("c-human", "human") : chip("c-ai", "agent"); }
export function doerChip(it) { return it.doer === "any" ? "" : personChip(it.doer); }
export function goalChips(it) { return (it.goals || []).map(g => chip("c-goal", esc(g), "goal")).join(""); }
export function projectChip(name) { return chip("c-p", esc(name)); }
export function ownerChip(owner) { return owner ? chip("c-ai", "owner · " + esc(owner)) : chip("c-waiting", "no owner"); }
// A count with its label; gray when it is zero.
export function countChip(n, label, cls) { return chip(n ? cls : "c-p", `<b>${n}</b>${label}`); }
