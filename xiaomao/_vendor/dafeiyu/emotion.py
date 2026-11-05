"""Extracted verbatim from EmotionAI v3.4. See PROVENANCE.md."""
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class EmotionalState:
    """情感状态数据类"""
    joy: int = 0
    trust: int = 0
    fear: int = 0
    surprise: int = 0
    sadness: int = 0
    disgust: int = 0
    anger: int = 0
    anticipation: int = 0
    pride: int = 0
    guilt: int = 0
    shame: int = 0
    envy: int = 0

    favor: int = 0
    intimacy: int = 0
    relationship: str = "陌生人"
    attitude: str = "中立"
    is_blacklisted: bool = False

    interaction_count: int = 0
    last_interaction: float = 0
    positive_interactions: int = 0
    negative_interactions: int = 0

    show_status: bool = False
    show_thought: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmotionalState':
        return cls(**data)


class EmotionAnalyzer:
    EMOTION_DISPLAY_NAMES = {
        "joy": "喜悦", "trust": "信任", "fear": "恐惧", "surprise": "惊讶",
        "sadness": "悲伤", "disgust": "厌恶", "anger": "愤怒", "anticipation": "期待",
        "pride": "得意", "guilt": "内疚", "shame": "害羞", "envy": "嫉妒"
    }

    TONE_INSTRUCTIONS = {
        "joy": "语气愉快、充满热情和活力。多使用积极词汇。",
        "trust": "语气平和、真诚且令人安心。展现可靠。",
        "fear": "语气紧张、谨慎或不安。表现出犹豫。",
        "surprise": "语气震惊、难以置信或充满好奇。",
        "sadness": "语气低落、消沉。句子简短，无力。",
        "disgust": "语气厌烦、抗拒甚至带有生理性不适。",
        "anger": "语气愤怒、急躁、有攻击性。句子简短有力。",
        "anticipation": "语气期待、急切。关注未来。",
        "pride": "语气自信、骄傲甚至有点自大。",
        "guilt": "语气歉疚、卑微。不断道歉或解释。",
        "shame": "语气害羞、尴尬。说话结巴或含糊。",
        "envy": "语气酸溜溜、不服气。表现出矛盾心理。"
    }

    @classmethod
    def get_dominant_emotions(cls, state: EmotionalState, count: int = 2) -> List[Tuple[str, int]]:
        """获取主导情感（返回前N个）"""
        emotions = {k: getattr(state, k) for k in cls.EMOTION_DISPLAY_NAMES.keys()}
        return sorted([(k, v) for k, v in emotions.items() if v > 0], key=lambda x: x[1], reverse=True)[:count]

    @classmethod
    def get_emotional_profile(cls, state: EmotionalState) -> Dict[str, Any]:
        """获取完整的情感档案"""
        top_emotions = cls.get_dominant_emotions(state, 2)

        dominant_emotion = "中立"
        dominant_key = None
        if top_emotions:
            dominant_key = top_emotions[0][0]
            dominant_emotion = cls.EMOTION_DISPLAY_NAMES.get(dominant_key, "中立")

        secondary_emotion = None
        secondary_key = None
        if len(top_emotions) > 1:
            secondary_key = top_emotions[1][0]
            if top_emotions[1][1] > top_emotions[0][1] * 0.3:
                secondary_emotion = cls.EMOTION_DISPLAY_NAMES.get(secondary_key, "")

        intensity = top_emotions[0][1] if top_emotions else 0

        all_vals = [getattr(state, k) for k in cls.EMOTION_DISPLAY_NAMES.keys()]
        total_intensity = min(100, sum(all_vals) // 2)

        if state.favor > state.intimacy: relationship_trend = "好感领先"
        elif state.intimacy > state.favor: relationship_trend = "亲密度领先"
        else: relationship_trend = "平衡发展"

        total_interactions = state.interaction_count
        positive_ratio = (state.positive_interactions / total_interactions * 100) if total_interactions > 0 else 0

        return {
            "dominant_emotion": dominant_emotion,
            "dominant_key": dominant_key,
            "secondary_emotion": secondary_emotion,
            "secondary_key": secondary_key,
            "emotion_intensity": intensity,
            "total_intensity": total_intensity,
            "relationship_trend": relationship_trend,
            "positive_ratio": positive_ratio
        }
