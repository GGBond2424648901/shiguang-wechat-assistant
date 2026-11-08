"""本机选群服务：读取标题并校验窗口；切换时保留收藏但仅启用当前群。"""
from dataclasses import dataclass,replace,asdict
from pathlib import Path
import re
import threading
from .client import ControlUnavailable
from .config import Settings,Calibration,TARGET_GROUP,group_name
from .vision import LocalOCR,title_key,read_header,header_matches
from .windows import WindowsDesktop,window_details,DesktopUnavailable,find_wechat_window
from .contacts import visual_name_key,resolve_contact,resolve_title_contact


@dataclass(frozen=True)
class CurrentGroup:
    name: str
    calibration: Calibration
    contact: object = None


class CalibrationIdentityError(ValueError):
    """A displayable observation is not yet permission to change the recipient."""
    def __init__(self,message,observed_title,kind=''):
        super().__init__(message)
        self.observed_title=observed_title
        self.kind=kind


def load_directory(root,cancelled=None):
    return directory_for_settings(Settings.load(root),cancelled)


def directory_for_settings(cfg,cancelled=None,*,process_id=None):
    """Read the full pinned account directory, independently of recent chats.

    A stale HWND/PID is not a directory identity. A unique live process may
    recover a read-only directory after restart, but its account must still
    equal every saved recipient's account before any rows reach the UI.
    """
    from .database import DatabaseHub,DatabaseUnavailable
    from .windows import list_calibration_windows
    cancelled=cancelled or threading.Event()
    accounts={target['account'] for target in cfg.conversation_targets.values()}
    if cfg.calibration_account:accounts.add(cfg.calibration_account)
    if len(accounts)>1:raise ValueError('已保存会话的微信账号不一致，请重新选择当前登录账号')
    expected_account=next(iter(accounts),None)
    explicit=process_id is not None
    if process_id is None:
        bindings=[cfg.binding,*(cfg.binding_for(name) for name in cfg.saved_groups)]
        process_id=next((bound.pid for bound in bindings if bound),None)
    def read(pid):
        if cancelled.is_set():raise ValueError('联系人读取已取消')
        hub=DatabaseHub(cfg,cancelled,process_id=pid)
        try:
            rows=hub.directory()
            read_accounts={row.account for row in rows}
            if (len(read_accounts)!=1 or not next(iter(read_accounts),'') or
                    expected_account and read_accounts!={expected_account}):
                raise ValueError('该窗口对应的微信账号已改变或未能唯一确认，请重新校准')
            if cancelled.is_set():raise ValueError('联系人读取已取消')
            return rows
        finally:hub.close()
    original_error=None
    if process_id is not None:
        try:return read(process_id)
        except DatabaseUnavailable as error:
            if explicit:raise ValueError(str(error)) from None
            original_error=error
    try:
        candidates={row['pid'] for row in list_calibration_windows() if row['pid']!=process_id}
    except DesktopUnavailable as error:raise ValueError(str(error)) from None
    if len(candidates)>1:
        raise ValueError('找到多个微信登录进程，请先在校准中明确选择当前账号的窗口，再刷新联系人')
    if not candidates:
        raise ValueError(str(original_error) if original_error else '请先打开已登录的微信窗口，再刷新联系人') from None
    try:return read(next(iter(candidates)))
    except DatabaseUnavailable as error:raise ValueError(str(error)) from None


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
        if binding.window_kind=='detached':raise ValueError('原独立聊天窗口已关闭，请从会话列表重新校准该窗口') from None
        try:info=recover(binding.pid)
        except DesktopUnavailable as error:raise ValueError(str(error)) from None
    if info['pid']!=binding.pid:raise ValueError('微信窗口身份已变，请重新校准')
    if info.get('kind',binding.window_kind)!=binding.window_kind:
        raise ValueError('微信窗口类型已变化，请重新校准')
    if binding.manual_regions and (info['width'],info['height'])!=(binding.width,binding.height):
        raise ValueError('人工校准后的窗口尺寸已改变，请重新校准')
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
            lines=read_header(ocr,image.crop(binding.rect('header')),binding)
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
                contact=resolve_title_contact(entries,label,binding.window_kind)
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


