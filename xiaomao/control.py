"""带私有令牌的回环控制端口，不接受浏览器跨站调用。"""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from time import monotonic
from .privacy import private_dir, make_private


class ControlServer:
    def __init__(self, root: Path, engine):
        self.root, self.engine = Path(root), engine
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass  # 不记录请求头、正文或控制令牌。

            def answer(self, status, result):
                data = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.close_connection = True
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except ConnectionError:
                    pass

            def discard_rejected_body(self,length):
                # Closing a Windows socket with unread POST data can reset it
                # before the client receives 413. Drain only small authenticated
                # bodies, under a hard time/size cap; never parse or execute them.
                if not 0<length<=128*1024:return
                previous=self.connection.gettimeout();deadline=monotonic()+.2
                try:
                    while length:
                        remaining=deadline-monotonic()
                        if remaining<=0:break
                        self.connection.settimeout(remaining)
                        chunk=self.rfile.read1(min(length,16384))
                        if not chunk:break
                        length-=len(chunk)
                except OSError:
                    pass
                finally:
                    self.connection.settimeout(previous)

            def route(self, method):
                if self.headers.get("Origin") is not None or self.headers.get("Sec-Fetch-Site") is not None:
                    return self.answer(403, {"error": "不接受浏览器跨站控制"})
                if self.headers.get("Host") != f"127.0.0.1:{owner.server.server_port}":
                    return self.answer(403, {"error": "Host 无效"})
                received = self.headers.get("Authorization", "").encode("utf-8")
                expected = ("Bearer " + owner.token).encode("ascii")
                if not hmac.compare_digest(received, expected):
                    return self.answer(401, {"error": "控制凭据无效"})
                routes = {"GET": {"/status": engine.status, "/pending": lambda: {"tasks": engine.pending()}},
                          "POST": {"/observe": engine.observe, "/enable": engine.enable, "/pause": engine.pause, "/stop": engine.stop,
                                   "/reply_latest":lambda:engine.reply_latest(**payload),
                                   "/stop_if_idle":lambda:engine.stop_if_idle(),
                                   "/reload_windows":lambda:engine.reload_windows(),
                                   "/queue_text":lambda:engine.queue_text(**payload),
                                   "/queue_outbound":lambda:engine.queue_outbound(**payload),
                                   "/configure_reply_rules":lambda:engine.configure_reply_rules(**payload),
                                   "/configure_reply_defaults":lambda:engine.configure_reply_defaults(**payload),
                                   "/enable_selected":lambda:engine.enable_selected(**payload),
                                   "/pause_selected":lambda:engine.pause_selected(**payload),
                                   "/companion_configure":lambda:engine.companion_configure(**payload),
                                   "/companion_preview":lambda:engine.companion_preview(**payload),
                                   "/companion_once":lambda:engine.companion_once(**payload),
                                   "/news_command":lambda:engine.news_command(**payload),
                                   "/news_invalidate":lambda:engine.news_invalidate(**payload)}}
                action = routes[method].get(self.path)
                if action is None:
                    return self.answer(404, {"error": "没有这个控制操作"})
                if self.headers.get("Transfer-Encoding"):
                    return self.answer(400, {"error": "不接受分块请求"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.answer(400, {"error": "请求长度无效"})
                limit=32768 if self.path=='/companion_configure' else 4096
                if length > limit:
                    self.discard_rejected_body(length)
                    return self.answer(413, {"error": "请求过大"})
                if length < 0:
                    return self.answer(400, {"error": "请求长度无效"})
                if method == "POST":
                    if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                        return self.answer(400, {"error": "需要 JSON 请求"})
                    try:
                        payload=json.loads(self.rfile.read(length) or b"{}")
                        if self.path=='/queue_text':
                            if not isinstance(payload,dict) or set(payload)!={'group','text','request_id'} or not all(isinstance(v,str) for v in payload.values()):
                                return self.answer(400, {"error": "需要指定会话、文字和请求编号"})
                        elif self.path=='/queue_outbound':
                            if (not isinstance(payload,dict) or set(payload)!={'group','scope','text','sticker_ids','request_id'}
                                    or not all(isinstance(payload[k],str) for k in ('group','scope','text','request_id'))
                                    or not payload['group'] or not payload['scope']
                                    or not isinstance(payload['sticker_ids'],list)
                                    or not all(isinstance(s,str) for s in payload['sticker_ids'])):
                                return self.answer(400,{'error':'需要指定会话身份、文字、素材编号和请求编号'})
                        elif self.path=='/news_command':
                            if (not isinstance(payload,dict) or set(payload)!={'subscription','action','edition'}
                                    or not isinstance(payload['subscription'],str) or not 1<=len(payload['subscription'])<=64
                                    or payload['action'] not in ('preview','push','cancel')
                                    or payload['edition'] is not None and (not isinstance(payload['edition'],str) or len(payload['edition'])>64)):
                                return self.answer(400, {'error':'资讯推送参数无效'})
                        elif self.path=='/news_invalidate':
                            if (not isinstance(payload,dict) or set(payload)!={'account','subscription'}
                                    or not all(isinstance(v,str) and 1<=len(v)<=128 for v in payload.values())):
                                return self.answer(400, {'error':'资讯订阅身份无效'})
                        elif self.path=='/configure_reply_defaults':
                            if not isinstance(payload,dict) or set(payload)!={'preferences'}:
                                return self.answer(400, {"error": "需要回复偏好设置"})
                        elif (self.path in ('/enable_selected','/pause_selected','/companion_configure','/configure_reply_rules') or
                                self.path in ('/reply_latest','/companion_preview','/companion_once') and payload!={}):
                            keys={'group','scope','preferences'} if self.path in ('/companion_configure','/configure_reply_rules') else {'group','scope'}
                            if self.path=='/configure_reply_rules' and isinstance(payload,dict) and 'expected' in payload:
                                keys.add('expected')
                                if not isinstance(payload['expected'],dict):
                                    return self.answer(400, {"error": "回复规则的原始值无效"})
                            if not isinstance(payload,dict) or set(payload)!=keys or not all(isinstance(payload[k],str) and payload[k] for k in ('group','scope')):
                                return self.answer(400, {"error": "请指定监听会话及身份"})
                        elif payload != {}:
                            return self.answer(400, {"error": "操作不接受附加参数"})
                    except (ValueError, UnicodeError):
                        return self.answer(400, {"error": "JSON 无效"})
                try:
                    return self.answer(200, action())
                except ValueError as error:
                    return self.answer(409, {"error": str(error)})
                except Exception:
                    return self.answer(500, {"error": "控制请求未能完成"})

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = None

    def __enter__(self):
        directory = private_dir(self.root / "runtime")
        temporary = directory / "control.json.tmp"
        temporary.write_text(json.dumps({"host": "127.0.0.1", "port": self.server.server_port,
                                         "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        make_private(temporary)
        os.replace(temporary, directory / "control.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="xiaomao-control")
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(2)
        endpoint = self.root / "runtime" / "control.json"
        try:
            if json.loads(endpoint.read_text(encoding="utf-8")).get("token") == self.token:
                endpoint.unlink()
        except (FileNotFoundError, ValueError):
            pass
