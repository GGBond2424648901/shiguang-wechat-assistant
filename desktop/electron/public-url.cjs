"use strict";
const { isIP } = require("node:net");

function publicWebUrl(value) {
  if (typeof value !== "string" || value.length > 800 || /[\s\\\u0000-\u001f]/.test(value)) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/\.$/, "").toLowerCase();
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port && !["80", "443"].includes(url.port)
      || !host.includes(".") || isIP(host) || /\.(local|localhost|internal|test|invalid)$/.test(host)) return null;
    return url.href;
  } catch { return null; }
}
module.exports = { publicWebUrl };
