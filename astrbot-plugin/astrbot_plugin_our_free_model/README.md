# astrbot_plugin_our_free_model

把 [dsh-our-free-model](https://github.com/)（下称 OFM）的免费模型接进 AstrBot，
**默认经 [Nami](https://github.com/) 中转**：在 WebUI 的「模型提供商」和聊天页模型
选择器里直接选到这些免费模型，同时拿到 Nami 的路由、会话与工具能力。

```
AstrBot ──插件──► Nami (:8787/v1) ──NAMI_OFM_URL──► OFM 本地网关 (:18899/v1) ──► 免费车道
```

## 它是怎么工作的

1. OFM 在 `设置 → Our Free Model → 本地转发` 打开后，会在本机监听一个 OpenAI 兼容端口
   （默认 `http://127.0.0.1:18899/v1`，端口被占用时会顺延，以 OFM 设置页显示的为准）。
2. Nami 把该网关配成上游（`NAMI_LLM_PROVIDER=ofm`，一条命令：
   `npm run ofm -- --write`），于是 OFM 当前的免费模型目录变成 Nami 的 `/v1/models`，
   并且多出别名映射、模型白名单、每会话历史、工具循环这些 Nami 的能力。
3. 本插件从 **Nami** 的 `GET /v1/models` 拉取清单，把这些模型注册成 AstrBot 内置的
   `openai_chat_completion` 提供商（ID 形如 `ofm-<模型slug>-<6位摘要>`，摘要保证
   `a/b` 与 `a-b` 这类撞名模型互不覆盖，且同一个模型在任何时候都算出同一个 ID，
   与 `relay_mode` 无关），`api_base` 指向 Nami，于是它们出现在 WebUI 提供商列表，
   也能在聊天页被选为对话模型。
4. 提供 `/ofm` 指令做同步、切换、自检和清理。

真正跟上游打交道、挑免费车道的是 OFM；路由与托管是 Nami。**两者都得在跑**：
Nami 要能连上 OFM（Nami 的 `/readyz` 会告诉你），插件要能连上 Nami（`/ofm status` 会告诉你）。

本插件不需要额外的第三方依赖（`aiohttp` 是 AstrBot 自带的依赖），因此没有 `requirements.txt`。

## 安装

1. 在 Nami 目录里把 OFM 配成上游并启动：

   ```bash
   cd neko-NamiServer
   npm run ofm -- --write     # 探测 OFM 网关 → 写 .env（NAMI_LLM_PROVIDER=ofm 等）
   npm start                  # 默认 127.0.0.1:8787
   ```

   OFM 与 Nami 不在同一台机器上时：`npm run ofm -- --url http://<OFM 主机>:18899 --key <本机 Key> --write`。

2. 把整个 `astrbot_plugin_our_free_model` 目录放进 `AstrBot/data/plugins/`；
3. 在 WebUI「插件管理」里重载插件（或重启 AstrBot）；
4. 在插件配置里填 **Nami 入口 Base URL** 和 **Nami API Key**（后者就是 Nami 的
   `NAMI_API_KEYS` 里的任意一个；启动日志里会打印自动生成的那个）；
5. `/ofm sync` 同步一次，或者直接重启让插件自动同步。

## 配置项

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `relay_mode` | string | `nami` | `nami`：经 Nami 中转（推荐）；`direct`：AstrBot 直连 OFM 网关 |
| `nami_base_url` | string | `http://127.0.0.1:8787/v1` | Nami 入口 Base URL；少写 `/v1` 会自动补上 |
| `nami_api_key` | string(secret) | 空 | Nami 的 `NAMI_API_KEYS` 里的任意一个 |
| `gateway_base_url` | string | `http://127.0.0.1:18899/v1` | 仅 `direct` 模式使用：OFM 网关 Base URL |
| `gateway_api_key` | string(secret) | 空 | 仅 `direct` 模式使用：OFM 的「本机 Key」（不是「局域网 Key」） |
| `models` | list | `[]` | 要注册的模型 ID 白名单；留空 = 自动发现 |
| `max_models` | int | `8` | 自动发现时最多注册几个；`0` 表示不限 |
| `provider_enable` | bool | `true` | 注册出来的提供商是否立即启用 |
| `auto_register` | bool | `true` | AstrBot 启动后自动同步一次 |
| `request_timeout` | int | `20` | 拉清单 / 链路探测 / `/ofm test` 的超时（秒） |
| `chat_timeout` | int | `300` | 写入提供商配置的生成超时（秒） |

> 一次注册几十个提供商会把 WebUI 列表塞满，所以默认只同步 8 个。想要哪几个模型稳定在手，
> 就把模型 ID 填进 `models`。注意填的必须是 **Nami 的 `/v1/models` 里显示的名字**——
> 如果 Nami 侧配了别名（`NAMI_MODEL_ALIASES`），用别名本身即可。

## 指令

| 指令 | 作用 |
| --- | --- |
| `/ofm models` | 列出入口上的模型，`✓` 表示已注册 |
| `/ofm status` | 模式、入口地址、鉴权、已注册数量；中转模式下还会报 Nami 的 `/healthz`、`/readyz`，以及 Nami 当前的上游 provider/model |
| `/ofm sync` | 按配置重新同步（幂等，同名提供商只更新差异） |
| `/ofm use <模型ID>` | 把**当前会话**的对话模型切到该模型，未注册则按需注册 |
| `/ofm test [模型ID]` | 直接打一次最小对话请求，验证模型是否真的能用 |
| `/ofm cleanup` | 移除本插件注册的全部提供商（只删 `ofm-` 前缀的） |

## 断了在哪一跳

`/ofm status` 是按「谁连不上谁」设计的：

```
模式：Nami 中转
入口：http://127.0.0.1:8787/v1
鉴权：已配置 Key
已注册提供商：8 个
Nami /healthz：HTTP 200
Nami /readyz：HTTP 200
Nami 上游：ofm / deepseek-v4.1-flash（3 available）
入口可达，模型 9 个
```

| 看到什么 | 说明 | 下一步 |
| --- | --- | --- |
| `Nami /healthz：不可达` | AstrBot 连不上 Nami | 启动 Nami（`npm start`），或检查 `nami_base_url` 与端口 |
| `Nami /readyz：HTTP 503` | Nami 起来了，但它连不上 OFM | 打开 OFM 的「本地转发」，必要时重跑 `npm run ofm -- --write` 后重启 Nami |
| `Nami 上游：mock / …` | Nami 当前上游不是 OFM | 在 Nami 目录跑 `npm run ofm -- --write` 后重启 Nami |
| `Nami 上游：ofm / …`，但 `/ofm test` 报 404 | 该免费车道已被 OFM 下线 | 换一个模型（`/ofm models` 看当前清单） |
| `鉴权：未配置 Key` | 没填 Nami API Key | 填 `NAMI_API_KEYS` 里的任意一个 |

## 常见问题

- **返回 401**：Nami 的 Key 填错了（`direct` 模式下则是填成了 OFM 的「局域网 Key」）。
- **WebUI 能看到提供商，但聊天还是用别的模型**：提供商只是「可用清单」，
  对话模型要么在「配置文件 → AI → 模型」里设为它，要么对本会话执行 `/ofm use <模型ID>`。
- **模型列表里没有想要的模型**：上游清单跟随刷新。被 Nami 判定为不可路由的模型不会出现在
  `/v1/models` 里（Nami 的免费车道清单随 OFM 变化）；也可能是 `max_models` 截断了——
  把它填进 `models` 白名单即可。
- **想临时绕开 Nami 排查**：把 `relay_mode` 改成 `direct`、填上 OFM 的 Base URL 与「本机 Key」，
  再 `/ofm sync`。提供商 ID 不变，切回来也是就地改写，不会留下一堆孤儿提供商。
- **不想用了**：`/ofm cleanup`，然后禁用/删除插件。
- **手动改过某个 `ofm-` 提供商的 Base URL / Key 又被打回去了**：这些提供商由插件托管，
  每次 `sync` 会按配置覆盖；要改就改插件配置，别改提供商本身。

## 自测

无需真实 AstrBot、Nami、OFM：`tests/` 下有 `astrbot` 替身包，以及两个假入口
（假 Nami 带 `/healthz` 与 `/readyz`，假 OFM 只有 OpenAI 兼容接口）。

```bash
python tests/selftest.py     # 需要 aiohttp、pyyaml
```

覆盖注册幂等、白名单、默认模式与地址规范化、按会话切换、slug 撞车、
中转链路状态（含上游断掉、上游不是 ofm）、直连模式、模式切换就地改写、
`/ofm test` 的 Bearer 鉴权、入口掉线、清理等 112 项断言。

## 兼容性

- 提供商注册走 `context.provider_manager.create_provider/update_provider/delete_provider`，
  模型选择走 `set_provider(..., ProviderType.CHAT_COMPLETION, umo=...)`；这些 API 已对照
  AstrBot master（4.29）源码核过，并带旧版本兜底：缺少动态注册 API 时会直接报错并提示
  在 WebUI 手动添加 OpenAI 兼容提供商（Base URL 填本插件配置里的同一个）。
- 插件用的适配器类型 `openai_chat_completion`、配置项 `model` / `api_base` / `key`
  都是 AstrBot 自带 OpenAI 兼容提供商的字段，不自造新类型。
- 两跳都是 OpenAI Chat Completions，所以本插件对 Nami 与 OFM 一视同仁：
  换掉任意一跳，只要对方仍是 OpenAI 兼容端点即可。

## 发布前

`metadata.yaml` 里没有写 `repo`（还没建仓库）。要发到插件市场的话，先建 GitHub 仓库，
再补上 `repo: https://github.com/<你的名字>/astrbot_plugin_our_free_model`，
并保持 `metadata.yaml` 的 `name`/`version` 与市场记录一致。

## 来源与许可

- 上游免费模型能力来自第三方项目 OFM（`dsh-our-free-model`），中转与托管来自 Nami；
  本插件只做转发接入，不对上游可用性与合规性负责；请遵守 OFM 与其上游服务各自的条款。
- 本插件以 MIT 许可发布。
