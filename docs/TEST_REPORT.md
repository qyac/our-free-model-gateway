# our-free-model-gateway 实测报告

- 仓库：https://github.com/beichen24a1/our-free-model-gateway （`main`，2 个提交）
- 测试目录：`gateway-test/our-free-model-gateway`（工作副本）+ `gateway-test/gw2`（降级语义专用副本）
- 环境：Windows 11 / Node v24.13.1 / npm 11.8.0 / bsdtar 3.8.9
- 测试时间：2026-10-26 18:51 ~ 19:05（Asia/Shanghai）
- 参考项目：`neko-free-model/astrbot_plugin_our_free_model`（AstrBot 侧插件，链路 AstrBot → Nami:8787 → OFM:18899）

## 结论

**能用。** 独立网关起得来、协议对得上、免费上游确实出字，`auto` 的失败切换按 README 说的那样工作。
问题集中在外围：Windows 上 `setup.mjs` 有一个必现的解压 bug，输入校验偏松，文档数字有漂移。

## 实测结果

### 协议 / 接口（29 项用例，27 项通过）

| 项 | 结果 |
|---|---|
| `GET /health`、`GET /`（免 key） | 200 `{"ok":true,"service":"our-free-model","version":"2.1.0"}` |
| `/v1/models` 无 key / 错 key | 401 |
| `/v1/models` 正确 key、`x-api-key` 变体 | 200，29 行，每行带 `supports_vision`，其中 8 个视觉模型 |
| `POST /v1/chat/completions`（auto，非流式） | 200，1.0–1.5s，返回中文正文 + usage |
| 同上 `stream:true` | 200，标准 SSE（`data:` 帧 + `[DONE]`），13 帧 |
| `text` 模式 + 纯文本 | 200 |
| `text` 模式 + 图片 | **400**，并说明「改用 vision 或 auto」，不静默丢图 |
| `vision` 模式 + 图片 | 200，正确描述图片（纯白） |
| `POST /v1/messages`（Anthropic） | 200，`content` 含 text / thinking 块 |
| `POST /v1/messages` `stream:true` | 200，`event: message_start` / `content_block_delta` |
| `POST /v1/messages/count_tokens` | 200，本地估算 `input_tokens` |
| `POST /v1/responses`（OpenAI Responses） | 200，`output` 结构正确（约 5–11s，比 chat 慢） |
| function calling（`scripts/tools.mjs`） | `finish_reason: tool_calls`，参数 JSON 正确 |
| 未知模型 / 未知路径 / GET 聊天接口 | 404 / 404 / 404 |
| 非法 JSON / 缺 `model` | 400 |
| 无 key 调聊天 | 401 |
| 5 路并发 auto | 5/5 成功，1596ms 内完成 |
| 1MB 请求体 | 200（自动走大窗口模型） |
| 连续 10 次 auto | **10/10 成功**，0.8–3.3s，中位 2.25s |
| 重启后 key 持久化 | 一致（`data/settings.json` 的 `forwardKey`） |
| 端口被占用 | 自动顺延到 18900 并在控制台告警 |

两处「通过但值得记一笔」：
- `messages` 缺失 / 空数组 / 传字符串 → 网关不校验，转成上游 **502 `upstream HTTP 400`**，不是 400。属于校验缺口，不影响正常调用。
- `scripts/smoke.mjs` 首次跑非流式那半段撞到上游 503（`upstream HTTP 503`），流式那半段正常；重试即恢复。上游抖动，不是网关问题。

### 新增 README 段落（19 行）——已逐条验证

新版本只改 `README.md`（+19 行，`server.mjs` 等文件字节未变）：

1. AstrBot 注意事项里加两条：`auto` 会自动换模型；推荐只配一个提供商。
2. 新增小节「后面的模型失效了，会自动换吗」，给出三行对照表（`auto/text/vision` 会换、`default` 不会、具体模型 id 不会）。

我用一份独立配置（`gw2`，`routing.prefer` 首位故意放已被限流的 `deepseek-v4.1-flash-free`，`verbose: true`）复现了这张表：

| 用例 | 实测 |
|---|---|
| `model: auto` | **HTTP 200**，3284ms。日志：`[router] auto → deepseek-v4.1-flash-free` → `失败（Free usage limit reached…），换下一个` → `[router] auto → kilo-auto/free` → 出字「你好」 |
| `model: deepseek-v4.1-flash-free` | **HTTP 502** `Free usage limit reached…`，798ms，不换 |
| `model: default` | 固定 `kilo-auto/free`，502 `upstream HTTP 503`（当时上游过载），不换 |

即：README 新写的「会换 / 不会换」与「候选全败返回 502」两个边界，实测与文档一致。

## 发现的问题

### 1. Windows 上 `node scripts/setup.mjs` 必现失败（阻断首次运行）

```
[失败] 解压失败（需要系统里有 tar）：
Command failed: tar -xzf C:\Users\…\pkg.tgz -C C:\Users\…\unpacked
```

原因：`execFileSync('tar', ['-xzf', tarball, …])` 把带盘符的 `C:\…` 路径直接交给 `tar`；当 PATH 里的 `tar` 是 **Git for Windows 的 GNU tar** 时，它把 `C:` 当成远端主机名：

```
tar (child): Cannot connect to C: resolve failed
```

绕法（本次测试采用）：让 Windows 自带 bsdtar 排在前面，或从 cmd/PowerShell 而不是 Git Bash 环境启动：

```powershell
$env:PATH = "C:\Windows\System32;$env:PATH"; node scripts/setup.mjs
```

仓库侧改法建议：解压前 `path.relative` 化 / 用 `tar -xzf pkg.tgz -C unpacked` 且 `cwd: work`，或在 Windows 上优先 `C:\Windows\System32\tar.exe`。

