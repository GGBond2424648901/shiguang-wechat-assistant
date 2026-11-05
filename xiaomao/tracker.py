"""仅从有重叠且连续两次一致的帧中承认新消息。"""
from .domain import Message
from collections import Counter
import logging


class AmbiguousFrame(ValueError):
    pass


class SequenceTracker:
    def __init__(self):
        self.baseline: tuple | None = None
        self.candidate: tuple | None = None
        self.boxes: tuple = ()
        self.qualities: tuple = ()

    def update(self, messages: list[Message] | tuple[Message, ...]) -> list[Message]:
        # 列表自然上移会把最顶部图片裁成 partial；它不是完整消息或可靠锚点。
        messages=[message for message in messages if message.kind!='partial']
        keys = tuple(m.key for m in messages)
        boxes = tuple(m.box for m in messages)
        qualities=tuple(m.quality for m in messages)
        if self.baseline is None:
            self.baseline = keys
            self.boxes = boxes
            self.qualities=qualities
            return []
        if keys == self.baseline:
            if any(old != (0,0,0,0) and new != (0,0,0,0) and new[1]-old[1] > 3 for old,new in zip(self.boxes,boxes)):
                raise AmbiguousFrame("消息位置改变，请回到底部重新观察")
            # 相同消息因时间标签/布局完成而整体上移，不产生新消息，也无需丢弃已确认任务。
            self.boxes=boxes
            self.qualities=qualities
            self.candidate = None
            return []
        before_count,after_count=Counter(self.baseline),Counter(keys)
        if len(keys)==len(self.baseline) and len(keys)>=3:
            changed=[i for i,(old,new) in enumerate(zip(self.baseline,keys)) if old!=new]
            fixed_boxes=all(old!=(0,0,0,0) and new!=(0,0,0,0) and max(abs(a-b) for a,b in zip(old,new))<=2 for old,new in zip(self.boxes,boxes))
            older_uncertain=all(self.baseline[i][:4]==keys[i][:4] and keys[i][2]=='text' and min(self.qualities[i],qualities[i])<.92 for i in changed)
            later_anchors={keys[i] for i in range(max(changed)+1,len(keys)) if keys[i]==self.baseline[i] and before_count[keys[i]]==after_count[keys[i]]==1 and min(self.qualities[i],qualities[i])>=.92}
            if fixed_boxes and older_uncertain and len(later_anchors)>=2:
                # 较早低置信度OCR变字、几何和后续两个可靠锚点不变，只刷新观测。
                # 不产生候选，不把这条旧文字当新消息；末尾变化仍按严格重叠规则处理。
                self.baseline,self.boxes,self.qualities=keys,boxes,qualities
                self.candidate=None
                return []
        overlap = 0
        for count in range(1, min(len(keys), len(self.baseline)) + 1):
            independent={key for key in keys[:count] if before_count[key]==after_count[key]==1}
            if self.baseline[-count:] == keys[:count] and len(independent)>=2:
                overlap = count
        if overlap < 2:
            logging.getLogger(__name__).warning('消息锚点不足: old_count=%s new_count=%s same_fields=%s old_boxes=%s new_boxes=%s',len(self.baseline),len(keys),[tuple(a==b for a,b in zip(old,new)) for old,new in zip(self.baseline,keys)],self.boxes,boxes)
            raise AmbiguousFrame("需要至少两个不同的重叠锚点，请回到底部后重新观察")
        for old,new in zip(self.boxes[-overlap:],boxes[:overlap]):
            if old != (0,0,0,0) and new != (0,0,0,0) and new[1]-old[1] > 3:
                raise AmbiguousFrame("检测到向上滚动，不能把历史当作新消息")
        tail = keys[overlap:]
        if not tail:
            raise AmbiguousFrame("出现滚动或重复消息，无法可靠判断新旧")
        if self.candidate != keys:
            self.candidate = keys
            return []
        self.baseline = keys
        self.boxes = boxes
        self.qualities=qualities
        self.candidate = None
        return list(messages[overlap:])
