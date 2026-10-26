# 本地补丁说明（在 5932b47 之上）

这个包是 `our-free-model-gateway` 的**离线可运行版**：`vendor/` 已经下载好并打过补丁，
解压就能起，不需要联网跑 `setup.mjs`。

上游提交：`5932b47 docs: state the upstream fingerprint mechanism and personal-use scope`
本地补丁：`8758629 fix: detect images by content block, patch the two vendor gaps`（PR: https://github.com/beichen24a1/our-free-model-gateway/pull/1）

## 改了什么

1. `server.mjs` — 图片判断从「序列化后搜 `image_url` 字样」改成按结构化 content 块识别
   （`image` / `image_url` / `input_image`）。以前正文里提到 `image_url` 这个词，
   `text` 模式会误判成发图直接 400，`auto` 会白跑到视觉模型。
2. `vendor/our-free-model/src/forward.js` — 两个补丁（由 `scripts/patch-vendor.mjs` 施加）：
   - 非流式 JSON 响应补 `corsHeaders()`：浏览器里的前端不再被 CORS 拦；
   - 认证接受 Azure 风格的 `api-key` 请求头，不只是 `authorization: Bearer …` / `x-api-key`。

## 跑起来

### Linux / macOS

```bash
tar -xzf our-free-model-gateway-2.1.0-patched.tar.gz
cd our-free-model-gateway

bash install.sh      # 检查环境 + 装 vendor（已带则跳过）+ 打补丁，会逐项告诉你缺什么
./start.sh           # 起网关；或 bash start.sh
```

`install.sh` 会检查 Node 版本（需 ≥ 22.19，不达标直接给对应发行版的安装命令）、
`tar` 是否存在、vendor 是否就绪、补丁是否齐全、端口占用，最后打印启动方式。
只做体检、不动文件：`bash install.sh --check`；纯诊断报告：`bash check.sh`。

> `start.sh` 在 zip 里会丢可执行位（zip 不保存 Unix 权限），所以 zip 包请用
> `bash start.sh`；tar.gz 包保留了 755，可以 `./start.sh`。

常驻：

```bash
nohup ./start.sh > gateway.log 2>&1 &
```

### Windows

```powershell
# 解压后双击 开始使用.cmd（会自动找 Node、检查依赖、打补丁、启动）
# 或手动：
node server.mjs
```

### 通用

控制台会打印 `Base URL` 和一枚自动生成的 `API Key`（`ofm-…`，存在 `data/settings.json`）。
客户端填 `http://127.0.0.1:18899/v1` + 那把 key，模型填 `auto`。


换端口 / 固定 key：

```powershell
$env:OUR_FREE_MODEL_PORT = 18899
$env:OUR_FREE_MODEL_API_KEY = "sk-whatever"   # 或改 config.json 的 apiKey
node server.mjs
```

## 重装 vendor 的话

`node scripts/setup.mjs` 会重新下载上游代码、**冲掉补丁**，重装后再跑一次：

```powershell
node scripts/setup.mjs
node scripts/patch-vendor.mjs      # 或 npm run patch
```

`patch-vendor.mjs --check` 只检查不写盘；首次写入会留 `forward.js.orig` 备份。

## 包里的东西

```
our-free-model-gateway/
├─ server.mjs              入口（含图片判断补丁）
├─ config.json             配置（端口 / 别名 / 路由 / lan）
├─ package.json            npm scripts（start / setup / patch / smoke …）
├─ start.sh                启动（Linux/macOS）
├─ install.sh              环境检查 + 装依赖 + 打补丁（Linux/macOS）
├─ check.sh                纯诊断报告（Linux/macOS）
├─ 开始使用.cmd            一键启动（Windows）
├─ 诊断.cmd                纯诊断报告（Windows）
├─ start.cmd               直接启动（Windows）
├─ vendor/our-free-model/  上游 dsh-our-free-model 2.1.0（已打补丁，MIT）
├─ scripts/                setup / patch-vendor / smoke / protocols / modes / tools / check-models
├─ patches/                本次改动的 git patch（可 apply 到上游仓库）
└─ README.md               上游原始文档
```

不含 `data/`（首次启动自动生成，含本机 key）、不含运行日志。
