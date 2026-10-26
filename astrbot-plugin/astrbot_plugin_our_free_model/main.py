"""Our Free Model (OFM) → AstrBot 提供商接入插件。

dsh-our-free-model 会在本机开一个 OpenAI 兼容网关（设置 → Our Free Model →
本地转发，默认 127.0.0.1:18899）。本插件把该网关上的模型注册成 AstrBot 的
`openai_chat_completion` 提供商，于是 WebUI 的提供商列表与聊天页模型选择器里
就能直接选到这些免费模型。

设计取舍：

1. 不自己写 Provider 适配器，直接复用内置的 OpenAI 兼容适配器——Nami 与 OFM
   说的都是 OpenAI Chat Completions，复用适配器可以少写代码，也能跟着 AstrBot
   升级走。
2. 默认走 **Nami 中转**：注册出来的提供商指向 Nami（默认 127.0.0.1:8787/v1），
   由 Nami 再转发到 OFM。于是免费模型照样能选，还顺带拿到 Nami 的按名字路由、
   别名与白名单、每会话历史、工具循环。想直连 OFM 就把 `relay_mode` 改成 `direct`。
3. 提供商 ID 只由模型 ID 决定，与 `relay_mode` 无关。所以「直连 ↔ 中转」互相切换
   是**就地改写同一批提供商**，不会留下孤儿提供商，`/ofm use` 的选择也不会因为
   换模式而失效。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

import aiohttp
from astrbot.api import AstrBotConfig, logger, sp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:  # 4.x 均在此路径，缺失时退化为直接写会话状态
    from astrbot.core.provider.entities import ProviderType
except Exception:  # pragma: no cover - 仅为跨版本兜底
    ProviderType = None  # type: ignore[assignment]

PROVIDER_ID_PREFIX = "ofm-"
PROVIDER_TYPE = "openai_chat_completion"
PROVIDER_KIND = "chat_completion"
MODEL_LIST_DISPLAY_LIMIT = 40

# 两种入口。nami 是默认值：AstrBot → Nami → OFM。
RELAY_NAMI = "nami"
RELAY_DIRECT = "direct"
RELAY_MODES = (RELAY_NAMI, RELAY_DIRECT)
DEFAULT_NAMI_BASE_URL = "http://127.0.0.1:8787/v1"
DEFAULT_OFM_BASE_URL = "http://127.0.0.1:18899/v1"


def slugify_model(model_id: str) -> str:
    """把模型 ID 变成可用作提供商 ID 的短横线形式。"""
    slug = re.sub(r"[^a-z0-9]+", "-", str(model_id).strip().lower()).strip("-")
    return slug or "model"


def provider_id_for(model_id: str) -> str:
    """模型 ID -> 提供商 ID。

    只由模型 ID 本身决定：同名永远同 ID（可重复 sync），不同模型永远不同 ID
    （`a/b` 与 `a-b` 这种 slug 撞车的模型也不会互相顶掉）。附加的短摘要就是为了
    这一点——ID 必须在任何调用点、任何顺序下都能算出一致结果。

    刻意与 relay_mode 无关：切换「直连 ↔ 中转」时改写的是同一个提供商的
    api_base/key，而不是另建一批。
    """
    slug = slugify_model(model_id)
    digest = hashlib.sha1(str(model_id).encode("utf-8")).hexdigest()[:6]
    return f"{PROVIDER_ID_PREFIX}{slug}-{digest}"


def normalize_base_url(raw: Any, fallback: str) -> str:
    """把配置里填的地址规范成 OpenAI 兼容 Base URL（以 `/v1` 结尾）。

    「少写 /v1」是这个插件最容易踩的坑，而两个入口的根地址都是合法输入，
    所以两种写法都接受，而不是让用户在聊天里收到一个 404。
    """
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return fallback
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def host_root_of(base_url: str) -> str:
    """Base URL -> 站点根（去掉结尾的 `/v1`），用于 /healthz、/readyz。"""
    value = base_url.rstrip("/")
    value = value.removesuffix("/v1")
    return value.rstrip("/")


def extract_trailing_arg(event: AstrMessageEvent, fallback: str = "") -> str:
    """取出 `ofm use <模型ID>` 里 `use` 之后的全部内容（模型 ID 可能含 `/`）。"""
    text = (getattr(event, "message_str", "") or "").strip()
    if not text:
        return fallback
    parts = text.split(maxsplit=2)
    if len(parts) >= 3 and parts[1].lower() == "use":
        return parts[2].strip() or fallback
    return fallback


class OurFreeModelPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._sync_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 配置读取

    @property
    def relay_mode(self) -> str:
        """`nami`（默认，走 Nami 中转）或 `direct`（AstrBot 直连 OFM）。"""
        raw = str(self.config.get("relay_mode") or RELAY_NAMI).strip().lower()
        return raw if raw in RELAY_MODES else RELAY_NAMI

    @property
    def base_url(self) -> str:
        """注册给 AstrBot 的 Base URL：中转模式下是 Nami，直连模式下是 OFM。"""
        if self.relay_mode == RELAY_NAMI:
            return normalize_base_url(
                self.config.get("nami_base_url"),
                DEFAULT_NAMI_BASE_URL,
            )
        return normalize_base_url(
            self.config.get("gateway_base_url"),
            DEFAULT_OFM_BASE_URL,
        )

    @property
    def api_key(self) -> str:
        """入口对应的 Key：Nami 的 API Key，或 OFM 的「本机 Key」。"""
        field = "nami_api_key" if self.relay_mode == RELAY_NAMI else "gateway_api_key"
        return str(self.config.get(field) or "").strip()

    @property
    def host_root(self) -> str:
        """入口的站点根，用于 /healthz 与 /readyz。"""
        return host_root_of(self.base_url)

    @property
    def endpoint_label(self) -> str:
        return "Nami 中转" if self.relay_mode == RELAY_NAMI else "OFM 直连"

    @property
    def request_timeout(self) -> int:
        try:
            return max(1, int(self.config.get("request_timeout", 20) or 20))
        except (TypeError, ValueError):
            return 20

    @property
    def chat_timeout(self) -> int:
        try:
            return max(1, int(self.config.get("chat_timeout", 300) or 300))
        except (TypeError, ValueError):
            return 300

    def configured_models(self) -> list[str]:
        raw = self.config.get("models") or []
        if isinstance(raw, str):
            raw = re.split(r"[,\n]", raw)
        models: list[str] = []
        for item in raw:
            model = str(item).strip()
            if model and model not in models:
                models.append(model)
        return models

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ------------------------------------------------------------------ 入口访问

    async def fetch_models(self) -> list[str]:
        """GET {base_url}/models，返回模型 ID 列表。"""
        url = f"{self.base_url}/models"
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers()) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"入口返回 HTTP {resp.status}：{body[:300]}",
                    )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"入口返回的不是 JSON：{body[:200]}") from exc
        items = payload.get("data") if isinstance(payload, dict) else payload
        models: list[str] = []
        for item in items or []:
            model_id = item.get("id") if isinstance(item, dict) else item
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
        return sorted(set(models))

    async def probe_relay(self) -> dict[str, str]:
        """中转模式下读 Nami 的 /healthz 与 /readyz，把整条链路的状态取回来。

        `/ofm status` 的价值就在这里：出问题时能一眼分清是 AstrBot 连不上 Nami，
        还是 Nami 连不上 OFM（后者写在 Nami 的 `checks.provider` / `checks.model`
        里）。探测失败不抛异常——状态命令本身不能因为上游挂掉就不可用。
        """
        out: dict[str, str] = {}
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for path, label in (("/healthz", "health"), ("/readyz", "ready")):
                    try:
                        async with session.get(f"{self.host_root}{path}") as resp:
                            out[label] = f"HTTP {resp.status}"
                            if path != "/readyz" or resp.status != 200:
                                continue
                            data = await resp.json(content_type=None)
                            checks = (data or {}).get("checks") or {}
                            out["provider"] = str(checks.get("provider") or "?")
                            out["model"] = str(checks.get("model") or "?")
                            out["namiModels"] = str(checks.get("models") or "?")
                    except Exception as exc:
                        out[label] = f"不可达（{str(exc)[:120]}）"
        except Exception as exc:  # 连 ClientSession 都建不起来
            out.setdefault("health", f"不可达（{str(exc)[:120]}）")
        return out

    async def resolve_targets(self) -> tuple[list[str], list[str]]:
        """返回 (要注册的模型, 入口当前全部模型)。

        配了白名单时入口只做「尽量读取」：没开入口也能先把白名单注册好。
        """
        configured = self.configured_models()
        try:
            available = await self.fetch_models()
        except Exception:
            if not configured:
                raise
            available = []
        if configured:
            return configured, available
        try:
            limit = int(self.config.get("max_models", 8) or 0)
        except (TypeError, ValueError):
            limit = 8
        if limit <= 0:
            return available, available
        return available[:limit], available

    async def probe_chat(self, model: str) -> str:
        """直接打一次最小对话请求，用于 /ofm test。"""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 16,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=self.request_timeout + 60)
        started = time.monotonic()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                body = await resp.text()
                elapsed = time.monotonic() - started
                if resp.status != 200:
                    raise RuntimeError(f"入口返回 HTTP {resp.status}：{body[:300]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"入口返回的不是 JSON：{body[:200]}") from exc
        choices = data.get("choices") or [{}]
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "").strip()
        return f"{model} 可用（{elapsed:.1f}s）：{text[:80] or '(空回复)'}"

    # -------------------------------------------------------------- 提供商注册

    def _provider_config(
        self,
        provider_id: str,
        model: str,
        *,
        enable: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "id": provider_id,
            "provider": "openai",
            "type": PROVIDER_TYPE,
            "provider_type": PROVIDER_KIND,
            "enable": bool(self.config.get("provider_enable", True)) if enable is None else enable,
            "key": [self.api_key],
            "api_base": self.base_url,
            "model": model,
            "timeout": self.chat_timeout,
            "proxy": "",
            "custom_headers": {},
            # [] = 未声明能力 = 不限制（AstrBot 只有读到空列表才认为提供商可收图，
            # 键缺失会被判成「不支持图像」而把图片丢掉）
            "modalities": [],
        }

    def _provider_entries(self) -> list[dict[str, Any]]:
        """当前提供商配置列表。

        优先读配置本体：个别版本 `delete_provider()` 会重绑配置里的 provider 列表，
        而 `providers_config` 仍指向旧列表（读旧列表会让 /ofm cleanup 后的自记账过期）。
        """
        manager = self.context.provider_manager
        conf = getattr(getattr(manager, "acm", None), "default_conf", None)
        entries = conf.get("provider") if conf is not None else None
        if isinstance(entries, list):
            return entries
        return list(getattr(manager, "providers_config", None) or [])

    def _find_provider_config(self, provider_id: str) -> dict[str, Any] | None:
        manager = self.context.provider_manager
        getter = getattr(manager, "get_provider_config_by_id", None)
        if callable(getter):
            found = getter(provider_id)
            if found is not None:
                return found
        for item in self._provider_entries():
            if item.get("id") == provider_id:
                return dict(item)
        return None

    def _registered_map(self) -> dict[str, str]:
        """本插件注册的提供商：provider_id -> model。"""
        registered: dict[str, str] = {}
        for item in self._provider_entries():
            provider_id = str(item.get("id", ""))
            if provider_id.startswith(PROVIDER_ID_PREFIX):
                registered[provider_id] = str(item.get("model") or "")
        return registered

    def _registered_provider_ids(self) -> list[str]:
        return list(self._registered_map())

    @staticmethod
    def _drifted(current: dict[str, Any], desired: dict[str, Any]) -> bool:
        for key, value in desired.items():
            if key == "id":
                continue
            if current.get(key) != value:
                return True
        return False

    async def _create_provider(self, desired: dict[str, Any]) -> None:
        manager = self.context.provider_manager
        creator = getattr(manager, "create_provider", None)
        if callable(creator):
            await creator(desired)
            return
        conf = getattr(getattr(manager, "acm", None), "default_conf", None)
        if conf is None or "provider" not in conf:
            raise RuntimeError(
                "当前 AstrBot 版本不支持插件动态添加提供商，请在 WebUI 手动新增"
                f" OpenAI 兼容提供商（Base URL: {self.base_url}）。",
            )
        conf["provider"].append(desired)
        saver = getattr(conf, "save_config", None)
        if callable(saver):
            saver()
        await manager.load_provider(desired)

    async def _update_provider(self, provider_id: str, desired: dict[str, Any]) -> None:
        manager = self.context.provider_manager
        updater = getattr(manager, "update_provider", None)
        if callable(updater):
            await updater(provider_id, desired)
            return
        conf = getattr(getattr(manager, "acm", None), "default_conf", None)
        if conf is None or "provider" not in conf:
            return
        for index, item in enumerate(conf["provider"]):
            if item.get("id") == provider_id:
                conf["provider"][index] = desired
                break
        saver = getattr(conf, "save_config", None)
        if callable(saver):
            saver()
        await manager.reload(desired)

    async def _delete_provider(self, provider_id: str) -> None:
        manager = self.context.provider_manager
        deleter = getattr(manager, "delete_provider", None)
        if callable(deleter):
            await deleter(provider_id=provider_id)
            return
        conf = getattr(getattr(manager, "acm", None), "default_conf", None)
        if conf is not None and "provider" in conf:
            conf["provider"] = [item for item in conf["provider"] if item.get("id") != provider_id]
            saver = getattr(conf, "save_config", None)
            if callable(saver):
                saver()
        await manager.terminate_provider(provider_id)

    async def sync(self) -> dict[str, Any]:
        """按配置把网关模型同步成 AstrBot 提供商（幂等）。"""
        async with self._sync_lock:
            targets, available = await self.resolve_targets()
            created: list[str] = []
            updated: list[str] = []
            unchanged: list[str] = []
            for model in targets:
                provider_id = provider_id_for(model)
                desired = self._provider_config(provider_id, model)
                current = self._find_provider_config(provider_id)
                if current is None:
                    await self._create_provider(desired)
                    created.append(provider_id)
                elif self._drifted(current, desired):
                    await self._update_provider(provider_id, desired)
                    updated.append(provider_id)
                else:
                    unchanged.append(provider_id)
            return {
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "available": available,
                "targets": targets,
            }

    async def ensure_model(self, model: str) -> str:
        """确保某个模型已有可用提供商，返回提供商 ID。

        `/ofm use` 是显式指定，因此这里一律启用——否则用户明明点了用却切不过去。
        同步按当前 relay_mode 决定 api_base/key，所以换模式后再 use 会就地改写。
        """
        provider_id = provider_id_for(model)
        desired = self._provider_config(provider_id, model, enable=True)
        current = self._find_provider_config(provider_id)
        if current is None:
            await self._create_provider(desired)
        elif self._drifted(current, desired):
            await self._update_provider(provider_id, desired)
        return provider_id

    async def set_session_model(
        self,
        event: AstrMessageEvent,
        provider_id: str,
    ) -> None:
        """把当前会话的对话模型切到指定提供商。"""
        umo = event.unified_msg_origin
        manager = self.context.provider_manager
        setter = getattr(manager, "set_provider", None)
        if callable(setter) and ProviderType is not None:
            await setter(provider_id, ProviderType.CHAT_COMPLETION, umo=umo)
            return
        await sp.session_put(umo, f"provider_perf_{PROVIDER_KIND}", provider_id)

    # ------------------------------------------------------------------ 生命周期

    @filter.on_astrbot_loaded()
    async def on_loaded(self, *_) -> None:
        if not self.config.get("auto_register", True):
            return
        try:
            result = await self.sync()
        except Exception as exc:  # 入口没开不该拖垮 AstrBot 启动
            logger.warning(
                "Our Free Model 同步失败（%s 入口 %s 可能未就绪）：%s",
                self.endpoint_label,
                self.base_url,
                exc,
            )
            return
        logger.info(
            "Our Free Model 已同步（%s → %s）：新增 %d，更新 %d，未变 %d，可用模型 %d 个。",
            self.endpoint_label,
            self.base_url,
            len(result["created"]),
            len(result["updated"]),
            len(result["unchanged"]),
            len(result["available"]),
        )

    # -------------------------------------------------------------------- 指令

    @filter.command_group("ofm")
    def ofm(self):
        """Our Free Model：免费模型接入管理（默认经 Nami 中转）。"""

    @ofm.command("models")
    async def ofm_models(self, event: AstrMessageEvent):
        """列出入口上当前可用的模型。"""
        try:
            available = await self.fetch_models()
        except Exception as exc:
            yield event.plain_result(f"读取模型清单失败：{exc}")
            return
        if not available:
            yield event.plain_result("网关没有返回任何模型。")
            return
        registered = set(self._registered_provider_ids())
        lines = []
        for model in available[:MODEL_LIST_DISPLAY_LIMIT]:
            mark = "✓" if provider_id_for(model) in registered else "·"
            lines.append(f"{mark} {model}")
        head = f"免费模型 {len(available)} 个（✓ 已注册）："
        tail = (
            f"\n…… 其余 {len(available) - MODEL_LIST_DISPLAY_LIMIT} 个已省略"
            if len(available) > MODEL_LIST_DISPLAY_LIMIT
            else ""
        )
        yield event.plain_result(f"{head}\n" + "\n".join(lines) + tail)

    @ofm.command("status")
    async def ofm_status(self, event: AstrMessageEvent):
        """查看链路与注册状态。"""
        registered = self._registered_provider_ids()
        lines = [
            f"模式：{self.endpoint_label}",
            f"入口：{self.base_url}",
            f"鉴权：{'已配置 Key' if self.api_key else '未配置 Key'}",
            f"已注册提供商：{len(registered)} 个",
        ]

        if self.relay_mode == RELAY_NAMI:
            relay = await self.probe_relay()
            lines.append(f"Nami /healthz：{relay.get('health', '未探测')}")
            lines.append(f"Nami /readyz：{relay.get('ready', '未探测')}")
            if "provider" in relay:
                lines.append(
                    f"Nami 上游：{relay['provider']} / {relay['model']}"
                    f"（{relay.get('namiModels', '?')}）",
                )
                if relay["provider"] != "ofm":
                    lines.append(
                        "⚠ Nami 当前上游不是 ofm："
                        "在 Nami 目录里跑 `npm run ofm -- --write` 后重启 Nami。",
                    )

        try:
            available = await self.fetch_models()
            lines.append(f"入口可达，模型 {len(available)} 个")
        except Exception as exc:
            lines.append(f"入口不可达：{exc}")

        yield event.plain_result("\n".join(lines))

    @ofm.command("sync")
    async def ofm_sync(self, event: AstrMessageEvent):
        """重新同步入口模型到 AstrBot 提供商。"""
        try:
            result = await self.sync()
        except Exception as exc:
            yield event.plain_result(f"同步失败：{exc}")
            return
        yield event.plain_result(
            f"同步完成（{self.endpoint_label}）：新增 {len(result['created'])}，"
            f"更新 {len(result['updated'])}，未变 {len(result['unchanged'])}；"
            f"入口上共 {len(result['available'])} 个模型。",
        )

    @ofm.command("use")
    async def ofm_use(self, event: AstrMessageEvent, model: str = ""):
        """把当前会话的模型切到指定模型：/ofm use <模型ID>。"""
        target = extract_trailing_arg(event, model).strip()
        if not target:
            yield event.plain_result("用法：/ofm use <模型ID>，先用 /ofm models 查看可用模型。")
            return
        try:
            provider_id = await self.ensure_model(target)
            await self.set_session_model(event, provider_id)
        except Exception as exc:
            yield event.plain_result(f"切换失败：{exc}")
            return
        yield event.plain_result(f"当前会话已切换到 {target}（提供商 {provider_id}）。")

    @ofm.command("test")
    async def ofm_test(self, event: AstrMessageEvent, model: str = ""):
        """探测某个模型是否真的可用：/ofm test [模型ID]。"""
        target = (model or "").strip()
        if not target:
            configured = self.configured_models()
            if configured:
                target = configured[0]
            else:
                registered = [model for model in self._registered_map().values() if model]
                target = registered[0] if registered else ""
        if not target:
            yield event.plain_result("用法：/ofm test <模型ID>，或先 /ofm sync 注册模型。")
            return
        try:
            yield event.plain_result(await self.probe_chat(target))
        except Exception as exc:
            yield event.plain_result(f"{target} 探测失败：{exc}")

    @ofm.command("cleanup")
    async def ofm_cleanup(self, event: AstrMessageEvent):
        """移除本插件注册的全部提供商。"""
        provider_ids = self._registered_provider_ids()
        removed, failed = 0, []
        for provider_id in provider_ids:
            try:
                await self._delete_provider(provider_id)
                removed += 1
            except Exception as exc:
                failed.append(f"{provider_id}: {exc}")
        text = f"已移除 {removed} 个提供商。"
        if failed:
            text += "\n失败：" + "；".join(failed)
        yield event.plain_result(text)

    async def terminate(self) -> None:
        """插件卸载时不做破坏性清理，注册的提供商保留给用户继续使用。"""
