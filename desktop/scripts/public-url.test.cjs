const test = require("node:test");
const assert = require("node:assert/strict");
const { publicWebUrl } = require("../electron/public-url.cjs");
test("news source links open only public HTTP(S) destinations", () => {
  assert.equal(publicWebUrl("https://www.bilibili.com/video/BV1abc?p=2"), "https://www.bilibili.com/video/BV1abc?p=2");
  for (const url of ["file:///C:/private.txt", "javascript:alert(1)", "https://user:pass@example.com", "https://127.0.0.1", "http://0x7f000001", "http://2130706433", "http://[::1]", "https://host.local/path", "https://localhost.", "https://example.com:8443", "https://example.com\\@localhost", "https://example.com\n/"]) assert.equal(publicWebUrl(url), null, url);
});
