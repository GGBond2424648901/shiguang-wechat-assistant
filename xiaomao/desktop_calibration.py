"""User-directed calibration with short-lived HWND leases and identity checks."""

import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from io import BytesIO
from threading import Event, RLock
from time import monotonic
from uuid import uuid4

from . import selection, windows
from .client import ControlUnavailable
from .config import Calibration, Settings
from .desktop_settings import config_revision
from .vision import detached_title_matches


@dataclass
class Capture:
    id: str
    revision: str
    lease: object
    cancelled: Event
    expires: float
    draft: Calibration | None = None
    busy: bool = False
    image: object = None


class DesktopCalibration:
    def __init__(self, root, client):
        self.root = root
        self.client = client
        self._lock = RLock()
        self._candidates = {}
        self._capture = None
        self._vision = None

    def _recognizer(self):
        if self._vision is None:
            from .vision import Vision
            self._vision = Vision()
        return self._vision

    def discover(self, session=''):
        cfg = Settings.load(self.root)
        name = next((name for name in cfg.saved_groups if cfg.conversation_pause_key(name) == session), '')
        if session and not name:
            raise ValueError('该会话已变化，请重新选择')
        current = cfg.binding_for(name) if name else None
        target = cfg.conversation_targets.get(name, {})
        try:
            candidates = windows.list_calibration_windows()
        except windows.DesktopUnavailable as error:
            raise ValueError(str(error)) from None
        with self._lock:
            self._candidates = {uuid4().hex: (candidate, monotonic() + 90, config_revision(cfg)) for candidate in candidates}
            rows = [{'id': key, 'title': info['title'], 'kind': info['kind'], 'minimized': info['minimized']}
                    for key, (info, _, _) in self._candidates.items()]
            probe = Calibration(1, 1, 1, 1, name, {}, target_group=name, target_kind=target.get('kind', 'group'))
            matching = [key for key, (info, _, _) in self._candidates.items() if name and info['kind'] == 'detached'
                        and (not current or info['pid'] == current.pid) and detached_title_matches(info['title'], probe)]
            exact = next((key for key, (info, _, _) in self._candidates.items() if current and
                          (info['hwnd'], info['pid']) == (current.hwnd, current.pid)), '')
            suggestion = (matching[0] if len(matching) == 1 else exact if not matching or exact in matching else '')
            if len(rows) == 1:
                suggestion = rows[0]['id']
        return {'windows': rows, 'suggested': suggestion, 'target': name}

    def open_wechat(self):
        try:
            windows.open_wechat_for_calibration()
        except windows.DesktopUnavailable as error:
            raise ValueError(str(error)) from None
        return {'opened': True}

    def _drop(self, capture):
        capture.lease.close()
        if self._capture is capture:
            self._capture = None

    def cancel(self, token=''):
        with self._lock:
            capture = self._capture
            if capture and (not token or token == capture.id):
                capture.cancelled.set()
                if not capture.busy:
                    self._drop(capture)
        return {'cancelled': True}

    @contextmanager
    def _use(self, token):
        with self._lock:
            capture = self._capture
            if not capture or capture.id != token or capture.cancelled.is_set():
                raise ValueError('该校准已结束，请重新选窗口')
            if capture.busy:
                raise ValueError('窗口正在核对，请稍候')
            if monotonic() > capture.expires:
                self._drop(capture)
                raise ValueError('截图已过期，请重新截图')
            capture.busy = True
        try:
            yield capture
        except windows.DesktopUnavailable as error:
            raise ValueError(str(error)) from None
        finally:
            with self._lock:
                capture.busy = False
                if capture.cancelled.is_set():
                    self._drop(capture)

    @staticmethod
    def _picture(image):
        preview = image.convert('RGB')
        preview.thumbnail((1650, 1050))
        result = BytesIO()
        preview.save(result, format='JPEG', quality=88, optimize=True)
        return 'data:image/jpeg;base64,' + base64.b64encode(result.getvalue()).decode('ascii')

    def _projection(self, capture, image, observed='', note='', *, identified=False):
        capture.image = image
        draft = capture.draft
        return {'token': capture.id, 'target': draft.target_group, 'kind': draft.target_kind,
                'width': draft.width, 'height': draft.height, 'regions': draft.regions,
                'image': self._picture(image), 'observed': observed, 'note': note, 'identified': identified}

    def capture(self, window, session='', deferIdentify=False):
        if type(deferIdentify) is not bool:
            raise ValueError('截图选项无效')
        cfg = Settings.load(self.root)
        name = next((name for name in cfg.saved_groups if cfg.conversation_pause_key(name) == session), None)
        if session and not name:
            raise ValueError('该会话已变化，请重新选择')
        with self._lock:
            candidate = self._candidates.get(window)
            if not candidate or monotonic() > candidate[1] or candidate[2] != config_revision(cfg):
                raise ValueError('微信窗口列表已过期，请刷新')
            if self._capture:
                raise ValueError('已有校准截图，请先结束上一次校准')
            try:
                lease = windows.CalibrationWindowLease(candidate[0]['hwnd'])
            except (OSError, windows.DesktopUnavailable) as error:
                raise ValueError(str(error)) from None
            capture = Capture(uuid4().hex, candidate[2], lease, Event(), monotonic() + 600)
            self._capture = capture
        try:
            with self._use(capture.id):
                choice = candidate[0]
                # The window-only capture can proceed when Windows keeps focus elsewhere.
                windows.activate_calibration_window(choice['hwnd'], choice['pid'], False,
                                                    (choice['kind'], choice['title']))
                if capture.cancelled.is_set():
                    raise ValueError('校准已取消')
                image, capture.draft = selection.preview_window(cfg, choice, name)
                if deferIdentify:
                    return self._projection(capture, image, note='截图已就绪，正在核对会话身份…')
                try:
                    verified, identity = selection.identify_reviewed_window(
                        cfg, capture.draft, capture.cancelled, vision=self._recognizer())
                except selection.CalibrationIdentityError as error:
                    return self._projection(capture, image, error.observed_title, str(error))
                except ValueError as error:
                    return self._projection(capture, image, note=str(error))
                capture.draft = identity.calibration
                return self._projection(capture, verified, identity.name, '请核对区域、聊天底部和空白输入框', identified=True)
        except BaseException:
            self.cancel(capture.id)
            raise

    def _edit(self, capture, regions):
        if (not isinstance(regions, dict) or set(regions) != {'header', 'messages', 'input', 'send'}
                or any(not isinstance(box, list) or len(box) != 4 or
                       any(type(point) not in (int, float) for point in box) for box in regions.values())):
            raise ValueError('请填写完整的四个区域')
        draft = deepcopy(capture.draft)
        draft.regions = regions
        draft.manual_regions = True
        draft.validate()
        return draft

    def identify(self, token, regions, chooseObserved=False):
        if type(chooseObserved) is not bool:
            raise ValueError('会话选择无效')
        with self._use(token) as capture:
            cfg = Settings.load(self.root)
            if config_revision(cfg) != capture.revision:
                raise ValueError('校准期间设置已变化，请重新截图')
            draft = self._edit(capture, regions)
            try:
                image, identity = selection.identify_reviewed_window(cfg, draft, capture.cancelled,
                    choose_observed=chooseObserved, vision=self._recognizer())
            except selection.CalibrationIdentityError as error:
                capture.draft = draft
                return self._projection(capture, capture.image, error.observed_title, str(error))
            capture.draft = identity.calibration
            return self._projection(capture, image, identity.name, '请核对区域、聊天底部和空白输入框', identified=True)

    def save(self, token, regions, confirmed):
        if confirmed is not True:
            raise ValueError('请先确认会话、聊天底部和空白输入框')
        with self._use(token) as capture:
            cfg = Settings.load(self.root)
            if config_revision(cfg) != capture.revision:
                raise ValueError('校准期间设置已变化，请重新截图')
            draft = self._edit(capture, regions)
            if not draft.target_group:
                raise ValueError('请先识别当前会话')
            checked, contact = selection.verify_reviewed_window(cfg, draft, capture.cancelled, vision=self._recognizer())
            updated = selection.bind_reviewed_window(cfg, checked, contact, stage_for_enable=True)
            if capture.cancelled.is_set():
                raise ValueError('校准已取消')
            updated.save(expected=cfg)
            capture.cancelled.set()
            try:
                previous = self.client.start(initial_mode='observe')
            except ControlUnavailable:
                return {'target': checked.target_group, 'applied': False, 'note': '校准已保存，后台暂未连接'}
            try:
                result = self.client.reload_windows()
            except ControlUnavailable as error:
                if ('没有这个控制操作' in str(error) and previous.get('mode') == 'paused'
                        and not self.client.pending().get('tasks')):
                    selection.stop_confirmed(self.root, self.client)
                    result = self.client.start(initial_mode='paused')
                else:
                    return {'target': checked.target_group, 'applied': False, 'note': '校准已保存，后台暂未确认应用'}
            pending = bool(result.get('window_reload_pending'))
            return {'target': checked.target_group, 'applied': not pending,
                    'paused': updated.is_conversation_paused(checked.target_group),
                    'note': '正在等待后台应用校准' if pending else ''}
