"""Measure the legacy panel with disposable local data and a fake resident."""

import cProfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import os
import pstats
import secrets
import sys
from threading import Thread
import time
import tkinter as tk

import psutil
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xiaomao.config import Settings
from xiaomao.persona_modules import PersonaModule, load_modules, save_modules

GROUPS = ('晨光摄影群', '圆桌讨论组', '小禾')


def prepare_profile(root, count=5000):
    """Reused by both desktop benchmarks. It never reads WeChat data."""
    root.mkdir(parents=True, exist_ok=True)
    cfg = Settings(
        root, group=GROUPS[0], group_names=GROUPS,
        conversation_targets={
            name: {'id': f'example_{index}' + ('@chatroom' if index < 2 else ''),
                   'account': 'ui-qa', 'kind': 'group' if index < 2 else 'private'}
            for index, name in enumerate(GROUPS)
        },
        auto_on_launch=False,
    )
    cfg.save()
    if not (root / 'config/persona_modules.json').exists():
        source = Path(__file__).resolve().parents[1] / 'xiaomao/default_persona.md'
        save_modules(root, (
            PersonaModule('custom_intro', '身份与语气', source.read_text(encoding='utf-8')),
            PersonaModule('custom_examples', '聊天示例', '说话自然，结合上下文接话。\n' * 1000),
        ), title='验收人格', expected_revision=None)
    folder = root / 'assets/sticker_library'
    folder.mkdir(parents=True, exist_ok=True)
    catalog = folder / 'catalog.json'
    if catalog.exists() and len(json.loads(catalog.read_text(encoding='utf-8'))['items']) == count:
        return
    rows = []
    for i in range(count):
        img = Image.new('RGB', (260, 220), (38 + i % 53, 45 + i % 65, 76 + i % 89))
        pen = ImageDraw.Draw(img)
        pen.rounded_rectangle((35, 22, 225, 194), 34, fill=(246, 209 - i % 48, 161 + i % 64))
        pen.ellipse((81, 79, 99, 99), fill='#393244')
        pen.ellipse((161, 79, 179, 99), fill='#393244')
        pen.arc((106, 91, 154, 127), 18, 162, fill='#393244', width=4)
        pen.text((117, 161), str(i + 1), fill='#393244')
        animated = i < 3
        target = folder / f'fixture-{i:04d}.{("gif" if animated else "png")}'
        if animated:
            frame = img.copy()
            ImageDraw.Draw(frame).ellipse((112, 93, 148, 121), fill='#583942')
            img.save(target, save_all=True, append_images=[frame], duration=240, loop=0)
        else:
            img.save(target, compress_level=1)
        rows.append({
            'id': f'fixture_{i:04d}', 'auto_select': True, 'animated': animated,
            'path': target.relative_to(root).as_posix(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'character': ('菲比', '呆猫八条', '鲸鱼娘')[i % 3],
            'meaning': f'{("开心", "点头", "晚安", "吃瓜", "惊讶", "抱抱")[i % 6]} {i + 1:04d}',
            'tags': ['验收素材'], 'source_page': '本机生成的验收图片',
        })
    catalog.write_text(json.dumps({'items': rows}, ensure_ascii=False), encoding='utf-8')


def prepare_visual_profile(root):
    """Populate the design states with fictional messages and account data."""
    from dataclasses import asdict, replace
    from xiaomao.companion import CompanionPreferences
    from xiaomao.companion_store import CompanionStore
    from xiaomao.config import Calibration
    from xiaomao.credentials import CredentialStore
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory
    from xiaomao.routines import routine_preset

    cfg = Settings.load(root)
    extra = ('周末约拍群', '文件传输助手')
    targets = {**cfg.conversation_targets,
               extra[0]: {'id': 'example_3@chatroom', 'account': 'ui-qa', 'kind': 'group'},
               extra[1]: {'id': 'example_4', 'account': 'ui-qa', 'kind': 'private'}}
    cfg = replace(cfg, group_names=(*GROUPS, *extra), conversation_targets=targets,
                  reply_mode='natural', paused_conversations=(), account_display_name='示例微信',
                  min_interval=4, hourly_limit=180, minute_limit=10, pacing_jitter=4,
                  self_display_name='小玲', receive_mode='database')
    regions = {'header': [.26, .05, .96, .11], 'messages': [.26, .13, .96, .74],
               'input': [.27, .78, .96, .85], 'send': [.82, .87, .95, .94]}
    cfg = cfg.with_window_binding(GROUPS[0], Calibration(
        221, 444, 920, 630, GROUPS[0], regions, True, GROUPS[0], window_kind='detached'))
    cfg.save()
    CredentialStore(cfg.runtime).set_keys(['sk-fixture-invalid-8119', 'sk-fixture-invalid-4268'])
    current = load_modules(root)
    names = ('身份与语气', '情绪陪伴', '主动接话', '长期记忆', '聊天示例', '称呼与边界',
             '共同话题', '闲时陪伴', '输出结构', '微信格式', '收到媒体', '语言偏好', '记忆写入', '暂存规则')
    source = current.modules[0].text
    modules = tuple(PersonaModule('demo_' + str(i), name, source if i == 0 else
                    f'## {name}\n这份示例仅供界面验收，聊天时遵循当前会话状态。\n' * 12,
                    enabled=i < len(names)-1, origin=('基础人设、称呼、说话风格' if i == 0 else '合并模块'))
                    for i, name in enumerate(names))
    save_modules(root, modules, title='验收人格', expected_revision=current.revision)
    texts = ('赞同这个思路，我们可以再补充一个对比案例。', '好的，我这边已经整理完了，稍后发你。')
    for i, name in enumerate(GROUPS[1:], 1):
        peer = cfg.conversation_targets[name]['id']
        memory = ConversationMemory(cfg.runtime, cfg.for_group(name).memory_scope)
        memory.remember(Message(texts[i-1], direction='out', speaker_id='self', message_id=peer+'/s/3',
                                created_at=1794124680-i*1320, reply_to_id=peer+'/s/2',
                                reply_to_speaker='p_1a2b3c4d5e6f7777'))
    first = cfg.for_group(GROUPS[0])
    memory = ConversationMemory(cfg.runtime, first.memory_scope)
    speaker = 'p_2a3b4c5d6e7f8888'
    peer = cfg.conversation_targets[first.group]['id']
    memory.remember(Message('@拾光 发个摸鱼表情包吧', direction='in', speaker_id=speaker,
                            message_id=peer+'/s/21', created_at=1794124600))
    memory.remember(Message('鲸鱼娘 · 蓝色大肥鱼躺在米饭锅里', direction='out', kind='emoji', speaker_id='self',
                            message_id=peer+'/s/22', reply_to_id=peer+'/s/21',
                            reply_to_speaker=speaker, created_at=1794124680))
    friend = ConversationMemory(cfg.runtime, cfg.for_group(extra[1]).memory_scope)
    friend.remember(Message('测试消息已发送。', direction='out', speaker_id='self',
                            message_id='example_4/s/3', reply_to_id='example_4/s/2',
                            reply_to_speaker=speaker, created_at=1794120900))
    phrases = ('周末想去拍日落，看看云会不会散', '如果晴天，想试试从桥下拍过去', '下次给大家看照片')
    for index, phrase in enumerate(phrases):
        memory.remember(Message(phrase, '小岚', speaker_id=speaker, message_id=f'demo_in_{index}',
                                created_at=1794124680 + index * 14))
    if memory.summary != '摄影群在约周末日落。天气好就去桥下找机位，拍完分享照片。':
        memory._save(memory.messages, '摄影群在约周末日落。天气好就去桥下找机位，拍完分享照片。', memory._seen)
    notes = CompanionStore(cfg.runtime, first.memory_scope)
    notes.data['preferences'] = asdict(CompanionPreferences(
        enabled=True, daily_max=8, idle_min_minutes=15, idle_max_minutes=45,
        quiet_start=1410, quiet_end=450, routines=[
            {**routine_preset('morning'), 'id': 'sample-morning', 'time': '07:30', 'enabled': True},
            {**routine_preset('evening'), 'id': 'sample-evening', 'time': '23:00', 'enabled': True},
        ]))
    notes.data['facts'] = [{'source': 'demo_in_0', 'speaker': speaker, 'quote': phrases[0],
                            'at': 1794124680, 'kind': 'topic'}]
    notes.data['diary'] = [{'day': '2026-11-06', 'text': '聊了周末的取景地点', 'at': 1794124680, 'sources': ['demo_in_0']}]
    notes.save()


def prepare_receipt_pages(root):
    from xiaomao.domain import Message
    from xiaomao.memory import ConversationMemory

    cfg = Settings.load(root)
    peer = cfg.conversation_targets[cfg.group]['id']
    memory = ConversationMemory(cfg.runtime, cfg.memory_scope)
    for index in range(1, 7):
        memory.remember(Message(f'历史回执 {index}', direction='out', speaker_id='self',
            message_id=f'{peer}/s/{index}', reply_to_id=f'{peer}/s/200',
            reply_to_speaker='p_2a3b4c5d6e7f8888', created_at=1794124680 + index * 60))


def start_fixture(root, *, visual=False):
    token = secrets.token_hex(32)
    runtime = root / 'runtime'
    groups = Settings.load(root).allowed_groups
    runtime.mkdir(exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer ' + token:
                self.send_error(403)
                return
            if self.path == '/status':
                companion = {
                    'next_candidate_at': 1794129780, 'next_stage': '闲时话题',
                    'preview': '周末要是没下雨，我投桥下那个机位一票，落日照在水面上挺好看的',
                    'routines': [{'routine_id': 'sample-morning', 'next_due_at': 1794199800}],
                    'routine_history': [
                        {'routine_id': 'sample-morning', 'day': '2026-11-06', 'status': 'verified'},
                        {'routine_id': 'sample-evening', 'day': '2026-11-06', 'status': 'missed'},
                    ],
                }
                data = {
                    'running': True, 'mode': 'auto' if visual else 'observe', 'reason': '离线界面验收',
                    'group': GROUPS[0], 'group_names': list(groups),
                    'counts': {'verified': 24 if visual else 2}, 'pending_count': 1,
                    'groups': [{'name': name, 'mode': ('auto' if i in (0, 2) else 'paused') if visual else 'observe',
                                'reply_mode': 'mentions' if visual and i == 0 else 'natural',
                                'pending_count': int(i == 0), 'reason': '离线验收', 'calibrated': visual and i == 0,
                                'companion': companion if visual and i == 0 else {}}
                               for i, name in enumerate(groups)],
                    'tray': {'running': True},
                }
            elif self.path == '/pending':
                data = {'tasks': [{'group': GROUPS[0], 'text': '这周有约拍吗？',
                                   'state': 'generated' if visual else 'ready', 'reply': '周六有空，可以约一场'}]}
            else:
                self.send_error(404)
                return
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    (runtime / 'control.json').write_text(json.dumps(
        {'host': '127.0.0.1', 'port': server.server_port, 'token': token}), encoding='utf-8')
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def legacy_benchmark(profile, output):
    from xiaomao.feedback_ui import CloseChoice
    from xiaomao.gui import Panel
    from xiaomao.theme import PAGES
    process = psutil.Process(os.getpid())
    start = time.perf_counter()
    root = tk.Tk()
    panel = Panel(root, profile)
    root.title('拾光 · 旧版性能验收')
    root.update()
    cold = time.perf_counter() - start
    timings = {}
    callbacks = []
    previous = time.perf_counter()

    def beat():
        nonlocal previous
        now = time.perf_counter()
        callbacks.append((now - previous) * 1000)
        previous = now
        root.after(16, beat)

    def pump(until=None, seconds=.15):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            root.update()
            if until is not None and until():
                return
            time.sleep(.002)
        if until is not None:
            raise RuntimeError('Legacy fixture did not settle')

    def measure(name, action):
        begin = time.perf_counter()
        action()
        root.update_idletasks()
        timings.setdefault(name, []).append(round((time.perf_counter() - begin) * 1000, 2))
        root.update()

    def show(page):
        panel.view.select(page)

    root.after(16, beat)
    pump(lambda: panel.view.status.get('mode') == 'observe', 5)
    for key in PAGES:
        measure('first_' + key, lambda key=key: show(key))
    measure('first_sticker_open', lambda: show('stickers'))
    gallery = panel.view.pages['stickers']
    pump(lambda: gallery.lib is not None and bool(gallery.photos), 8)
    profiler = cProfile.Profile()
    for _ in range(12):
        for key in PAGES:
            measure('warm_navigation', lambda key=key: show(key))
    show('stickers')
    for _ in range(25):
        measure('gallery_cold_page', lambda: gallery._turn(1))
    for i in range(10):
        def search(i=i):
            gallery.query.set(f'{i + 1:04d}')
            gallery._search()
        measure('gallery_search_dispatch', search)
        pump(seconds=.2)
    gallery.query.set('')
    gallery._search()
    pump(seconds=.22)
    for width in range(940, 1231, 10):
        measure('resize_dispatch', lambda width=width: root.geometry(f'{width}x690'))
        pump(seconds=.016)
    pump(seconds=.2)
    show('assistant')
    for name in GROUPS * 3:
        def refresh(name=name):
            panel.view.queue_group = name
            panel.view.refresh_pending()
        measure('queue_scope_dispatch', refresh)
        pump(seconds=.12)
    show('persona')
    editor = panel.view.pages['persona']
    for i in range(8):
        measure('persona_module', lambda i=i: editor.select_module(
            ('custom_intro', 'custom_examples')[i % 2]))
    start = time.perf_counter()
    editor.save()
    pump(lambda: not editor.saving, 8)
    timings['persona_save'] = [round((time.perf_counter() - start) * 1000, 2)]
    show('assistant')
    for _ in range(12):
        def close_open():
            dialog = CloseChoice(root)
            root.update_idletasks()
            dialog.choose(None)
        measure('open_cancel_popup', close_open)
    before = process.memory_info().rss
    cpu = process.cpu_times()
    start = time.perf_counter()
    pump(seconds=2)
    idle_cpu = process.cpu_times()
    elapsed = time.perf_counter() - start
    show('stickers')
    profiler.enable()
    for _ in range(25):
        gallery._turn(1)
        root.update()
    profiler.disable()
    show('assistant')
    start = time.perf_counter()
    panel.hide_panel()
    timings['hide_to_tray'] = [round((time.perf_counter() - start) * 1000, 2)]
    start = time.perf_counter()
    reopened = tk.Tk()
    reopened_panel = Panel(reopened, profile)
    reopened.update_idletasks()
    timings['panel_reopen'] = [round((time.perf_counter() - start) * 1000, 2)]
    cprofile = output.with_suffix('.profile.txt')
    with cprofile.open('w', encoding='utf-8') as stream:
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(28)
    output.write_text(json.dumps({
        'catalogSize': len(gallery.lib.items), 'panelConstructMs': round(cold * 1000, 2),
        'operationMs': timings, 'heartbeatGapMs': callbacks, 'workingSetBytes': before,
        'idleCpuPercentOfOneCore': round(100 * (idle_cpu.user + idle_cpu.system - cpu.user - cpu.system) / elapsed, 2),
        'measurement': 'Own Tk event loop, fake resident, warm and cold actions separate. Not presentation or network latency.',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    reopened_panel.hide_panel()


if __name__ == '__main__':
    folder = Path(sys.argv[1]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    profile = folder / 'profile'
    visual = '--visual' in sys.argv
    prepare_profile(profile, count=24 if visual else 5000)
    if visual:
        prepare_visual_profile(profile)
    elif '--serve' in sys.argv:
        prepare_receipt_pages(profile)
    server = start_fixture(profile, visual=visual)
    try:
        if '--serve' in sys.argv:
            print('READY', flush=True)
            sys.stdin.buffer.read()
        else:
            legacy_benchmark(profile, folder / 'legacy.json')
    finally:
        server.shutdown()
        server.server_close()
        (profile / 'runtime/control.json').unlink(missing_ok=True)
