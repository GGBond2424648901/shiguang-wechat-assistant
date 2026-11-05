"""Ordinary search navigation only; no message input or Send action here."""
import ctypes
import time
from .vision import title_key,TextLine


SECTIONS={'联系人','群聊','聊天记录','收藏','搜索网络结果','最近在搜','公众号','功能','小程序'}


def title_rows(lines):
    rows=[]
    for line in sorted(lines,key=lambda r:(r.box[1],r.box[0])):
        for i,row in enumerate(rows):
            height=min(row.box[3]-row.box[1],line.box[3]-line.box[1])
            overlap=min(row.box[3],line.box[3])-max(row.box[1],line.box[1])
            left,right=sorted((row,line),key=lambda r:r.box[0])
            if overlap>=height*.65 and 0<=right.box[0]-left.box[2]<=height*4:
                rows[i]=TextLine(left.text+right.text,(left.box[0],min(left.box[1],right.box[1]),right.box[2],max(left.box[3],right.box[3])),min(left.score,right.score))
                break
        else:rows.append(line)
    return sorted(rows,key=lambda r:(r.box[1],r.box[0]))


def search_result(lines,target,kind,*,ocr_title=''):
    from .contacts import visual_name_key
    key=visual_name_key if ocr_title and ocr_title==visual_name_key(target) else title_key
    section='';heading=None;found=[]
    for line in title_rows(lines):
        text=title_key(line.text)
        if text in SECTIONS:
            section=text if line.score>=.92 else '';heading=line;continue
        if (section==('群聊' if kind=='group' else '联系人') and heading
                and line.score>=.92 and key(line.text)==key(target)
                and line.box[0]>=heading.box[0]+15):found.append(line)
    return found[0] if len(found)==1 else None


def first_result_is(lines,found,kind):
    ordered=title_rows(lines)
    expected='群聊' if kind=='group' else '联系人'
    headings=[r for r in ordered if title_key(r.text) in SECTIONS]
    if not headings or title_key(headings[0].text)!=expected:return False
    rows=[r for r in ordered if r.box[1]>headings[0].box[3] and r.box[0]>=headings[0].box[0]+15]
    return bool(rows and rows[0]==found)


