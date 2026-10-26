"""astrbot.api.star 替身。"""


class Context:
    def __init__(self, provider_manager=None) -> None:
        self.provider_manager = provider_manager


class Star:
    def __init__(self, context) -> None:
        self.context = context
