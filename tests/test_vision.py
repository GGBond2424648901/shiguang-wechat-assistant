from PIL import Image, ImageDraw
import pytest


def binding():
    from xiaomao.config import Calibration
    return Calibration(1, 1, 800, 600, "示例会话(2)", {
        "header": [0, 0, 1, .1], "messages": [0, .1, 1, .75],
        "input": [0, .8, .8, .95], "send": [.85, .9, .98, .98],
    }, True)


def test_local_ocr_reuses_identical_pixels_but_invalidates_after_any_change():
    from types import SimpleNamespace
    import numpy as np
    from xiaomao.vision import LocalOCR
    calls=[];ocr=LocalOCR()
    def engine(array):
        calls.append(array.shape)
        return SimpleNamespace(boxes=np.array([[[1,1],[5,1],[5,5],[1,5]]]),txts=['正文'],scores=[.99])
    ocr._engine=engine
    image=Image.new('RGB',(100,100),'white')
    first=ocr(image);first.clear()
    assert ocr(image.copy())[0].text=='正文' and len(calls)==1
    image.putpixel((50,50),(0,0,0));ocr(image)
    assert len(calls)==2


class RecordedOCR:
    def __init__(self, messages=(), draft=(), header="示例会话 (2)"):
        from xiaomao.vision import TextLine
        self.outputs = [
            [TextLine(header, (10, 10, 200, 35), .99)], list(messages), list(draft)
        ]

    def __call__(self, image):
        return self.outputs.pop(0)


@pytest.mark.parametrize('top,bottom,expected',[(150,190,False),(405,449,True)])
def test_visible_scrollbar_away_from_bottom_blocks_latest_confirmation(top,bottom,expected):
    from xiaomao.vision import Vision,scrollbar_thumb
    image=screenshot();draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((791,top,796,bottom),2,fill=(174,174,174))
    crop=image.crop(binding().rect('messages'))
    assert scrollbar_thumb(crop) is not None
    assert Vision(RecordedOCR()).read(image,binding()).at_bottom is expected


