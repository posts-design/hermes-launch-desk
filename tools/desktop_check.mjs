#!/usr/bin/env node
// Checks desktop/plugin.js the way Hermes Desktop loads it, without Desktop:
//
//   1. imports: only @hermes/plugin-sdk, react and react/jsx-runtime (the loader refuses others);
//      with --sdk-src, every name it imports from the SDK must be a real SDK export;
//   2. it parses as an ES module (no JSX: the file is loaded uncompiled);
//   3. with --react, it is loaded through a specifier rewrite (as Desktop does) against a stub
//      SDK, register(ctx) is called, and the page and status chip are rendered to HTML for the
//      loaded, empty, error and loading states. The facts come back as JSON on stdout.
//   4. with --out, a static preview page of the loaded state is written (light and dark).
//
// node tools/desktop_check.mjs [--react <node_modules>] [--sdk-src <apps/desktop/src>]
//                              [--data <packs.json>] [--out <preview.html>]
// --data is JSON {"packs": <GET /packs body>, "images": {<name>: <GET /packs/<name>/images body>}}
// (tools/desktop_preview_data.py writes one). Exit code 1 on any failed check.

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const PLUGIN = path.join(ROOT, 'desktop', 'plugin.js')
const ALLOWED = new Set(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'])

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, a, i, all) => (a.startsWith('--') ? [...pairs, [a.slice(2), all[i + 1]]] : pairs), [])
)
const failures = []
const fail = msg => failures.push(msg)
const report = { imports: [], sdkNames: [] }

// ------------------------------------------------------------------ 1. imports
const source = fs.readFileSync(PLUGIN, 'utf8')
const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"])\/\/[^\n]*/g, '$1')
const specifiers = [
  ...code.matchAll(/\bimport\s+(?:[\s\S]*?\bfrom\s*)?['"]([^'"]+)['"]/g),
  ...code.matchAll(/\bexport\s+[^;]*?\bfrom\s*['"]([^'"]+)['"]/g),
  ...code.matchAll(/\bimport\s*\(\s*([^)]*)\)/g)
].map(m => m[1])
report.imports = [...new Set(specifiers)]
for (const s of report.imports) if (!ALLOWED.has(s)) fail(`import not allowed in a Desktop plugin: ${s}`)

const sdkImport = code.match(/import\s*\{([^}]*)\}\s*from\s*['"]@hermes\/plugin-sdk['"]/)
report.sdkNames = sdkImport ? sdkImport[1].split(',').map(n => n.trim().split(/\s+as\s+/)[0]).filter(Boolean) : []

if (args['sdk-src']) {
  const sdkDir = path.join(args['sdk-src'], 'sdk')
  const exported = new Set()
  for (const file of ['index.ts', 'areas.ts']) {
    const text = fs.readFileSync(path.join(sdkDir, file), 'utf8')
    for (const block of text.matchAll(/export\s*\{([^}]*)\}/g))
      for (const part of block[1].split(',')) {
        const name = part.trim().replace(/^type\s+/, '').split(/\s+as\s+/).pop()
        if (name && !part.trim().startsWith('type ')) exported.add(name)
      }
    for (const m of text.matchAll(/export\s+(?:const|function|class)\s+([A-Za-z_$][\w$]*)/g)) exported.add(m[1])
    for (const m of text.matchAll(/export\s+\*\s+as\s+([A-Za-z_$][\w$]*)/g)) exported.add(m[1])
  }
  const routes = fs.readFileSync(path.join(args['sdk-src'], 'app', 'routes.ts'), 'utf8')
  for (const m of routes.matchAll(/export\s+const\s+([A-Z_]+)\s*=/g)) exported.add(m[1])
  for (const name of report.sdkNames) if (!exported.has(name)) fail(`not an export of @hermes/plugin-sdk: ${name}`)
  report.sdkChecked = true
}

// ------------------------------------------------------------------ 2. parses as a module
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'ld-desktop-check-'))
try {
  const parsed = path.join(work, 'parse.mjs')
  fs.writeFileSync(parsed, source)
  execFileSync(process.execPath, ['--check', parsed], { stdio: 'pipe' })
  report.parses = true
} catch (err) {
  fail(`does not parse as an ES module: ${String(err.stderr || err.message).split('\n').slice(0, 5).join(' ')}`)
}

