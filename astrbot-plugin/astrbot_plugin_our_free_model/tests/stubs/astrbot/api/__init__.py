"""astrbot.api 替身：只保留插件 main.py 用到的名字。"""

import logging

logger = logging.getLogger("astrbot.stub")


class AstrBotConfig(dict):
    """真实 AstrBotConfig 继承 dict，这里保持同样的行为。"""

    def save_config(self) -> None:
        self.saved = getattr(self, "saved", 0) + 1


class _SessionPersistence:
    """替身 sp：只需支持 session_put。"""

    def __init__(self) -> None:
        self.session: dict[tuple[str, str], object] = {}

    async def session_put(self, umo: str, key: str, value: object) -> None:
        self.session[(umo, key)] = value

    def get(self, *args, **kwargs):
        return None


sp = _SessionPersistence()

__all__ = ["AstrBotConfig", "logger", "sp"]
