const WM_DPICHANGED = 0x02E0;
const fullScreenWindows = new WeakSet();
function isWindowFullscreen(window) {
  return fullScreenWindows.has(window) || window.isFullScreen();
}

// Let Chromium composite the CSS curve with fractional alpha. setShape uses
// integer rectangles: cutting at the same radius destroys those edge pixels,
// particularly at 125% and 150% display scaling. Clear every backing layer
// instead, so transparent corners cannot expose the default white View paint.
function prepareTransparentWindow(window) {
  function refreshTransparency() {
    if (window.isDestroyed()) return;
    window.setBackgroundColor('#00000000');
    window.contentView.setBackgroundColor('#00000000');
    for (const view of window.contentView.children) view.setBackgroundColor('#00000000');
  }
  window.on('enter-full-screen', () => fullScreenWindows.add(window));
  window.on('leave-full-screen', () => fullScreenWindows.delete(window));
  window.hookWindowMessage?.(WM_DPICHANGED, () => setImmediate(refreshTransparency));
  return refreshTransparency;
}
module.exports = { prepareTransparentWindow, isWindowFullscreen };