// ------------------------------------------------------------------ 3. load, register, render
const FIXTURE = {
  packs: {
    checkins_due: 1,
    packs: [
      {
        name: '2026-11-06-acme-dark-mode', date: '2026-11-06', created: '2026-11-06T09:00:00+00:00',
        product: 'Acme', product_url: 'https://acme.test', network: 'x', what: 'dark mode',
        post: 'Most of our support emails asked for dark mode.\n\nDark mode is live in the editor.\n\nhttps://acme.test',
        hook: 'Most of our support emails asked for dark mode.', hook_source: 'founder', hooks: [],
        checks: [{ label: 'First line: 9 words', ok: true, detail: null }, { label: 'Fits X: 101/280', ok: true, detail: null }],
        references: [{ brand: 'Notion', views: 20483, url: 'https://posts.design/a' }, { brand: 'Linear', views: null, url: '' }],
        layout: 'statement', screenshots: 0, intent_url: 'https://x.com/intent/post?text=x', alt_text: 'Launch card for Acme.',
        has_card: true, has_preview: true,
        results: [{ channel: 'X', signups: 12, recorded_at: null }], signups: [{ channel: 'X', signups: 12, recorded_at: null }],
        checkins: [{ id: 'j1', state: 'scheduled', next_run_at: '2026-11-07T09:00:00+00:00', last_run_at: null, last_status: null }]
      },
      {
        name: '2026-11-05-acme-export', date: '2026-11-05', created: null, product: null, product_url: null, network: 'x',
        what: null, post: 'Export to CSV.', hook: 'Export to CSV.', hook_source: null, hooks: [], checks: null, references: [],
        layout: null, screenshots: null, intent_url: null, alt_text: null, has_card: false, has_preview: false,
        results: [], signups: [], checkins: []
      }
    ]
  },
  images: {}
}

