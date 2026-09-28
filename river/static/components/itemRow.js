// Item rows, and the one order items take in a list.
import { esc } from "../lib.js";
import { goalChips, prioChip, dueChip, doerChip, replanChip, statusChip, projectChip } from "./chip.js";

// Ready first, then more important, then what unblocks more, then the project's own order.
export function itemOrder(a, b) {
  return (b.ready - a.ready) || (a.effective_priority - b.effective_priority) || (b.unblocks_count - a.unblocks_count) || (a.rank - b.rank);
}

// One item: id, title, an optional line under it (why), and its chips. Open items can be dragged onto an agent.
export function itemRow(it, why) {
  return `<div class="row" data-id="${it.id}"${it.status === "open" ? ' draggable="true"' : ""}>
    <div class="id">#${it.id}</div>
    <div class="t"><div class="title">${esc(it.title)}</div>${it.blocked_reason && it.status === "open" ? `<div class="blk">${esc(it.blocked_text)}</div>` : ""}${why ? `<div class="why">${esc(why)}</div>` : ""}</div>
    <div class="chips">${goalChips(it)}${prioChip(it)}${dueChip(it)}${doerChip(it)}${replanChip(it)}${statusChip(it)}</div></div>`;
}

// One finished item in the Done log: the same id and title, its project, output, and when and by whom.
export function doneRow(it, when) {
  return `<div class="done-row" data-open="${it.id}">
        <div class="id">#${it.id}</div>
        <div><div class="title">${esc(it.title)} ${projectChip(it.project)}</div>${it.output ? `<div class="out">${esc(it.output)}</div>` : ""}</div>
        <div class="when">${when}${it.by_agent && it.by_agent !== "?" ? "<br>" + esc(it.by_agent) : ""}</div>
      </div>`;
}
