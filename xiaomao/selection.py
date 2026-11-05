"""本机选群服务：读取标题并校验窗口；切换时保留收藏但仅启用当前群。"""
from dataclasses import dataclass,replace,asdict
from pathlib import Path
import re
import threading
from .client import ControlUnavailable
from .config import Settings,Calibration,TARGET_GROUP,group_name
from .vision import LocalOCR,title_key,read_header,header_matches
from .windows import WindowsDesktop,window_details,DesktopUnavailable,find_wechat_window
from .contacts import visual_name_key,resolve_contact


@dataclass(frozen=True)
class CurrentGroup:
    name: str
    calibration: Calibration
    contact: object = None


def load_directory(root,cancelled=None):
    return directory_for_settings(Settings.load(root),cancelled)


def directory_for_settings(cfg,cancelled=None):
    """Read an account directory using an existing or uncommitted window binding."""
    from .database import DatabaseHub,DatabaseUnavailable
    if not cfg.binding:raise ValueError('请先校准一次已登录的微信窗口')
    hub=DatabaseHub(cfg,cancelled or threading.Event())
    try:return hub.directory()
    except DatabaseUnavailable as error:raise ValueError(str(error)) from None
    finally:hub.close()


def _initial_window():
    """Identify the sole WeChat main-window candidate, without showing or activating it."""
    import win32gui
    from .windows import input_desktop_available
    if not input_desktop_available():
        raise ValueError('桌面已锁定，请解锁后校准')
    candidates=[]
    unverified=[]
    def inspect(hwnd,_):
        if win32gui.GetWindow(hwnd,4) or win32gui.GetWindowText(hwnd) not in ('微信','Weixin','WeChat'):
            return
        try:info=window_details(hwnd)
        except DesktopUnavailable:
            unverified.append(hwnd)
            return
        hidden=win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd)
        width,height=info['width'],info['height']
        if hidden:
            left,top,right,bottom=win32gui.GetWindowPlacement(hwnd)[4]
            width,height=right-left,bottom-top
        if width>=600 and height>=450:
            candidates.append((info,hidden))
    win32gui.EnumWindows(inspect,None)
    if unverified:
        raise ValueError('候选窗口的微信进程未能核实，请检查登录窗口后重试')
    if not candidates:
        raise ValueError('未找到已登录的微信主窗口，请打开需要校准的聊天')
    if len(candidates)!=1:
        raise ValueError('找到多个可能的微信主窗口，无法唯一确认登录账号，请仅保留目标微信后重试')
    info,hidden=candidates[0]
    if hidden:
        raise ValueError('微信已最小化或隐藏，请手动显示聊天窗口后重试，无需保持前台')
    return info


def bind_saved_conversations(cfg, names, rows, contacts=()):
    """Build a previewable, account-bound list from freshly read directory rows.

    New personal chats need the Contact selected by the operator. A legacy
    group name can resolve to one current group; an existing binding must
    still resolve to its original ID. Only names explicitly retained count.
    """
    from .contacts import resolve_contact
    rows = list(rows)
    selected = cfg.with_saved_conversations(names)
    targets = dict(selected.conversation_targets)
    for contact in contacts:
        if contact.name not in selected.saved_groups:
            continue
        if not contact.selectable:
            raise ValueError(contact.reason)
        if contact.name in targets and targets[contact.name] != contact.binding:
            raise ValueError('同名会话身份改变，请先移除旧收藏再选择')
        targets[contact.name] = contact.binding
    selected = replace(selected, conversation_targets=targets)
    confirmed = {name:resolve_contact(rows,selected,name).binding for name in selected.saved_groups}
    if len({target['account'] for target in confirmed.values()}) != 1:
        raise ValueError('同时监听的会话须属于同一个登录账号')
    return replace(selected, conversation_targets=confirmed).with_saved_conversations(selected.saved_groups)


def stop_confirmed(root,client):
    try:client.stop()
    except ControlUnavailable:
        try:status=client.status()
        except ControlUnavailable:
            if (Path(root)/'runtime/control.json').exists():
                raise ControlUnavailable('无法确认旧助手已停止，切换未执行，请先停止旧助手') from None
        else:
            if status.get('running',True):raise ControlUnavailable('旧助手仍在运行，切换未执行')
    # 等待失败必须向上传播；不能用新配置去复用未退出的旧进程。
    client.wait_stopped()


