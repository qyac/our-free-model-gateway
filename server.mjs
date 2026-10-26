#!/usr/bin/env node
/**
 * Our Free Model —— 独立网关（standalone gateway）
 *
 * 把 dsh-our-free-model 插件的推理链路从 dsh 宿主里摘出来，做成一个单独的
 * Node 进程：监听一个本机端口，对外提供 OpenAI / Anthropic 兼容的 HTTP API。
 * 别的程序只要填 base_url + api_key 就能用，不需要安装 dsh，也不需要开 GUI。
 *
 * 复用（零改动）的插件模块：
 *   vendor/our-free-model/src/core/runtime.js  —— createRuntimeStores / createModelRuntime
 *   vendor/our-free-model/src/forward.js       —— startForwardServer（协议、SSE、鉴权、端口顺延）
 *
 * vendor/ 不在仓库里：第三方代码按需下载。首次运行前先执行 `node scripts/setup.mjs`。
 *
 * 上游链路完全由插件自己决定（opencode.ai Zen 网关 / api.kilo.ai 免费池），
 * 本文件只做：读配置、准备数据目录、签发 key、启动监听、打印接入信息。
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.dirname(fileURLToPath(import.meta.url))
const CONFIG_FILE = path.join(ROOT, 'config.json')
const VENDOR_DIR = path.join(ROOT, 'vendor', 'our-free-model')
const PKG_FILE = path.join(VENDOR_DIR, 'package.json')

// 静态 import 会在文件加载阶段就抛 MODULE_NOT_FOUND，这里先给一句人话。
if (!fs.existsSync(path.join(VENDOR_DIR, 'src', 'core', 'runtime.js'))) {
  console.error('\n[缺少依赖] vendor/our-free-model 还没有准备好。')
  console.error('  先运行：node scripts/setup.mjs')
  console.error('  再启动：node server.mjs\n')
  process.exit(1)
}

const { createRuntimeStores, createModelRuntime } = await import('./vendor/our-free-model/src/core/runtime.js')
const { generateKey, rankLanAddresses, startForwardServer, startLanRelay } = await import('./vendor/our-free-model/src/forward.js')

/** 缺省配置。config.json 只写需要覆盖的字段即可。 */
const DEFAULTS = {
  /** 监听地址。本版本只接受回环地址（127.0.0.1 / ::1 / localhost）。 */
  host: '127.0.0.1',
  /** 监听端口。被占用时自动顺延，并在控制台打印实际端口。 */
  port: 18899,
  /** 插件状态目录（相对本文件，或绝对路径）：settings.json / stats.json / catalog.json。 */
  dataDir: 'data',
  /**
   * 调用方要携带的 API Key。留空表示：沿用 data/settings.json 里已有的
   * forwardKey，没有则自动生成一枚 ofm-... 并落盘。
   */
  apiKey: '',
  /** 启动时是否做一轮可用性探测。会为每个模型发一次极短请求（消耗免费额度），默认关闭。 */
  probeOnStart: false,
  /** 打印每个请求的诊断行。排查问题时打开。 */
  verbose: false,
  /** 模型清单的自动刷新间隔（分钟）。0 表示只在启动时刷新。 */
  refreshMinutes: 60,
  /**
   * 模型别名：调用方写左边的名字，网关转成右边的真实模型 id。
   * 这样客户端不必记住上游那一串 kilo-/nvidia- 前缀，也能一键换默认模型。
   */
  aliases: {
    default: 'kilo-auto/free',
  },
  /**
   * 智能路由：客户端把模型写 `auto`（或 `smart`）时，网关自己判断用哪个真实模型。
   *
   * 判断依据来自请求本身：带不带图片、输入有多长、以及 prefer 列表的优先级。
   * 选中的模型失败（429/403/超时等，且尚未输出正文）时自动换下一个候选，
   * 并把失败的模型熔断一段时间，避免每个请求都先撞一次墙。
   */
  routing: {
    enabled: true,
    /** 普通对话的优先级（第一个能用就用它）。 */
    prefer: ['kilo-auto/free', 'openrouter/free', 'cohere/north-mini-code:free'],
    /** 请求带图片时优先这些（需支持视觉）。 */
    vision: ['nvidia/nemotron-3.5-super-vl:free', 'kilo-auto/free'],
    /** 输入超过 longContextTokens 时优先这些（上下文窗口要够）。 */
    longContext: ['kilo-auto/free', 'openrouter/free'],
    longContextTokens: 120000,
    /** 最多尝试几个候选（含首选）。 */
    candidates: 3,
    /** 失败后熔断多少分钟。 */
    cooldownMinutes: 5,
  },
  /**
   * 局域网中继：把网关再开一扇门给同网段的设备 / Docker 容器用
   * （AstrBot 跑在容器里时，容器的 127.0.0.1 是它自己，必须走宿主的可路由地址）。
   * 默认关闭。开启后使用独立的 LAN Key，本机 key 不会外泄；只放行 /v1/* 白名单。
   */
  lan: {
    enabled: false,
    host: '0.0.0.0',
    port: 18900,
    key: '',
  },
}

