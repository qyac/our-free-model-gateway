#!/usr/bin/env node
// 复刻 AstrBot v4.28.1 的插件包校验逻辑，用来在上传前自检 zip：
//   astrbot/core/zip_updater.py :: _resolve_archive_root_dir()
//   astrbot/core/star/updater.py :: find_plugin_metadata_entry / validate_plugin_metadata
//
// 用法: node verify-plugin-zip.mjs <plugin.zip> [...]
//
// 为什么要精确复刻：AstrBot 的判定不是"包里任意位置有 metadata.yaml"，
// 而是先解析出"归档根目录"，再要求 <根目录>/metadata.yaml 精确存在。
// 只要包里有任何一个顶层散文件（desktop.ini / .DS_Store / 一个 README），
// 根目录会被判成 ""，于是变成"必须在 zip 根目录下找 metadata.yaml"。

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'

const METADATA_NAMES = ['metadata.yaml', 'metadata.yml']
const REQUIRED_FIELDS = ['name', 'desc', 'version', 'author']

/** zip 条目列表：用 bsdtar 列（Windows 自带），避免额外依赖。 */
function entriesOf(zipPath) {
  const tar = process.platform === 'win32'
    ? `${process.env.SystemRoot ?? 'C:\\Windows'}\\System32\\tar.exe`
    : 'tar'
  const out = execFileSync(tar, ['-tf', zipPath], { encoding: 'utf8' })
  return out.split(/\r?\n/).filter(line => line !== '')
}

/** os.path.normpath 的最小等价：去掉 "." 段和空段。 */
function normalize(entry) {
  const parts = entry.split(/[\\/]/).filter(part => part !== '' && part !== '.')
  return parts.join('/')
}

/** 移植 _resolve_archive_root_dir()。 */
function resolveRootDir(entries) {
  const portable = entries.map(normalize)
  const candidates = []
  for (let i = 0; i < entries.length; i += 1) {
    const raw = entries[i]
    const self = portable[i]
    if (self === '') continue
    const hasChildren = portable.some((other, j) => j !== i && other !== self && other.startsWith(`${self}/`))
    if (raw.endsWith('/') || raw.endsWith('\\') || hasChildren) {
      candidates.push(self)
      continue
    }
    const parent = self.includes('/') ? self.slice(0, self.lastIndexOf('/')) : ''
    // 顶层散文件：AstrBot 直接把根目录判成 ""，后面就只认 zip 根下的 metadata.yaml
    if (parent === '') return ''
    candidates.push(parent)
  }
  if (candidates.length === 0) return ''
  // os.path.commonpath：逐段取公共前缀
  const split = candidates.map(item => item.split('/'))
  const first = split[0]
  const common = []
  for (let i = 0; i < first.length; i += 1) {
    if (split.every(parts => parts[i] === first[i])) common.push(first[i])
    else break
  }
  return common.join('/')
}

function findMetadataEntry(entries, root) {
  for (const name of METADATA_NAMES) {
    const candidate = root === '' ? name : `${root}/${name}`
    if (entries.map(normalize).includes(candidate)) return candidate
  }
  return null
}

function check(zipPath) {
  console.log(`\n=== ${zipPath} ===`)
  if (!fs.existsSync(zipPath)) return false
  const entries = entriesOf(zipPath)
  const root = resolveRootDir(entries)
  console.log(`条目数   : ${entries.length}`)
  console.log(`归档根目录: ${root === '' ? '（zip 根，无外层目录）' : root}`)
  const topLevelFiles = entries.map(normalize).filter(item => item !== '' && !item.includes('/'))
  if (topLevelFiles.length > 0 && root === '') {
    console.log(`顶层散文件: ${topLevelFiles.join(', ')}`)
  }

  const metadataEntry = findMetadataEntry(entries, root)
  if (metadataEntry === null) {
    console.log('结果     : ✗ AstrBot 会拒绝 —— 未找到 metadata.yaml / metadata.yml')
    return false
  }
  console.log(`metadata : ✓ ${metadataEntry}`)

  // 解压出 metadata.yaml 做字段校验（等价 validate_plugin_metadata 的必要字段检查）
  const tar = process.platform === 'win32'
    ? `${process.env.SystemRoot ?? 'C:\\Windows'}\\System32\\tar.exe`
    : 'tar'
  let text = ''
  try {
    text = execFileSync(tar, ['-xOf', zipPath, metadataEntry], { encoding: 'utf8' })
  } catch (error) {
    console.log(`metadata 读取失败: ${error.message}`)
    return false
  }
  const missing = REQUIRED_FIELDS.filter(field => !new RegExp(`^${field}\\s*:\\s*\\S`, 'm').test(text))
  if (missing.length > 0) {
    console.log(`结果     : ✗ 缺少必需字段: ${missing.join(', ')}`)
    return false
  }
  const nameLine = (text.match(/^name\s*:\s*(\S+)/m) ?? [])[1] ?? ''
  console.log(`插件名   : ${nameLine}`)
  console.log('结果     : ✓ 可以上传')
  return true
}

const targets = process.argv.slice(2)
if (targets.length === 0) {
  console.error('用法: node verify-plugin-zip.mjs <plugin.zip> [...]')
  process.exit(2)
}
let allOk = true
for (const target of targets) allOk = check(target) && allOk
process.exit(allOk ? 0 : 1)