def refresh_binding(settings,*,details=window_details,recover=find_wechat_window,desktop_factory=WindowsDesktop):
    binding=settings.binding
    if not binding:raise ValueError('请先校准一次微信窗口，再使用选群功能')
    try:info=details(binding.hwnd)
    except DesktopUnavailable:
        try:info=recover(binding.pid)
        except DesktopUnavailable as error:raise ValueError(str(error)) from None
    if info['pid']!=binding.pid:raise ValueError('微信窗口身份已变，请重新校准')
    sx,sy=info['width']/binding.width,info['height']/binding.height
    result=replace(binding,hwnd=info.get('hwnd',binding.hwnd),width=info['width'],height=info['height'])
    result.validate()
    if not .75<=sx<=2.5 or not .75<=sy<=2.5 or abs(sx-sy)>max(sx,sy)*.025:
        desktop=desktop_factory(result)
        try:desktop.capture()
        except DesktopUnavailable as error:raise ValueError(str(error)) from None
        if desktop.calibration is result:raise ValueError('微信窗口布局未能完整识别，请打开目标会话后重新校准')
        result=desktop.calibration
    return result


def _read_chat_frames(cfg,binding,desktop_factory,recognizer,entries,expected=None):
    desktop=desktop_factory(binding)
    ocr=recognizer or LocalOCR()
    if entries is not None:
        entries=tuple(entries)
        if len({row.account for row in entries})!=1:
            raise ValueError('当前窗口未能对应一个完整的登录账号目录')
    previous=None
    contact=None
    try:
        for _ in range(2):
            image=desktop.capture()
            binding=getattr(desktop,'calibration',None) or binding
            lines=read_header(ocr,image.crop(binding.rect('header')))
            label=''.join(line.text for line in lines)
            if not lines or any(line.score<.92 for line in lines):
                raise ValueError('当前会话标题不清晰')
            if entries is None and expected is not None:
                target=cfg.conversation_targets.get(expected,{})
                binding=replace(binding,target_group=expected,target_kind=target.get('kind','group'),
                                ocr_title=visual_name_key(expected) if target else '')
                if not header_matches(label,binding):
                    raise ValueError(f'微信标题与所选“{expected}”不符，请先在微信打开该会话')
                name=expected
            elif entries is None:
                if not re.fullmatch(r'.+\(\d+\)',title_key(label)):
                    raise ValueError('请从联系人列表选择个人或群聊')
                name=group_name(label)
                binding=replace(binding,target_kind='group',ocr_title='')
            else:
                matches=[row for row in entries if row.kind in ('private','group') and header_matches(
                    label,replace(binding,target_group=row.name,target_kind=row.kind,ocr_title=visual_name_key(row.name)))]
                if len(matches)!=1:
                    raise ValueError('当前微信标题未能与联系人列表唯一对应')
                contact=matches[0]
                if not contact.selectable:
                    raise ValueError(contact.reason)
                name=contact.name
                resolve_contact(entries,cfg.select_conversation(contact),name)
                binding=replace(binding,target_kind=contact.kind,ocr_title=visual_name_key(name))
            if expected is not None and name!=expected:
                raise ValueError(f'微信标题与所选“{expected}”不符，请先在微信打开该会话')
            identity=(name,contact.account,contact.id) if contact else (title_key(name),)
            if previous is not None and previous!=identity:
                raise ValueError('读取时微信会话发生变化，请重新读取当前会话')
            previous=identity
            binding=replace(binding,header_label=label,target_group=name)
            desktop.calibration=binding
            desktop.validate_target(image)
    except DesktopUnavailable as error:
        raise ValueError(str(error)) from None
    return image,CurrentGroup(name,binding,contact)


def read_current_group(root:Path,*,desktop_factory=WindowsDesktop,recognizer=None,details=window_details,entries=None):
    cfg=Settings.load(root)
    binding=refresh_binding(cfg,details=details)
    _,current=_read_chat_frames(cfg,binding,desktop_factory,recognizer,entries)
    return current


