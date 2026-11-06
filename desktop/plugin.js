// Launch Desk for Hermes Desktop: a page with every launch pack /launch wrote.
//
// Plain ESM, loaded uncompiled by Desktop (no JSX). It reads the plugin's own backend
// (dashboard/plugin_api.py, mounted at /api/plugins/launch-desk) through ctx.rest, which
// only carries JSON, so images arrive as data URLs from /packs/<name>/images. Read-only:
// nothing here writes a pack, posts anything or schedules anything.

import {
  Badge,
  Button,
  Codicon,
  CopyButton,
  EmptyState,
  ErrorState,
  fmtDateTime,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  Skeleton,
  STATUSBAR_AREAS,
  useQuery
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'launch-desk'
const PATH = '/launch-desk'
const PACKS_KEY = [ID, 'packs']
const LAYOUTS = { hero: 'Hero card', statement: 'Statement card', split: 'Split card' }
const STATES = { completed: 'Done', error: 'Failed', paused: 'Paused' }

const muted = { color: 'var(--ui-text-tertiary)' }
const label = { ...muted, fontSize: '0.6875rem', fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase' }
const row = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 10px' }
const stack = gap => ({ display: 'flex', flexDirection: 'column', gap })

const fmtViews = n => (typeof n === 'number' ? `${new Intl.NumberFormat().format(n)} views` : null)

function fmtWhen(iso) {
  const date = iso ? new Date(iso) : null
  return date && !Number.isNaN(date.getTime()) ? fmtDateTime.format(date) : null
}

export const packsQuery = ctx => ({ queryKey: PACKS_KEY, queryFn: () => ctx.rest('/packs') })

async function openLink(ctx, url) {
  if (!url) return
  if (await ctx.os.openExternal(url).catch(() => false)) return
  const copied = await ctx.os.writeClipboard(url).catch(() => false)
  host.notify({ kind: 'error', message: copied ? "Couldn't open the browser, so the link is copied." : "Couldn't open the browser." })
}

// ------------------------------------------------------------------ pieces of a pack

function Section({ title, aside, children }) {
  return jsxs('section', {
    style: stack('6px'),
    children: [jsxs('div', { style: row, children: [jsx('span', { style: label, children: title }), aside] }), children]
  })
}

function CardImage({ ctx, pack }) {
  const [view, setView] = useState('card')
  const images = useQuery({
    queryKey: [ID, 'images', pack.name],
    queryFn: () => ctx.rest(`/packs/${encodeURIComponent(pack.name)}/images`),
    enabled: Boolean(pack.has_card || pack.has_preview),
    staleTime: Infinity
  })
  const src = images.data?.[view] ?? images.data?.card ?? images.data?.preview
  const frame = { width: '100%', borderRadius: 6, border: '1px solid var(--ui-stroke-secondary)' }

  let body
  if (images.isLoading) body = jsx(Skeleton, { style: { ...frame, aspectRatio: '16 / 9' } })
  else if (src) body = jsx('img', { src, alt: pack.alt_text || `Launch card for ${pack.product || pack.name}`, style: { ...frame, display: 'block', height: 'auto' } })
  else body = jsx('div', { style: { ...frame, ...muted, padding: 24, fontSize: '0.75rem', textAlign: 'center' }, children: 'No card image in this pack.' })

  return jsxs('div', {
    style: stack('8px'),
    children: [
      body,
      pack.has_card && pack.has_preview && jsx(SegmentedControl, {
        value: view,
        onChange: setView,
        options: [{ id: 'card', label: 'Card' }, { id: 'preview', label: 'In the feed' }]
      })
    ]
  })
}

function Post({ pack }) {
  const founder = pack.hook_source === 'founder'
  const lines = pack.post.split('\n')
  const first = lines[0] ?? ''
  const rest = lines.slice(1).join('\n')
  return jsxs('div', {
    style: { ...stack('4px'), fontSize: '0.8125rem', lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
    children: [
      jsx('div', {
        'data-founder-words': founder || undefined,
        style: { fontWeight: 600, ...(founder ? { borderLeft: '2px solid var(--ui-accent)', paddingLeft: 8 } : {}) },
        children: first
      }),
      rest.trim() && jsx('div', { style: { color: 'var(--ui-text-secondary)' }, children: rest.replace(/^\n+/, '') })
    ]
  })
}

function Checks({ checks }) {
  return jsx('ul', {
    style: { ...stack('3px'), margin: 0, padding: 0, listStyle: 'none', fontSize: '0.75rem' },
    children: checks.map((c, i) =>
      jsxs('li', {
        style: { display: 'flex', gap: 6, alignItems: 'baseline' },
        children: [
          jsx(Codicon, {
            name: c.ok === false ? 'close' : c.ok === true ? 'check' : 'circle-small',
            style: { color: c.ok === false ? 'var(--color-destructive)' : 'var(--ui-accent)', flexShrink: 0 }
          }),
          jsxs('span', { children: [c.label, c.detail && jsx('span', { style: muted, children: ` ${c.detail}` })] })
        ]
      }, i)
    )
  })
}

function References({ ctx, refs }) {
  return jsx('ul', {
    style: { ...stack('2px'), margin: 0, padding: 0, listStyle: 'none', fontSize: '0.75rem' },
    children: refs.map((r, i) =>
      jsxs('li', {
        style: row,
        children: [
          r.url
            ? jsx(Button, { variant: 'link', size: 'inline', onClick: () => void openLink(ctx, r.url), children: r.brand || r.url })
            : jsx('span', { children: r.brand }),
          fmtViews(r.views) && jsx('span', { style: muted, children: fmtViews(r.views) })
        ]
      }, i)
    )
  })
}

function FollowUp({ pack }) {
  const checkins = pack.checkins.map(c => {
    const when = fmtWhen(c.next_run_at)
    const text = c.state === 'scheduled' ? (when ? `Scheduled for ${when}` : 'Scheduled') : STATES[c.state] || c.state
    return jsx('span', { 'data-checkin': c.state, children: text }, c.id)
  })
  return jsxs('div', {
    style: { ...stack('6px'), fontSize: '0.75rem' },
    children: [
      jsx('div', {
        style: { ...row, ...(checkins.length ? {} : muted) },
        children: checkins.length ? checkins : 'No check-ins scheduled.'
      }),
      pack.signups.length
        ? jsx('div', {
            style: row,
            children: pack.signups.map(s =>
              jsx(Badge, { variant: 'muted', 'data-channel': s.channel, children: `${s.channel} ${s.signups}` }, s.channel)
            )
          })
        : jsx('div', { style: muted, children: 'No sign-ups recorded yet.' })
    ]
  })
}

function PackCard({ ctx, pack }) {
  const when = fmtWhen(pack.created) || pack.date
  return jsxs('article', {
    'data-pack': pack.name,
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
      gap: 20,
      padding: 16,
      borderRadius: 8,
      border: '1px solid var(--ui-stroke-secondary)',
      background: 'var(--ui-bg-card)'
    },
    children: [
      jsx(CardImage, { ctx, pack }),
      jsxs('div', {
        style: { ...stack('14px'), minWidth: 0 },
        children: [
          jsxs('div', {
            style: row,
            children: [
              jsx('span', { style: { fontSize: '0.875rem', fontWeight: 600 }, children: pack.product || pack.name }),
              when && jsx('span', { style: { ...muted, fontSize: '0.75rem' }, children: when }),
              LAYOUTS[pack.layout] && jsx(Badge, { variant: 'outline', children: LAYOUTS[pack.layout] })
            ]
          }),
          jsx(Section, {
            title: 'Post',
            aside: pack.hook_source === 'founder' && jsx(Badge, { children: 'Your words' }),
            children: jsx(Post, { pack })
          }),
          jsxs('div', {
            style: row,
            children: [
              jsx(CopyButton, { text: pack.post, label: 'Copy post', showLabel: true, buttonVariant: 'secondary', buttonSize: 'sm' }),
              pack.intent_url && jsxs(Button, {
                size: 'sm',
                onClick: () => void openLink(ctx, pack.intent_url),
                children: [jsx(Codicon, { name: 'link-external' }), 'Open X']
              })
            ]
          }),
          pack.checks && pack.checks.length > 0 && jsx(Section, { title: 'Checks', children: jsx(Checks, { checks: pack.checks }) }),
          pack.references.length > 0 && jsx(Section, { title: 'Posts that shaped it', children: jsx(References, { ctx, refs: pack.references }) }),
          jsx(Section, { title: 'After posting', children: jsx(FollowUp, { pack }) })
        ]
      })
    ]
  })
}

// ------------------------------------------------------------------ the page

export function LaunchDeskPage({ ctx }) {
  const packs = useQuery({ ...packsQuery(ctx), refetchInterval: 60_000, retry: 1 })
  const list = packs.data?.packs ?? []

  let body
  if (packs.isLoading) {
    body = jsx(Skeleton, { style: { height: 240, borderRadius: 8 } })
  } else if (packs.isError) {
    body = jsx(ErrorState, {
      title: "Can't reach Launch Desk",
      description: 'Turn on the launch-desk plugin where Hermes runs, then restart the gateway.',
      children: jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => void packs.refetch(), children: 'Try again' })
    })
  } else if (!list.length) {
    body = jsx(EmptyState, {
      title: 'No launch packs yet',
      description: 'In a chat, send /launch with your link and what you shipped, like /launch https://yoursite.com dark mode.'
    })
  } else {
    body = jsx('div', { style: stack('16px'), children: list.map(p => jsx(PackCard, { ctx, pack: p }, p.name)) })
  }

  return jsx('div', {
    style: { height: '100%', overflowY: 'auto' },
    children: jsxs('div', {
      style: { ...stack('16px'), maxWidth: 1080, margin: '0 auto', padding: '20px 24px 40px' },
      children: [
        jsxs('header', {
          style: { ...row, justifyContent: 'space-between' },
          children: [
            jsxs('div', {
              style: stack('2px'),
              children: [
                jsx('h1', { style: { fontSize: '1rem', fontWeight: 600, margin: 0 }, children: 'Launch Desk' }),
                jsx('span', {
                  style: { ...muted, fontSize: '0.75rem' },
                  children: list.length ? `${list.length} ${list.length === 1 ? 'pack' : 'packs'}. Nothing here is posted until you post it.` : 'Your launch packs from /launch.'
                })
              ]
            }),
            jsxs(Button, {
              variant: 'ghost',
              size: 'sm',
              disabled: packs.isFetching,
              onClick: () => void packs.refetch(),
              children: [jsx(Codicon, { name: 'refresh' }), 'Refresh']
            })
          ]
        }),
        body
      ]
    })
  })
}

// ------------------------------------------------------------------ status bar

export function CheckinChip({ ctx }) {
  const packs = useQuery({ ...packsQuery(ctx), refetchInterval: 5 * 60_000, retry: false })
  const due = packs.data?.checkins_due ?? 0
  if (!due) return null
  return jsx('button', {
    type: 'button',
    'data-checkins-due': due,
    style: { ...muted, fontSize: '0.6875rem', padding: '0 6px', background: 'none', border: 0, cursor: 'pointer' },
    onClick: () => host.navigate(PATH),
    children: `Launch Desk · ${due} ${due === 1 ? 'check-in' : 'check-ins'} due`
  })
}

export default {
  id: ID,
  name: 'Launch Desk',
  register(ctx) {
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(LaunchDeskPage, { ctx }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Launch Desk', codicon: 'rocket' } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: { id: 'launch-desk.open', label: 'Open Launch Desk', keywords: ['launch', 'pack', 'post'], run: () => host.navigate(PATH) }
      },
      { id: 'checkins', area: STATUSBAR_AREAS.right, order: 140, render: () => jsx(CheckinChip, { ctx }) }
    ])
  }
}
