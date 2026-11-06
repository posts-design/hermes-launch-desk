// A stand-in for @hermes/plugin-sdk, for tools/desktop_check.mjs only (never shipped to Desktop).
// Each export mirrors a real SDK export by name (the checker verifies the names against the
// Hermes source when it has it); the components render plain HTML with a data-sdk marker so the
// checker can see what the page chose to show, and an `sdk-*` class the preview CSS styles.
import React from 'react'

const h = React.createElement
const state = (globalThis.__LD_STUB__ ??= { queries: {}, navigated: [], notified: [], opened: [] })

const classes = (...xs) => xs.filter(Boolean).join(' ')

export const PANES_AREA = 'panes'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }

export const host = {
  navigate: path => void state.navigated.push(path),
  notify: n => void state.notified.push(n)
}

export const fmtDateTime = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })

export function useQuery(options) {
  const key = JSON.stringify(options.queryKey)
  const preset = state.queries[key] ?? state.queries['*'] ?? { status: 'pending' }
  const disabled = options.enabled === false  // React Query: a disabled query with no data is not loading
  return {
    data: disabled ? undefined : preset.data,
    error: preset.error,
    isLoading: !disabled && preset.status === 'pending',
    isError: preset.status === 'error',
    isFetching: false,
    refetch: () => Promise.resolve()
  }
}

export function Badge({ variant = 'default', className, children, ...rest }) {
  return h('span', { ...rest, 'data-sdk': 'Badge', className: classes('sdk-badge', `sdk-badge-${variant}`, className) }, children)
}

export function Button({ variant = 'default', size = 'default', className, children, onClick, disabled, ...rest }) {
  return h('button', { ...rest, type: 'button', disabled, onClick, 'data-sdk': 'Button', className: classes('sdk-btn', `sdk-btn-${variant}`, `sdk-btn-${size}`, className) }, children)
}

export function Codicon({ name, style }) {
  return h('i', { 'aria-hidden': 'true', 'data-sdk': 'Codicon', 'data-icon': name, className: `sdk-icon sdk-icon-${name}`, style })
}

export function CopyButton({ text, label = 'Copy', buttonVariant = 'ghost', buttonSize = 'default', showLabel }) {
  return h('button', { type: 'button', 'data-sdk': 'CopyButton', 'data-copy': typeof text === 'string' ? text : '', className: classes('sdk-btn', `sdk-btn-${buttonVariant}`, `sdk-btn-${buttonSize}`) },
    h('i', { 'aria-hidden': 'true', className: 'sdk-icon sdk-icon-copy' }), showLabel === false ? null : label)
}

export function EmptyState({ title, description }) {
  return h('div', { 'data-sdk': 'EmptyState', className: 'sdk-empty' }, h('div', { className: 'sdk-empty-title' }, title), description && h('div', { className: 'sdk-empty-desc' }, description))
}

export function ErrorState({ title, description, children }) {
  return h('div', { 'data-sdk': 'ErrorState', className: 'sdk-error' }, h('h2', null, title), description && h('p', null, description), children)
}

export function SegmentedControl({ options, value }) {
  return h('div', { 'data-sdk': 'SegmentedControl', className: 'sdk-seg' },
    options.map(o => h('button', { key: o.id, type: 'button', 'aria-pressed': value === o.id, className: value === o.id ? 'sdk-seg-on' : '' }, o.label)))
}

export function Skeleton({ className, style }) {
  return h('div', { 'data-sdk': 'Skeleton', className: classes('sdk-skeleton', className), style })
}
