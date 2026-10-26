# Our Free Model · 独立网关

把 [`dsh-our-free-model`](https://www.npmjs.com/package/dsh-our-free-model) 插件里的免费模型通道，从 dsh 宿主中摘出来，做成一个**独立的 Node 进程**。
跑起来之后监听本机 `127.0.0.1:18899`，对外提供 **OpenAI / Anthropic 兼容 API**。
任何支持自定义 `base_url` + `api_key` 的程序（OpenAI SDK、Claude Code、AstrBot、Cherry Studio、各种 agent 框架、curl）都能直接调，不需要安装 dsh，也不需要开 GUI。

模型名写 **`auto`** 就行：网关按请求自己判断该用哪个模型（带图片→视觉模型，超长输入→大窗口模型），某个模型限流/拒绝时自动换下一个，调用方无感。

同时可选开一扇**局域网中继**（`0.0.0.0:18900`，**默认关闭**），给 Docker 里的应用或同网段的其它设备用，使用独立的一把 LAN Key。需要时把 `config.json` 的 `lan.enabled` 改成 `true`。

> **使用前必读**
>
> - 本项目是**客户端**，不是模型服务方。它不产生任何模型能力，只把公开的免费额度接到一个标准 HTTP 端口上。
> - 上游（OpenCode Zen、Kilo AI）的额度、可用模型、地区策略**由对方决定，随时可能变**；不保证稳定，也不保证长期可用。
> - 上游条款与免费池的运营方明确反对转售或公开共享此类访问权。**请仅在个人、受信任的环境中使用，不要公开部署或分发给他人。** 滥用会导致所有人的通道被收紧。
> - 本项目不绕过任何配额、地区或鉴权限制；被上游拒绝时它就是拒绝。
> - 模型输出由上游生成，可能有错。重要用途请自行核实。
> - 仓库不含任何账号、凭据或 Token；API Key 在首次启动时本机随机生成。

---

## 快速开始

需要 **Node.js >= 22.19**，以及系统自带的 `tar`（Windows 10+ / macOS / Linux 都有）。

```bash
git clone https://github.com/beichen24a1/our-free-model-gateway.git
cd our-free-model-gateway

node scripts/setup.mjs    # 首次：拉取上游依赖并裁剪（保留 47 个文件 / 约 680 KB）
node server.mjs           # 启动；Windows 也可以双击 start.cmd
```

`vendor/` 不在仓库里。`setup.mjs` 会从 npm 取 `dsh-our-free-model`，只保留运行时真正用到的文件，写进 `vendor/`（已加进 `.gitignore`）。换版本：`node scripts/setup.mjs 2.2.0`；离线安装：`node scripts/setup.mjs --from ./dsh-our-free-model-2.1.0.tgz`；只想确认有没有准备好：`node scripts/setup.mjs --check`。

启动后控制台会打印：

```
  Base URL    http://127.0.0.1:18899/v1
  API Key     ofm-xxxxxxxxxxxxxxxxxxxxxxxx
  模型清单    http://127.0.0.1:18899/v1/models
```

- `Base URL` + `API Key` = 本机程序要填的两个值。
- 局域网中继默认不开；需要时把 `config.json` 的 `lan.enabled` 改成 `true`，重启后会多打印两行局域网地址和局域网 Key。
- Key 首次启动自动生成，存在 `data/settings.json`（`forwardKey` / `forwardLanKey`）。要换 key 就在 `config.json` 里填 `apiKey` / `lan.key`，或设环境变量 `OUR_FREE_MODEL_API_KEY`。
- 端口被占用时会自动顺延到下一个可用端口，并在控制台打印实际端口。

---

## 怎么调用

### curl

```bash
curl http://127.0.0.1:18899/v1/chat/completions \
  -H "Authorization: Bearer ofm-xxxx" \
  -H "content-type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:18899/v1", api_key="ofm-xxxx")
resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

### OpenAI Node SDK

```js
import OpenAI from 'openai'
const client = new OpenAI({ baseURL: 'http://127.0.0.1:18899/v1', apiKey: 'ofm-xxxx' })
const resp = await client.chat.completions.create({
  model: 'auto',
  messages: [{ role: 'user', content: '你好' }],
})
console.log(resp.choices[0].message.content)
```

### Claude Code（走 Anthropic 协议）

注意 `ANTHROPIC_BASE_URL` **不带** `/v1`：

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:18899"
$env:ANTHROPIC_API_KEY  = "ofm-xxxx"
$env:ANTHROPIC_MODEL    = "auto"
claude
```

### Anthropic SDK

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://127.0.0.1:18899", api_key="ofm-xxxx")
msg = client.messages.create(
    model="auto",
    max_tokens=256,
    messages=[{"role": "user", "content": "你好"}],
)
print(msg.content[0].text)
```

### 其它客户端

凡是能填「OpenAI 兼容端点」的软件（Cherry Studio、Chatbox、LobeChat、Dify、LangChain、各种 IDE 插件）都一样：

- Base URL：`http://127.0.0.1:18899/v1`
- API Key：控制台打印的那串 `ofm-...`
- Model：`auto`（智能路由，推荐；也可写具体模型 id）

### AstrBot

AstrBot 把 OpenAI 兼容服务当作「对话提供商」，正好对上。网关的 `GET /v1/models` 能让它的**「保存并获取模型」**直接拉到清单，不用手填模型 ID。

**AstrBot 装在本机（Windows 直接跑）**：

1. WebUI →「模型提供商」→「对话」→「新增」
2. 类型选 **`OpenAI Compatible`**
3. 填写：
   - 提供商名称：随便，如 `Our Free Model`
   - **API Key**：控制台启动时打印的那串 `ofm-...`（第一次启动自动生成，存在 `data/settings.json`）
   - **API Base URL**：`http://127.0.0.1:18899/v1`（**必须带 `/v1`**）
4. 点「保存并获取模型」，勾选 **`auto`**（推荐，网关自己挑模型；也可选 `text` / `vision` 或具体模型），点右侧 `+` 确认启用
5. 点「测试模型」验证连通，然后在「配置文件」→「AI」→「模型」里把「对话模型」设为它

> 选 `auto` 的好处：上游免费池哪个模型活着每天都在变，AstrBot 这边不用跟着改；某个模型 429 了网关会自己换下一个，AstrBot 只看到一次成功。

**想明确区分"文本 / 视觉"两个提供商**：在 AstrBot 里加两次，base_url 和 API Key 完全一样，只有模型不同——

| 提供商 | 模型 | 用途 |
|---|---|---|
| Our Free Model · 文本 | `text` | 纯聊天、角色卡、长对话（发的消息带图会 400） |
| Our Free Model · 视觉 | `vision` | 群里有人发图时切过去 |

AstrBot 的「配置文件」里可以给不同场景挂不同的对话模型，所以你可以让默认配置用 `text`，另一个用 `vision`，按群或按用途切换。

**AstrBot 装在 Docker 里**：容器的 `127.0.0.1` 是容器自己，必须走宿主的地址——这就是网关要开「局域网中继」的原因：

- Base URL：`http://host.docker.internal:18900/v1`（Windows / macOS 的 Docker Desktop 直接可用）
- API Key：控制台打印的**局域网 Key**（`ofm-...`，和本机 Key 不是同一把）
- Linux 宿主要让容器能解析这个名字：启动容器时加 `--add-host=host.docker.internal:host-gateway`

**AstrBot 装在另一台设备 / 服务器上**：

- Base URL：`http://192.168.x.y:18900/v1`（换成你机器的局域网地址，控制台会打印）
- 需要用局域网 Key；跨公网还要自己解决端口映射 + 加固，不建议

前提是把 `config.json` 的 `lan.enabled` 设为 `true`（仓库里默认是 `false`，即默认不暴露局域网）。第一次启用时 Windows 防火墙会弹窗询问是否允许 Node 入站，**要选允许**，否则别的设备连不上。

**AstrBot 注意事项**：

- 模型填 `auto` 最省心；要明确区分纯文本 / 多模态，就按上面的两张卡配 `text` 和 `vision`。
- **上游模型失效不用管**：填 `auto` 时网关每次请求自己挑模型，挂了就换下一个，你这边不报错、也不用改配置。填具体模型 id 就没有这个待遇（见「后面的模型失效了，会自动换吗」）。
- 推荐只配**一个**提供商（模型填 `auto`），不要给每个模型各配一个——AstrBot 不会在提供商之间自动切换。
- AstrBot 的「Agent 执行器 / 工具调用」建议关掉或谨慎使用：上游免费池对工具声明的校验很严，插件会给每个请求补 `bash/glob/grep/read` 四个占位工具，模型有时会去调它们，AstrBot 侧会报未知工具。
- 带图片的消息发给 `text` 会立刻 400；想让它自己判断就发 `auto`。
- 免费池有并发与频率限制，AstrBot 多群同时刷屏时仍可能 502——`auto` 会在候选耗尽后才把错误抛给 AstrBot。

---

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/models` | 模型清单（含别名行） |
| POST | `/v1/chat/completions` | OpenAI Chat Completions，支持 `stream:true` |
| POST | `/v1/responses` | OpenAI Responses |
| POST | `/v1/messages` | Anthropic Messages，支持 `stream:true`（含 thinking / tool_use 块） |
| POST | `/v1/messages/count_tokens` | Token 本地估算（插件不含 tokenizer） |
| GET | `/` `/health` | 存活探测，不需要 key |

除了 `/` 与 `/health`，所有请求都要带 `Authorization: Bearer <key>`（或 `x-api-key`）。

流式响应是标准 SSE，思考阶段由 15 秒一次的注释帧保活，客户端不会误判断连。

---

## 模型与模式

网关广播这些"虚拟模型"，客户端写哪个就走哪条路：

| 模型名 | 行为 | 什么时候用 |
|---|---|---|
| **`auto`** / `smart` | 自动判断：带图片→视觉模型，超长输入→大窗口模型，否则按优先级 | 不想操心时（推荐） |
| **`text`** | **只走纯文本模型**，请求带图片直接 **400** | 确定不会有图，想避开视觉模型 |
| **`vision`** | **只走多模态（视觉）模型** | 要识图，或想固定用视觉模型 |
| `default` | 固定指向 `kilo-auto/free` | 兼容旧配置 |
| 真实模型 id | 精确指定，不经路由 | 调试或锁定某个模型 |

`/v1/models` 每行都带 **`supports_vision`** 字段（`true` / `false`），客户端可以自己过滤。

当前它返回 **29 行** = 3 个模式（`auto` / `text` / `vision`）+ 1 个别名（`default`）+ **25 个真实模型**。其中 `supports_vision: true` 的有 **8 个**——`auto`、`vision` 是虚拟模型，真实模型里只有这 6 个支持视觉：

```
mimo-v2.6-flash-free,
nvidia/nemotron-3.5-super-vl:free,
dots-studio/dots-3-note-preview:free,
nvidia/nemotron-3.5-content-safety:free,
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,
openrouter/free
```

> 这些数字（29 行 / 25 个模型 / 6 个视觉模型）是 2026-10-26 的实测值，会随上游清单变化，**以 `/v1/models` 的实时返回为准**。

> `nemotron-3.5-super-vl-free`（不带 `nvidia/` 前缀的短名）被插件标为 text，因为它按名字前缀匹配；实际能力以 `/v1/models` 的 `supports_vision` 为准。

### 三种选择怎么挑

- **AstrBot 只配一个提供商** → 用 `auto`。带图的消息自动走视觉模型，不带图走文本，最省事。
- **AstrBot 想明确分开**（比如配两个提供商、两个模型）→ 一个填 `text`，一个填 `vision`，base_url 和 key 都一样。什么时候用哪个由你在 AstrBot 里选。
- **同一会话里图文混发** → 用 `auto` 或 `vision`；用 `text` 会直接 400，这是故意的，避免图片被静默丢掉。

### 后面的模型失效了，会自动换吗

**取决于模型那一栏填的是什么：**

| 你填的 | 那个模型被限流 / 下架 / 报错时 |
|---|---|
| `auto` / `text` / `vision` | **会换**。每个请求重新挑候选，失败换下一个；失败模型熔断 5 分钟，到期自动放回 |
| `default` | **不会**。固定指向 `kilo-auto/free`，它挂了就一直报错 |
| 具体模型 id（`deepseek-v4.1-flash-free` 等） | **不会**。直接透传给上游，网关不插手 |

两个边界：

- **已经开始输出正文就不再切换**——否则调用方会收到两段拼接的回答。思考阶段的帧会先缓冲，所以"想了很久才撞上配额墙"是能安全切走的。
- **候选全部失败就返回 502**，网关不会凭空变出模型。这时只能等额度重置，或换一条上游车道。

所以推荐：**AstrBot 里只配一个提供商、模型填 `auto`**。别把多个具体模型注册成一堆 provider —— AstrBot 自己不会在 provider 之间自动切换（它用的是"当前会话的对话模型"），配得越多，反而把切换权锁死在 AstrBot 手里，模型一挂就得手动改。

### `auto` 怎么判断

每次请求进来，网关按这个顺序排候选：

1. **带图片** → 先试 `routing.vision` 里的视觉模型
2. **输入 ≥ `routing.longContextTokens`**（默认 12 万 token）→ 先试 `routing.longContext` 里的大窗口模型
3. **其它** → 按 `routing.prefer` 的优先级
4. 以上都不行 → 兜底试清单里其它可用模型

选出候选后逐个尝试，**失败自动换下一个**（最多 `candidates` 个）：

- 429 限流、403 地区封锁、超时、上游报错，只要**还没输出正文**，就静默换下一个模型，调用方完全无感。
- 思考阶段（`reasoning-delta`）的帧会先缓冲，换模型时丢弃，所以不会出现"两段思考拼在一起"。
- 失败过的模型会被**熔断** `cooldownMinutes` 分钟（默认 5），避免每个请求都先撞一次墙。

实测（本机出口 opencode 车道全挂时）：`auto` 先试 `deepseek-v4.1-flash-free`，0.9 秒后拿到 429，自动切到 `kilo-auto/free` 并正常出字，客户端只看到一次 200。

```bash
# 客户端写 auto，剩下交给网关
curl http://127.0.0.1:18899/v1/chat/completions \
  -H "Authorization: Bearer ofm-xxxx" -H "content-type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
```

要看当前有哪些真实模型，以及**哪些真的能出字**：

```powershell
$env:OFM_KEY="ofm-xxxx"
node scripts\check-models.mjs
```

这个脚本会逐个模型发一次极短请求，输出 `OK` / `FAIL` 与失败原因。**它会消耗免费额度，只在需要时跑。**

> 实测记录（2026 年 10 月，本机出口）：opencode 车道（`deepseek-v4.1-flash-free`、`mimo-*`、`glm-*` 等短名模型）返回 `Free usage limit reached` 或 `not available in your country`；Kilo 车道（`kilo-auto/free`、`openrouter/free`、`*:free` 系列）13/25 可用。
> 这是上游按出口 IP 的配额/地区策略，不是网关的问题。换网络、等额度重置、或用插件里的出口代理再试，清单会变。

---

## 配置 `config.json`

| 字段 | 默认 | 说明 |
|---|---|---|
| `host` | `127.0.0.1` | 本机监听地址。**只接受回环地址**（`127.0.0.1` / `::1` / `localhost`） |
| `port` | `18899` | 本机监听端口，占用时自动顺延 |
| `dataDir` | `data` | 插件状态目录（settings / stats / catalog） |
| `apiKey` | `""` | 本机 Key。空 = 沿用已有 key，没有则自动生成 |
| `probeOnStart` | `false` | 启动时探测每个模型可用性。会消耗额度，默认关 |
| `verbose` | `false` | 打印每个请求的诊断行 |
| `refreshMinutes` | `60` | 模型清单自动刷新间隔，`0` = 只在启动时刷新 |
| `aliases` | `default` → `kilo-auto/free` | 模型别名表，键是客户端写的名字，值是真实模型 id |
| `routing.enabled` | `true` | 是否启用 `auto` / `smart` 智能路由 |
| `routing.prefer` | `kilo-auto/free` 起 5 个 | 普通请求的模型优先级（第一个能用的就用） |
| `routing.vision` | 视觉模型优先 | 请求带图片时的优先级 |
| `routing.longContext` | `kilo-auto/free` 等 | 长输入时的优先级（大窗口模型） |
| `routing.longContextTokens` | `120000` | 超过多少估算 token 视为长输入 |
| `routing.candidates` | `3` | 每个请求最多尝试几个候选模型 |
| `routing.cooldownMinutes` | `5` | 失败模型熔断多少分钟 |
| `lan.enabled` | `false` | 是否开局域网中继（Docker / 同网段设备需要） |
| `lan.host` | `0.0.0.0` | 中继监听地址 |
| `lan.port` | `18900` | 中继端口，**不能和本机端口相同** |
| `lan.key` | `""` | 局域网 Key。空 = 自动生成，与本机 Key 互不通用 |

环境变量覆盖：`OUR_FREE_MODEL_API_KEY`、`OUR_FREE_MODEL_PORT`。

关于 `lan`：中继只放行 `/v1/models`、`/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/messages/count_tokens` 五条路径（各自不带 `/v1` 的写法也放行），入口处把 LAN Key 换成本机 Key，因此：

- 本机 Key 打到中继端口 → 401；LAN Key 打到本机端口 → 401（两把钥匙互不通用，泄露只需轮换对应一侧）。
- 中继端口连 `/health`、`/` 都要 key，不会向子网宣告"这台机器在代理"。
- 要用这扇门就把 `lan.enabled` 改成 `true`（默认关闭，避免一 clone 就向子网暴露）。

---

## 目录结构

```
our-free-model-gateway/
├─ server.mjs                  入口：配置 → runtime → 网关 → 打印接入信息
├─ config.json                 配置
├─ package.json                元信息与 npm scripts
├─ start.cmd / start.sh        Windows / Linux-macOS 启动
├─ LICENSE                     MIT
├─ THIRD_PARTY_NOTICES.md      第三方组件与来源声明
├─ vendor/our-free-model/      上游代码（setup 生成，**不进仓库**，MIT）
│  └─ src/
│     ├─ core/runtime.js       createRuntimeStores / createModelRuntime（纯 Node）
│     ├─ forward.js            startForwardServer：协议、SSE、鉴权、端口顺延
│     ├─ adapter.js            推理适配器：工具指纹、思考档位、降级链、恢复
│     ├─ upstream.js           上游端点、客户端指纹、会话 id
│     └─ ...
├─ data/                       运行时生成（已在 .gitignore 中）
│  ├─ settings.json            forwardKey 在这里
│  ├─ catalog.json             模型清单缓存
│  ├─ stats.json               用量统计
│  └─ models.txt               最近一次刷新的模型清单
└─ scripts/
   ├─ check-models.mjs         逐个模型探测可用性
   ├─ modes.mjs                text / vision 模式分流与 400 边界
   ├─ validate.mjs             请求体校验：缺字段/类型错的 400 回归
   ├─ smoke.mjs                冒烟：非流式 + 流式
   ├─ protocols.mjs            /v1/messages、count_tokens、/v1/responses
   └─ tools.mjs                function calling
```

---

## 常见问题

**Q：请求返回 502，message 是 `Free usage limit reached` 或 `not available in your country`？**
上游拒绝了。前者是该出口 IP 的免费额度用完（等重置或换出口），后者是地区封锁。用 `auto` 让网关自己换到能用的模型，比写死一个模型稳。

**Q：需要局域网里的另一台设备 / Docker 容器调用？**
已经内置了，就是 `lan` 那组配置（**默认关闭**）。把 `config.json` 的 `lan.enabled` 设为 `true`，重启后控制台会打印 `http://<局域网IP>:18900/v1` 和一把独立的局域网 Key。Docker 里用 `host.docker.internal:18900`。代价是同网段拿到这把 key 的人都能消耗本机免费额度，所以：只在可信网络开，不用了就关掉。

**Q：Windows 防火墙拦住了局域网访问？**
第一次监听 `0.0.0.0` 时系统会弹「是否允许 Node.js 通过防火墙」，选**允许**（至少勾选专用网络）。没弹窗或点错了，就用管理员 PowerShell 补一条：

```powershell
New-NetFirewallRule -DisplayName "Our Free Model GW" -Direction Inbound -Protocol TCP -LocalPort 18900 -Action Allow
```

**Q：key 泄露了怎么办？**
本机 Key 改 `config.json` 的 `apiKey`；局域网 Key 改 `lan.key`（或清空让它重新生成）。重启生效，对应一侧立即失效，另一侧不受影响。

**Q：怎么让它开机自启 / 常驻？**
用任务计划程序指向 `start.cmd`，或：

```powershell
schtasks /create /tn "OurFreeModelGateway" /tr "node D:\dsh\free-tokens\server.mjs" /sc onlogon
```

**Q：上游插件升级了怎么办？**
改一行命令，`data/` 和 `config.json` 不动：

```powershell
node scripts/setup.mjs 2.2.0
```

---

## 边界

- 上游是 **OpenCode Zen** 和 **Kilo AI** 的公开免费池，额度、地区、可用模型都由对方决定，随时可能变；不是无限量，也不保证稳定。
- 这条车道能走通的机制是：上游把免费额度发给"看起来像官方桌面客户端"的请求（`Authorization: Bearer public` 加一组客户端指纹头、规范的会话/请求 id）。**对方一旦改校验，车道就会断**，本仓库的设计目标不是对抗它，也不会跟着去打补丁绕过。
- 本网关只是把插件已经写好的客户端链路接到一个独立端口上，**不产生任何新的模型能力**，也**不绕过**上游的配额与地区限制。
- 插件作者的 README 明确：这是客户端，不是模型服务方；请勿转售或公开共享该免费额度的访问权。
- `data/settings.json` 里的 key 是明文（Node 在 Windows 上不强制执行 0600，也不是 ACL 保护），把它当本机文件对待。
- 模型输出由上游生成，可能有错，重要用途请自行核实。
- **适合个人本机使用**。公开部署、多租户、对外转卖都不在支持范围内——既违反上游条款，也会让所有人的通道被收紧。

---

## 许可证与来源

本仓库代码以 **MIT** 发布，见 [LICENSE](LICENSE)。

`vendor/` **不在本仓库里**。`scripts/setup.mjs` 在运行时从 npm 拉取 [`dsh-our-free-model`](https://www.npmjs.com/package/dsh-our-free-model) 2.1.0（MIT，Copyright (c) 2026 Ebony-Vinyl），按 import 闭包只保留 47 个文件写进 `vendor/`。仓库本身不含任何第三方代码——这样既不用重分发别人的代码，clone 下来也不会多出几百 KB 用不上的东西。第三方组件的完整声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

运行时访问的 OpenCode Zen 与 Kilo AI 网关不是本仓库的组件，也不受本仓库许可证约束——使用它们即表示你自行接受其服务条款。

### 致谢

- [`dsh-our-free-model`](https://www.npmjs.com/package/dsh-our-free-model) —— 上游客户端链路、协议实现与本项目的核心依赖
- `astrbot_plugin_our_free_model`（NekoHome，尚未公开仓库）—— AstrBot 侧接入方式与诊断设计的参考
- [`WorkBuddy-Access-Platform`](https://github.com/mydanyi/WorkBuddy-Access-Platform) —— 内嵌网关与主备降级思路的参考
---

## 本仓库额外内容

在官方网关之外，这里还放了几样配套的东西（都在上游没有的目录里，随时可以删掉）：

| 路径 | 内容 |
|---|---|
| `astrbot-plugin/astrbot_plugin_our_free_model/` | AstrBot 插件源码：把网关注册成 AstrBot 的 `openai_chat_completion` 提供商，带 `/ofm` 指令、`ruff.toml` 与 112 项离线自测 |
| `tools/build_plugin_zip.py` | 打 AstrBot 上传包（顶层目录 + `metadata.yaml`，只含运行时文件） |
| `tools/verify-plugin-zip.mjs` | 按 AstrBot v4.28 的归档校验规则预检 zip，上传前先跑一遍 |
| `tools/test-gateway.mjs` | 网关接口用例（鉴权、三套协议、流式、模式边界、并发、大请求体） |
| `patches/0001-images-and-vendor-gaps.patch` | 本仓库相对上游的改动（图片判断 + vendor 两处补丁） |
| `patches/astrbot-4.28.1-stat-timezone.patch` | AstrBot 4.28.1 统计页 naive datetime 崩溃的一行修复 |
| `docs/ASTRBOT-PLUGIN.md` | 插件两种链路、配置项与常见上传失败原因 |
| `docs/PATCH-NOTES.md` | 网关相对上游改了什么、怎么跑、重装 vendor 后怎么重打补丁 |
| `docs/TEST_REPORT.md` | 实测报告与已知问题 |

插件打包与自检：

```bash
python tools/build_plugin_zip.py
node tools/verify-plugin-zip.mjs dist/astrbot_plugin_our_free_model-*.zip
```