def preview_window(cfg,candidate,expected):
    """Open the geometry editor from fresh pixels, even if automatic detection fails."""
    from .layout import adapt_calibration,default_calibration_regions
    from .windows import capture_calibration_window
    image,info=capture_calibration_window(candidate['hwnd'],candidate['pid'])
    if (candidate['kind'],candidate['title'])!=(info['kind'],info['title']):
        raise ValueError('所选微信窗口已改变，请重新选择')
    kind=info['kind']
    existing=cfg.binding_for(expected) if expected else None
    draft=Calibration(info['hwnd'],info['pid'],info['width'],info['height'],
                      expected or '待识别的会话',default_calibration_regions(kind),
                      target_group=expected or '',target_kind=cfg.conversation_targets.get(expected,{}).get('kind','group'),
                      window_kind=kind)
    if kind=='detached':
        # Qt keeps this title row at the same DPI-scaled height when the
        # chat is resized. A percentage cut through titles in short windows.
        header=draft.regions['header']
        header[1]=18*info.get('scale',1)/draft.height
        header[3]=96*info.get('scale',1)/draft.height
        draft.regions['messages'][1]=max(draft.regions['messages'][1],header[3]+2/draft.height)
    if existing and (existing.hwnd,existing.pid,existing.width,existing.height,existing.window_kind)==(
            draft.hwnd,draft.pid,draft.width,draft.height,kind):
        draft=replace(draft,regions=existing.regions.copy(),manual_regions=existing.manual_regions)
    draft=adapt_calibration(image,draft,info.get('scale',1))
    return image,draft


def bind_reviewed_window(cfg,binding,contact,*,stage_for_enable=False):
    """Keep subscription order and mode while installing one verified calibration."""
    name=binding.target_group
    if not name or not binding.bottom_confirmed:raise ValueError('请先核对会话与校准区域')
    previous_binding=cfg.binding_for(name) if name in cfg.allowed_groups else None
    needs_enable=stage_for_enable and (previous_binding is None or not previous_binding.bottom_confirmed
                                      or (contact and name not in cfg.conversation_targets))
    paused=cfg.is_conversation_paused(name) or all(cfg.is_conversation_paused(n) for n in cfg.allowed_groups)
    evidence=getattr(binding,'_capture_contact',None)
    if evidence and (not contact or contact.binding!=evidence.binding or contact.name!=evidence.name):
        raise ValueError('校准窗口的会话身份已改变，请重新识别')
    if contact:
        if not contact.selectable or (contact.name,contact.kind)!=(name,binding.target_kind):
            raise ValueError('校准窗口与所选会话身份不一致')
        old=cfg.conversation_targets.get(name)
        if old and old!=contact.binding:raise ValueError('同名会话身份已改变，请重新选择')
        accounts={v['account'] for v in cfg.conversation_targets.values()}
        if accounts and accounts!={contact.account}:raise ValueError('校准对象须属于当前监听的登录账号')
        cfg=replace(cfg,conversation_targets={**cfg.conversation_targets,name:contact.binding})
    elif cfg.receive_mode=='database':
        raise ValueError('未核实当前会话身份')
    names=cfg.saved_groups
    if names==(TARGET_GROUP,) and not cfg.calibration and not cfg.window_bindings:
        names=(name,)
    elif name not in names:names=(*names,name)
    updated=cfg.with_saved_conversations(names).with_window_binding(name,binding)
    if needs_enable:
        if name not in updated.allowed_groups:
            updated=replace(updated,monitor_all_groups=True)
            for saved in updated.allowed_groups:
                if saved not in cfg.allowed_groups:
                    updated=updated.with_conversation_pause(saved,True)
        return updated.with_conversation_pause(name,True)
    return updated.with_conversation_pause(name,True) if paused else updated


