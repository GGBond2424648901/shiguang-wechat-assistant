"""本地 OCR 与聊天气泡识别；不把任意屏幕文字当作聊天正文。"""
from dataclasses import dataclass
import re
import hashlib
from collections import OrderedDict
import cv2
import numpy as np
from PIL import Image
from .config import Calibration, group_name
from .domain import Frame, Message, normalized


@dataclass(frozen=True)
class TextLine:
    text: str
    box: tuple[int, int, int, int]
    score: float


def exact_draft_line(lines):
    """Join adjacent OCR fragments of one line without inventing spaces."""
    if not lines or min(line.score for line in lines)<.96:return ''
    lines=sorted(lines,key=lambda line:line.box[0])
    height=min(line.box[3]-line.box[1] for line in lines)
    overlap=min(line.box[3] for line in lines)-max(line.box[1] for line in lines)
    if height<=0 or overlap<.75*height:return ''
    for left,right in zip(lines,lines[1:]):
        gap=right.box[0]-left.box[2]
        if not -.35*height<=gap<=.25*height:return ''
    return ''.join(line.text for line in lines)


def title_key(text: str) -> str:
    return re.sub(r"\s+", "", normalized(text))


def header_matches(label, binding):
    from .contacts import visual_name_key
    target=binding.target_group or group_name(binding.header_label)
    key=visual_name_key if binding.ocr_title else title_key
    suffix=r'\(\d+\)' if binding.target_kind=='group' else ''
    if suffix and getattr(binding,'window_kind','main')=='detached' and binding.ocr_title:
        suffix=r'(?:\(\d+\))?'  # directory identity and the exact native window caption corroborate this group
    return bool(re.fullmatch(re.escape(key(target))+suffix,key(label)))


def detached_title_matches(title,binding):
    """Native window text retains characters that visual OCR can lose, including emoji."""
    target=binding.target_group or group_name(binding.header_label)
    suffix=r'(?:\(\d+\))?' if binding.target_kind=='group' else ''
    return bool(target and re.fullmatch(re.escape(title_key(target))+suffix,title_key(title)))


def read_header(ocr, image):
    lines=ocr(image)
    if lines:
        if any(line.score<.92 for line in lines):
            # Small text beside color emoji can merge into a poor OCR token.
            # Enlarge the complete crop, retaining every suffix and title word.
            enhanced=[TextLine(r.text,tuple(round(v/3) for v in r.box),r.score)
                      for r in ocr(image.resize((image.width*3,image.height*3)))]
            if enhanced and min(r.score for r in enhanced)>min(r.score for r in lines):lines=enhanced
        return sorted(lines,key=lambda r:r.box[0])
    # A very wide, short crop can make OCR miss a two-character title.
    # Keep ALL contrasting pixels (including a possible member-count suffix),
    # trim only empty background, then add room around the complete title.
    pixels=np.asarray(image.convert('RGB'))
    colors,counts=np.unique(pixels.reshape(-1,3),axis=0,return_counts=True)
    background=colors[counts.argmax()]
    ys,xs=np.where(np.max(np.abs(pixels.astype(np.int16)-background),axis=2)>35)
    if not len(xs):return []
    left,top,right,bottom=int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)
    from PIL import ImageOps
    padded=ImageOps.expand(image.crop((left,top,right,bottom)),border=30,fill=tuple(map(int,background)))
    return sorted([TextLine(line.text,(line.box[0]+left-30,line.box[1]+top-30,
        line.box[2]+left-30,line.box[3]+top-30),line.score) for line in ocr(padded)],key=lambda r:r.box[0])


def calibration_send_matches(ocr,image,binding):
    """Require the complete send label at the actual click point of the reviewed crop."""
    crop=image.crop(binding.rect('send'))
    if crop.width<4 or crop.height<4:return False
    def matches(sample):
        lines=ocr(sample)
        if not lines or any(line.score<.9 for line in lines):return False
        text=title_key(''.join(line.text for line in sorted(lines,key=lambda r:r.box[0]))).casefold()
        if not re.fullmatch(r'(?:发送|send)(?:\((?:s|enter|ctrl\+enter)\))?',text):return False
        x1=min(line.box[0] for line in lines);x2=max(line.box[2] for line in lines)
        y1=min(line.box[1] for line in lines);y2=max(line.box[3] for line in lines)
        pad=max(1,(y2-y1)*.30)
        return x1-pad <= sample.width/2 <= x2+pad and y1-pad <= sample.height/2 <= y2+pad
    if matches(crop):return True
    # The disabled button is pale; resample its complete crop to retain short labels.
    from PIL import ImageOps
    enlarged=ImageOps.autocontrast(crop.convert('L')).convert('RGB').resize((crop.width*3,crop.height*3))
    return matches(enlarged)


