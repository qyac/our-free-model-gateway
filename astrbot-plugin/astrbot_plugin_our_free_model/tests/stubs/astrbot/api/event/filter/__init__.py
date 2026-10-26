"""astrbot.api.event.filter 替身：复刻装饰器的注册语义。

真实实现里 command_group 返回 RegisteringCommandable，其 .command 用来注册子指令；
这里用同名结构复现，使插件可以按真实写法被测。
"""

COMMANDS: dict[tuple[str, str], object] = {}
GROUPS: dict[str, "_Group"] = {}
HOOKS: list[object] = []


class _Group:
    def __init__(self, name: str) -> None:
        self.name = name
        self.commands: dict[str, object] = {}

    def command(self, name: str, **kwargs):
        def decorator(fn):
            self.commands[name] = fn
            COMMANDS[(self.name, name)] = fn
            return fn

        return decorator

    def group(self, name: str, **kwargs):
        return command_group(name, **kwargs)


def command(name: str, **kwargs):
    def decorator(fn):
        COMMANDS[("", name)] = fn
        return fn

    return decorator


def command_group(name: str, **kwargs):
    def decorator(fn):
        group = _Group(name)
        GROUPS[name] = group
        return group

    return decorator


def on_astrbot_loaded(**kwargs):
    def decorator(fn):
        HOOKS.append(fn)
        return fn

    return decorator


def reset() -> None:
    COMMANDS.clear()
    GROUPS.clear()
    HOOKS.clear()


__all__ = [
    "COMMANDS",
    "GROUPS",
    "HOOKS",
    "command",
    "command_group",
    "on_astrbot_loaded",
    "reset",
]