def rediscover_detached(cfg,names,cancelled):
    """Rebind reopened chat windows by account/recipient, never a recycled HWND."""
    from .windows import list_calibration_windows
    from .vision import detached_title_matches
    candidates=list_calibration_windows()
    for name in names:
        old=cfg.binding_for(name)
        if not old or old.window_kind!='detached' or name not in cfg.conversation_targets:continue
        matches=[w for w in candidates if w['kind']=='detached' and detached_title_matches(w['title'],old)]
        if len(matches)!=1 or matches[0]['minimized'] or cancelled.is_set():continue
        try:
            _,draft=preview_window(cfg,matches[0],name)
            checked,contact=verify_reviewed_window(cfg,draft,cancelled)
            if contact and contact.binding==cfg.conversation_targets[name]:
                cfg=bind_reviewed_window(cfg,checked,contact)
        except (ValueError,RuntimeError,OSError):continue
    return cfg


def _reviewed_identity(cfg,draft,cancelled,vision):
    """Read one immutable window/recipient observation, without sending input."""
    from .vision import detached_title_matches
    from .windows import capture_calibration_window
    if cancelled.is_set():raise ValueError('校准已取消')
    rows=directory_for_settings(cfg,cancelled,process_id=draft.pid) if cfg.receive_mode=='database' else None
    image,info=capture_calibration_window(draft.hwnd,draft.pid)
    if (info['kind'],info['width'],info['height'])!=(draft.window_kind,draft.width,draft.height):
        raise ValueError('微信窗口尺寸或类型已变化，请重新截图')
    lines=read_header(vision.ocr,image.crop(draft.rect('header')),draft)
    if not lines or any(line.score<.92 for line in lines):
        raise ValueError('未读到完整会话标题。请先在微信打开目标聊天。如果已打开，调整“会话标题”选区后点“重新识别”')
    title=''.join(line.text for line in lines)
    try:contact=resolve_title_contact(rows,title,draft.window_kind) if rows is not None else None
    except ValueError as error:raise CalibrationIdentityError(str(error),title) from None
    name,kind=(contact.name,contact.kind) if contact else (group_name(title),'group')
    if draft.target_group and name!=draft.target_group:
        raise CalibrationIdentityError(f'读到“{name}”，当前要校准的是“{draft.target_group}”。要绑定这个新对象，请明确选择“使用窗口中的会话”；原有绑定会保留',title,kind)
    evidence=getattr(draft,'_capture_contact',None)
    if evidence and (not contact or contact.binding!=evidence.binding):
        raise ValueError('校准期间微信账号或会话身份已改变，请重新识别')
    checked=replace(draft,header_label=title,target_group=name,target_kind=kind,bottom_confirmed=False,
                    ocr_title=visual_name_key(name) if contact else '')
    if checked.window_kind=='detached' and not detached_title_matches(info['title'],checked):
        raise ValueError('独立窗口标题与已识别聊天对象不一致')
    if contact:checked._capture_contact=contact
    return image,CurrentGroup(name,checked,contact),rows


def identify_reviewed_window(cfg,draft,cancelled,*,choose_observed=False,vision=None):
    """Preview the exact account-bound object; choosing another object is explicit."""
    from .vision import Vision
    if choose_observed:
        draft=replace(draft,target_group='',ocr_title='',bottom_confirmed=False)
    draft.validate()
    vision=vision or Vision()
    previous=None
    for _ in range(2):
        image,current,_rows=_reviewed_identity(cfg,draft,cancelled,vision)
        identity=(current.name,current.contact.binding if current.contact else None)
        if previous is not None and identity!=previous:
            raise ValueError('两次截图的会话身份不同，请重新识别')
        previous=identity
    return image,current


