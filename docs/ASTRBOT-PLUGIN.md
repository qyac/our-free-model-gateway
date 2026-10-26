# AstrBot 插件：astrbot_plugin_our_free_model

把本网关（以及它背后的免费模型池）接进 AstrBot，注册成 AstrBot 内置的
`openai_chat_completion` 提供商。源码在 [`astrbot-plugin/`](../astrbot-plugin/)，
上传包用脚本现打。

## 两种链路

```
nami 模式（默认）：AstrBot ──插件──► Nami (:8787/v1) ──NAMI_OFM_URL──► 本网关 (:18899/v1)
direct 模式：      AstrBot ──插件──────────────────────────────────► 本网关 (:18899/v1)
```

只跑本网关、没跑 Nami 的话用 `direct`，配置两项即可：

| 配置项 | 值 |
|---|---|
| `relay_mode` | `direct` |
| `gateway_base_url` | 网关打印的 `Base URL`，默认 `http://127.0.0.1:18899/v1` |
| `gateway_api_key` | 网关打印的 `API Key`（`ofm-…`），不是局域网 Key |

AstrBot 在 Docker 里时，容器里的 `127.0.0.1` 是容器自己：把网关 `config.json` 的
`lan.enabled` 改成 `true`，重启后用宿主地址 `http://<宿主IP>:18900/v1` + 局域网 Key。

## 打包与安装

```bash
# 1) 打上传包（只含运行时文件，顶层目录 + metadata.yaml）
python tools/build_plugin_zip.py            # → dist/astrbot_plugin_our_free_model-1.1.0.zip

# 2) 上传前自查（按 AstrBot v4.28 的校验规则）
node tools/verify-plugin-zip.mjs dist/astrbot_plugin_our_free_model-1.1.0.zip

# 3) WebUI → 插件 → 安装插件 → 选这个 zip
#    或直接把 astrbot-plugin/astrbot_plugin_our_free_model 整个目录拷进 data/plugins/
```

> 上传失败报「未找到 metadata.yaml 或 metadata.yml」，基本都是包的问题：传了别的
> zip（比如网关包）、用了 tar.gz、或者包里除了单个顶层目录还混了根目录散文件
> （`desktop.ini`、`.DS_Store`）。AstrBot 是先算「归档根目录」再找
> `<根目录>/metadata.yaml`，根目录一乱就找不到。

## 指令

| 指令 | 作用 |
|---|---|
| `/ofm models` | 列出入口上的模型，`✓` 表示已注册 |
| `/ofm status` | 模式、入口地址、鉴权、已注册数量；nami 模式还会报 Nami 的 `/healthz`、`/readyz` |
| `/ofm sync` | 按配置重新同步（幂等） |
| `/ofm use <模型ID>` | 把当前会话的对话模型切过去 |
| `/ofm test [模型ID]` | 打一次最小对话请求，验证真能用 |
| `/ofm cleanup` | 移除本插件注册的全部 `ofm-` 提供商 |

## 开发

```bash
python -m ruff check astrbot-plugin/astrbot_plugin_our_free_model       # 配置见插件目录 ruff.toml
python -m ruff format --check astrbot-plugin/astrbot_plugin_our_free_model
python astrbot-plugin/astrbot_plugin_our_free_model/tests/selftest.py   # 112 项断言，不需要真 AstrBot
```

`tests/` 里有 `astrbot` 替身包和假入口（假 Nami 带 `/healthz`、`/readyz`，假 OFM 只有
OpenAI 兼容接口），所以自测完全离线。
