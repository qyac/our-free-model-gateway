"""插件自测：用 astrbot 替身 + 假 Nami / 假 OFM 跑通主要路径。

运行： python tests/selftest.py      （需要 aiohttp、pyyaml）

不依赖真实 AstrBot；替身只复刻被测代码用到的 API 形状（见 tests/stubs/）。

链路是两跳的，所以这里有两个假入口：

    AstrBot ← 插件 → 假 Nami（含 /healthz、/readyz）→（真实网络里才是）OFM
    AstrBot ← 插件 → 假 OFM（只有 OpenAI 兼容接口）

两者协议相同、只是 Nami 多两个健康探针，所以共用 FakeGateway，由 `health`
参数决定扮演哪个角色。
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR / "stubs"))
sys.path.insert(0, str(PLUGIN_DIR))

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event import filter as stub_filter
from astrbot.api.star import Context
from astrbot.core.provider.entities import ProviderType

import main as plugin_mod

OFM_KEY = "ofm-local-key-123"
NAMI_KEY = "nami-api-key-456"
GATEWAY_MODELS = [
    "a/b",
    "a-b",
    "claude-opus-5",
    "deepseek-v4.1-flash",
    "glm-5",
    "gpt-6",
    "kimi-k3",
    "mimo-v2.6-flash-free",
    "qwen4-max",
]

RESULTS: list[tuple[bool, str]] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(condition), label if not detail else f"{label} — {detail}"))


# --------------------------------------------------------------- 假 OpenAI 入口


class FakeGateway:
    """一个 OpenAI 兼容入口的替身。

    `health=None` → 只是 OpenAI 兼容端点（假 OFM）；
    `health={...}` → 额外提供 /healthz 与 /readyz（假 Nami）。
    `/v1/models` 一律要求 Bearer Key，与真假两边的行为一致。
    """

    def __init__(
        self,
        models: list[str],
        require_key: str | None = None,
        health: dict | None = None,
    ) -> None:
        self.models = models
        self.require_key = require_key
        self.health = health
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # 静音
                pass

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if not outer.require_key:
                    return True
                expected = f"Bearer {outer.require_key}"
                return self.headers.get("Authorization") == expected

            def do_GET(self) -> None:
                path = self.path.rstrip("/")
                outer.requests.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization", ""),
                    },
                )

                # 只有假 Nami 才有的两个探针：它们是**无凭证**的。
                if outer.health is not None and path in ("/healthz", "/readyz"):
                    if path == "/healthz":
                        self._json(200, {"status": "ok", "version": "1.0.0"})
                        return
                    ready = bool(outer.health.get("ready", True))
                    self._json(
                        200 if ready else 503,
                        {
                            "ready": ready,
                            "checks": {
                                "database": "ok",
                                "provider": outer.health.get("provider", "ofm"),
                                "model": outer.health.get("model", "glm-5"),
                                "models": outer.health.get("models", "3 available"),
                                "tools": "8/16 enabled",
                                "apiKeys": "configured",
                            },
                        },
                    )
                    return

                if path != "/v1/models":
                    self._json(404, {"error": {"message": "no route"}})
                    return
                if not self._authorized():
                    self._json(401, {"error": {"message": "unauthorized"}})
                    return
                rows = [{"id": m} for m in outer.models]
                self._json(200, {"object": "list", "data": rows})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    payload = {}
                outer.requests.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "payload": payload,
                        "authorization": self.headers.get("Authorization", ""),
                    },
                )
                if self.path.rstrip("/") != "/v1/chat/completions":
                    self._json(404, {"error": {"message": "no route"}})
                    return
                if not self._authorized():
                    self._json(401, {"error": {"message": "unauthorized"}})
                    return
                if payload.get("model") not in outer.models:
                    self._json(400, {"error": {"message": "model not found"}})
                    return
                self._json(
                    200,
                    {
                        "id": "chatcmpl-stub",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "pong"},
                            },
                        ],
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def host_root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------- 替身 ProviderManager


class FakeProviderManager:
    def __init__(self) -> None:
        self.providers_config: list[dict] = []
        self.loaded: dict[str, dict] = {}
        self.selected: tuple[str, str | None] | None = None

    def get_provider_config_by_id(self, provider_id: str) -> dict | None:
        for item in self.providers_config:
            if item.get("id") == provider_id:
                return dict(item)
        return None

    async def create_provider(self, config: dict) -> None:
        if self.get_provider_config_by_id(config["id"]):
            raise AssertionError(f"duplicate provider id {config['id']}")
        self.providers_config.append(dict(config))
        self.loaded[config["id"]] = dict(config)

    async def update_provider(self, origin_id: str, config: dict) -> None:
        for index, item in enumerate(self.providers_config):
            if item.get("id") == origin_id:
                self.providers_config[index] = dict(config)
                break
        else:
            raise AssertionError(f"provider {origin_id} not found")
        self.loaded[origin_id] = dict(config)

    async def delete_provider(
        self,
        provider_id: str | None = None,
        provider_source_id=None,
    ) -> None:
        self.providers_config = [
            item for item in self.providers_config if item.get("id") != provider_id
        ]
        self.loaded.pop(provider_id, None)

    async def set_provider(self, provider_id, provider_type, umo=None) -> None:
        assert provider_type is ProviderType.CHAT_COMPLETION
        self.selected = (provider_id, umo)


def make_plugin(
    endpoint_base_url: str,
    manager: FakeProviderManager,
    mode: str | None = "nami",
    **overrides,
):
    """构造插件：`endpoint_base_url` 会被填进当前模式对应的那个地址字段。

    `mode=None` 表示**不写** relay_mode 键，用来验证默认值。
    """
    config = {
        "gateway_base_url": plugin_mod.DEFAULT_OFM_BASE_URL,
        "gateway_api_key": OFM_KEY,
        "nami_base_url": plugin_mod.DEFAULT_NAMI_BASE_URL,
        "nami_api_key": NAMI_KEY,
        "models": [],
        "max_models": 8,
        "provider_enable": True,
        "auto_register": True,
        "request_timeout": 10,
        "chat_timeout": 300,
    }
    if mode is not None:
        config["relay_mode"] = mode
    if mode == "direct":
        config["gateway_base_url"] = endpoint_base_url
    else:
        config["nami_base_url"] = endpoint_base_url
    config.update(overrides)
    return plugin_mod.OurFreeModelPlugin(Context(manager), config)


async def run_command(name: str, plugin, event: AstrMessageEvent, **kwargs) -> list:
    handler = stub_filter.COMMANDS[("ofm", name)]
    produced = handler(plugin, event, **kwargs)
    if hasattr(produced, "__aiter__"):
        return [item async for item in produced]
    return [await produced]


def texts(results: list) -> str:
    return "\n".join(item[1] for item in results)


# ------------------------------------------------------------------- 测试用例


async def test_registration(nami: FakeGateway) -> None:
    manager = FakeProviderManager()
    manager.providers_config.append({"id": "openai", "type": "openai_chat_completion"})
    plugin = make_plugin(nami.base_url, manager)

    result = await plugin.sync()
    check(len(result["created"]) == 8, "sync 按 max_models=8 注册", str(result["created"]))
    check(len(result["available"]) == len(GATEWAY_MODELS), "读到入口全部模型")
    check(all(pid.startswith("ofm-") for pid in result["created"]), "提供商 ID 带 ofm- 前缀")

    sample = manager.get_provider_config_by_id(result["created"][0])
    check(sample["provider"] == "openai", "复用 openai 适配器")
    check(sample["type"] == "openai_chat_completion", "provider type 正确")
    check(sample["provider_type"] == "chat_completion", "provider_type 正确")
    check(sample["api_base"] == nami.base_url, "api_base 指向 Nami 入口", sample["api_base"])
    check(sample["key"] == [NAMI_KEY], "key 用的是 Nami 的 API Key", str(sample["key"]))
    check(sample["enable"] is True, "默认启用")
    check(sample["timeout"] == 300, "timeout 取自 chat_timeout")
    check(
        sample["modalities"] == [],
        "modalities 为空列表（AstrBot 只有看到空列表才不限能力，否则会丢图片）",
        str(sample.get("modalities")),
    )
    check("model" in sample and sample["model"], "model 字段非空", str(sample.get("model")))

    ofm_ids = [p["id"] for p in manager.providers_config if p["id"].startswith("ofm-")]
    check(len(ofm_ids) == len(set(ofm_ids)) == 8, "8 个提供商 ID 唯一", str(sorted(ofm_ids)))
    check(
        len([pid for pid in result["created"] if pid.startswith("ofm-a-b")]) == 2,
        "slug 撞车的两个模型各注册一次",
    )

    again = await plugin.sync()
    check(
        not again["created"] and not again["updated"] and len(again["unchanged"]) == 8,
        "重复 sync 幂等",
    )

    plugin.config["chat_timeout"] = 180
    drifted = await plugin.sync()
    check(len(drifted["updated"]) == 8, "配置变化触发更新", str(len(drifted["updated"])))
    updated = manager.get_provider_config_by_id(drifted["updated"][0])
    check(updated["timeout"] == 180, "更新后的 timeout 生效")


async def test_whitelist_and_enable(nami: FakeGateway) -> None:
    manager = FakeProviderManager()
    plugin = make_plugin(
        nami.base_url,
        manager,
        models=["kimi-k3", "glm-5"],
        provider_enable=False,
    )
    result = await plugin.sync()
    check(len(result["created"]) == 2, "白名单只注册指定模型", str(result["created"]))
    check(
        all(p["enable"] is False for p in manager.providers_config),
        "provider_enable=False 时注册为未启用",
    )

    manager2 = FakeProviderManager()
    plugin2 = make_plugin(nami.base_url, manager2, models="kimi-k3, glm-5")
    result2 = await plugin2.sync()
    check(len(result2["created"]) == 2, "models 支持逗号分隔字符串")

    # 白名单 + 入口没开：仍应把白名单注册好（离线预置）
    offline = FakeProviderManager()
    plugin3 = make_plugin(
        f"http://127.0.0.1:{closed_port()}/v1",
        offline,
        models=["glm-5"],
    )
    offline_result = await plugin3.sync()
    check(
        offline_result["created"] == [plugin_mod.provider_id_for("glm-5")],
        "入口不可达时白名单仍注册",
        str(offline_result["created"]),
    )

    # 显式 /ofm use 必须能用：即便 provider_enable=False 也要把这一个打开
    use_event = AstrMessageEvent("ofm use kimi-k3", umo="group:off")
    used = texts(await run_command("use", plugin, use_event, model="kimi-k3"))
    check("已切换到 kimi-k3" in used, "provider_enable=False 时 use 仍可用", used)
    enabled = manager.get_provider_config_by_id(plugin_mod.provider_id_for("kimi-k3"))
    check(enabled is not None and enabled["enable"] is True, "use 会把该提供商置为启用")
    check(
        manager.selected == (plugin_mod.provider_id_for("kimi-k3"), "group:off"),
        "use 写入会话选择",
    )


async def test_commands(nami: FakeGateway) -> None:
    manager = FakeProviderManager()
    plugin = make_plugin(nami.base_url, manager)
    event = AstrMessageEvent("ofm models", umo="group:1")

    listed = texts(await run_command("models", plugin, event))
    check("kimi-k3" in listed, "/ofm models 列出模型")
    check("免费模型 9 个" in listed, "/ofm models 统计数量", listed.splitlines()[0])

    synced = texts(await run_command("sync", plugin, event))
    check("新增 8" in synced, "/ofm sync 有结果", synced)
    check("Nami 中转" in synced, "/ofm sync 报出当前模式", synced)

    listed = texts(await run_command("models", plugin, event))
    marked = [line for line in listed.splitlines() if line.startswith("✓")]
    check(len(marked) == 8, "/ofm models 标出已注册模型", str(len(marked)))
    check(
        any(line.startswith("· qwen4-max") for line in listed.splitlines()),
        "/ofm models 未注册模型不标记",
        listed,
    )

    status = texts(await run_command("status", plugin, event))
    check("入口可达" in status, "/ofm status 报告入口可达", status)
    check("已注册提供商：8 个" in status, "/ofm status 报告数量")

    use_event = AstrMessageEvent("ofm use deepseek-v4.1-flash", umo="group:1")
    used = texts(
        await run_command("use", plugin, use_event, model="deepseek-v4.1-flash"),
    )
    check("已切换到 deepseek-v4.1-flash" in used, "/ofm use 切换会话模型", used)
    check(
        manager.selected == (plugin_mod.provider_id_for("deepseek-v4.1-flash"), "group:1"),
        "会话模型落在 unified_msg_origin 上",
        str(manager.selected),
    )

    new_event = AstrMessageEvent("ofm use kimi-k3", umo="group:2")
    await run_command("use", plugin, new_event, model="kimi-k3")
    check(
        manager.get_provider_config_by_id(plugin_mod.provider_id_for("kimi-k3")) is not None,
        "/ofm use 未注册模型时按需注册",
    )

    # 回归：slug 撞车的模型必须各切各的（曾经按单模型分配 ID 会切到另一个模型）
    collision_event = AstrMessageEvent("ofm use a-b", umo="group:3")
    await run_command("use", plugin, collision_event, model="a-b")
    collision_provider = manager.get_provider_config_by_id(
        plugin_mod.provider_id_for("a-b"),
    )
    check(
        collision_provider is not None and collision_provider["model"] == "a-b",
        "use 撞车模型时指向的是同一个模型",
        str(collision_provider),
    )
    check(
        manager.selected is not None and manager.selected[0] == plugin_mod.provider_id_for("a-b"),
        "use 写入的提供商 ID 与注册时一致",
        str(manager.selected),
    )

    tested = texts(await run_command("test", plugin, new_event, model="kimi-k3"))
    check("可用" in tested and "pong" in tested, "/ofm test 探测成功", tested)
    posted = [r for r in nami.requests if r["method"] == "POST"]
    check(
        posted and posted[-1]["authorization"] == f"Bearer {NAMI_KEY}",
        "/ofm test 带上 Nami 的 Bearer Key",
    )

    probed_default = texts(await run_command("test", plugin, new_event))
    check("可用" in probed_default, "/ofm test 默认选已注册模型", probed_default)

    manager.providers_config.append({"id": "keep-me", "type": "openai_chat_completion"})
    cleaned = texts(await run_command("cleanup", plugin, new_event))
    check("已移除" in cleaned, "/ofm cleanup 有结果", cleaned)
    check(
        [p["id"] for p in manager.providers_config] == ["keep-me"],
        "cleanup 只删本插件注册的提供商",
        str([p["id"] for p in manager.providers_config]),
    )


async def test_relay_mode_selection(ofm: FakeGateway, nami: FakeGateway) -> None:
    """默认模式、地址规范化、以及非法模式的处理。"""

    # 不写 relay_mode：默认必须是 Nami 中转（这次改动的核心决定）
    default_plugin = make_plugin("http://127.0.0.1:8787", FakeProviderManager(), mode=None)
    check(default_plugin.relay_mode == "nami", "未配置 relay_mode 时默认走 Nami 中转")
    check(
        default_plugin.base_url == "http://127.0.0.1:8787/v1",
        "少写 /v1 会自动补上",
        default_plugin.base_url,
    )
    check(default_plugin.host_root == "http://127.0.0.1:8787", "host_root 去掉 /v1")

    weird = make_plugin("http://127.0.0.1:8787", FakeProviderManager(), relay_mode="banana")
    check(weird.relay_mode == "nami", "非法 relay_mode 回退到 nami")

    # 两种模式各自读写自己的地址与 Key，不能互相串
    direct = make_plugin(ofm.base_url, FakeProviderManager(), mode="direct")
    check(direct.relay_mode == "direct", "relay_mode=direct 生效")
    check(direct.base_url == ofm.base_url, "direct 模式用 gateway_base_url", direct.base_url)
    check(direct.api_key == OFM_KEY, "direct 模式用 gateway_api_key", direct.api_key)

    relayed = make_plugin(nami.base_url, FakeProviderManager())
    check(relayed.base_url == nami.base_url, "nami 模式用 nami_base_url", relayed.base_url)
    check(relayed.api_key == NAMI_KEY, "nami 模式用 nami_api_key", relayed.api_key)

    # 规范化：根地址、带斜杠、带 /v1、带子路径、两侧空白
    cases = [
        ("http://h:8787", "http://h:8787/v1"),
        ("http://h:8787/", "http://h:8787/v1"),
        ("http://h:8787/v1", "http://h:8787/v1"),
        ("http://h:8787/v1/", "http://h:8787/v1"),
        ("  http://h:8787/v1  ", "http://h:8787/v1"),
        ("https://box/nami", "https://box/nami/v1"),
        ("https://box/nami/v1", "https://box/nami/v1"),
    ]
    for raw, want in cases:
        got = plugin_mod.normalize_base_url(raw, "fallback")
        check(got == want, f"normalize_base_url({raw!r})", got)

    check(
        plugin_mod.normalize_base_url("", "fallback") == "fallback",
        "空配置回退到默认地址",
    )
    check(
        plugin_mod.host_root_of("https://box/nami/v1") == "https://box/nami",
        "带子路径时 host_root 只剥掉 /v1",
    )


async def test_nami_relay(nami: FakeGateway) -> None:
    """中转模式：提供商指向 Nami，/ofm status 把整条链路报出来。"""

    manager = FakeProviderManager()
    plugin = make_plugin(nami.base_url, manager)
    event = AstrMessageEvent("ofm status", umo="group:7")

    await plugin.sync()
    status = texts(await run_command("status", plugin, event))

    check("模式：Nami 中转" in status, "/ofm status 报出模式", status)
    check(f"入口：{nami.base_url}" in status, "/ofm status 报出入口地址", status)
    check("鉴权：已配置 Key" in status, "/ofm status 报出鉴权状态")
    check("Nami /healthz：HTTP 200" in status, "读到 Nami 存活探针", status)
    check("Nami /readyz：HTTP 200" in status, "读到 Nami 就绪探针", status)
    check(
        "Nami 上游：ofm / glm-5（3 available）" in status,
        "报出 Nami 的上游 provider 与默认模型",
        status,
    )
    check("入口可达，模型 9 个" in status, "/ofm status 报出入口模型数", status)

    probed = [r for r in nami.requests if r["method"] == "GET" and "/healthz" in r["path"]]
    check(probed and probed[0]["authorization"] == "", "健康探针不带凭证（Nami 不需要）")

    # Nami 起得来但上游断了：/readyz 503，应把这一点说出来
    upstream_down = FakeGateway(
        GATEWAY_MODELS,
        require_key=NAMI_KEY,
        health={"ready": False},
    )
    try:
        plugin_down = make_plugin(upstream_down.base_url, FakeProviderManager())
        status_down = texts(await run_command("status", plugin_down, event))
        check("Nami /readyz：HTTP 503" in status_down, "上游断时报告 503", status_down)
        check(
            "Nami 上游" not in status_down,
            "未就绪时不谎报上游信息",
            status_down,
        )
        check("入口可达，模型 9 个" in status_down, "Nami 的模型清单仍可读")
    finally:
        upstream_down.stop()

    # Nami 当前上游不是 ofm：要给出明确提示，而不是让人盯着 404 猜
    wrong_upstream = FakeGateway(
        GATEWAY_MODELS,
        require_key=NAMI_KEY,
        health={"provider": "mock", "model": "nami-mock-1", "models": "1 available"},
    )
    try:
        plugin_wrong = make_plugin(wrong_upstream.base_url, FakeProviderManager())
        status_wrong = texts(await run_command("status", plugin_wrong, event))
        check("Nami 上游：mock / nami-mock-1" in status_wrong, "如实报出上游", status_wrong)
        check(
            "npm run ofm -- --write" in status_wrong,
            "上游不是 ofm 时提示怎么修",
            status_wrong,
        )
    finally:
        wrong_upstream.stop()


async def test_direct_mode(ofm: FakeGateway) -> None:
    """直连模式：还认得 OFM 网关，且不去探测 Nami。"""

    manager = FakeProviderManager()
    plugin = make_plugin(ofm.base_url, manager, mode="direct")
    event = AstrMessageEvent("ofm status", umo="group:8")

    result = await plugin.sync()
    sample = manager.get_provider_config_by_id(result["created"][0])
    check(sample["api_base"] == ofm.base_url, "直连模式 api_base 指向 OFM", sample["api_base"])
    check(sample["key"] == [OFM_KEY], "直连模式用 OFM 本机 Key", str(sample["key"]))

    status = texts(await run_command("status", plugin, event))
    check("模式：OFM 直连" in status, "报出直连模式", status)
    check("Nami /healthz" not in status, "直连模式不做 Nami 探针", status)
    check("入口可达，模型 9 个" in status, "直连模式同样报入口状态", status)

    tested = texts(await run_command("test", plugin, event, model="kimi-k3"))
    check("可用" in tested and "pong" in tested, "直连模式 /ofm test 成功", tested)
    posted = [r for r in ofm.requests if r["method"] == "POST"]
    check(
        posted and posted[-1]["authorization"] == f"Bearer {OFM_KEY}",
        "直连模式带上 OFM 本机 Key",
    )


async def test_mode_switch_is_in_place(ofm: FakeGateway, nami: FakeGateway) -> None:
    """切换模式必须就地改写同一批提供商，而不是新建一批或留下孤儿。"""

    manager = FakeProviderManager()
    plugin = make_plugin(nami.base_url, manager)

    first = await plugin.sync()
    relayed = manager.get_provider_config_by_id(first["created"][0])
    check(relayed["api_base"] == nami.base_url, "初始为中转模式", relayed["api_base"])

    plugin.config["relay_mode"] = "direct"
    plugin.config["gateway_base_url"] = ofm.base_url
    second = await plugin.sync()

    check(not second["created"], "换模式不新建提供商", str(second["created"]))
    check(
        len(second["updated"]) == len(first["created"]),
        "换模式就地改写同一批提供商",
        str(len(second["updated"])),
    )
    check(
        [p["id"] for p in manager.providers_config] == first["created"],
        "提供商 ID 不随模式变化",
        str([p["id"] for p in manager.providers_config]),
    )
    direct = manager.get_provider_config_by_id(first["created"][0])
    check(direct["api_base"] == ofm.base_url, "改写后指向 OFM", direct["api_base"])
    check(direct["key"] == [OFM_KEY], "改写后换成 OFM 本机 Key", str(direct["key"]))

    # 会话上已经选过的提供商仍然有效：切模式不会让 /ofm use 白选
    use_event = AstrMessageEvent("ofm use kimi-k3", umo="group:9")
    await run_command("use", plugin, use_event, model="kimi-k3")
    check(
        manager.selected == (plugin_mod.provider_id_for("kimi-k3"), "group:9"),
        "切模式后会话选择仍然指向同一个提供商",
        str(manager.selected),
    )


async def test_endpoint_down() -> None:
    manager = FakeProviderManager()
    plugin = make_plugin(f"http://127.0.0.1:{closed_port()}/v1", manager)

    try:
        await plugin.sync()
    except Exception as exc:
        check("入口返回 HTTP" not in str(exc), "端口关闭时给出连接类错误", str(exc)[:120])
    else:
        check(False, "端口关闭时 sync 应当抛错")

    hook = stub_filter.HOOKS[0]
    try:
        await hook(plugin)
    except Exception as exc:  # on_loaded 必须吞掉异常，不能拖垮启动
        check(False, "on_loaded 不应抛出异常", str(exc)[:120])
    else:
        check(True, "on_loaded 吞掉入口不可用异常")

    event = AstrMessageEvent("ofm status")
    status = texts(await run_command("status", plugin, event))
    check("入口不可达" in status, "/ofm status 报告入口不可达", status)
    check(
        "Nami /healthz：不可达" in status,
        "/ofm status 把不可达归到健康探针上",
        status,
    )
    check("鉴权：已配置 Key" in status, "不可达时仍照常报配置")

    bad_key_gateway = FakeGateway(GATEWAY_MODELS, require_key="other-key")
    plugin_bad = make_plugin(bad_key_gateway.base_url, FakeProviderManager())
    unusable = texts(await run_command("test", plugin_bad, event, model="kimi-k3"))
    check("401" in unusable, "Key 不对时报 401", unusable)
    bad_key_gateway.stop()


async def test_pure_helpers() -> None:
    # ID 只由模型 ID 决定：同名同 ID、异名异 ID、与列表顺序无关
    check(
        plugin_mod.provider_id_for("kimi-k3") == plugin_mod.provider_id_for("kimi-k3"),
        "provider_id_for 稳定",
    )
    ids = {plugin_mod.provider_id_for(model) for model in ("a/b", "a-b", "a b", "a-b-2")}
    check(len(ids) == 4, "slug 冲突的模型各有独立 ID", str(sorted(ids)))
    check(
        all(pid.startswith("ofm-a-b") for pid in ids),
        "ID 仍保留可读的 slug 前缀",
        str(sorted(ids)),
    )
    check(
        plugin_mod.provider_id_for("kimi-k3").startswith("ofm-kimi-k3-"),
        "ID 形如 ofm-<slug>-<摘要>",
        plugin_mod.provider_id_for("kimi-k3"),
    )

    check(
        plugin_mod.extract_trailing_arg(AstrMessageEvent("/ofm use kimi-k3")) == "kimi-k3",
        "extract_trailing_arg 处理唤醒前缀",
    )
    check(
        plugin_mod.extract_trailing_arg(AstrMessageEvent("ofm use a/b c")) == "a/b c",
        "extract_trailing_arg 保留模型 ID 内的斜杠与空格",
    )
    check(
        plugin_mod.extract_trailing_arg(AstrMessageEvent("ofm status"), "fallback") == "fallback",
        "extract_trailing_arg 无参时回退",
    )


def test_package_files() -> None:
    metadata_path = PLUGIN_DIR / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    for field in ("name", "desc", "version", "author"):
        check(bool(metadata.get(field)), f"metadata.yaml 含 {field}")
    check(metadata["name"] == PLUGIN_DIR.name, "metadata.name 与目录名一致")
    check(
        str(metadata.get("astrbot_version", "")).startswith(">="),
        "astrbot_version 是 PEP 440 范围",
    )
    check(
        "Nami" in str(metadata.get("desc", "")) or "Nami" in str(metadata.get("short_desc", "")),
        "metadata 里写明了 Nami 中转",
    )

    schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8"))
    check(schema["gateway_api_key"]["secret"] is True, "OFM Key 配置标记 secret")
    check(schema["nami_api_key"]["secret"] is True, "Nami Key 配置标记 secret")
    check(
        set(schema)
        >= {
            "relay_mode",
            "nami_base_url",
            "nami_api_key",
            "gateway_base_url",
            "gateway_api_key",
            "models",
            "max_models",
        },
        "配置项齐全",
        str(sorted(schema)),
    )
    check(
        schema["relay_mode"]["default"] == "nami",
        "配置默认值为 nami 中转",
        str(schema["relay_mode"].get("default")),
    )
    check(
        schema["relay_mode"]["options"] == ["nami", "direct"],
        "relay_mode 只提供两个选项",
        str(schema["relay_mode"].get("options")),
    )


async def main() -> int:
    # 假 OFM：只有 OpenAI 兼容接口；假 Nami：同一个接口 + 健康探针。
    ofm = FakeGateway(GATEWAY_MODELS, require_key=OFM_KEY)
    nami = FakeGateway(
        GATEWAY_MODELS,
        require_key=NAMI_KEY,
        health={"provider": "ofm", "model": "glm-5", "models": "3 available"},
    )
    try:
        await test_registration(nami)
        await test_whitelist_and_enable(nami)
        await test_commands(nami)
        await test_relay_mode_selection(ofm, nami)
        await test_nami_relay(nami)
        await test_direct_mode(ofm)
        await test_mode_switch_is_in_place(ofm, nami)
        await test_endpoint_down()
        await test_pure_helpers()
        test_package_files()
    finally:
        ofm.stop()
        nami.stop()

    failed = [label for ok, label in RESULTS if not ok]
    for ok, label in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        print("失败项：")
        for label in failed:
            print(" -", label)
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):  # Windows 控制台默认 GBK，避免打印 ✓ 时崩
            reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