async function loadPlugin(reactDir) {
  const require = createRequire(path.join(reactDir, 'noop.js'))
  const React = require('react')
  const runtime = require('react/jsx-runtime')
  const server = require('react-dom/server')
  globalThis.__LD_REACT__ = React
  globalThis.__LD_JSX__ = runtime
  const shim = (globalName, ns) => {
    const names = Object.keys(ns).filter(n => n !== 'default' && /^[A-Za-z_$][\w$]*$/.test(n))
    const file = path.join(work, `${globalName}.mjs`)
    fs.writeFileSync(file, `const m = globalThis.${globalName};\nexport default m.default ?? m;\nexport const { ${names.join(', ')} } = m;\n`)
    return pathToFileURL(file).href
  }
  const reactUrl = shim('__LD_REACT__', React)
  const jsxUrl = shim('__LD_JSX__', runtime)
  const stub = fs.readFileSync(path.join(HERE, 'desktop_sdk_stub.mjs'), 'utf8').replace(/from 'react'/g, `from '${reactUrl}'`)
  fs.writeFileSync(path.join(work, 'sdk.mjs'), stub)
  const map = { '@hermes/plugin-sdk': pathToFileURL(path.join(work, 'sdk.mjs')).href, 'react/jsx-runtime': jsxUrl, react: reactUrl }
  const rewritten = source.replace(/(\bfrom\s*|\bimport\s*)(['"])(@hermes\/plugin-sdk|react\/jsx-runtime|react)\2/g, (_m, pre, q, spec) => `${pre}${q}${map[spec]}${q}`)
  const entry = path.join(work, 'plugin.mjs')
  fs.writeFileSync(entry, rewritten)
  const mod = await import(pathToFileURL(entry).href)
  return { React, server, mod }
}

function fakeCtx(contributions) {
  const ctx = {
    source: 'plugin:launch-desk',
    register: c => (contributions.push(c), () => {}),
    registerMany: cs => (contributions.push(...cs), () => {}),
    rest: () => Promise.reject(new Error('not in a check')),
    os: { openExternal: async () => true, writeClipboard: async () => true },
    storage: { get: (_k, d) => d, set() {}, remove() {} },
    onDispose() {},
    setTimeout: () => () => {},
    setInterval: () => () => {}
  }
  return ctx
}

const count = (html, re) => (html.match(re) || []).length

if (args.react) {
  const data = args.data ? JSON.parse(fs.readFileSync(args.data, 'utf8')) : FIXTURE
  const { React, server, mod } = await loadPlugin(args.react)
  const stub = globalThis.__LD_STUB__
  const plugin = mod.default
  if (!plugin || plugin.id !== 'launch-desk') fail(`default export id must be launch-desk, got ${plugin && plugin.id}`)
  if (typeof plugin?.register !== 'function') fail('default export has no register(ctx)')

  const contributions = []
  const ctx = fakeCtx(contributions)
  plugin.register(ctx)
  report.contributions = contributions.map(c => ({ id: c.id, area: c.area, path: c.data?.path ?? null, hasRender: typeof c.render === 'function' }))
  const route = contributions.find(c => c.area === 'routes')
  const nav = contributions.find(c => c.area === 'sidebar.nav')
  const chip = contributions.find(c => c.area === 'statusBar.right')
  const palette = contributions.find(c => c.area === 'palette')
  if (!route || route.data?.path !== '/launch-desk' || typeof route.render !== 'function') fail('no /launch-desk route')
  if (!nav || nav.data?.path !== route?.data?.path || !nav.data?.label || !nav.data?.codicon) fail('no sidebar entry for the route')
  if (!palette || typeof palette.data?.run !== 'function') fail('no palette command')
  palette?.data?.run?.()
  if (stub.navigated.at(-1) !== '/launch-desk') fail('palette command does not open the page')

  const warnings = []
  const render = (queries, node) => {
    stub.queries = queries
    const original = console.error
    console.error = (...a) => warnings.push(a.map(String).join(' ').slice(0, 300))
    try {
      return server.renderToStaticMarkup(node)
    } finally {
      console.error = original
    }
  }
  const packsKey = JSON.stringify(['launch-desk', 'packs'])
  const imageQueries = Object.fromEntries(
    Object.entries(data.images || {}).map(([name, body]) => [JSON.stringify(['launch-desk', 'images', name]), { status: 'success', data: body }])
  )
  const page = () => route.render()

  const loaded = render({ [packsKey]: { status: 'success', data: data.packs }, ...imageQueries }, page())
  const packs = data.packs.packs
  const founder = packs.filter(p => p.hook_source === 'founder').length
  report.loaded = {
    packs: count(loaded, /data-pack="/g),
    founderMarked: count(loaded, /data-founder-words="true"/g),
    copyButtons: count(loaded, /data-sdk="CopyButton"/g),
    images: count(loaded, /<img /g),
    checkins: count(loaded, /data-checkin="/g),
    channels: count(loaded, /data-channel="/g),
    emptyOrError: count(loaded, /data-sdk="(EmptyState|ErrorState)"/g)
  }
  const expect = (name, got, want) => got !== want && fail(`loaded page: ${name} ${got}, expected ${want}`)
  expect('pack cards', report.loaded.packs, packs.length)
  expect('founder hooks marked', report.loaded.founderMarked, founder)
  expect('copy buttons', report.loaded.copyButtons, packs.length)
  expect('check-ins shown', report.loaded.checkins, packs.reduce((n, p) => n + p.checkins.length, 0))
  expect('channels shown', report.loaded.channels, packs.reduce((n, p) => n + p.signups.length, 0))
  expect('empty or error states', report.loaded.emptyOrError, 0)
  expect('images', report.loaded.images, packs.filter(p => {
    const img = (data.images || {})[p.name]
    return img && (img.card || img.preview)
  }).length)
  for (const p of packs) {
    const html = loaded.split(`data-pack="${p.name}"`)[1]?.split('data-pack="')[0] ?? ''
    for (const r of p.references) if (r.brand && !html.includes(r.brand.replace(/&/g, '&amp;'))) fail(`reference ${r.brand} missing from ${p.name}`)
  }

  const empty = render({ [packsKey]: { status: 'success', data: { packs: [], checkins_due: 0 } } }, page())
  if (!empty.includes('data-sdk="EmptyState"') || empty.includes('data-pack=')) fail('no packs: the empty state is not shown')
  const error = render({ [packsKey]: { status: 'error', error: new Error('404: Not Found') } }, page())
  if (!error.includes('data-sdk="ErrorState"') || error.includes('data-pack=')) fail('backend off: the error state is not shown')
  const loading = render({ [packsKey]: { status: 'pending' } }, page())
  if (!loading.includes('data-sdk="Skeleton"')) fail('loading: no placeholder')
  report.states = { empty: true, error: true, loading: true }

  const chipDue = render({ [packsKey]: { status: 'success', data: { packs: [], checkins_due: 2 } } }, chip.render())
  const chipNone = render({ [packsKey]: { status: 'success', data: { packs: [], checkins_due: 0 } } }, chip.render())
  const chipOff = render({ [packsKey]: { status: 'error', error: new Error('off') } }, chip.render())
  if (!chipDue.includes('data-checkins-due="2"')) fail('status chip does not show check-ins due')
  if (chipNone !== '' || chipOff !== '') fail('status chip shows when nothing is due or the backend is off')
  report.chip = { due: true, hiddenWhenNone: chipNone === '', hiddenWhenOff: chipOff === '' }
  report.reactWarnings = warnings
  for (const w of warnings) fail(`React warned while rendering: ${w}`)

  if (args.out) {
    const css = fs.readFileSync(path.join(HERE, 'desktop_preview.css'), 'utf8')
    const pane = theme => `<section class="frame" data-theme="${theme}"><div class="titlebar">Launch Desk · ${theme}</div><div class="pane">${loaded}</div></section>`
    fs.writeFileSync(args.out, `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Launch Desk pane preview</title><style>${css}</style></head><body>${pane('light')}${pane('dark')}</body></html>\n`)
    report.preview = args.out
  }
}

fs.rmSync(work, { recursive: true, force: true })
report.failures = failures
process.stdout.write(JSON.stringify(report, null, 2) + '\n')
process.exit(failures.length ? 1 : 0)
