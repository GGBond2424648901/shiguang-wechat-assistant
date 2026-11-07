"""Substitute only desktop and account inputs for source-build visual QA.

The screenshot harness copies this file to its disposable PYTHONPATH as
sitecustomize.py. The released application never imports it.
"""

import os
from pathlib import Path
import sys
from types import SimpleNamespace


def install(profile):
    from PIL import Image, ImageDraw
    from xiaomao import selection, windows
    from xiaomao.config import Calibration, Settings
    from xiaomao.contacts import Contact

    def directory(root, *_):
        if Path(root).resolve() != profile:
            raise ValueError('验收数据路径不匹配')
        cfg = Settings.load(root)
        saved = [Contact(row['id'], row['account'], name, row['kind'])
                 for name, row in cfg.conversation_targets.items()]
        return [*saved, Contact('demo_city@chatroom', 'ui-qa', '周末城市漫步', 'group'),
                Contact('demo_yun', 'ui-qa', '阿云', 'private'),
                Contact('demo_print@chatroom', 'ui-qa', '海边印相室', 'group'),
                Contact('demo_wood', 'ui-qa', '小木', 'private')]

    def windows_list():
        return [{'hwnd': 221 + index, 'pid': 444, 'kind': 'detached', 'title': name,
                 'width': 920, 'height': 630, 'minimized': False}
                for index, name in enumerate(Settings.load(profile).saved_groups)]

    def screenshot():
        image = Image.new('RGB', (920, 630), '#242437')
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((17, 17, 903, 612), 16, fill='#242b39', outline='#6b6478', width=2)
        draw.rounded_rectangle((28, 32, 222, 590), 12, fill='#171f2d')
        for y in range(65, 550, 67):
            draw.rounded_rectangle((41, y, 72, y + 32), 7, fill='#5e5472')
            draw.rounded_rectangle((83, y + 6, 177, y + 13), 4, fill='#93889f')
            draw.rounded_rectangle((83, y + 22, 144, y + 28), 3, fill='#635c70')
        draw.rounded_rectangle((244, 41, 402, 54), 5, fill='#bab2c5')
        for at, y in enumerate(range(106, 395, 61)):
            if at % 2 == 0:
                draw.rounded_rectangle((252, y, 438 + at * 16, y + 37), 9, fill='#423d56')
            else:
                draw.rounded_rectangle((592 - at * 11, y, 867, y + 37), 9, fill='#477452')
        draw.line((242, 476, 883, 476), fill='#7e7388', width=2)
        draw.rounded_rectangle((757, 549, 867, 587), 8, fill='#47684b')
        return image

    def preview(cfg, candidate, name):
        name = name or candidate['title']
        kind = cfg.conversation_targets[name]['kind']
        regions = {'header': [.26, .045, .96, .11], 'messages': [.26, .13, .96, .74],
                   'input': [.27, .78, .96, .85], 'send': [.82, .87, .95, .94]}
        draft = Calibration(candidate['hwnd'], candidate['pid'], 920, 630, name, regions,
                            target_group=name, target_kind=kind, window_kind='detached')
        return screenshot(), draft

    def identify(_, draft, *_args, **_kwargs):
        return screenshot(), SimpleNamespace(calibration=draft, name=draft.target_group)

    def reject_save(*_):
        raise ValueError('示例窗口用于预览，请在微信实机校准')

    selection.load_directory = directory
    windows.list_calibration_windows = windows_list
    windows.open_wechat_for_calibration = lambda: None
    windows.activate_calibration_window = lambda *_: None
    windows.CalibrationWindowLease = lambda _: SimpleNamespace(close=lambda: None)
    selection.preview_window = preview
    selection.identify_reviewed_window = identify
    selection.verify_reviewed_window = reject_save


if (profile_arg := os.environ.get('SHIGUANG_UI_FIXTURE_PROFILE')) and '--root' in sys.argv:
    root_at = sys.argv.index('--root') + 1
    if root_at < len(sys.argv) and Path(sys.argv[root_at]).resolve() == Path(profile_arg).resolve():
        install(Path(profile_arg).resolve())