def scrollbar_thumb(image,messages=()):
    """只取消息区最右侧窄灰色滚动滑块；边框和图片不作为底部证据。"""
    array=np.asarray(image.convert('RGB'));height,width=array.shape[:2]
    strip=max(14,round(width*.018));start=max(0,width-strip)
    pixels=array[:,start:]
    gray=(pixels.min(axis=2)>=135)&(pixels.max(axis=2)<=205)&(np.ptp(pixels,axis=2)<=6)
    count,_,stats,_=cv2.connectedComponentsWithStats(gray.astype(np.uint8),8)
    found=[]
    for x,y,w,h,area in stats[1:]:
        if 2<=w<=max(10,width*.012) and 16<=h<=height*.8 and area/(w*h)>.7:
            box=(int(start+x),int(y),int(start+x+w),int(y+h))
            cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
            if any(m.box[0]-3<=cx<=m.box[2]+3 and m.box[1]-3<=cy<=m.box[3]+3 for m in messages):continue
            sides=np.concatenate((array[y:y+h,max(0,box[0]-3):box[0]].reshape(-1,3),array[y:y+h,box[2]:min(width,box[2]+3)].reshape(-1,3)))
            if not len(sides) or np.mean((sides.min(axis=1)>=227)&(sides.max(axis=1)<=247)&(np.ptp(sides,axis=1)<=5))<.8:continue
            found.append(box)
    return found[0] if len(found)==1 else None


def navigation_lines(image,lines,messages=None):
    """只接受右侧白色导航徽标，聊天气泡中的同文不能定位滚动。"""
    messages=Vision._messages(image,lines) if messages is None else messages
    array=np.asarray(image.convert('RGB'));height,width=array.shape[:2]
    found=[]
    for line in lines:
        x1,y1,x2,y2=line.box;cx,cy=(x1+x2)/2,(y1+y2)/2
        if line.score<.92 or not re.fullmatch(r'\d+条新消息|回到底部|新消息[↓]?',title_key(line.text)):continue
        if cx<=width*.65 or not (y1<height*.25 or y2>height*.75):continue
        if any(m.box[0]<=cx<=m.box[2] and m.box[1]<=cy<=m.box[3] for m in messages):continue
        patch=array[max(0,y1-3):min(height,y2+4),max(0,x1-3):min(width,x2+4)]
        if patch.size and np.mean(np.all(patch>247,axis=2))>=.55:found.append(line)
    return found


class LocalOCR:
    def __init__(self):
        self._engine = None
        self._cache=OrderedDict()

    def __call__(self, image: Image.Image) -> list[TextLine]:
        rgb=image.convert('RGB')
        key=(rgb.size,hashlib.sha256(rgb.tobytes()).digest())
        if key in self._cache:
            self._cache.move_to_end(key)
            return list(self._cache[key])
        if self._engine is None:
            from rapidocr import RapidOCR
            self._engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2})
        array = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
        result = self._engine(array)
        if result.boxes is None or result.txts is None:
            lines=[]
        else:
            lines=[]
            for box, text, score in zip(result.boxes, result.txts, result.scores):
                xs, ys = box[:, 0], box[:, 1]
                lines.append(TextLine(text, (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), float(score)))
            lines.sort(key=lambda item:(item.box[1],item.box[0]))
        self._cache[key]=tuple(lines)
        if len(self._cache)>8:self._cache.popitem(last=False)
        return list(lines)


