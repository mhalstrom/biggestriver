// Start an agent: the agent picker and the button(s) that start one, the same wherever the page starts an
// agent. agents: the Start button's list (S.launch_agents, setting launch_agents). Every picker shows the
// same choice: a change stores it (river.launch.agent) and sets the other pickers on the page.
import { esc, store } from "../lib.js";

// The agent the page starts: the stored choice while it is in the list, else the first.
export function pickedAgent(agents) {
  const LA = agents || [], s = store("river.launch.agent");
  return LA.includes(s) ? s : LA[0];
}

// The picker alone; nothing when there is only one agent to pick.
export function agentPick(agents) {
  const LA = agents || [], pick = pickedAgent(LA);
  if (LA.length < 2) return "";
  return `<select class="agentPick" title="Which agent this starts (setting launch_agents; add more in Settings > Setup)" aria-label="Agent">${LA.map(a => `<option ${a === pick ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>`;
}

// The picker and its buttons. Each button: {label, title, attrs, cls}; attrs are raw attributes (data-*),
// label and title are plain text. With one agent there is no picker, and the title names that agent.
export function agentStart(agents, buttons) {
  const LA = agents || [], who = LA.length === 1 ? ` (${LA[0]})` : "";
  const bs = (Array.isArray(buttons) ? buttons : [buttons]).map(b =>
    `<button class="${b.cls || "btn"}" ${b.attrs || ""} title="${esc((b.title || "") + who)}">▶ ${esc(b.label)}</button>`);
  return `<span class="agentStart">${agentPick(LA)}${bs.join("")}</span>`;
}

// Once per page: keep every picker on the same choice.
export function wireAgentPicks() {
  document.addEventListener("change", (e) => {
    if (!e.target.classList || !e.target.classList.contains("agentPick")) return;
    store("river.launch.agent", e.target.value);
    for (const s of document.querySelectorAll(".agentPick")) if (s !== e.target) s.value = e.target.value;
  });
}
