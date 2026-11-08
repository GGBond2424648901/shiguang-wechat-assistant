const WM_DPICHANGED = 0x02E0;
const fullScreenWindows = new WeakSet();

function isWindowFullscreen(window) {
  return fullScreenWindows.has(window) || window.isFullScreen();
}

function roundedWindowShape(width, height, radius) {
  const corner = Math.floor(Math.min(radius, width / 2, height / 2));
  if (corner <= 0) return [];

  const top = [];
  let row = 0;
  for (; row < corner; row++) {
    const distance = corner - row - 0.5;
    const inset = Math.round(corner - Math.sqrt(corner ** 2 - distance ** 2));
    if (inset === 0) break;

    const previous = top.at(-1);
    if (previous?.x === inset) previous.height++;
    else top.push({ x: inset, y: row, width: width - 2 * inset, height: 1 });
  }
  if (row === 0) return [];

  const middle = { x: 0, y: row, width, height: height - 2 * row };
  const bottom = top.map(rect => ({
    ...rect,
    y: height - rect.y - rect.height,
  })).reverse();
  return [...top, middle, ...bottom];
}

function applyRoundedWindowShape(window, radius = 24) {
  let appliedShape;

  function syncShape() {
    if (window.isDestroyed() || window.isMinimized() || typeof window.setShape !== "function") return;
    const expanded = window.isMaximized() || isWindowFullscreen(window);
    const { width, height } = window.getBounds();
    const key = expanded ? "expanded" : `${width}:${height}`;
    if (key === appliedShape) return;
    window.setShape(expanded ? [] : roundedWindowShape(width, height, radius));
    appliedShape = key;
  }

  for (const event of ["resize", "maximize", "restore", "unmaximize"]) window.on(event, syncShape);
  // Electron emits these events for a frameless window's bounds-based fullscreen.
  window.on("enter-full-screen", () => {
    fullScreenWindows.add(window);
    syncShape();
  });
  window.on("leave-full-screen", () => {
    fullScreenWindows.delete(window);
    syncShape();
  });
  window.hookWindowMessage?.(WM_DPICHANGED, () => {
    setImmediate(() => {
      appliedShape = undefined;
      syncShape();
    });
  });

  return syncShape;
}

module.exports = { applyRoundedWindowShape, isWindowFullscreen, roundedWindowShape };
