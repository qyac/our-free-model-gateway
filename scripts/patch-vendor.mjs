#!/usr/bin/env node
/**
 * 给 vendor/our-free-model 打两个本地补丁。可重复执行（幂等），
 * `node scripts/setup.mjs` 重装 vendor 之后再跑一次即可。
 *
 *   1. 非流式 JSON 响应补 CORS 头
 *      preflight 有 access-control-allow-origin，但真正带数据的响应没有，
 *      浏览器里的前端（不是桌面客户端）会被 CORS 拦掉。
 *
 *   2. 接受 `api-key` 请求头（Azure 风格）
 *      现在只认 `authorization: Bearer …` 和 `x-api-key`。不少客户端只会发
 *      `api-key`，接了就是一句 401，排查起来很费劲。
 *
 * 用法：
 *   node scripts/patch-vendor.mjs           # 打补丁
 *   node scripts/patch-vendor.mjs --check   # 只检查（已打/未打），不写文件
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const TARGET = path.join(ROOT, 'vendor', 'our-free-model', 'src', 'forward.js')
const checkOnly = process.argv.includes('--check')

if (!fs.existsSync(TARGET)) {
  console.error(`\n[缺少依赖] ${TARGET}\n  先运行：node scripts/setup.mjs\n`)
  process.exit(1)
}

const PATCHES = [
  {
    name: 'CORS 头补到非流式 JSON 响应',
    from: "  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body), 'cache-control': 'no-store' })",
    to: "  res.writeHead(status, { ...corsHeaders(), 'content-type': 'application/json', 'content-length': Buffer.byteLength(body), 'cache-control': 'no-store' })",
  },
  {
    name: '接受 api-key 请求头（Azure 风格）',
    from: "  const key = req.headers['x-api-key']",
    to: "  const key = req.headers['x-api-key'] ?? req.headers['api-key']",
  },
]

const source = fs.readFileSync(TARGET, 'utf8')
let patched = source
const report = []

for (const patch of PATCHES) {
  if (patched.includes(patch.to)) {
    report.push(`已打过  ${patch.name}`)
    continue
  }
  if (!patched.includes(patch.from)) {
    report.push(`跳过    ${patch.name}（上游代码变了，没找到锚点）`)
    continue
  }
  patched = patched.replace(patch.from, patch.to)
  report.push(`打上    ${patch.name}`)
}

for (const line of report) console.log(`  ${line}`)

if (checkOnly) {
  const done = report.every(line => line.startsWith('已打过'))
  console.log(done ? '\n[检查] 补丁齐全' : '\n[检查] 还有未打的补丁')
  process.exit(done ? 0 : 1)
}

if (patched === source) {
  console.log('\n[完成] 无需改动')
  process.exit(0)
}

// 备份一次，便于回滚（不覆盖已有备份）。
const backup = `${TARGET}.orig`
if (!fs.existsSync(backup)) fs.copyFileSync(TARGET, backup)
fs.writeFileSync(TARGET, patched, 'utf8')
console.log(`\n[完成] 已写入 ${path.relative(ROOT, TARGET)}（原文件备份在 ${path.basename(backup)}）`)
console.log('       重启网关生效：node server.mjs')
