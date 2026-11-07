"""Private UTF-8 JSON-lines bridge between Electron and local Python services."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import BoundedSemaphore, Event, Lock, Thread

from .client import ControlUnavailable
from .desktop_service import DesktopService, clean_text

MAX_LINE = 2 * 1024 * 1024


def serve(service, source, sink, lease=None):
    output = Lock()
    commands = BoundedSemaphore(12)
    previews = BoundedSemaphore(40)

    def write(message):
        line = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        with output:
            sink.write(line + b'\n')
            sink.flush()

    def publish(topic, payload):
        try:
            write({'event': topic, 'payload': payload})
        except (BrokenPipeError, OSError):
            pass

    service.events = publish
    done = Event()

    def watch_panel():
        while not done.wait(.12):
            if lease.consume_exit_signal():
                publish('panel', 'exit')
            elif lease.consume_signal():
                publish('panel', 'show')

    watcher = Thread(target=watch_panel, daemon=True) if lease else None
    if watcher:
        publish('panel', 'ready')
        watcher.start()

    def run(request, slots):
        try:
            reply = {'id': request['id']}
            try:
                if request['method'] == 'panel.exitAck':
                    accepted = request['params'].get('accepted')
                    if not lease or type(accepted) is not bool:
                        raise ValueError('面板退出确认无效')
                    lease.acknowledge_exit(accepted)
                    reply['result'] = {'acknowledged': True}
                else:
                    reply['result'] = service.call(request['method'], request['params'])
            except (ValueError, ControlUnavailable) as error:
                reply['error'] = clean_text(str(error), 500)
            except TypeError:
                reply['error'] = '界面请求参数无效'
            except Exception:
                reply['error'] = '本机操作未完成，请重试'
            write(reply)
        finally:
            slots.release()

    with ThreadPoolExecutor(max_workers=4) as command_pool, ThreadPoolExecutor(max_workers=4) as preview_pool:
        while line := source.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE or not line.endswith(b'\n'):
                break
            try:
                request = json.loads(line)
                if (not isinstance(request, dict) or set(request) != {'id', 'method', 'params'}
                        or type(request['id']) is not int or not 0 < request['id'] < 2**53
                        or not isinstance(request['method'], str) or not isinstance(request['params'], dict)):
                    raise ValueError('invalid frame')
            except (ValueError, UnicodeError):
                write({'id': None, 'error': '无效的本机界面请求'})
                continue
            media = request['method'] in ('stickers.image', 'imports.preview')
            pool, slots = (preview_pool, previews) if media else (command_pool, commands)
            if slots.acquire(blocking=False):
                pool.submit(run, request, slots)
            else:
                write({'id': request['id'], 'error': '本机服务忙，请稍后重试'})
    done.set()
    if watcher:
        watcher.join(.3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--managed', action='store_true')
    args = parser.parse_args()
    run_bridge(args.root, read_only=args.read_only, managed=args.managed)


def run_bridge(root, *, read_only=False, managed=False):
    from .panel_entry import PanelLease, signal_panel
    lease = PanelLease(root) if managed and not read_only else None
    try:
        if lease:
            if not lease.owner:
                result = 'delegated' if signal_panel(root) else 'occupied'
                sys.stdout.buffer.write(json.dumps({'event': 'panel', 'payload': result}).encode() + b'\n')
                sys.stdout.buffer.flush()
                return
            from .desktop_launch import snapshot_upgrade_configuration
            snapshot_upgrade_configuration(root)
            lease.open_signal()
        service = DesktopService(root, read_only=read_only)
        try:
            serve(service, sys.stdin.buffer, sys.stdout.buffer, lease)
        finally:
            service.calibration.cancel()
    finally:
        if lease:
            lease.close()


if __name__ == '__main__':
    main()
