const WM_DPICHANGED = 0x02E0;

function roundedWindowShape(width, height, radius, frameInset = 0) {
  const corner = Math.floor(Math.min(radius, width / 2, height / 2));
  if (corner <= 0) return [];

  const top = [];
  let row = 0;
  for (; row < corner; row++) {
    const distance = corner - row - 0.5;
    const inset = Math.round(corner - Math.sqrt(corner ** 2 - distance ** 2));
    if (inset === 0) break;

    const x = frameInset + inset;
    const previous = top.at(-1);
    if (previous?.x === x) previous.height++;
    else top.push({ x, y: row, width: width - 2 * inset, height: 1 });
  }
  if (row === 0) return [];

  // Keep the HWND's invisible resize bands where the rounded client reaches an edge.
  const middle = { x: 0, y: row, width: width + 2 * frameInset, height: height - 2 * row };
  const bottom = top.map(rect => ({
    ...rect,
    y: height - rect.y - rect.height,
  })).reverse();
  // The non-client band below the rounded client keeps both diagonal handles.
  if (frameInset > 0) bottom.push({ x: frameInset, y: height, width, height: frameInset });
  return [...top, middle, ...bottom];
}

function applyRoundedWindowShape(window, radius = 24) {
  if (typeof window.setShape !== "function") return async () => {};

  let frameInset;
  let appliedShape;
  const isExpanded = () => window.isMaximized() || window.isFullScreen();

  function syncShape() {
    if (window.isDestroyed() || window.isMinimized()) return;
    const expanded = isExpanded();
    if (!expanded && frameInset === undefined) return;

    const { width, height } = window.getBounds();
    const key = expanded ? "expanded" : `${width}:${height}:${frameInset}`;
    if (key === appliedShape) return;
    appliedShape = key;
    window.setShape(expanded ? [] : roundedWindowShape(width, height, radius, frameInset));
  }

  async function prepare() {
    if (window.isDestroyed() || window.isMinimized() || isExpanded()) {
      syncShape();
      return;
    }

    // Electron's logical bounds omit the Windows resize bands. The renderer's
    // outer bounds cover the entire HWND and remain in DIP at any page zoom.
    const { width, height } = window.getBounds();
    const outer = await window.webContents.executeJavaScript(
      "({ width: window.outerWidth, height: window.outerHeight })",
    ).catch(() => null);
    if (outer && !window.isDestroyed() && !window.isMinimized() && !isExpanded()) {
      const bounds = window.getBounds();
      const inset = outer.height - height;
      if (bounds.width === width && bounds.height === height && Number.isInteger(inset)
          && inset >= 0 && outer.width === width + 2 * inset) {
        frameInset = inset;
      }
    }
    syncShape();
  }

  for (const event of ["resize", "maximize", "enter-full-screen"]) window.on(event, syncShape);
  for (const event of ["restore", "unmaximize", "leave-full-screen"]) {
    window.on(event, () => {
      syncShape();
      void prepare();
    });
  }
  window.hookWindowMessage?.(WM_DPICHANGED, () => {
    setImmediate(() => {
      appliedShape = undefined;
      syncShape();
      void prepare();
    });
  });

  return prepare;
}

module.exports = { applyRoundedWindowShape, roundedWindowShape };