def _without_editor_caret(crop,background,scale):
    # The focused WeChat editor can blink a single green caret at its
    # leading edge between the two captures. No other ink is removed.
    r,g,b=crop.transpose(2,0,1)
    caret=(r<80)&(g>110)&(g>r+60)&(b<160)
    if caret.any():
        ys,xs=np.where(caret)
        if not (xs.max()-xs.min()+1<=max(3,3*scale) and 10*scale<=ys.max()-ys.min()+1<=34*scale
                and xs.min()<=12*scale and ys.min()<=4*scale):
            return None
        crop=crop.copy()
        crop[caret]=background
    return crop


def _halo_center(editor,background,scale):
    pixels=_without_editor_caret(editor,background,scale)
    if pixels is None:return None
    red,green,blue=(background-pixels).transpose(2,0,1)
    y,x=np.where(np.any(pixels!=background,axis=2))
    if not len(x) or y.min()!=0 or y.max()>54*scale or x.max()-x.min()>138*scale:return None
    # The observed overlay blends one light-blue tint into a neutral editor.
    # Its strongest cross-section is at the cropped top edge and fades down.
    if (red.min()<0 or not 44<=red.max()<=76 or np.max(np.abs(green-.43*red))>2.5
            or np.max(np.abs(blue+.10*red))>2.5):return None
    cy,cx=np.where(red>35)
    if (not len(cx) or cy.min()!=0 or not 18*scale<=cx.max()-cx.min()+1<=75*scale
            or not 5*scale<=cy.max()+1<=25*scale or len(np.unique(red[cy,cx]))<12):return None
    profile=red[0]
    center=int(np.argmax(profile))
    if (center==0 or center>=len(profile)-1 or np.diff(profile[:center+1]).min() < -2
            or np.diff(profile[center:]).max()>2 or np.diff(red,axis=0).max()>2
            or np.abs(np.diff(red,axis=1)).max()>6):return None
    return center


def possible_editor_halo(visible,binding,scale):
    if not .75<=scale<=3 or visible.size!=(binding.width,binding.height):return False
    crop=np.asarray(visible.crop(binding.rect('input')).convert('RGB'),dtype=np.int16)
    if min(crop.shape[:2])<2:return False
    return _halo_center(crop,crop[-1,-1],scale) is not None


def _message_difference(visible,painted,binding,center,scale):
    rect=binding.rect('messages')
    native=np.asarray(painted.crop(rect).convert('RGB'),dtype=np.int16)
    live=np.asarray(visible.crop(rect).convert('RGB'),dtype=np.int16)
    changed=np.any(native!=live,axis=2)
    bubble_fringe=np.zeros_like(changed)
    ys,xs=np.where(changed)
    if len(xs):
        left,top,_,_=binding.rect('input')
        colors=native[ys,xs]
        red,green,blue=(colors-live[ys,xs]).T
        # The faint part of the same desktop halo can reach the message
        # pane. Its location and RGB blend matter, not the pane's area.
        near_halo=(np.abs(xs+rect[0]-left-center)<=75*scale)&(top-ys-rect[1]<=140*scale)
        # The extreme feather can tint the rounded edge of an outgoing green
        # bubble by one RGB level. Require two identical live observations
        # before disregarding this very small patch in the message pane.
        r,g,b=colors.T
        fringe=(near_halo&(r>=149)&(r<236)&(g>=235)&(g<=237)&(b>=105)&(b<=r)
                &(red==1)&(green>=0)&(green<=1)&(blue>=-1)&(blue<=0))
        if (0<fringe.sum()<=64*scale**2 and np.ptp(xs[fringe])<=32*scale
                and np.ptp(ys[fringe])<=4*scale):
            bubble_fringe[ys[fringe],xs[fringe]]=True
        feather=(near_halo
                 &(colors.min(1)>=220)&(np.ptp(colors,axis=1)<=3)&(red>0)&(red<=90)
                 &(np.abs(green-.43*red)<=5)&(np.abs(blue+.10*red)<=5))
        if feather.any():
            profile=np.zeros(changed.shape,dtype=np.int16)
            profile[ys[feather],xs[feather]]=red[feather]
            # Require the same smooth feather to enter from the bottom of
            # the pane. A blue pixel near it cannot hide a changed message.
            if (profile[-1].max()==profile.max() and len(np.unique(red[feather]))>=4
                    and np.diff(profile,axis=0).min()>=-2 and np.abs(np.diff(profile,axis=1)).max()<=6):
                changed[ys[feather],xs[feather]]=False
    return live,native,changed,bubble_fringe


