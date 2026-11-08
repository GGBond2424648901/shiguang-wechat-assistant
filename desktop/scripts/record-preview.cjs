/* Record the owned renderer. Desktop cursor and other apps are outside this capture. */
const { once } = require("node:events");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

module.exports = async function recordPreview(page, output, ffmpeg) {
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  const button = (name) => page.getByRole("button", { name, exact: true });
  await button("工作台").click();
  const started = performance.now();
  cdp.on("Page.screencastFrame", ({ data, sessionId }) => {
    if (frames.length < 650) frames.push({ time: performance.now() - started, data: Buffer.from(data, "base64") });
    void cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1440, maxHeight: 920, everyNthFrame: 1 });
  try {
    await page.waitForTimeout(2700);
    await button("会话").click();
    await page.waitForTimeout(2600);
    await button("人格").click();
    await page.waitForTimeout(2400);
    await button("表情包").click();
    await page.waitForTimeout(2400);
    await button("运行设置").click();
    await button("界面外观").click();
    await page.waitForTimeout(1000);
    await page.getByRole("slider", { name: "玻璃透明度" }).press("End");
    await page.waitForTimeout(1000);
    await button("缩小界面").click();
    await page.waitForTimeout(700);
    await button("恢复默认").click();
    await page.waitForTimeout(1200);
  } finally {
    await cdp.send("Page.stopScreencast");
    await cdp.detach();
  }
  if (frames.length < 10) throw new Error("界面录制没有足够的帧");
  const video = path.join(output, "shiguang-desktop-15s.mp4");
  const fps = 24;
  const filter = "scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2:color=0x191b2c";
  const encoder = spawn(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe",
    "-framerate", String(fps), "-vcodec", "mjpeg", "-i", "pipe:0", "-vf", filter,
    "-c:v", "libx264", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", video],
    { windowsHide: true, stdio: ["pipe", "ignore", "inherit"] });
  const closed = once(encoder, "close");
  let cursor = 0;
  for (let i = 0; i < 15 * fps; i++) {
    while (cursor + 1 < frames.length && frames[cursor + 1].time <= i * 1000 / fps) cursor++;
    if (!encoder.stdin.write(frames[cursor].data)) await once(encoder.stdin, "drain");
  }
  encoder.stdin.end();
  const [code] = await closed;
  if (code !== 0 || (await fs.stat(video)).size < 50_000) throw new Error("界面演示编码失败");
  return video;
};
