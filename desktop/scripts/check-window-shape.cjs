const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const { applyRoundedWindowShape, roundedWindowShape } = require("../electron/window-shape.cjs");

function covers(rects, x, y) {
  return rects.some(rect => x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height);
}

test("window region preserves the resize bands around a rounded client area", () => {
  const shape = roundedWindowShape(530, 344, 24, 7);

  for (const [x, y] of [[7, 0], [536, 0], [7, 343], [536, 343], [0, 0], [543, 350]]) {
    assert.equal(covers(shape, x, y), false, `corner at ${x}, ${y}`);
  }
  for (const [x, y] of [[272, 0], [272, 350], [0, 172], [543, 172], [7, 172], [536, 172], [17, 5]]) {
    assert.equal(covers(shape, x, y), true, `client or resize edge at ${x}, ${y}`);
  }
  assert.ok(covers(shape, 14, 349), "bottom-left resize target");
  assert.ok(covers(shape, 529, 349), "bottom-right resize target");
  for (const rect of shape) {
    assert.ok(Object.values(rect).every(Number.isInteger));
    assert.ok(rect.x >= 0 && rect.y >= 0 && rect.width > 0 && rect.height > 0);
    assert.ok(rect.x + rect.width <= 544 && rect.y + rect.height <= 351);
  }
});

class TestWindow extends EventEmitter {
  width = 530;
  height = 344;
  inset = 7;
  maximized = false;
  fullScreen = false;
  minimized = false;
  destroyed = false;
  shape = null;

  getBounds() { return { x: 759, y: 380, width: this.width, height: this.height }; }
  isMaximized() { return this.maximized; }
  isFullScreen() { return this.fullScreen; }
  isMinimized() { return this.minimized; }
  isDestroyed() { return this.destroyed; }
  setShape(shape) { this.shape = shape; }
  hookWindowMessage(message, callback) { this.dpiChange = message === 0x02E0 ? callback : undefined; }

  webContents = {
    executeJavaScript: async () => ({ width: this.width + this.inset * 2, height: this.height + this.inset }),
  };
}

test("normal, resized, restored, and full-screen windows use the appropriate region", async () => {
  const window = new TestWindow();
  const prepare = applyRoundedWindowShape(window, 24);

  await prepare();
  assert.ok(covers(window.shape, 543, 172));
  assert.ok(!covers(window.shape, 544, 172));

  window.width = 650;
  window.height = 440;
  window.emit("resize");
  assert.ok(covers(window.shape, 663, 220));
  assert.ok(covers(window.shape, 332, 446));
  assert.ok(!covers(window.shape, 663, 446));

  window.maximized = true;
  window.emit("maximize");
  assert.deepEqual(window.shape, []);
  window.maximized = false;
  window.emit("unmaximize");
  assert.ok(covers(window.shape, 663, 220));

  window.fullScreen = true;
  window.emit("enter-full-screen");
  assert.deepEqual(window.shape, []);
  window.fullScreen = false;
  window.emit("leave-full-screen");
  assert.ok(covers(window.shape, 663, 220));

  window.minimized = true;
  window.width = 600;
  window.emit("resize");
  assert.ok(covers(window.shape, 663, 220));
  window.minimized = false;
  window.emit("restore");
  assert.ok(covers(window.shape, 613, 220));
  assert.ok(!covers(window.shape, 614, 220));
});

test("a DPI change refreshes frame insets even when logical dimensions stay the same", async () => {
  const window = new TestWindow();
  const prepare = applyRoundedWindowShape(window, 24);
  await prepare();
  assert.ok(!covers(window.shape, 545, 172));

  window.inset = 8;
  window.dpiChange();
  await new Promise(setImmediate);
  assert.ok(covers(window.shape, 545, 172));
});
