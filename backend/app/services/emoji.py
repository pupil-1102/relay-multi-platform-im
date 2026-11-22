"""Feishu text-emoji normalization.

Feishu clients send some emoji as bracketed codes such as ``[OK]`` or
``[微笑]`` instead of Unicode. Relay stores the text and renders it in the
web client, so convert the common codes to Unicode while ingesting.
"""
from __future__ import annotations

# Keep longer codes first so [开心] is not partially replaced by [开].
_FEISHU_EMOJI: dict[str, str] = {
    "[微笑]": "😊",
    "[大笑]": "😄",
    "[开心]": "😄",
    "[笑哭]": "😂",
    "[捂脸]": "🤦",
    "[赞]": "👍",
    "[强]": "👍",
    "[OK]": "👌",
    "[ok]": "👌",
    "[鼓掌]": "👏",
    "[握手]": "🤝",
    "[爱心]": "❤️",
    "[心]": "❤️",
    "[玫瑰]": "🌹",
    "[庆祝]": "🎉",
    "[烟花]": "🎉",
    "[加油]": "💪",
    "[抱拳]": "🙏",
    "[谢谢]": "🙏",
    "[调皮]": "😜",
    "[眨眼]": "😉",
    "[色]": "😍",
    "[飞吻]": "😘",
    "[思考]": "🤔",
    "[疑问]": "❓",
    "[晕]": "😵",
    "[哭]": "😭",
    "[流泪]": "😭",
    "[睡]": "😴",
    "[困]": "😴",
    "[发呆]": "😳",
    "[酷]": "😎",
    "[得意]": "😏",
    "[坏笑]": "😏",
    "[偷笑]": "🤭",
    "[嘘]": "🤫",
    "[灵光一闪]": "💡",
    "[火]": "🔥",
    "[火箭]": "🚀",
    "[礼物]": "🎁",
    "[红包]": "🧧",
    "[咖啡]": "☕",
    "[蛋糕]": "🎂",
    "[西瓜]": "🍉",
    "[啤酒]": "🍺",
    "[干杯]": "🍻",
    "[来看我]": "👀",
    "[看]": "👀",
    "[围观]": "👀",
    "[互粉]": "🤝",
    "[中国赞]": "👍",
    "[比心]": "🫰",
    "[耶]": "✌️",
    "[胜利]": "✌️",
    "[拳头]": "👊",
    "[勾引]": "😏",
    "[汗]": "😓",
    "[尴尬]": "😅",
    "[惊呆]": "😲",
    "[哈欠]": "🥱",
    "[白眼]": "🙄",
    "[奸笑]": "😏",
    "[亲亲]": "😚",
    "[可怜]": "🥺",
    "[委屈]": "🥺",
    "[快哭了]": "😢",
    "[黑线]": "😓",
    "[擦汗]": "😅",
    "[皱眉]": "😟",
    "[鼓掌]": "👏",
    "[左右为难]": "😣",
    "[献吻]": "😘",
    "[惊吓]": "😱",
    "[惊讶]": "😮",
    "[口水]": "🤤",
    "[花痴]": "😍",
    "[表情]": "🙂",
}


def normalize_feishu_emoji(text: str) -> str:
    if not text:
        return text
    for code, char in _FEISHU_EMOJI.items():
        if code in text:
            text = text.replace(code, char)
    return text
