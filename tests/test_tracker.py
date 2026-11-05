import pytest


def msg(text, **fields):
    from xiaomao.domain import Message
    return Message(text, "朋友", **fields)


def test_baseline_and_two_observations_prevent_replaying_history():
    from xiaomao.tracker import SequenceTracker
    tracker = SequenceTracker()
    anchor, old, new = msg("更早的独立锚点"), msg("小猫，旧消息"), msg("小猫，新消息")
    assert tracker.update([anchor, old]) == []
    assert tracker.update([anchor, old, new]) == []
    assert tracker.update([anchor, old, new]) == [new]
    assert tracker.update([anchor, old, new]) == []


def test_shifted_visible_tail_with_overlap_is_new():
    from xiaomao.tracker import SequenceTracker
    tracker = SequenceTracker()
    a, b, c, d = msg("第一句"), msg("第二句"), msg("第三句"), msg("小猫你好")
    tracker.update([a, b, c])
    tracker.update([b, c, d])
    assert tracker.update([b, c, d]) == [d]


@pytest.mark.parametrize("before,after", [
    (["你好"], ["另一段历史", "小猫你好"]),
    (["你好"], ["你好", "你好"]),
    (["a", "b"], ["a"]),
])
def test_ambiguous_scroll_or_repeated_text_stops_tracking(before, after):
    from xiaomao.tracker import SequenceTracker, AmbiguousFrame
    tracker = SequenceTracker()
    tracker.update([msg(x) for x in before])
    with pytest.raises(AmbiguousFrame):
        tracker.update([msg(x) for x in after])


def test_empty_baseline_cannot_safely_classify_first_message():
    from xiaomao.tracker import SequenceTracker, AmbiguousFrame
    tracker = SequenceTracker()
    tracker.update([])
    with pytest.raises(AmbiguousFrame):
        tracker.update([msg("小猫你好")])


def test_repeated_overlap_anchor_cannot_replay_an_old_addressed_message():
    from xiaomao.tracker import SequenceTracker, AmbiguousFrame
    tracker=SequenceTracker()
    a,b,c=msg("重复锚点"),msg("另一条"),msg("小猫，旧历史")
    tracker.update([a,b,a])
    with pytest.raises(AmbiguousFrame):
        tracker.update([a,c])


def test_only_one_overlap_anchor_is_insufficient_evidence():
    from xiaomao.tracker import SequenceTracker, AmbiguousFrame
    tracker=SequenceTracker()
    a,b,c=msg("a"),msg("b"),msg("小猫你好")
    tracker.update([a,b])
    with pytest.raises(AmbiguousFrame):
        tracker.update([b,c])


def test_message_anchors_moving_downward_indicate_upward_scroll():
    from xiaomao.tracker import SequenceTracker, AmbiguousFrame
    tracker=SequenceTracker()
    a=msg("a",box=(50,100,150,140));b=msg("b",box=(50,160,150,200))
    tracker.update([a,b])
    with pytest.raises(AmbiguousFrame):
        tracker.update([msg("a",box=(50,140,150,180)),msg("b",box=(50,200,150,240)),msg("小猫旧消息")])


def test_repeated_new_mention_is_allowed_with_two_unique_anchors():
    from xiaomao.tracker import SequenceTracker
    tracker=SequenceTracker();a,b,q=msg('唯一锚点甲'),msg('唯一锚点乙'),msg('@小玲你好')
    tracker.update([a,b,q]);tracker.update([a,b,q,q])
    assert tracker.update([a,b,q,q])==[q]


def test_repeated_pattern_without_two_unique_anchors_remains_ambiguous():
    from xiaomao.tracker import SequenceTracker,AmbiguousFrame
    tracker=SequenceTracker();a,b=msg('重复甲'),msg('重复乙')
    tracker.update([a,b])
    with pytest.raises(AmbiguousFrame):tracker.update([a,b,a,b,msg('@小玲你好')])


def test_top_image_clipped_by_reply_scroll_does_not_break_text_anchors():
    from xiaomao.tracker import SequenceTracker
    tracker=SequenceTracker()
    image=msg('[image]',kind='image')
    a,b,q=msg('小玲在不在'),msg('@小凶许'),msg('@小玲你好')
    tracker.update([image,a,b,q])
    own=msg('你好喵',direction='out')
    after=[msg('[partial]',kind='partial'),a,b,q,own]
    assert tracker.update(after)==[]
    assert tracker.update(after)==[own]
    assert tracker.update(after)==[]


def test_unchanged_messages_settling_upward_does_not_pause_or_create_new_messages():
    from dataclasses import replace
    from xiaomao.tracker import SequenceTracker
    tracker=SequenceTracker()
    before=[msg('a',box=(50,100,150,140)),msg('b',box=(50,170,150,210))]
    tracker.update(before)
    settled=[replace(m,box=(m.box[0],m.box[1]-33,m.box[2],m.box[3]-33)) for m in before]
    assert tracker.update(settled)==[]
    assert tracker.update(settled)==[]


def test_unchanged_messages_moving_downward_still_reject_upward_history_scroll():
    from dataclasses import replace
    from xiaomao.tracker import SequenceTracker,AmbiguousFrame
    tracker=SequenceTracker()
    before=[msg('a',box=(50,100,150,140)),msg('b',box=(50,170,150,210))]
    tracker.update(before)
    with pytest.raises(AmbiguousFrame):tracker.update([replace(m,box=(50,m.box[1]+33,150,m.box[3]+33)) for m in before])


def test_low_confidence_older_ocr_change_with_fixed_boxes_and_two_later_anchors_is_not_new():
    from dataclasses import replace
    from xiaomao.tracker import SequenceTracker
    tracker=SequenceTracker()
    before=[msg('旧字甲',quality=.82,box=(50,40,130,85)),msg('独立乙',box=(50,200,160,245)),msg('独立丙',box=(50,345,170,390)),msg('[image]',kind='image',quality=0,box=(50,440,120,520)),msg('独立丁',box=(50,620,200,665))]
    tracker.update(before)
    corrected=[replace(before[0],text='旧字乙',quality=.8),*before[1:]]
    assert tracker.update(corrected)==[]
    assert tracker.update(corrected)==[]
    new=msg('普通新问题',box=(50,680,200,725))
    assert tracker.update([*corrected,new])==[]
    assert tracker.update([*corrected,new])==[new]


@pytest.mark.parametrize('change',['confident','latest','moved'])
def test_ocr_correction_cannot_mask_newest_text_or_history_scroll(change):
    from dataclasses import replace
    from xiaomao.tracker import SequenceTracker,AmbiguousFrame
    before=[msg('甲',quality=.8,box=(50,40,150,85)),msg('乙',box=(50,200,150,245)),msg('丙',box=(50,345,150,390))]
    if change=='confident':before[0]=replace(before[0],quality=.99)
    tracker=SequenceTracker();tracker.update(before);after=list(before)
    if change=='latest':after[-1]=replace(after[-1],text='新问题',quality=.8)
    else:after[0]=replace(after[0],text='改变',box=(50,80,150,125) if change=='moved' else after[0].box)
    with pytest.raises(AmbiguousFrame):tracker.update(after)