def test_window_edge_and_picture_colors_are_not_scrollbar():
    from xiaomao.vision import scrollbar_thumb
    image=Image.new('RGB',(800,390),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rectangle((798,0,799,389),fill=(174,174,174))
    draw.rectangle((600,150,720,190),fill=(174,174,174))
    assert scrollbar_thumb(image) is None


def test_gray_strip_inside_outgoing_image_is_not_a_scrollbar():
    from xiaomao.vision import scrollbar_thumb,Vision
    image=Image.new('RGB',(800,390),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rectangle((540,90,796,181),fill=(149,236,105))
    draw.rectangle((790,105,794,146),fill=(174,174,174))
    messages=Vision._messages(image,[])
    assert messages and scrollbar_thumb(image,messages) is None


def screenshot(own=False, tail=True):
    image = Image.new("RGB", (800, 600), (237, 237, 237))
    draw = ImageDraw.Draw(image)
    if own:
        draw.rounded_rectangle((540, 140, 740, 200), 5, fill=(149, 236, 105))
        if tail:
            draw.polygon([(739, 153), (747, 157), (739, 163)], fill=(149, 236, 105))
    else:
        draw.rounded_rectangle((60, 140, 300, 200), 5, fill="white")
        if tail:
            draw.polygon([(61, 153), (53, 157), (61, 163)], fill="white")
    return image


def test_nickname_outside_bubble_cannot_be_a_trigger():
    from xiaomao.vision import Vision, TextLine
    frame = Vision(RecordedOCR([
        TextLine("小猫", (60, 52, 90, 70), .99),
        TextLine("普通聊天", (74, 95, 170, 116), .99),
    ])).read(screenshot(), binding())
    assert frame.safe
    assert frame.messages[0].sender == "小猫"
    assert frame.messages[0].text == "普通聊天"


@pytest.mark.parametrize("own,expected", [(True, "out"), (False, "in")])
def test_bubble_geometry_identifies_direction(own, expected):
    from xiaomao.vision import Vision, TextLine
    box = (550, 95, 710, 116) if own else (74, 95, 250, 116)
    frame = Vision(RecordedOCR([TextLine("小猫你好", box, .99)])).read(screenshot(own), binding())
    assert frame.messages[0].direction == expected


def test_image_without_speech_tail_is_not_accepted_as_text():
    from xiaomao.vision import Vision, TextLine
    frame = Vision(RecordedOCR([TextLine("小猫，图片上的字", (74, 95, 260, 116), .99)])).read(screenshot(tail=False), binding())
    assert frame.messages[0].kind != "text"


def test_quote_area_marks_bubble_non_replyable():
    from xiaomao.vision import Vision, TextLine
    image = screenshot()
    ImageDraw.Draw(image).rectangle((70, 170, 290, 195), fill=(225, 225, 225))
    frame = Vision(RecordedOCR([TextLine("小猫，旧引用", (74, 95, 250, 116), .99)])).read(image, binding())
    assert frame.messages[0].quoted


def test_empty_ocr_does_not_make_a_picture_draft_empty():
    from xiaomao.vision import Vision
    image = screenshot()
    ImageDraw.Draw(image).rectangle((50, 500, 100, 550), fill="blue")
    frame = Vision(RecordedOCR()).read(image, binding())
    assert not frame.draft_empty


def test_group_mismatch_prevents_sending():
    from xiaomao.vision import Vision
    frame = Vision(RecordedOCR(header="示例会话聊天记录")).read(screenshot(), binding())
    assert not frame.safe


def test_top_bubble_with_clipped_sender_is_partial_not_a_changed_anchor():
    from xiaomao.vision import Vision,TextLine
    image=Image.new('RGB',(800,600),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((60,69,180,105),5,fill='white')
    draw.polygon([(61,80),(53,85),(61,90)],fill='white')
    frame=Vision(RecordedOCR([TextLine('@小明白',(74,80,160,99),.99)])).read(image,binding())
    assert len(frame.messages)==1
    assert frame.messages[0].kind=='partial'


def test_size_change_invalidates_calibration():
    from xiaomao.vision import Vision
    frame = Vision(RecordedOCR()).read(Image.new("RGB", (900, 600)), binding())
    assert not frame.safe


def test_green_blinking_caret_is_not_a_user_draft():
    from xiaomao.vision import Vision, TextLine
    image = screenshot()
    ImageDraw.Draw(image).rectangle((10, 490, 11, 507), fill=(0, 153, 88))
    frame = Vision(RecordedOCR(draft=[TextLine("I", (9, 9, 13, 30), .7)])).read(image, binding())
    assert frame.draft_empty


def test_top_clipped_green_caret_misread_as_chinese_one_is_empty():
    from xiaomao.vision import Vision,TextLine
    image=screenshot()
    ImageDraw.Draw(image).rectangle((6,480,7,496),fill=(7,193,96))
    frame=Vision(RecordedOCR(draft=[TextLine('一',(0,0,11,20),.8595)])).read(image,binding())
    assert frame.draft_empty and frame.draft==''


def test_real_small_draft_next_to_caret_is_never_discarded():
    from xiaomao.vision import Vision,TextLine
    image=screenshot();draw=ImageDraw.Draw(image)
    draw.rectangle((6,480,7,496),fill=(7,193,96))
    draw.rectangle((12,490,14,492),fill='black')
    frame=Vision(RecordedOCR(draft=[TextLine('一',(0,0,11,20),.8595)])).read(image,binding())
    assert not frame.draft_empty


def test_even_an_ocr_missed_period_is_a_nonempty_draft():
    from xiaomao.vision import Vision
    image = screenshot()
    ImageDraw.Draw(image).rectangle((12, 501, 14, 503), fill="black")
    assert not Vision(RecordedOCR()).read(image, binding()).draft_empty


def test_exact_draft_line_orders_high_confidence_runs_without_inserting_spaces():
    from xiaomao.vision import TextLine,exact_draft_line
    left=TextLine('借到就好，',(0,2,84,19),.99)
    right=TextLine('今晚河边这趟稳了',(86,1,202,19),.99)
    assert exact_draft_line([right,left])=='借到就好，今晚河边这趟稳了'
    assert exact_draft_line([left,TextLine(right.text,(86,26,202,46),.99)])==''
    assert exact_draft_line([left,TextLine(right.text,right.box,.71)])==''
    assert exact_draft_line([left,TextLine(right.text,(100,1,202,19),.99)])==''


def overlay_pair():
    """A clipped desktop-only blue halo, on a calibrated live-looking scene."""
    import numpy as np
    b=binding()
    painted=Image.new('RGB',(b.width,b.height),(237,237,237))
    draw=ImageDraw.Draw(painted)
    for name in ('header','send','messages'):
        x,y,_,_=b.rect(name)
        draw.rectangle((x+10,y+5,x+62,y+14),fill=(66,66,66))
    x,y,_,_=b.rect('input')
    pixels=np.array(painted)
    yy,xx=np.indices(pixels.shape[:2])
    # Continuous radial alpha, just above the top of the editor.
    depth=np.maximum(0,1-np.sqrt(((xx-x-310)/68)**2+((yy-y+8)/42)**2))
    depth=np.where(depth>.05,depth,0)
    for i,amount in enumerate((-72,-31,7)):
        pixels[:,:,i]=np.rint(pixels[:,:,i]+amount*depth).clip(0,255)
    return Image.fromarray(pixels),painted,b


@pytest.mark.parametrize('change,allowed',[('halo',True),('caret',True),('painted_text',False),
    ('blue_sticker',False),('solid_overlay',False),('header',False),('send',False),('stale_message',False),
    ('tiny_editor_dot',False),('faint_message_dot',False),('black',False),('size',False),('floating_blue',False)])
def test_only_a_matching_clipped_gradient_over_an_empty_native_editor_can_clear_the_draft(change,allowed):
    from xiaomao.vision import corroborates_editor_halo
    visible,painted,b=overlay_pair()
    x,y,_,_=b.rect('input')
    if change=='caret':ImageDraw.Draw(visible).rectangle((x+5,y,x+6,y+18),fill=(7,193,96))
    if change=='painted_text':ImageDraw.Draw(painted).text((x+120,y+35),'user draft',fill='black')
    if change=='blue_sticker':
        for im in (visible,painted):ImageDraw.Draw(im).rectangle((x+10,y+20,x+60,y+53),fill='#a5d3f4')
    if change=='solid_overlay':
        visible=painted.copy()
        ImageDraw.Draw(visible).rectangle((x+280,y,x+328,y+14),fill='#b0d3f3')
    if change in ('header','send','stale_message'):
        left,top,_,_=b.rect('messages' if change=='stale_message' else change)
        ImageDraw.Draw(painted).rectangle((left+80,top+12,left+84,top+15),fill=(60,60,60))
    if change=='tiny_editor_dot':visible.putpixel((x+30,y+30),(210,210,210))
    if change=='faint_message_dot':visible.putpixel((x+272,y-110),(231,234,238))
    if change=='black':painted=Image.new('RGB',painted.size)
    if change=='size':painted=painted.resize((640,480))
    if change=='floating_blue':
        visible=painted.copy()
        halo,_,_=overlay_pair()
        visible.paste(halo.crop((x+225,y,x+395,y+42)),(x+225,y+30))
    assert corroborates_editor_halo(visible,painted,b,1.0) is allowed


@pytest.mark.parametrize('change,expected',[('latest_native',True),('three_frames',True),('cached_native',False),
    ('new_text',False),('changed_editor',False),('changed_header',False)])
def test_two_live_frames_can_attest_an_animation_but_not_a_stale_native_scene(change,expected):
    import numpy as np
    from xiaomao.vision import corroborates_editor_halo
    visible,painted,b=overlay_pair()
    closing=visible.copy()
    yy,xx=np.indices((65,116))
    def animation(im,offset):
        cells=np.stack([(xx*3+offset)%113+20,(yy*3+offset)%124+25,(xx+yy+offset)%148+22],axis=2)
        im.paste(Image.fromarray(cells.astype('uint8')),(435,202))
    animation(visible,0);animation(painted,25)
    animation(closing,0 if change=='cached_native' else 48 if change=='three_frames' else 25)
    if change=='new_text':ImageDraw.Draw(closing).text((80,230),'new group message',fill='black')
    if change=='changed_editor':ImageDraw.Draw(closing).text((44,525),'draft',fill='black')
    if change=='changed_header':ImageDraw.Draw(closing).text((110,18),'another group',fill='black')
    assert corroborates_editor_halo(visible,painted,b,1.0,closing=closing) is expected


@pytest.mark.parametrize('scene,expected',[('moving_face',True),('gray_roundoff',True),
    ('gray_edit',False),('static',False),('text_outside',False),('static_strip',False)])
def test_native_gif_frame_may_differ_in_parts_still_during_the_live_interval(scene,expected):
    import numpy as np
    from xiaomao.vision import corroborates_editor_halo
    visible,painted,b=overlay_pair()
    closing=visible.copy()
    yy,xx=np.indices((76,128))
    original=np.stack([(xx+2*yy)%130+22,(2*xx+yy)%150+32,(xx+3*yy)%140+40],axis=2).astype('uint8')
    middle=original+22
    last=original.copy()
    if scene!='static':
        last[18:57,20:97]+=13
    for image,pixels in ((visible,original),(painted,middle),(closing,last)):
        image.paste(Image.fromarray(pixels),(425,210))
    if scene in ('gray_roundoff','gray_edit'):
        for image,gray in ((visible,60),(painted,80),(closing,77)):
            draw=ImageDraw.Draw(image)
            draw.rectangle((425,210,444,235),fill=(gray,gray,gray))
            draw.rectangle((425,210,434,219),fill=(51,51,51))
        painted.putpixel((433,218),(52,51,51) if scene=='gray_roundoff' else (109,51,51))
    if scene=='text_outside':
        ImageDraw.Draw(painted).text((377,192),'stale',fill='black')
    if scene=='static_strip':
        # A stale changed text block touching the media isn't part of the GIF.
        ImageDraw.Draw(painted).rectangle((551,230,576,253),fill=(75,75,75))
    assert corroborates_editor_halo(visible,painted,b,1,closing=closing) is expected


@pytest.mark.parametrize('case,expected',[('fringe',True),('moving',False),('strong',False),
                                          ('outside',False),('wide',False),('another_color',False)])
def test_halo_tint_on_sent_green_bubble_needs_same_tiny_fringe_on_both_live_grabs(case,expected):
    from xiaomao.vision import corroborates_editor_halo
    visible,painted,b=overlay_pair()
    closing=visible.copy()
    x=310 if case!='outside' else 70
    box=(x,399,x+(80 if case=='wide' else 12),400)
    base=(149,236,105) if case!='another_color' else (149,121,105)
    tint=(base[0]-(3 if case=='strong' else 1),base[1],base[2]+1)
    ImageDraw.Draw(painted).rectangle(box,fill=base)
    ImageDraw.Draw(visible).rectangle(box,fill=tint)
    ImageDraw.Draw(closing).rectangle(box,fill=base if case=='moving' else tint)
    assert not corroborates_editor_halo(visible,painted,b,1)
    assert corroborates_editor_halo(visible,painted,b,1,closing=closing) is expected


def test_chat_screenshot_inside_an_image_is_not_an_incoming_text_bubble():
    from xiaomao.vision import Vision,TextLine
    image=Image.new('RGB',(800,600),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rectangle((60,140,360,350),fill=(175,168,190))
    draw.rounded_rectangle((90,185,300,240),5,fill='white')
    draw.polygon([(91,200),(82,208),(91,216)],fill='white')
    frame=Vision(RecordedOCR([TextLine('小猫，截图里的旧消息',(103,140,285,160),.99)])).read(image,binding())
    assert not any(m.kind=='text' for m in frame.messages)


def test_small_quote_in_large_bubble_does_not_trigger_at_reply():
    from xiaomao.vision import Vision,TextLine
    from xiaomao.policy import TriggerPolicy
    image=screenshot();draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((60,140,300,410),5,fill='white')
    draw.rectangle((70,370,290,383),fill=(225,225,225))
    frame=Vision(RecordedOCR([TextLine('普通聊天',(74,95,170,116),.99),TextLine('@张三',(80,312,180,322),.99)])).read(image,binding())
    assert not any(TriggerPolicy('小猫','张三').matches(m) for m in frame.messages)


def test_horizontally_clipped_bubble_is_not_replyable_text():
    from xiaomao.vision import Vision,TextLine
    image=screenshot();draw=ImageDraw.Draw(image)
    draw.rectangle((60,140,810,210),fill='white')
    frame=Vision(RecordedOCR([TextLine('小猫，右侧被裁切',(74,95,270,116),.99)])).read(image,binding())
    assert not any(m.kind=='text' for m in frame.messages)


@pytest.mark.parametrize('own',[False,True])
def test_window_edge_shadow_cannot_merge_all_bubbles_into_one_picture(own):
    from xiaomao.vision import Vision,TextLine
    image=screenshot(own);draw=ImageDraw.Draw(image)
    draw.rectangle((797,60,799,449),fill=(203,206,214))
    box=(550,95,710,116) if own else (74,95,250,116)
    frame=Vision(RecordedOCR([TextLine('小玲你好',box,.99)])).read(image,binding())
    assert any(m.kind=='text' and m.text=='小玲你好' and m.direction==('out' if own else 'in') for m in frame.messages)


@pytest.mark.parametrize('own',[False,True])
def test_bubble_rail_tracks_uniform_window_scale(own):
    from xiaomao.vision import Vision,TextLine
    image=Image.new('RGB',(820,560),(237,237,237));draw=ImageDraw.Draw(image)
    if own:
        draw.rounded_rectangle((500,100,747,140),5,fill=(149,236,105));draw.polygon([(746,112),(755,117),(746,122)],fill=(149,236,105));box=(510,110,720,132)
    else:
        draw.rounded_rectangle((72,100,300,140),5,fill='white');draw.polygon([(73,112),(65,117),(73,122)],fill='white');box=(90,110,250,132)
    scaled=image.resize((1025,700),Image.Resampling.NEAREST)
    lines=[TextLine('普通问题',tuple(round(x*1.25) for x in box),.99)]
    assert any(m.kind=='text' and m.text=='普通问题' for m in Vision._messages(scaled,lines))


def test_real_white_navigation_pill_is_distinct_from_chat_message():
    from xiaomao.vision import navigation_lines,TextLine
    image=Image.new('RGB',(820,560),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((655,20,805,56),16,fill='white')
    line=TextLine('46条新消息',(680,28,795,48),.99)
    assert navigation_lines(image,[line])==[line]
    image=Image.new('RGB',(820,560),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((650,460,795,505),5,fill=(149,236,105));draw.polygon([(794,472),(803,477),(794,482)],fill=(149,236,105))
    assert navigation_lines(image,[TextLine('46条新消息',(662,470,785,493),.99)])==[]


def test_upward_unread_indicator_does_not_jump_into_old_history():
    from xiaomao.vision import navigation_lines,TextLine
    canvas=Image.new('RGB',(820,560),(237,237,237));draw=ImageDraw.Draw(canvas)
    draw.rounded_rectangle((655,20,805,56),16,fill='white')
    line=TextLine('10条新消息',(667,26,794,47),.99)
    for y in (30,36):
        draw.line([(669,y+5),(674,y),(679,y+5)],fill=(28,170,102),width=2)
    assert navigation_lines(canvas,[line])==[]