def _live_media_changes(first,last,native,changed,scale):
    # Two real desktop grabs must independently show motion in each patch
    # where the native rendition differs. Static text, a stale PrintWindow
    # image, or a change elsewhere in the chat must still veto the result.
    movement=np.any(first!=last,axis=2)
    nearby=cv2.dilate(movement.astype(np.uint8),np.ones((3,3),np.uint8))
    connected=cv2.dilate(changed.astype(np.uint8),np.ones((5,5),np.uint8))
    count,labels,stats,_=cv2.connectedComponentsWithStats(connected,8)
    for i in range(1,count):
        x,y,width,height,_=stats[i]
        patch=changed & (labels==i)
        if (not 32*scale<=width<=240*scale or not 20*scale<=height<=190*scale
                or patch.sum()<.22*width*height):return False
        # PrintWindow and the two desktop exposures sample different GIF
        # frames. A face may move while its background remains still during
        # the two desktop grabs. Prove motion for this specific media patch.
        moving_y,moving_x=np.where(movement & patch)
        if (len(moving_x)<max(32,.08*patch.sum()) or np.ptp(moving_x)<width*.25
                or np.ptp(moving_y)<height*.25):return False
        without_motion=patch[y:y+height,x:x+width] & (nearby[y:y+height,x:x+width]==0)
        native_tile=native[y:y+height,x:x+width]
        for scene in (first,last,native):
            pixels=scene[patch]
            if (np.ptp(pixels,axis=1)>=20).mean()<.20 or len(np.unique(pixels,axis=0))<24:return False
            if without_motion.any():
                # Unmoving differences must be inside the colorful image
                # on all three observations, not a neighboring text change.
                tile=scene[y:y+height,x:x+width]
                chroma=(np.ptp(tile,axis=2)>=20).astype(np.uint8)
                unsupported=without_motion & (cv2.dilate(chroma,np.ones((11,11),np.uint8))==0)
                if unsupported.any():
                    # A handful of gray edge pixels can round one or two RGB
                    # levels differently across animated frames. Accept only
                    # inside this proven media patch; never in the editor.
                    if unsupported.sum()>max(2,.005*patch.sum()):return False
                    for desktop in (first,last):
                        delta=desktop[y:y+height,x:x+width][unsupported]-native_tile[unsupported]
                        if np.abs(delta).max()>2:return False
    return True


def _matched_editor_scene(visible,painted,binding,scale):
    if not .75<=scale<=3 or visible.size!=painted.size or visible.size!=(binding.width,binding.height):return None
    for region,contrast in (('header',18),('send',12)):
        rect=binding.rect(region)
        anchor=np.asarray(painted.crop(rect).convert('RGB'))
        if (not anchor.size or np.ptp(anchor,axis=(0,1)).max()<contrast
                or not np.array_equal(anchor,np.asarray(visible.crop(rect).convert('RGB')))):return None
    editor_rect=binding.rect('input')
    native=np.asarray(painted.crop(editor_rect).convert('RGB'),dtype=np.int16)
    if min(native.shape[:2])<2:return None
    background=native[-1,-1]
    if background.min()<230 or background.max()>246 or np.ptp(background)>2:return None
    blank=_without_editor_caret(native,background,scale)
    if blank is None or np.any(blank!=background):return None
    center=_halo_center(np.asarray(visible.crop(editor_rect).convert('RGB'),dtype=np.int16),background,scale)
    if center is None:return None
    return _message_difference(visible,painted,binding,center,scale)


def editor_halo_verdict(visible,painted,binding,scale,*,closing=None):
    """Fixed diagnostic codes contain no text or image data."""
    scene=_matched_editor_scene(visible,painted,binding,scale)
    if scene is None:return 'first_scene_mismatch'
    first,native,changed,first_fringe=scene
    if closing is None:return 'motion_unconfirmed' if changed.any() else 'confirmed'
    final_scene=_matched_editor_scene(closing,painted,binding,scale)
    if final_scene is None:return 'closing_scene_mismatch'
    last,_,last_changed,last_fringe=final_scene
    same_fringe=first_fringe & last_fringe & np.all(first==last,axis=2)
    disputed=(changed | last_changed) & ~same_fringe
    if not disputed.any() or _live_media_changes(first,last,native,disputed,scale):return 'confirmed'
    return 'motion_unconfirmed'


