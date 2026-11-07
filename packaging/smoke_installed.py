"""Verify the installed CLI, OCR and GUI against synthetic data only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def launch_panel(app_dir, data, env, entry):
    executable = app_dir / 'Xiaomao.exe'
    arguments = ['--root', str(data), 'gui']
    if entry == 'vbs':
        # Stage the same payload under a disposable LOCALAPPDATA on the same drive.
        def link_or_copy(source, destination):
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
            return destination
        staged = Path(env['LOCALAPPDATA']) / 'Programs' / 'XiaomaoAssistant'
        shutil.copytree(app_dir, staged, copy_function=link_or_copy)
        script = data / '启动拾光微信聊天助手.vbs'
        shutil.copyfile(Path(__file__).resolve().parents[1] / script.name, script)
        return subprocess.Popen(['wscript.exe', '//B', str(script)], env=env), staged
    if entry == 'shortcut':
        import win32com.client
        shell = win32com.client.Dispatch('WScript.Shell')
        link = data / '拾光微信聊天助手.lnk'
        shortcut = shell.CreateShortcut(str(link))
        shortcut.TargetPath = str(executable)
        shortcut.Arguments = subprocess.list2cmdline(arguments)
        shortcut.WorkingDirectory = str(app_dir)
        shortcut.Save()
        os.startfile(str(link))
        return None, app_dir
    return subprocess.Popen([str(executable), *arguments], env=env), app_dir


def smoke(app_dir, entry='exe'):
    with tempfile.TemporaryDirectory(prefix="xiaomao-release-test-", dir=app_dir.parent) as temporary:
        data = Path(temporary) / "profile"
        runtime = data / "runtime"
        runtime.mkdir(parents=True)
        from xiaomao.config import Settings
        Settings(data, group='示例会话', auto_on_launch=False).save()
        config = (runtime / 'config.json').read_bytes()
        token = secrets.token_hex(32)
        calls = []

        class Status(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + token or self.path != "/status":
                    self.send_error(403)
                    return
                calls.append(self.path)
                payload = json.dumps({"mode":"observe", "reason":"离线安装检查", "group":"示例会话",
                                      "counts":{}, "pending_count":0}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + token or self.path != "/stop":
                    self.send_error(403)
                    return
                calls.append(self.path)
                (runtime / "control.json").unlink(missing_ok=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"stopped":true}')

            def log_message(self, *args):
                pass

        # Force this process tree away from the real installed user profile.
        env = {**os.environ, "LOCALAPPDATA":str(data.parent / "localappdata")}
        cli = [str(app_dir / "XiaomaoCLI.exe"), "--root", str(data)]
        result = subprocess.run([*cli, "doctor", "--ocr"], env=env, capture_output=True,
                                encoding="utf-8", timeout=90, check=True)
        doctor = json.loads(result.stdout.splitlines()[-1])
        assert doctor["ocr_verified"] and not doctor["calibrated"] and not doctor["credential_encrypted"], doctor
        server = ThreadingHTTPServer(("127.0.0.1", 0), Status)
        (runtime / "control.json").write_text(json.dumps({"host":"127.0.0.1", "port":server.server_port,
                                                        "token":token}), "utf-8")
        threading.Thread(target=server.serve_forever,daemon=True).start()
        proc, launcher_dir = launch_panel(app_dir, data, env, entry)
        try:
            from xiaomao.panel_entry import request_stop_and_exit, signal_panel, _identity, _panel_present
            panel_name = 'Local\\Shiguang.Panel.' + _identity(data)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and (not calls or not signal_panel(data)):
                time.sleep(.2)
            assert calls and _panel_present(panel_name), f"GUI never connected (launcher exit={proc.poll() if proc else entry})"
            if (app_dir / 'desktop/ShiguangDesktop.exe').is_file():
                assert (runtime / 'upgrade-v030/config.json').read_bytes() == config
            assert (runtime / 'config.json').read_bytes() == config
            # The tray command follows the same panel-owned exit channel.
            errors=[]
            def request_exit():
                try:
                    request_stop_and_exit(data,SimpleNamespace(stop=lambda:errors.append('GUI exit channel missing')))
                except Exception as error:errors.append(type(error).__name__)
            shutdown=threading.Thread(target=request_exit,daemon=True)
            shutdown.start();shutdown.join(20)
            assert not shutdown.is_alive() and not errors, errors
            if proc and proc.wait(timeout=20) != 0:
                raise RuntimeError("Installed GUI exited with an error")
            deadline = time.monotonic() + 15
            while _panel_present(panel_name) and time.monotonic() < deadline:
                time.sleep(.1)
            assert not _panel_present(panel_name), "GUI did not release its profile lock"
            assert "/stop" in calls and not (runtime / "control.json").exists()
            print(json.dumps({"doctor":doctor, "entry":entry, "synthetic_status_requests":calls.count('/status'),
                              "full_exit_requested":True,"closed":True},ensure_ascii=False))
        finally:
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            import psutil
            for child in psutil.process_iter(['exe', 'cmdline']):
                args = child.info['cmdline'] or ()
                if (child.info['exe'] and Path(child.info['exe']).parent == launcher_dir/'desktop'
                        and '--profile='+str(data) in args):
                    try:
                        child.terminate()
                        child.wait(10)
                    except psutil.NoSuchProcess:
                        pass
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installation",type=Path)
    parser.add_argument("--entry", choices=["exe", "vbs", "shortcut"], default="exe")
    args = parser.parse_args()
    smoke(args.installation.resolve(), args.entry)
