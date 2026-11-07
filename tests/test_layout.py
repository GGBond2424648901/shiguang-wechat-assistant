from PIL import Image, ImageDraw
from xiaomao.config import Calibration


def layout_image(divider=319, toolbar=889, width=1431, height=1102):
    image=Image.new('RGB',(width,height),(237,237,237))
    draw=ImageDraw.Draw(image)
    draw.rectangle((68,60,divider-1,height-8),fill=(247,247,247))
    draw.line((divider,15,divider,height-8),fill=(213,213,213))
    draw.line((divider+1,109,width-8,109),fill=(213,213,213))
    draw.line((divider+1,toolbar,width-8,toolbar),fill=(213,213,213))
    return image


def old_binding():
    return Calibration(1,1,1431,1102,'示例成员交流讨论分享群(297)',{
        'header':[.287,.021,.7,.073], 'messages':[.279,.128,.995,.763],
        'input':[.288,.817,.98,.932], 'send':[.90,.944,.984,.985],
    },True,target_group='示例成员交流讨论分享群')


def test_current_pane_dividers_replace_stale_header_and_input_crops():
    from xiaomao.layout import adapt_calibration
    original=old_binding()
    result=adapt_calibration(layout_image(),original)
    header=result.rect('header');draft=result.rect('input')
    assert header[0]<335 and header[2]>560 and header[1]<32<header[3]<76
    assert draft[0]<345 and draft[1]>930 and draft[3]>1020
    assert result.rect('messages')[0]==321
    assert original.rect('header')[0]==411  # no mutation of persisted manual binding
    result.validate()


def test_dragged_sidebar_and_input_separator_are_detected_again():
    from xiaomao.layout import adapt_calibration
    first=adapt_calibration(layout_image(),old_binding())
    changed=adapt_calibration(layout_image(divider=400,toolbar=780),first)
    assert changed.rect('header')[0]<420
    assert changed.rect('messages')[0]==402
    assert 820<changed.rect('input')[1]<850


def test_incomplete_or_ambiguous_layout_never_invents_click_coordinates():
    from xiaomao.layout import adapt_calibration
    binding=old_binding()
    assert adapt_calibration(Image.new('RGB',(1431,1102),'white'),binding) is binding
    image=layout_image()
    ImageDraw.Draw(image).line((450,15,450,1094),fill=(213,213,213))
    assert adapt_calibration(image,binding) is binding


def test_small_window_selected_sidebar_row_can_be_darker_than_divider():
    from dataclasses import replace
    from xiaomao.layout import adapt_calibration
    image=layout_image(divider=319,toolbar=514,width=1038,height=727)
    # A selected recent chat is 68 pixels tall. In a smaller window that
    # exceeds 10% of the sampled separator, invalidating a two-sided minimum.
    ImageDraw.Draw(image).rectangle((68,272,318,340),fill=(211,211,211))
    binding=replace(old_binding(),width=1038,height=727)
    result=adapt_calibration(image,binding)
    assert result is not binding
    assert result.rect('messages')[0]==321


def test_detached_header_separator_survives_bubbles_touching_its_lower_edge():
    from dataclasses import replace
    from xiaomao.layout import adapt_calibration,default_calibration_regions
    image=Image.new('RGB',(612,727),(237,237,237));draw=ImageDraw.Draw(image)
    draw.line((8,100,603,100),fill=(213,213,213))
    # Clipped messages/avatars immediately below the header are normal in Qt.
    draw.rectangle((40,101,190,105),fill=(101,101,101))
    draw.line((8,514,603,514),fill=(213,213,213))
    b=replace(old_binding(),width=612,height=727,window_kind='detached',regions=default_calibration_regions('detached'))
    result=adapt_calibration(image,b)
    assert result is not b and result.rect('messages')[1]==102
    # An incomplete separator must still fail closed.
    draw.line((300,100,450,100),fill=(237,237,237))
    assert adapt_calibration(image,b) is b