def capture_calibration(root,target,*,desktop_factory=WindowsDesktop,recognizer=None):
    """A first binding follows the actual open chat. A re-calibration stays on the selected recipient."""
    cfg=Settings.load(root)
    first=cfg.binding is None
    if first:
        from .gui import default_regions
        info=_initial_window()
        binding=Calibration(info['hwnd'],info['pid'],info['width'],info['height'],
                            '当前会话',default_regions())
    else:
        binding=refresh_binding(cfg)
    cfg=replace(cfg,calibration=asdict(binding))
    entries=None
    if cfg.receive_mode=='database':
        entries=directory_for_settings(cfg) if first else load_directory(root)
    image,current=_read_chat_frames(cfg,binding,desktop_factory,recognizer,entries,
                                    expected=None if first else target)
    binding=replace(current.calibration,bottom_confirmed=False)
    if current.contact is not None:
        # A deep copy in the editor keeps this capture-time identity. asdict()
        # deliberately excludes it from the persisted Calibration schema.
        binding._capture_contact=current.contact
    return image,binding


def switch_group(root:Path,name:str,client,*,binding=None,cancelled=None,contact=None):
    cancelled=cancelled or threading.Event()
    previous=Settings.load(root)
    first=previous.binding is None
    evidence=getattr(binding,'_capture_contact',None)
    if evidence and (not contact or contact.name!=evidence.name or contact.binding!=evidence.binding):
        raise ValueError('校准后微信账号或会话身份已改变，请重新校准')
    if contact and name!=contact.name:
        raise ValueError('选择的会话名称与联系人身份不一致')
    if first:
        if not binding or not binding.bottom_confirmed or binding.target_group!=name:
            raise ValueError('首次绑定需要先核对并确认微信窗口校准')
        binding.validate()
        if previous.receive_mode=='database' and not contact:
            raise ValueError('首次校准对象未能与联系人目录唯一对应')
        if not header_matches(binding.header_label,binding) or (contact and binding.target_kind!=contact.kind):
            raise ValueError('校准标题或会话类型与所选对象不一致')
        info=_initial_window()
        if any(info[key]!=getattr(binding,key) for key in ('hwnd','pid','width','height')):
            raise ValueError('校准后微信窗口身份或尺寸已改变，请重新校准')
    cfg=previous.select_conversation(contact) if contact else previous.select_group(name)
    if first and previous.saved_groups==(TARGET_GROUP,) and not previous.conversation_targets:
        cfg=cfg.with_saved_conversations((name,))
    if contact or name in cfg.conversation_targets:
        rows=directory_for_settings(replace(cfg,calibration=asdict(binding)),cancelled) if first else load_directory(root,cancelled)
        resolve_contact(rows,cfg,name)
    binding=binding or refresh_binding(cfg)
    kind=cfg.conversation_targets.get(name,{}).get('kind','group')
    cfg.calibration=asdict(replace(binding,header_label=name,target_group=name,target_kind=kind,
                                 ocr_title=visual_name_key(name) if name in cfg.conversation_targets else ''))
    cfg.__post_init__()
    def stopped():
        stop_confirmed(root,client)
        return {'running':False,'mode':'stopped','group':Settings.load(root).group,'reason':'群切换已取消，自动回复保持停止'}
    if cancelled.is_set():return stopped()
    if first and Settings.load(root)!=previous:
        raise ValueError('校准期间设置已变更，请重新打开校准')
    stop_confirmed(root,client)
    if cancelled.is_set():return stopped()
    if first and Settings.load(root)!=previous:
        raise ValueError('校准期间设置已变更，助手已停止，请重新校准')
    # A name-only archive cannot establish its account or recipient. Keep it
    # on disk and let the new binding use its own account/ID memory.
    cfg.save()
    if cancelled.is_set():return stopped()
    result=client.start()
    if cancelled.is_set():
        stop_confirmed(root,client)
        return stopped()
    if result.get('group')!=name or result.get('group_names')!=[name] or result.get('reply_mode')!=cfg.reply_mode:
        try:client.pause()
        except ControlUnavailable:raise ControlUnavailable('启动后的监听范围或回复模式不符，无法确认暂停；请停止助手') from None
        raise ValueError('启动后的监听范围或回复模式与所选设置不符，已暂停，未完成切换')
    return result
