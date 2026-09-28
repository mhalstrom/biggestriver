// Dialogs: a modal overlay (an element with a .box inside, hidden by the class "hidden").
// Escape closes it, and focus goes back to where it was before it opened.

export function makeDialog(el, { onClose } = {}) {
  let before = null;
  const isOpen = () => !el.classList.contains("hidden");
  function open() {
    if (!isOpen()) before = document.activeElement;
    el.classList.remove("hidden");
  }
  function close() {
    if (!isOpen()) return;
    el.classList.add("hidden");
    if (before && before.isConnected && before.focus) before.focus({ preventScroll: true });
    before = null;
    if (onClose) onClose();
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  return { open, close, isOpen };
}
