const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");

class Broker {
  constructor(executable, args, cwd) {
    this.requests = new Map();
    this.sequence = 0;
    this.closed = false;
    this.child = spawn(executable, args, {
      cwd, windowsHide: true, stdio: ["pipe", "pipe", "ignore"],
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    });
    this.exited = new Promise((resolve) => {
      this.child.once("close", () => {
        this.closed = true;
        this.fail(new Error("本机界面服务已断开"));
        resolve();
      });
    });
    this.child.on("error", () => this.fail(new Error("本机界面服务启动失败")));
    this.child.stdin.on("error", () => this.fail(new Error("本机界面服务未连接")));
    createInterface({ input: this.child.stdout }).on("line", (line) => {
      try {
        if (line.length > 13_000_000) throw new Error("reply size");
        const response = JSON.parse(line);
        if (typeof response.event === "string") {
          this.onEvent?.(response.event, response.payload);
          return;
        }
        const task = this.requests.get(response.id);
        if (!task) return;
        this.requests.delete(response.id);
        clearTimeout(task.timer);
        if (response.error) task.reject(new Error(response.error));
        else task.resolve(response.result);
      } catch {
        this.fail(new Error("本机界面服务返回了无效响应"));
      }
    });
  }

  fail(error) {
    for (const task of this.requests.values()) {
      clearTimeout(task.timer);
      task.reject(error);
    }
    this.requests.clear();
  }

  call(method, params = {}, timeout = 15_000) {
    if (this.closed || this.child.stdin.destroyed) return Promise.reject(new Error("本机界面服务已断开"));
    if (this.requests.size >= 54) return Promise.reject(new Error("本机界面服务忙"));
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.requests.delete(id);
        reject(new Error("本机请求响应超时"));
      }, timeout);
      this.requests.set(id, { timer, resolve, reject });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + "\n", (error) => {
        if (!error || !this.requests.delete(id)) return;
        clearTimeout(timer);
        reject(new Error("本机界面服务未连接"));
      });
    });
  }

  async close() {
    if (!this.closed && !this.child.stdin.destroyed) this.child.stdin.end();
    await this.exited;
  }
}

module.exports = { Broker };