function fail(message) {
  console.error(`\n[致命] ${message}\n`)
  process.exit(1)
}

function loadConfig() {
  let raw = {}
  if (fs.existsSync(CONFIG_FILE)) {
    try {
      raw = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'))
    } catch (error) {
      fail(`config.json 不是合法 JSON：${error.message}`)
    }
  }
  const merged = { ...DEFAULTS, ...raw }

  // 环境变量优先，方便临时改端口/换 key，不必动文件。
  if (typeof process.env.OUR_FREE_MODEL_API_KEY === 'string' && process.env.OUR_FREE_MODEL_API_KEY.trim() !== '') {
    merged.apiKey = process.env.OUR_FREE_MODEL_API_KEY.trim()
  }
  const envPort = Number(process.env.OUR_FREE_MODEL_PORT)
  if (Number.isFinite(envPort) && envPort > 0) merged.port = Math.trunc(envPort)

  if (typeof merged.host !== 'string' || merged.host.trim() === '') merged.host = '127.0.0.1'
  merged.host = merged.host.trim()
  const port = Number(merged.port)
  merged.port = Number.isFinite(port) && port > 0 ? Math.trunc(port) : DEFAULTS.port
  if (typeof merged.dataDir !== 'string' || merged.dataDir.trim() === '') merged.dataDir = DEFAULTS.dataDir
  const refresh = Number(merged.refreshMinutes)
  merged.refreshMinutes = Number.isFinite(refresh) && refresh >= 0 ? Math.min(refresh, 24 * 60) : DEFAULTS.refreshMinutes

  // 别名表：过滤掉空名/空值，避免把 404 留到请求时才暴露。
  const aliases = merged.aliases && typeof merged.aliases === 'object' && !Array.isArray(merged.aliases) ? merged.aliases : {}
  merged.aliases = {}
  for (const [name, target] of Object.entries(aliases)) {
    const key = String(name ?? '').trim()
    const value = String(target ?? '').trim()
    if (key === '' || value === '') continue
    if (key === value) continue
    merged.aliases[key] = value
  }

  // 智能路由配置：列表去空、数值兜底。
  const routing = merged.routing && typeof merged.routing === 'object' && !Array.isArray(merged.routing) ? merged.routing : {}
  const idList = value => (Array.isArray(value) ? value : [])
    .map(item => String(item ?? '').trim()).filter(item => item !== '')
  const routingNumber = (value, fallback, min, max) => {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? Math.min(Math.max(parsed, min), max) : fallback
  }
  merged.routing = {
    enabled: routing.enabled !== false,
    prefer: idList(routing.prefer?.length ? routing.prefer : DEFAULTS.routing.prefer),
    vision: idList(routing.vision?.length ? routing.vision : DEFAULTS.routing.vision),
    longContext: idList(routing.longContext?.length ? routing.longContext : DEFAULTS.routing.longContext),
    longContextTokens: routingNumber(routing.longContextTokens, DEFAULTS.routing.longContextTokens, 1000, 10_000_000),
    candidates: Math.trunc(routingNumber(routing.candidates, DEFAULTS.routing.candidates, 1, 10)),
    cooldownMinutes: routingNumber(routing.cooldownMinutes, DEFAULTS.routing.cooldownMinutes, 0, 24 * 60),
  }

  // 局域网中继：端口不能与本地监听相同（否则中继会拨回自己），调用方需独立 key。
  const lan = merged.lan && typeof merged.lan === 'object' && !Array.isArray(merged.lan) ? merged.lan : {}
  const lanPort = Number(lan.port)
  merged.lan = {
    enabled: lan.enabled === true,
    host: String(lan.host ?? '').trim() || '0.0.0.0',
    port: Number.isFinite(lanPort) && lanPort > 0 ? Math.trunc(lanPort) : DEFAULTS.lan.port,
    key: typeof lan.key === 'string' ? lan.key.trim() : '',
  }
  if (merged.lan.enabled && merged.lan.port === merged.port) {
    fail(`局域网中继端口不能和本地端口相同（都是 ${merged.port}）。请把 config.json 的 lan.port 改一个别的值，例如 18900。`)
  }
  return merged
}