def corroborates_editor_halo(visible,painted,binding,scale,*,closing=None):
    return editor_halo_verdict(visible,painted,binding,scale,closing=closing)=='confirmed'


class Vision:
    def __init__(self, recognizer=None):
        self.ocr = recognizer or LocalOCR()

    def read_controls(self,image,calibration):
        return self.read(image,calibration,controls_only=True)

    def read(self, image: Image.Image, calibration: Calibration,controls_only=False) -> Frame:
        if image.size != (calibration.width, calibration.height):
            return Frame("", safe=False, reason="窗口尺寸改变，请重新校准")
        header_crop=image.crop(calibration.rect("header"))
        header = read_header(self.ocr,header_crop)
        header_text = "".join(line.text for line in header)
        key = title_key(header_text)
        expected=calibration.target_group or group_name(calibration.header_label)
        if not header_matches(header_text,calibration):
            return Frame("", safe=False, reason="群标题与已校准目标不一致")
        if any(line.score < .92 for line in header):
            return Frame("", safe=False, reason="群标题识别不确定")
        detached=getattr(calibration,'window_kind','main')=='detached'
        if detached and not calibration_send_matches(self.ocr,image,calibration):
            return Frame("", safe=False, reason="独立聊天窗口发送区域无法确认，请检查校准")
        check_bottom=detached or not controls_only
        message_crop = image.crop(calibration.rect("messages")) if check_bottom else None
        lines = self.ocr(message_crop) if check_bottom else []
        draft_crop = image.crop(calibration.rect("input"))
        draft_lines = self.ocr(draft_crop)
        draft = " ".join(line.text for line in draft_lines)
        array = np.asarray(draft_crop.convert("RGB"))
        # 非文字图片、残留表情或输入法候选也不能被当作空草稿。
        values, counts = np.unique(array.reshape(-1, 3), axis=0, return_counts=True)
        background = values[counts.argmax()].astype(np.int16)
        ink = np.max(np.abs(array.astype(np.int16) - background), axis=2) > 35
        caret_color = (array[:, :, 0] < 80) & (array[:, :, 1] > 110) & (array[:, :, 1] > array[:, :, 0].astype(np.int16)+60) & (array[:, :, 2] < 160)
        caret_count, caret_labels, caret_stats, _ = cv2.connectedComponentsWithStats(caret_color.astype(np.uint8), 8)
        caret = np.zeros_like(ink)
        caret_components=0
        for index in range(1, caret_count):
            _, _, caret_width, caret_height, _ = caret_stats[index]
            if caret_width <= 3 and 12 <= caret_height <= 32:
                caret |= caret_labels == index
                caret_components+=1
        remaining_ink = ink & ~caret
        # 真实光标即使被裁到顶部也可能被 OCR 扩框后读成“一”。只有单条
        # 窄绿色光标且不存在任何其他墨迹时，按像素判空，不依赖误读字符。
        if caret_components==1 and not remaining_ink.any():
            draft = ""
        empty = not draft and not remaining_ink.any()
        if not check_bottom:return Frame(expected,(),draft,True,'',True,empty,key,exact_draft_line(draft_lines))
        messages = tuple(self._messages(message_crop, lines))
        thumb=scrollbar_thumb(message_crop,messages)
        at_bottom = calibration.bottom_confirmed and not navigation_lines(message_crop,lines,messages) and (thumb is None or thumb[3]>=message_crop.height-12)
        return Frame(expected, () if controls_only else messages, draft, True, "", at_bottom, empty, key,exact_draft_line(draft_lines))

    @staticmethod
    def _messages(image: Image.Image, lines: list[TextLine]) -> list[Message]:
        array = np.asarray(image.convert("RGB"))
        height, width = array.shape[:2]
        r, g, b = [array[:, :, i].astype(np.int16) for i in range(3)]
        white = (r > 247) & (g > 247) & (b > 247)
        green = (g > 175) & (g > r + 25) & (g > b + 40) & (r > 65)
        # 原生窗口最外几列可能是边框、滚动条或下方窗口的阴影。取内部边带
        # 的实际众数颜色，不能把两侧不同颜色平均成不存在的“背景”。
        inset=min(8,max(0,(width-2)//4))
        edge_pixels = np.concatenate((array[:,inset:inset+8].reshape(-1,3),array[:,width-inset-8:width-inset].reshape(-1,3)))
        colors,frequency=np.unique(edge_pixels,axis=0,return_counts=True)
        background = colors[frequency.argmax()].astype(np.float32)
        foreground = np.max(np.abs(array.astype(np.float32)-background),axis=2) > 10
        _, parent_labels, parent_stats, _ = cv2.connectedComponentsWithStats(foreground.astype(np.uint8),8)
        opaque_parents = set()
        messages = []
        for mask, direction in ((white, "in"), (green, "out")):
            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
            for component in range(1, count):
                x, y, w, h, area = map(int, stats[component])
                if w < 24 or h < 22 or area < 400 or w > width * .94:
                    continue
                # 头像、居中的系统提示，以及方向不明确的卡片不当作文字气泡。
                if direction == "in" and x > width * .4:
                    continue
                if direction == "out" and x + w < width * .65:
                    continue
                block = labels[y:y+h, x:x+w] == component
                edges = []
                for row in block:
                    columns = np.flatnonzero(row)
                    if len(columns):
                        edges.append(int(columns[0] if direction == "in" else columns[-1]))
                typical = int(np.median(edges)) if edges else 0
                protrusion = typical - min(edges) if direction == "in" else max(edges) - typical
                has_tail = protrusion >= 4
                fully_visible = x > 2 and x+w < width-2 and y > 2 and y+h < height-2
                body_lines = [line for line in lines if x <= (line.box[0] + line.box[2]) / 2 < x+w and y <= (line.box[1] + line.box[3]) / 2 < y+h]
                text = " ".join(line.text for line in sorted(body_lines, key=lambda l: (l.box[1], l.box[0])))
                quality = min((line.score for line in body_lines), default=0)
                sender_lines = [line for line in lines if 0 <= y-line.box[3] <= 32 and abs(line.box[0] - x) < 45]
                sender = max(sender_lines, key=lambda line: line.box[3]).text if sender_lines and direction == "in" else ""
                pixels = array[y:y+h, x:x+w]
                gray = (pixels.min(axis=2) >= 215) & (pixels.max(axis=2) <= 233) & (np.ptp(pixels, axis=2) < 5)
                quote_count, _, quote_stats, _ = cv2.connectedComponentsWithStats(gray.astype(np.uint8),8)
                quoted = any(qw >= 40 and qh >= 6 and qa >= 160 for _,_,qw,qh,qa in quote_stats[1:])
                sample_y,sample_x = np.argwhere(block)[len(np.argwhere(block))//2]
                parent = int(parent_labels[y+sample_y,x+sample_x])
                px,py,pw,ph,_ = map(int,parent_stats[parent])
                nested = parent == 0 or px < x-3 or py < y-3 or px+pw > x+w+3 or py+ph > y+h+3
                # 实际聊天气泡紧邻头像列；图片内部的嵌套气泡没有独立外层消息边界。
                rail=width*.10
                on_rail = x <= rail if direction == "in" else x+w >= width-rail
                kind = "text" if has_tail and fully_visible and body_lines and area/(w*h) > .65 and not nested and on_rail else "image"
                # 顶部气泡正文可完整但群昵称已出裁剪框；不能把它当成换了发送人的锚点。
                if not fully_visible or (direction=='in' and y<=32 and not sender):
                    kind = "partial"
                if kind != "text":
                    text = f"[{kind}]"  # 图片里的 OCR 文字不能触发回复。
                    parent_key=(parent,direction)
                    if parent_key in opaque_parents:
                        continue
                    opaque_parents.add(parent_key)
                messages.append(Message(text, sender, direction, kind, quoted, quality, (x, y, x+w, y+h)))
        return sorted(messages, key=lambda m: m.box[1])
