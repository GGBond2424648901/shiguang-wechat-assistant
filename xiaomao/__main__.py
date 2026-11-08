"""命令入口；后台启动遵循本机的自动回复偏好。"""
import argparse
import json
import os
from pathlib import Path
import sys
from .paths import default_root


def report_error(command, message):
    if command == 'gui' and sys.stderr is None and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, '拾光微信聊天助手 · 启动未完成', 0x10)
    else:
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr)


def apply_initial_mode(engine,store,initial_mode):
    """Apply before the worker thread and control endpoint become observable."""
    if initial_mode in ('paused','observe'):
        engine.pause() if initial_mode=='paused' else engine.observe()
    elif initial_mode=='auto' and not store.counts().get('unknown'):
        engine.restore_auto() if hasattr(engine,'restore_auto') else engine.enable()


def daemon(root: Path,initial_mode=None):
    import threading
    if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
        from .privacy import private_dir
        output = (private_dir(root / "runtime") / "worker.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = output
    # Native-library failures otherwise leave only a stale control file.
    # Tracebacks contain code locations, not chat contents or model credentials.
    import faulthandler
    faulthandler.enable(file=sys.stderr,all_threads=True)
    from .config import Settings
    from .control import ControlServer
    from .resident import Resident
    from .model import ChatModel
    from .store import StateStore
    from .vision import Vision
    from .windows import DesktopUnavailable, SingleInstance, hotkey_loop
    try:
        mutex = SingleInstance(root)
    except DesktopUnavailable as error:
        raise ValueError(str(error)) from None
    store = None
    tray = None
    try:
        settings = Settings.load(root)
        store = StateStore(settings.runtime)
        engine = Resident(settings,store)
        apply_initial_mode(engine,store,initial_mode)
        worker = threading.Thread(target=engine.run, daemon=True, name="xiaomao-observer")
        hotkey = threading.Thread(target=hotkey_loop, args=(engine.stop_event, engine.pause), daemon=True, name="xiaomao-hotkey")
        with ControlServer(root, engine):
            from .panel_entry import resident_tray
            tray=engine.tray=resident_tray(root,engine)
            worker.start()
            hotkey.start()
            try:
                engine.stop_event.wait()
            except KeyboardInterrupt:
                engine.stop()
            finally:
                engine.stop()
                worker.join(40)
                hotkey.join(2)
    finally:
        if tray:tray.stop()
        if store:
            store.close()
        mutex.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    from .brand import APP_NAME
    parser = argparse.ArgumentParser(description=APP_NAME+'，启动遵循本机运行偏好')
    parser.add_argument("--root", type=Path)
    parser.add_argument("command", nargs="?", default="gui", choices=["gui","daemon","mcp","start","observe","enable","status","pause","stop","doctor","key","test-model","desktop-bridge"])
    parser.add_argument("--stdin", action="store_true", help="key 命令从标准输入读取；一行更新主密钥，多行依次替换主密钥及备用密钥")
    parser.add_argument("--ocr", action="store_true", help="doctor 使用内置测试文字核对 OCR，可离线运行")
    parser.add_argument('--initial-mode',choices=['auto','observe','paused'],help=argparse.SUPPRESS)
    parser.add_argument('--legacy-ui', action='store_true', help='使用旧版桌面面板')
    parser.add_argument('--managed', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--read-only', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--remember-profile', action='store_true', help='让安装版入口沿用指定的现有数据目录')
    args = parser.parse_args()
    from .client import ControlClient, ControlUnavailable
    from .config import Settings
    from .credentials import CredentialStore, CredentialError
    from .model import ModelError
    try:
        root = (args.root or default_root()).resolve()
        if args.command == 'desktop-bridge':
            from .desktop_bridge import run_bridge
            run_bridge(root, read_only=args.read_only, managed=args.managed)
            return
        client = ControlClient(root)
        if args.command == "daemon":
            daemon(root,args.initial_mode)
            return
        if args.command == "gui":
            if args.remember_profile:
                if args.root is None:
                    raise ValueError('请明确指定要沿用的数据目录')
                from .paths import remember_profile
                remember_profile(root)
            from .desktop_launch import launch_desktop
            if not launch_desktop(root, force_legacy=args.legacy_ui):
                from .gui import launch
                launch(root)
            return
        if args.command == "mcp":
            from .mcp_server import build_server
            build_server(root).run(transport="stdio")
            return
        if args.command == "key":
            import getpass
            value = sys.stdin.read() if args.stdin else getpass.getpass("DeepSeek API key（输入隐藏）: ")
            store = CredentialStore(root / "runtime")
            values = [line for line in value.splitlines() if line.strip()]
            if len(values) == 1:
                store.set_key(values[0])
            else:
                store.set_keys(values)
            result = {"credential_encrypted": True, "credential_count": store.count}
        elif args.command == "doctor":
            settings = Settings.load(root)
            result = {"windows": os.name == "nt", "python": sys.version.split()[0],
                      "credential_encrypted": CredentialStore(settings.runtime).available,
                      "model_configured": settings.model_ready, "model": settings.model_name,
                      "calibrated": settings.binding is not None, "at_enabled": bool(settings.at_display_name)}
            if args.ocr:
                from PIL import Image, ImageDraw, ImageFont
                from .vision import LocalOCR
                sample = Image.new("RGB", (420, 100), "white")
                ImageDraw.Draw(sample).text((18, 22), "LOCAL TEST 123", font=ImageFont.load_default(size=38), fill="black")
                lines = LocalOCR()(sample)
                result["ocr_verified"] = "123" in "".join(line.text for line in lines)
                if not result["ocr_verified"]:
                    raise ValueError("离线 OCR 未能识别测试数字")
        elif args.command == "test-model":
            from .model import ChatModel
            from .domain import Message
            model = ChatModel(Settings.load(root))
            models = model.models()
            reply = model.reply([Message("小猫，只回复连接正常", "本机连接测试")])
            result = {"connected": True, "model": model.settings.model_name, "available_models": models, "received_characters": len(reply)}
        else:
            result = getattr(client, args.command)()
            if args.command == "stop":
                client.wait_stopped()
        print(json.dumps(result, ensure_ascii=False))
    except (ControlUnavailable, CredentialError, ModelError, ValueError) as error:
        report_error(args.command, str(error))
        raise SystemExit(1)
    except Exception:
        # 不把可能携带请求信息的外部异常写入日志。
        report_error(args.command, '操作未能完成，请检查本机设置和运行环境')
        raise SystemExit(1)


if __name__ == "__main__":
    main()