/** 回环判断。真正的 DNS 校验交给插件的 resolveLoopbackBind。 */
function looksLoopback(host) {
  const value = String(host ?? '').trim().toLowerCase()
  if (value === '' || value === 'localhost') return true
  const literal = value.replace(/^\[|\]$/g, '')
  return literal === '::1' || literal.startsWith('127.')
}

/** 只把 forward 相关字段写进插件设置；其他字段保留用户/插件自己的值。 */
function applySettings(settings, cfg) {
  settings.edit(value => ({
    ...value,
    enabled: true,
    forward: {
      ...(value.forward ?? {}),
      enabled: true,
      host: cfg.host,
      port: cfg.port,
      lan: { ...(value.forward?.lan ?? {}), enabled: false },
    },
  }))
  settings.flush()
}

/**
 * 取（或签发）一枚 key，落盘到 data/settings.json 的指定字段。
 * @param {object} settings 插件设置 store
 * @param {'forwardKey'|'forwardLanKey'} field 字段名
 * @param {string} [override] config.json / 环境变量给的值，优先
 */
function ensureKey(settings, field, override) {
  if (typeof override === 'string' && override.trim() !== '') return override.trim()
  const existing = settings.get()[field]
  if (typeof existing === 'string' && existing.trim() !== '') return existing.trim()
  const minted = generateKey()
  settings.update({ [field]: minted })
  settings.flush()
  return minted
}

function readPluginVersion() {
  try {
    return String(JSON.parse(fs.readFileSync(PKG_FILE, 'utf8')).version ?? '')
  } catch {
    return ''
  }
}

const RULE = '─'.repeat(60)

function banner(title) {
  console.log(`\n${RULE}\n  ${title}\n${RULE}`)
}

function printEndpoint({ url, key, dataDir, version }) {
  banner(`Our Free Model 独立网关${version ? `  v${version}` : ''}`)
  console.log(`  Base URL    ${url}`)
  console.log(`  API Key     ${key}`)
  console.log(`  模型清单    ${url}/models`)
  console.log(`  数据目录    ${dataDir}`)
  console.log(`${RULE}\n`)
}

function printModels(rows, limit = 30) {
  if (rows.length === 0) {
    console.log('[模型] 清单为空——首次拉取可能还没完成，稍后重试 /v1/models。\n')
    return
  }
  console.log(`[模型] 当前可用 ${rows.length} 个：`)
  for (const row of rows.slice(0, limit)) {
    const ctx = Number.isFinite(row.context_window) ? `  ctx ${row.context_window}` : ''
    const tag = row.id === 'vision' || row.supports_vision === true ? '  [视觉]' : row.id === 'text' ? '  [纯文本]' : ''
    console.log(`  • ${row.id}${ctx}${tag}`)
  }
  if (rows.length > limit) console.log(`  … 其余 ${rows.length - limit} 个见 /v1/models 或 data/models.txt`)
  console.log('')
}

