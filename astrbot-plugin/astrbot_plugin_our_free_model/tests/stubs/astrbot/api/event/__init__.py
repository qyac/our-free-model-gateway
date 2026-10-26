"""astrbot.api.event 替身。"""

from . import filter


class AstrMessageEvent:
    """最小事件替身，只提供插件用到的两个属性。"""

    def __init__(self, message_str: str = "", umo: str = "stub:umo") -> None:
        self.message_str = message_str
        self.unified_msg_origin = umo
        self.sent: list[str] = []

    def plain_result(self, text: str):
        return ("plain", text)

    async def send(self, result) -> None:
        self.sent.append(result)


__all__ = ["AstrMessageEvent", "filter"]
