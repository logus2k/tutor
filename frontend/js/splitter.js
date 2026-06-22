// splitter.js — draggable vertical splitter between two panels.
//
// The divider is dragged horizontally; the left panel's width (as a fraction of
// the container) is persisted to localStorage so the layout survives reloads.
// Works for mouse and touch. Mirrors the resizer pattern used in noted/job2cool.

const LS_KEY = 'tutor.splitFrac';
const MIN_FRAC = 0.2;
const MAX_FRAC = 0.8;

export function initSplitter(container, left, handle, right) {
  let frac = clamp(parseFloat(localStorage.getItem(LS_KEY)) || 0.58);
  apply(frac);

  let dragging = false;

  const onMove = (clientX) => {
    const rect = container.getBoundingClientRect();
    frac = clamp((clientX - rect.left) / rect.width);
    apply(frac);
  };

  const start = (e) => {
    dragging = true;
    document.body.classList.add('is-resizing');
    e.preventDefault();
  };
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('is-resizing');
    localStorage.setItem(LS_KEY, String(frac));
  };

  handle.addEventListener('mousedown', start);
  window.addEventListener('mousemove', (e) => { if (dragging) onMove(e.clientX); });
  window.addEventListener('mouseup', stop);

  handle.addEventListener('touchstart', start, { passive: false });
  window.addEventListener('touchmove', (e) => { if (dragging && e.touches[0]) onMove(e.touches[0].clientX); }, { passive: false });
  window.addEventListener('touchend', stop);

  // Double-click resets to the default split.
  handle.addEventListener('dblclick', () => { frac = 0.58; apply(frac); localStorage.setItem(LS_KEY, String(frac)); });

  function apply(f) {
    left.style.flex = `0 0 calc(${(f * 100).toFixed(3)}% - 4px)`;
    right.style.flex = '1 1 0';
  }
}

function clamp(f) {
  if (!Number.isFinite(f)) return 0.58;
  return Math.min(MAX_FRAC, Math.max(MIN_FRAC, f));
}