function writeModelsFile(dataDir, rows) {
  try {
    const text = rows
      .map(row => `${row.id}\t${row.context_window ?? ''}\t${row.supports_vision === true ? 'vision' : 'text'}`)
      .join('\n') + '\n'
    fs.writeFileSync(path.join(dataDir, 'models.txt'), text, 'utf8')
  } catch (error) {
    console.warn(`[warn] 写 data/models.txt 失败：${error?.message ?? error}`)
  }
}

async function main() {
  const cfg = loadConfig()

  if (!looksLoopback(cfg.host)) {
    fail(`本版本只支持本机回环监听（127.0.0.1 / ::1 / localhost），当前配置是 "${cfg.host}"。\n`
      + '       需要给局域网里的其它设备用时，请把 host 改回 127.0.0.1；\n'
      + '       插件自带的中继（startLanRelay + 独立 LAN key）可以在此基础上再加，但默认关闭。')
  }

  const dataDir = path.isAbsolute(cfg.dataDir) ? cfg.dataDir : path.join(ROOT, cfg.dataDir)
  fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 })

  const logger = {
    warn: message => console.warn(`[warn] ${message}`),
    // 插件里写的是 `logger.warn?.(...) ?? logger.log?.(...)`，warn 返回 undefined
    // 会接着调 log；给它一个空实现，避免同一条警告打两遍。
    log: () => {},
    info: message => { if (cfg.verbose) console.log(`[info] ${message}`) },
  }

  const stores = createRuntimeStores({ dataDir, logger })
  applySettings(stores.settings, cfg)
  const key = ensureKey(stores.settings, 'forwardKey', cfg.apiKey)

  const runtime = createModelRuntime({ ...stores, logger })

  // 别名在网关入口解析，插件内部仍然只看见真实模型 id。
  const aliasNames = Object.keys(cfg.aliases)
  const resolveAlias = name => cfg.aliases[name] ?? name

  // ── 智能路由 ────────────────────────────────────────────────────────────────
  // 客户端把 model 写成 auto / smart 时，由下面这套逻辑决定实际用哪个模型。

  /** 熔断表：失败过的模型在冷却期内不再被选为候选。 */
  const cooldowns = new Map()
  const cooling = id => (cooldowns.get(id) ?? 0) > Date.now()
  const cool = id => {
    if (cfg.routing.cooldownMinutes <= 0) return
    cooldowns.set(id, Date.now() + cfg.routing.cooldownMinutes * 60_000)
  }

  /**
   * 请求里有没有真正的图片块。
   *
   * 以前是 `JSON.stringify(...).includes('image_url')`：用户正文里只要提到
   * `image_url` 这个词（问接口怎么用、贴一段 JSON），text 模式就会误判成
   * "带图片"直接 400，auto 模式也会白白路由到视觉模型。
   * 改成按结构化 content 块判断，认 OpenAI chat / Responses / Anthropic 三种图片块。
   */
  const IMAGE_BLOCK_TYPES = new Set(['image', 'image_url', 'input_image'])
  const isImageBlock = block => block !== null
    && typeof block === 'object'
    && !Array.isArray(block)
    && IMAGE_BLOCK_TYPES.has(block.type)
  const contentHasImage = value => Array.isArray(value)
    && value.some(item => item !== null && typeof item === 'object' && (
      isImageBlock(item)
      || (Array.isArray(item.content) && item.content.some(isImageBlock))
    ))
  const wantsVision = request => contentHasImage(request.messages)
    || contentHasImage(request.openAi?.messages)
    || contentHasImage(request.openAi?.input)

  /** 粗略估算输入长度（字符数 / 3，中英混排在数量级上够用）。 */
  const estimateInputTokens = request => {
    try {
      const text = JSON.stringify(request.openAi?.messages ?? request.openAi?.input ?? request.messages ?? '')
      return Math.ceil(text.length / 3)
    } catch {
      return 0
    }
  }

  /** 当前真的能被路由到的模型（插件探测后不可用的已从 membership 剔除）。 */
  const routableIds = () => new Set(Object.values(runtime.state().membership).flat())

  /** 按请求特征排出候选模型，已剔除不可用与熔断中的。 */
  const pickCandidates = request => {
    const usable = routableIds()
    const catalog = runtime.state().catalog
    const known = id => catalog.some(entry => entry.id === id)
    const order = []
    const add = list => {
      for (const id of list) {
        if (order.includes(id) || !usable.has(id) || cooling(id) || !known(id)) continue
        order.push(id)
      }
    }
    if (wantsVision(request)) add(cfg.routing.vision)
    if (estimateInputTokens(request) >= cfg.routing.longContextTokens) add(cfg.routing.longContext)
    add(cfg.routing.prefer)
    // 兜底：清单里其它可用模型，保证总有东西可试。
    for (const entry of catalog) {
      if (!usable.has(entry.id) || cooling(entry.id) || order.includes(entry.id)) continue
      if (String(entry.id).startsWith('combo:')) continue
      order.push(entry.id)
    }
    return order.slice(0, Math.max(1, cfg.routing.candidates))
  }

  /** 统一的重试/降级循环：按候选顺序试，出正文前失败就换下一个。 */
  const completeWithFallback = async (request, candidates, onChunk) => {
    if (candidates.length === 0) {
      throw Object.assign(new Error('此刻没有符合条件且可用的模型（全部不可达或正在冷却），请稍后再试'), { statusCode: 503 })
    }
    let lastError
    for (const id of candidates) {
      // 思考帧先缓冲：本轮若在出正文前失败，这些帧就丢掉换下一个模型，
      // 调用方不会看到两段拼接的思考。正文一旦发出就不能再换。
      let committed = false
      let buffered = []
      const flush = () => {
        for (const chunk of buffered) onChunk?.(chunk)
        buffered = []
      }
      const forwardChunk = chunk => {
        const thinking = chunk.type === 'reasoning-delta'
          || (chunk.type === 'block-end' && chunk.block?.type === 'reasoning')
        if (thinking && !committed) { buffered.push(chunk); return }
        // 失败帧先压着：能换模型就换，换不了（已经出正文）再由上层转发错误。
        if (!committed && chunk.type === 'finish' && chunk.reason?.kind === 'error') return
        committed = true
        flush()
        onChunk?.(chunk)
      }
      try {
        if (cfg.verbose) console.log(`[router] ${request.model} → ${id}`)
        const result = await runtime.complete({ ...request, model: id }, forwardChunk)
        // 插件的失败是写在返回值里的，不是抛出来的。
        if (result?.error !== undefined && !committed) {
          buffered = []
          lastError = Object.assign(new Error(String(result.error)), { statusCode: 502 })
          cool(id)
          if (cfg.verbose) console.warn(`[router] ${id} 失败（${lastError.message}），换下一个`)
          continue
        }
        flush()
        return result
      } catch (error) {
        buffered = []
        lastError = error
        if (committed) throw error
        cool(id)
        if (cfg.verbose) console.warn(`[router] ${id} 失败（${error?.message ?? error}），换下一个`)
      }
    }
    throw lastError ?? Object.assign(new Error('所有候选模型都失败了'), { statusCode: 502 })
  }

  /**
   * 对外广播的三个模式（另外保留 `smart` 作为 auto 的别名）：
   *   auto   —— 按请求自动判断（带图转视觉、长文转大窗口）
   *   text   —— 只走纯文本模型，带图片直接 400
   *   vision —— 只走多模态模型
   */
  const modeNames = cfg.routing.enabled ? ['auto', 'text', 'vision'] : []

  const isVisionModel = id => runtime.state().catalog.find(entry => entry.id === id)?.vision === true

  /** 某一模式下按顺序排出的候选模型。 */
  const candidatesForMode = mode => {
    const usable = routableIds()
    const catalog = runtime.state().catalog
    const order = []
    const push = id => {
      if (order.includes(id) || !usable.has(id) || cooling(id)) return
      order.push(id)
    }
    if (mode === 'vision') {
      // 只收插件标注了 vision 的模型：routing.vision 里没标注的（如 kilo-auto）跳过。
      for (const id of cfg.routing.vision) if (isVisionModel(id)) push(id)
      for (const entry of catalog) if (entry.vision === true) push(entry.id)
    } else {
      for (const id of cfg.routing.prefer) if (!isVisionModel(id)) push(id)
      for (const entry of catalog) if (entry.vision !== true) push(entry.id)
    }
    return order.slice(0, Math.max(1, cfg.routing.candidates))
  }

  /** /v1/models：模式行 + 别名行 + 真实模型，每行带 supports_vision。 */
  const modelRows = () => {
    const created = Math.floor(Date.now() / 1000)
    const capability = id => {
      if (id === 'vision' || id === 'auto' || id === 'smart') return true
      if (id === 'text') return false
      return isVisionModel(id)
    }
    const modeRows = modeNames.map(id => ({ id, object: 'model', created, owned_by: 'our-free-model:mode' }))
    const aliasRows = aliasNames.map(id => ({ id, object: 'model', created, owned_by: 'our-free-model:alias' }))
    const rows = [...modeRows, ...aliasRows, ...runtime.publicModelRows()]
    return rows.map(row => ({ ...row, supports_vision: capability(row.id) }))
  }

  /**
   * 请求体基本校验：缺字段 / 类型错在本地就报 400。
   *
   * 不校验的后果是这些请求被原样转给上游，上游回一个 HTTP 400，再被网关包成
   * 502「upstream HTTP 400」——调用方看到的是一次服务端错误，实际是自己传错了。
   * Anthropic 端点（/v1/messages）已由 forward.js 的 fromAnthropicRequest 校验，
   * 这里只补 OpenAI 的两条路。
   */
  const validateBody = request => {
    const invalid = message => Object.assign(new Error(message), { statusCode: 400 })

    // Anthropic 路径：forward 已经投影成 harness messages。
    if (Array.isArray(request.messages)) {
      if (request.messages.length === 0) throw invalid('`messages` must not be empty')
      return
    }

    const body = request.openAi
    if (body === null || typeof body !== 'object') return

    const hasMessages = Array.isArray(body.messages)
    // responses 端点：forward 一定构造了 input 数组（可能为空）。
    const hasInput = Object.hasOwn(body, 'input')
    if (!hasMessages && !hasInput) {
      throw invalid('`messages` is required and must be a non-empty array')
    }
    if (hasMessages) {
      if (body.messages.length === 0) throw invalid('`messages` must not be empty')
      for (const [index, row] of body.messages.entries()) {
        if (row === null || typeof row !== 'object' || Array.isArray(row)) {
          throw invalid(`\`messages[${index}]\` must be an object`)
        }
        if (row.role !== undefined && typeof row.role !== 'string') {
          throw invalid(`\`messages[${index}].role\` must be a string`)
        }
      }
    } else if (!Array.isArray(body.input) || body.input.length === 0) {
      throw invalid('`input` must be a non-empty string or array')
    }
    if (body.tools !== undefined && !Array.isArray(body.tools)) {
      throw invalid('`tools` must be an array')
    }
  }

  const complete = async (request, onChunk) => {
    validateBody(request)
    const mode = request.model
    // 明确选了纯文本模式却带图片：直接说清楚，不静默丢图、也不偷偷换模型。
    if (mode === 'text' && wantsVision(request)) {
      throw Object.assign(
        new Error('text 模式只走纯文本模型，不接受图片输入；请把 model 改成 vision（只用多模态模型）或 auto（自动判断）'),
        { statusCode: 400 },
      )
    }
    if (mode === 'text' || mode === 'vision') {
      return completeWithFallback(request, candidatesForMode(mode), onChunk)
    }
    if (mode === 'auto' || mode === 'smart') {
      return completeWithFallback(request, pickCandidates(request), onChunk)
    }
    const target = resolveAlias(mode)
    return runtime.complete(target === mode ? request : { ...request, model: target }, onChunk)
  }

  const server = await startForwardServer({
    config: () => ({ host: cfg.host, port: cfg.port, enabled: true, key }),
    complete,
    modelRows,
    log: message => console.warn(`[forward] ${message}`),
    onTrace: event => { if (cfg.verbose) console.log(`[trace] ${JSON.stringify(event)}`) },
    health: () => ({ ok: true, service: 'our-free-model', version: readPluginVersion() }),
  })

  const actualPort = server.port
  const baseUrl = `http://${cfg.host}:${actualPort}/v1`
  if (server.fellBack === true) {
    console.warn(`[warn] 端口 ${server.requestedPort} 被占用（${server.bindError?.code ?? '未知'}），`
      + `已改用 ${actualPort}。\n`)
  }

  printEndpoint({ url: baseUrl, key, dataDir, version: readPluginVersion() })
  if (modeNames.length > 0) {
    console.log(`  模型模式    ${modeNames.join(' / ')}（smart = auto）`)
    console.log('              auto 按请求自动挑 · text 只走纯文本 · vision 只走多模态')
    console.log(`              首选 ${cfg.routing.prefer.join(' → ')}，失败自动换下一个\n`)
  }
  if (aliasNames.length > 0) {
    console.log(`  模型别名    ${aliasNames.map(name => `${name} → ${cfg.aliases[name]}`).join(' · ')}\n`)
  }

  // 可选的局域网中继：本机之外的门。用独立 key，入口处换成本机 key，
  // 因此 LAN key 泄露只需轮换它，本机已配好的客户端不受影响。
  let relay = null
  if (cfg.lan.enabled) {
    const lanKey = ensureKey(stores.settings, 'forwardLanKey', cfg.lan.key)
    try {
      relay = await startLanRelay({
        config: () => ({
          enabled: true,
          host: cfg.lan.host,
          port: cfg.lan.port,
          lanKey,
          localKey: key,
          targetPort: actualPort,
        }),
        log: message => console.warn(`[lan] ${message}`),
        onTrace: event => { if (cfg.verbose) console.log(`[trace:lan] ${JSON.stringify(event)}`) },
      })
      const addresses = rankLanAddresses(os.networkInterfaces())
      const first = addresses[0]
      console.log(`  局域网中继  ${first === undefined ? `（未找到可路由网卡，监听 ${cfg.lan.host}:${relay.port}）` : `http://${first}:${relay.port}/v1`}`)
      console.log(`  局域网 Key  ${lanKey}`)
      if (addresses.length > 1) console.log(`  其它网卡    ${addresses.slice(1).map(ip => `http://${ip}:${relay.port}/v1`).join(' · ')}`)
      console.log('')
    } catch (error) {
      relay = null
      console.warn(`[warn] 局域网中继启动失败：${error?.message ?? error}（本机端点不受影响）\n`)
    }
  }

  console.log('  端点：GET /v1/models · POST /v1/chat/completions · POST /v1/responses')
  console.log('        POST /v1/messages · POST /v1/messages/count_tokens\n')

  // 启动后的第一次清单刷新：不阻塞监听，但通常几秒内完成。
  const refresh = runtime.refreshCatalog({ probe: cfg.probeOnStart })
    .then(() => {
      const rows = modelRows()
      writeModelsFile(dataDir, rows)
      printModels(rows)
    })
    .catch(error => {
      console.warn(`[warn] 模型清单刷新失败：${error?.message ?? error}（继续使用 data/catalog.json 里的缓存清单）`)
      const rows = modelRows()
      writeModelsFile(dataDir, rows)
      printModels(rows)
    })
  await Promise.race([refresh, new Promise(resolve => setTimeout(resolve, 20_000))])

  if (cfg.refreshMinutes > 0) {
    const timer = setInterval(() => {
      void runtime.refreshCatalog({ probe: false })
        .then(() => writeModelsFile(dataDir, modelRows()))
        .catch(error => console.warn(`[warn] 定时刷新模型清单失败：${error?.message ?? error}`))
    }, cfg.refreshMinutes * 60_000)
    timer.unref?.()
  }

  let shuttingDown = false
  const shutdown = async signal => {
    if (shuttingDown) return
    shuttingDown = true
    console.log(`\n[关闭] 收到 ${signal}，正在停止…`)
    await relay?.close().catch(() => {})
    await server.close().catch(() => {})
    runtime.dispose()
    stores.dispose?.()
    process.exit(0)
  }
  process.on('SIGINT', () => void shutdown('SIGINT'))
  process.on('SIGTERM', () => void shutdown('SIGTERM'))
}

main().catch(error => {
  fail(`网关启动失败：${error?.stack ?? error?.message ?? error}`)
})
