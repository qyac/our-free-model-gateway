#!/usr/bin/env node
// 独立测试脚本：只依赖网关本身，不 import 仓库代码。
// 用法：node test-gateway.mjs <baseUrl> <apiKey>
const BASE = (process.argv[2] ?? 'http://127.0.0.1:18899').replace(/\/+$/, '')
const KEY = process.argv[3]
if (!KEY) {
  console.error('用法: node test-gateway.mjs <baseUrl> <apiKey>')
  process.exit(2)
}

const results = []
const record = (name, ok, detail) => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ${detail ?? ''}`)
}

async function req(path, { method = 'GET', body, headers = {}, raw = false, timeoutMs = 120000 } = {}) {
  const started = Date.now()
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'content-type': 'application/json', ...headers },
    body: body === undefined ? undefined : (typeof body === 'string' ? body : JSON.stringify(body)),
    signal: AbortSignal.timeout(timeoutMs),
  })
  const text = await res.text()
  return { status: res.status, text, headers: res.headers, ms: Date.now() - started, raw }
}

const auth = { authorization: `Bearer ${KEY}` }
const shortChat = (model, extra = {}) => ({
  model,
  messages: [{ role: 'user', content: '只回复两个字：你好' }],
  max_tokens: 64,
  ...extra,
})
const imageChat = (model) => ({
  model,
  messages: [{
    role: 'user',
    content: [
      { type: 'text', text: '这张图是什么颜色？' },
      { type: 'image_url', image_url: { url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==' } },
    ],
  }],
  max_tokens: 32,
})

// ---------- 1. 无需鉴权的存活探测 ----------
try {
  const r = await req('/health')
  const json = JSON.parse(r.text)
  record('GET /health (无 key)', r.status === 200 && json.ok === true, `${r.status} ${r.text.slice(0, 80)}`)
  const root = await req('/')
  record('GET / (无 key)', root.status === 200, `${root.status} ${root.text.slice(0, 60)}`)
} catch (error) { record('GET /health', false, error.message) }

// ---------- 2. 鉴权 ----------
{
  const noKey = await req('/v1/models')
  record('GET /v1/models 无 key → 401', noKey.status === 401, `${noKey.status} ${noKey.text.slice(0, 90)}`)
  const badKey = await req('/v1/models', { headers: { authorization: 'Bearer wrong-key' } })
  record('GET /v1/models 错误 key → 401', badKey.status === 401, `${badKey.status}`)
  const bearer = await req('/v1/models', { headers: auth })
  record('GET /v1/models 正确 key → 200', bearer.status === 200, `${bearer.status} bytes=${bearer.text.length}`)
  const xApiKey = await req('/v1/models', { headers: { 'x-api-key': KEY } })
  record('GET /v1/models x-api-key 变体 → 200', xApiKey.status === 200, `${xApiKey.status}`)
  try {
    const json = JSON.parse(bearer.text)
    const ids = json.data.map(row => row.id)
    record('/v1/models 结构 (object=list, data 数组)', json.object === 'list' && Array.isArray(json.data), `rows=${json.data.length}`)
    record('/v1/models 含虚拟模型 auto/text/vision', ['auto', 'text', 'vision'].every(id => ids.includes(id)), ids.slice(0, 6).join(','))
    record('/v1/models 每行带 supports_vision', json.data.every(row => typeof row.supports_vision === 'boolean'), '')
  } catch (error) { record('/v1/models JSON 解析', false, error.message) }
}

// ---------- 3. 聊天（非流式 / 流式） ----------
{
  const r = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('auto') })
  let json = {}
  try { json = JSON.parse(r.text) } catch { /* keep raw */ }
  const text = json?.choices?.[0]?.message?.content ?? ''
  record('POST /v1/chat/completions auto 非流式', r.status === 200 && text !== '', `${r.status} ${r.ms}ms model=${json?.model ?? '?'} text=${JSON.stringify(text).slice(0, 60)}`)
  record('响应结构 (choices/usage)', Array.isArray(json?.choices) && json?.usage !== undefined, `usage=${JSON.stringify(json?.usage ?? null)}`)

  const s = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('auto', { stream: true }) })
  const events = s.text.split('\n').filter(line => line.startsWith('data:'))
  const hasDone = s.text.includes('[DONE]')
  const deltas = events.map(line => { try { return JSON.parse(line.slice(5)) } catch { return null } }).filter(Boolean)
  const content = deltas.map(d => d?.choices?.[0]?.delta?.content ?? '').join('')
  record('POST /v1/chat/completions stream=true', s.status === 200 && events.length > 0 && hasDone, `${s.status} ${s.ms}ms events=${events.length} done=${hasDone} contentLen=${content.length}`)
  record('流式分片是 SSE (data: 前缀 + [DONE])', events.length > 0 && hasDone, '')
  record('流式内容非空', content.length > 0, JSON.stringify(content).slice(0, 60))
}

// ---------- 4. 模式语义 ----------
{
  const t = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('text') })
  let tj = {}; try { tj = JSON.parse(t.text) } catch {}
  record('text 模式纯文本请求 → 200', t.status === 200 && (tj?.choices?.[0]?.message?.content ?? '') !== '', `${t.status} ${t.ms}ms`)

  const bad = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: imageChat('text') })
  let bj = {}; try { bj = JSON.parse(bad.text) } catch {}
  record('text 模式 + 图片 → 400（不静默丢图）', bad.status === 400, `${bad.status} ${String(bj?.error?.message ?? bad.text).slice(0, 80)}`)

  const v = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: imageChat('vision') })
  record('vision 模式 + 图片 → 2xx', v.status >= 200 && v.status < 300, `${v.status} ${v.ms}ms ${v.text.slice(0, 100).replace(/\s+/g, ' ')}`)
}

// ---------- 5. 错误处理 ----------
{
  const unknown = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('no-such-model-xyz') })
  record('未知模型 → 4xx/5xx 且带错误体', unknown.status >= 400, `${unknown.status} ${unknown.text.slice(0, 120).replace(/\s+/g, ' ')}`)

  const badJson = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: '{not json' })
  record('非法 JSON → 400', badJson.status === 400, `${badJson.status} ${badJson.text.slice(0, 100)}`)

  const noMessages = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: { model: 'auto' } })
  record('缺 messages → 4xx', noMessages.status >= 400 && noMessages.status < 500, `${noMessages.status} ${noMessages.text.slice(0, 100).replace(/\s+/g, ' ')}`)

  const noAuthChat = await req('/v1/chat/completions', { method: 'POST', body: shortChat('auto') })
  record('聊天接口无 key → 401', noAuthChat.status === 401, `${noAuthChat.status}`)

  const wrongMethod = await req('/v1/chat/completions', { headers: auth })
  record('GET 聊天接口 → 405/404', [404, 405].includes(wrongMethod.status), `${wrongMethod.status}`)

  const unknownPath = await req('/v1/does-not-exist', { headers: auth })
  record('未知路径 → 404', unknownPath.status === 404, `${unknownPath.status}`)
}

// ---------- 6. Anthropic 协议 ----------
{
  const r = await req('/v1/messages', {
    method: 'POST', headers: auth,
    body: { model: 'auto', max_tokens: 64, messages: [{ role: 'user', content: '只回复两个字：你好' }] },
  })
  let json = {}; try { json = JSON.parse(r.text) } catch {}
  const block = Array.isArray(json?.content) ? json.content.find(b => b.type === 'text') : undefined
  record('POST /v1/messages (Anthropic)', r.status === 200 && typeof block?.text === 'string' && block.text !== '', `${r.status} ${r.ms}ms type=${json?.type ?? '?'} text=${JSON.stringify(block?.text ?? '').slice(0, 40)}`)

  const s = await req('/v1/messages', {
    method: 'POST', headers: auth,
    body: { model: 'auto', max_tokens: 64, stream: true, messages: [{ role: 'user', content: '数到三' }] },
  })
  record('/v1/messages stream=true → SSE 事件', s.status === 200 && /event:\s*(message_start|content_block_delta)/.test(s.text), `${s.status} ${s.ms}ms ${s.text.slice(0, 80).replace(/\n/g, '\\n')}`)

  const count = await req('/v1/messages/count_tokens', {
    method: 'POST', headers: auth,
    body: { model: 'auto', messages: [{ role: 'user', content: '你好世界，这是一段用于统计 token 的文本。' }] },
  })
  let cj = {}; try { cj = JSON.parse(count.text) } catch {}
  record('POST /v1/messages/count_tokens', count.status === 200 && Number.isFinite(cj?.input_tokens), `${count.status} input_tokens=${cj?.input_tokens}`)
}

// ---------- 7. Responses 协议 ----------
{
  const r = await req('/v1/responses', {
    method: 'POST', headers: auth,
    body: { model: 'auto', input: '只回复两个字：你好', max_output_tokens: 64 },
  })
  record('POST /v1/responses', r.status === 200, `${r.status} ${r.ms}ms ${r.text.slice(0, 140).replace(/\s+/g, ' ')}`)
}

// ---------- 8. 并发 ----------
{
  const started = Date.now()
  const settled = await Promise.allSettled(
    Array.from({ length: 5 }, () => req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('auto') })),
  )
  const codes = settled.map(item => (item.status === 'fulfilled' ? item.value.status : 'ERR'))
  const ok = codes.filter(code => code === 200).length
  record('5 路并发 auto (至少 3 成功)', ok >= 3, `${ok}/5 ok in ${Date.now() - started}ms codes=${codes.join(',')}`)
}

// ---------- 9. 大请求体 ----------
{
  const big = 'x'.repeat(1024 * 1024)
  const r = await req('/v1/chat/completions', { method: 'POST', headers: auth, body: shortChat('auto', { messages: [{ role: 'user', content: `忽略这段填充，只回复 OK。${big}` }] }) })
  record('1MB 请求体（超长输入走大窗口）', r.status === 200 || (r.status >= 400 && r.status < 600 && r.text.length > 0), `${r.status} ${r.ms}ms ${r.text.slice(0, 80).replace(/\s+/g, ' ')}`)
}

const passed = results.filter(item => item.ok).length
console.log(`\n结果：${passed}/${results.length} 通过`)
process.exit(passed === results.length ? 0 : 1)