def verify_reviewed_window(cfg,draft,cancelled,*,vision=None):
    """Match the user's four regions twice before persisting any window or recipient."""
    from .vision import Vision,calibration_send_matches
    draft.validate()
    vision=vision or Vision()
    checked=None
    previous=None
    for _ in range(2):
        image,current,rows=_reviewed_identity(cfg,draft,cancelled,vision)
        contact=current.contact
        name,kind=current.name,current.calibration.target_kind
        binding=replace(current.calibration,bottom_confirmed=True)
        if contact:binding._capture_contact=contact
        updated=bind_reviewed_window(cfg,binding,contact)
        if contact:resolve_contact(rows,updated,name)
        identity=(name,kind,contact.binding if contact else None)
        if previous is not None and identity!=previous:
            raise ValueError('两次截图的会话身份不同，请重新校准')
        previous=identity
        frame=vision.read(image,binding)
        if not frame.safe or not frame.at_bottom:raise ValueError(frame.reason or '请确认聊天位于底部，并调整消息区域')
        if not frame.draft_empty:raise ValueError('输入区域含有草稿或图标，请调整选区')
        if not calibration_send_matches(vision.ocr,image,binding):raise ValueError('发送按钮选区未匹配“发送”，请框选按钮')
        if cfg.receive_mode=='screen' and not frame.messages:raise ValueError('消息区域中未识别到聊天气泡')
        checked=binding
    return checked,contact


def switch_group(root:Path,name:str,client,*,binding=None,cancelled=None,contact=None):
    cancelled=cancelled or threading.Event()
    previous=Settings.load(root)
    first=not previous.calibration and not previous.window_bindings
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
    cfg=replace(cfg,auto_on_launch=previous.auto_on_launch)
    if first and previous.saved_groups==(TARGET_GROUP,) and not previous.conversation_targets:
        cfg=cfg.with_saved_conversations((name,))
    if contact or name in cfg.conversation_targets:
        rows=directory_for_settings(replace(cfg,calibration=asdict(binding)),cancelled) if first else load_directory(root,cancelled)
        resolve_contact(rows,cfg,name)
    binding=binding or refresh_binding(cfg)
    kind=cfg.conversation_targets.get(name,{}).get('kind','group')
    cfg=cfg.with_window_binding(name,replace(binding,header_label=name,target_group=name,target_kind=kind,
                                            ocr_title=visual_name_key(name) if name in cfg.conversation_targets else ''))
    def stopped():
        stop_confirmed(root,client)
        return {'running':False,'mode':'stopped','group':Settings.load(root).group,'reason':'群切换已取消，自动回复保持停止'}
    if cancelled.is_set():return stopped()
    try:status=client.status()
    except ControlUnavailable:
        if (Path(root)/'runtime/control.json').exists():
            raise ControlUnavailable('无法确认当前回复模式，未切换会话；请检查后台连接') from None
        status={'running':False,'mode':'stopped'}
    mode=status.get('mode') if status.get('running',True) else 'stopped'
    if mode not in ('paused','observe','auto','stopped'):
        raise ControlUnavailable('当前回复模式未能确认，未切换会话')
    expected_mode='paused' if mode=='auto' and cfg.is_conversation_paused(name) else mode
    if first and Settings.load(root)!=previous:
        raise ValueError('校准期间设置已变更，请重新打开校准')
    stop_confirmed(root,client)
    if cancelled.is_set():return stopped()
    if first and Settings.load(root)!=previous:
        raise ValueError('校准期间设置已变更，助手已停止，请重新校准')
    # A name-only archive cannot establish its account or recipient. Keep it
    # on disk and let the new binding use its own account/ID memory.
    cfg.save(expected=previous)
    if cancelled.is_set():return stopped()
    if mode=='stopped':
        return {'running':False,'mode':'stopped','group':cfg.group,'group_names':list(cfg.allowed_groups),
                'reply_mode':cfg.for_group(name).reply_mode,'reason':'会话已切换，自动回复保持停止'}
    result=client.start(initial_mode=mode)
    if cancelled.is_set():
        stop_confirmed(root,client)
        return stopped()
    if (result.get('group')!=name or result.get('group_names')!=[name] or
            result.get('reply_mode')!=cfg.for_group(name).reply_mode or result.get('mode')!=expected_mode):
        try:client.pause()
        except ControlUnavailable:raise ControlUnavailable('启动后的监听范围或回复模式不符，无法确认暂停；请停止助手') from None
        raise ValueError('启动后的监听范围或回复模式与所选设置不符，已暂停，未完成切换')
    return result