def search_target(desktop,idle_seconds):
    """Only an empty observed search box and an exact result may be operated."""
    from .windows import U,W,INPUT,unicode_units,window_details,DesktopTemporarilyHidden
    from PIL import ImageGrab
    from .clipboard import NativeClipboard,copied_text,ClipboardUnavailable
    binding=desktop.calibration
    target=binding.target_group
    units=unicode_units(target)  # Validate before opening any search field.
    desktop._search_diagnostic={'stage':'start'}
    if time.monotonic()-getattr(desktop,'_last_search',0)<30:return False
    desktop._last_search=time.monotonic()
    idle_credit=desktop.idle_for();generation=desktop.prepare_send(idle_seconds)
    info=desktop.check();scale=desktop._dpi_scale()
    image=desktop.capture();ocr=desktop._sidebar_ocr
    if ocr is None:
        from .vision import LocalOCR
        ocr=desktop._sidebar_ocr=LocalOCR()
    # Keep the magnifier and the + button out of the text crop. OCR otherwise
    # merges the magnifier into a low-confidence "Q. 搜索" token.
    region=(round(108*scale),round(20*scale),binding.rect('messages')[0]-50,round(59*scale))
    if region[2]<=region[0]:return False
    search_lines=ocr(image.crop(region))
    labels=[r for r in search_lines if title_key(r.text)=='搜索' and r.score>=.92]
    if not labels:
        # WeChat's empty placeholder is very pale and only two characters.
        # Upscale and stretch this search-only crop; coordinates map back to
        # the original image. This never relaxes name matching confidence.
        from PIL import ImageOps
        from dataclasses import replace
        crop=ImageOps.autocontrast(image.crop(region).convert('L')).convert('RGB')
        enlarged=crop.resize((crop.width*3,crop.height*3))
        search_lines=[replace(r,box=tuple(round(v/3) for v in r.box)) for r in ocr(enlarged)]
        labels=[r for r in search_lines if title_key(r.text)=='搜索' and r.score>=.92]
    desktop._search_diagnostic={'stage':'empty_search','labels':len(labels),'scale':scale,'region':region,
                                'scores':[(r.box,r.score) for r in search_lines]}
    if len(labels)!=1:return False  # Existing human search is not replaced.
    label=labels[0];popup=None;focus=None;cookie=generation;search_owned=True
    def guard():
        current=window_details(binding.hwnd)
        if (desktop.cancelled.is_set() or current['pid']!=binding.pid or current['rect']!=info['rect']
                or U.GetForegroundWindow()!=binding.hwnd or desktop.input_generation()!=cookie):
            raise DesktopTemporarilyHidden('搜索期间检测到人工操作或窗口变化，已停止定位')
        if focus is not None and desktop._input_snapshot()[0]!=focus:
            raise DesktopTemporarilyHidden('搜索焦点变化，已停止定位')
    def emit(events):
        nonlocal cookie
        guard()
        if U.SendInput(len(events),events,ctypes.sizeof(INPUT))!=len(events):
            raise DesktopTemporarilyHidden('搜索输入未完成，未操作消息输入框')
        cookie=desktop.input_generation()
    def keys(*values):
        sequence=[(v,0) for v in values]+[(v,2) for v in reversed(values)]
        events=(INPUT*len(sequence))()
        for e,(v,f) in zip(events,sequence):e.type=1;e.value.keyboard.vk=v;e.value.keyboard.flags=f
        emit(events)
    def click(x,y,root):
        guard();point=W.POINT(x,y)
        if U.GetAncestor(U.WindowFromPoint(point),2)!=root:raise DesktopTemporarilyHidden('搜索位置被遮挡')
        U.SetCursorPos(x,y)
        desktop.cancelled.wait(.08);guard()
        if U.GetAncestor(U.WindowFromPoint(point),2)!=root:raise DesktopTemporarilyHidden('搜索浮层位置变化')
        down=(INPUT*1)();down[0].value.mouse.flags=2
        emit(down)
        try:desktop.cancelled.wait(.06)
        finally:
            # Always release a press that this operation owns, even on pause.
            up=(INPUT*1)();up[0].value.mouse.flags=4
            if U.SendInput(1,up,ctypes.sizeof(INPUT))!=1:raise DesktopTemporarilyHidden('搜索鼠标释放失败')
        nonlocal cookie
        cookie=desktop.input_generation()
    x=info['rect'][0]+region[0]+(label.box[0]+label.box[2])//2
    y=info['rect'][1]+region[1]+(label.box[1]+label.box[3])//2
    click(x,y,binding.hwnd);desktop.cancelled.wait(.12);guard()
    focus=desktop._input_snapshot()[0]
    try:
        events=(INPUT*(2*len(units)))()
        for i,unit in enumerate(units):
            for up in (0,1):
                e=events[i*2+up];e.type=1;e.value.keyboard.scan=unit;e.value.keyboard.flags=4|up*2
        emit(events);desktop.cancelled.wait(.35);guard()
        # Exact clipboard readback protects long names the field clips.
        def copy():
            keys(0x11,0x41);desktop.cancelled.wait(.04);keys(0x11,0x43)
        actual=copied_text(NativeClipboard(binding.pid),copy,guard,desktop.cancelled.wait,expected=target)
        if actual!=target:raise DesktopTemporarilyHidden('搜索词回读不一致，停止定位')
        keys(0x27)
        previous=None
        for _ in range(2):
            desktop.cancelled.wait(.3);guard()
            point=W.POINT(info['rect'][0]+round(180*scale),info['rect'][1]+round(90*scale))
            candidate=U.GetAncestor(U.WindowFromPoint(point),2)
            desktop._search_diagnostic={'stage':'popup','separate':bool(candidate and candidate!=binding.hwnd)}
            if not candidate or candidate==binding.hwnd:return False
            pop=window_details(candidate)
            import win32gui
            native_client_origin=win32gui.ClientToScreen(candidate,(0,0))
            native_client_rect=win32gui.GetClientRect(candidate)
            desktop._search_diagnostic={'stage':'popup_bounds','rect':pop['rect'],'scale':scale}
            left,top,right,bottom=pop['rect'];l,t,r,b=info['rect']
            if (pop['pid']!=binding.pid or not l+50*scale<=left<=l+110*scale
                    or not t+40*scale<=top<=t+80*scale or right>min(r,l+600*scale) or bottom>b):return False
            if popup is not None and popup!=candidate:return False
            popup=candidate
            # Qt search results can reorder after the web-suggestion arrives.
            # PrintWindow may return the earlier cached popup while the visible
            # hit targets already moved. Navigation is foreground-only: read
            # the actual, unobstructed popup pixels instead of its paint cache.
            for px in range(left+30,right-29,32):
                for py in range(top+30,bottom-29,32):
                    if U.GetAncestor(U.WindowFromPoint(W.POINT(px,py)),2)!=popup:
                        raise DesktopTemporarilyHidden('搜索结果被遮挡，稍后重试')
            guard()
            shot=ImageGrab.grab(bbox=pop['rect'],all_screens=True)
            guard()
            lines=ocr(shot)
            desktop._search_diagnostic={'stage':'result','popup_rect':pop['rect'],'client_origin':native_client_origin,'client_rect':native_client_rect,'sections':[title_key(r.text) for r in lines if title_key(r.text) in SECTIONS],
                'matches':[(r.box,r.score) for r in lines if title_key(r.text)==title_key(target)]}
            found=search_result(lines,target,binding.target_kind,ocr_title=binding.ocr_title)
            if found is None:return False
            current=(pop['rect'],found.box)
            if previous is not None and current!=previous:return False
            previous=current
        guard()
        # Exact query is still focused. Confirm only when the exact contact is
        # the first displayed result, never a web/history suggestion. Enter is
        # confined to the search interaction, not the message composer.
        if not first_result_is(lines,found,binding.target_kind):return False
        # Enter can transfer focus into the destination's existing draft. No
        # destructive search cleanup is allowed after attempting confirmation.
        search_owned=False
        keys(0x0d)
        for _ in range(10):
            desktop.cancelled.wait(.1);guard()
            if not U.IsWindow(popup) or not U.IsWindowVisible(popup):break
        else:
            desktop._search_diagnostic['stage']='result_not_opened'
            return False
        focus=None
        desktop.navigation.note(desktop.input_generation(),idle_credit)
        desktop._input_cookie=None
        return True  # Engine still requires an exact full chat header before typing.
    except ClipboardUnavailable:
        raise DesktopTemporarilyHidden('剪贴板暂不可用于核对搜索，已清理自有搜索词，稍后重试') from None
    finally:
        # Only while the same search focus and our input generation still own
        # the field may we clear the exact query we inserted. Never touch draft.
        if focus is not None:
            guard()
            if search_owned:keys(0x11,0x41);keys(0x08)
            keys(0x1b)