### 2. 输入校验偏松

`messages` 缺失或类型错误时不本地拦截，请求被转发到上游，用户看到 `502 upstream HTTP 400`。`model` 缺失倒是本地 400。建议入口补一层 schema 校验，把 4xx 还给调用方。

### 3. 文档数字漂移

| README 写的 | 实际 |
|---|---|
| 25 个真实模型 / 6 个视觉模型 | `/v1/models` 29 行 / 8 个 `supports_vision=true` |
| setup 保留「46 个文件 / 约 560 KB」 | 47 个文件 / 680 KB |

上游清单每天都在变，这属于正常漂移，但「6 个视觉模型」这种写死的清单建议改成动态说明。

### 4. 端口顺延与局域网端口撞车（潜在）

端口被占用时顺延到 **18900**，而 `lan.port` 默认也是 18900。默认 `lan.enabled=false` 时无害；一旦开了局域网中继又恰好撞上顺延，两个 `start` 会互相抢端口。建议顺延时跳过 `lan.port`。

### 5. 凭据与指纹（知情风险，非缺陷）

- `data/settings.json` 里 `forwardKey` 明文，Windows ACL 只到「当前用户 FullControl + 若干 SID」，不是 0600（README 已声明）。
- 上游走的是 `Authorization: Bearer public` + `x-opencode-client: desktop` 的桌面客户端指纹（`vendor/our-free-model/src/upstream.js`）。这是免费池的既成玩法，不是本仓库发明，但**上游改校验就会断**，且属于对方条款里不欢迎的用法——只适合个人本机用。
- 网关只监听 `127.0.0.1`（实测 18899 仅回环），`lan` 默认关闭，未见 18900 监听；未见额外遥测上报，外部请求只到上游（opencode.ai / api.kilo.ai）与 mihomo 健康检查用的 gstatic。

## 当前状态 / 怎么用

网关现在还在跑：`http://127.0.0.1:18899/v1`，key `ofm-2TEMa9mXIgK4BFkClZ9YSaeP_BbHqt3R`（已落盘 `data/settings.json`，重启不变）。

```powershell
cd C:\Users\haoxu\Downloads\NekoHome\gateway-test\our-free-model-gateway
node server.mjs          # 或 start.cmd
```

客户端填：Base URL `http://127.0.0.1:18899/v1`，Key 同上，模型填 `auto`。

## 本地已落地的改动（4 处）

| 文件 | 改了什么 | 验证 |
|---|---|---|
| `scripts/setup.mjs` | Windows 优先用 `System32\tar.exe`；解压改成 `cwd` + 相对文件名，避开 GNU tar 把 `C:` 当主机名 | 把 Git 的 GNU tar 放到 PATH 最前面重跑 `setup.mjs`：47 文件 / 680 KB 正常解出 |
| `server.mjs` | `wantsVision` 从「JSON 字符串里搜 `image_url`」改成结构化 content 块判断（`image` / `image_url` / `input_image`） | 正文提到 `image_url` 字样：`text` 模式由 400 → **200**；真图片 `text` 仍 400、`vision` 仍 200 |
| `server.mjs` | 新增 `assertBodyShape()` 入口校验：`messages` 非空数组，`/v1/responses` 认 `input` | 缺 `messages` 由 502 → **400** `\`messages\` is required`；缺 `input` → 400 `\`input\` is required` |
| `scripts/patch-vendor.mjs`（新增，`npm run patch`） | 给 vendor 打两个可重入补丁：①非流式 JSON 响应补 `corsHeaders()`；②认证接受 `api-key` 头 | POST 响应带 `access-control-allow-origin: *`；`api-key: <key>` 由 401 → **200**；`--check` 幂等 |

vendor 补丁会因 `node scripts/setup.mjs` 重装而被覆盖，重装后再跑一次 `npm run patch` 即可（`forward.js.orig` 是备份）。

### 改了之后的上游状态说明

改完跑全量回归时是 26/29：3 个失败全部是上游免费额度耗尽（`Free usage limit reached…` / `Sign up for free to continue…`），不是补丁引起——同一把 key 直连 `kilo-auto/free` 也是同样报错，等额度重置即恢复。

## 仍未改的（建议上游 / 按需）

| 项 | 说明 |
|---|---|
| 端口顺延可能撞 `lan.port` | 18899 被占时顺延到 18900，正好等于 `lan.port` 默认值；开了 LAN 中继又撞上会互抢。位置在 vendor 的 `startForwardServer` |
| `GET /v1/models/{id}` | 返回 404 `no route`；部分客户端（Open WebUI / LiteLLM 一类）会探测单模型 |
| `?api_key=` 查询参数 | 仍不认，只补了 `api-key` 头 |
| README 数字 | 「25 模型 / 6 视觉 / 46 文件 560 KB」与实测「29 行 / 8 视觉 / 47 文件 680 KB」不符 |
| `data/settings.json` | key 明文，Windows ACL 非 0600（已声明）；独立网关还会把同一 schema 的 `forward.enabled=true`、`lan.enabled=false` 写回，若与 dsh 插件共用 dataDir 会互相覆盖设置 |
| 上游额度与指纹 | `Bearer public` + `x-opencode-client: desktop` 是免费池玩法，上游一改就断；本次实测把额度打满即为明证，只适合个人本机低频使用 |

## 复现脚本

`gateway-test/test-gateway.mjs`：

```powershell
node test-gateway.mjs http://127.0.0.1:18899 <apiKey>
```

覆盖 29 个用例（鉴权、三套协议、流式、模式边界、错误处理、并发、大请求体）。
