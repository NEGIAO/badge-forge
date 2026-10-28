/**
 * 配置页脚本：把表单拼成 /svg 的查询串，实时预览并生成可复制的嵌入代码。
 *
 * 几条刻意的设计
 * - **只输出与默认值不同**的参数，链接才不会又臭又长；
 * - 预览走 fetch 而不是给 <img> 直接赋 src —— 这样能读到 X-Badge-Status，
 *   失败时再去 /api/explain 问「到底哪一步错了」（<img> 的 onerror 拿不到这些）；
 * - 每次请求带序号，**过期响应直接丢弃**：快速输入时不会出现「旧结果盖掉新结果」；
 * - 表单状态存 localStorage，刷新/关标签页都不丢；首屏没有存档时自动套第一个预设，
 *   避免打开就看到一个空的 n/a 徽章；
 * - 无任何外部依赖，断网也能用。
 */

const $ = (id) => document.getElementById(id);

const DEFAULTS = {
  format: 'comma',
  ibg: '#555555',
  lbg: '#555555',
  rbg: '#4caf50',
  h: 20,
  r: 3,
  gloss: true,
};

/** 与服务端 NAMED_COLORS 对齐：<input type="color"> 只吃 #rrggbb，命名色必须先换算 */
const NAMED_COLORS = {
  green: '#4caf50',
  blue: '#007ec6',
  grey: '#555555',
  gray: '#555555',
  red: '#e05d44',
  orange: '#fe7d37',
  yellow: '#dfb317',
  purple: '#9f4dbc',
  pink: '#e75480',
  teal: '#1d9e75',
  lightgrey: '#9f9f9f',
  lightgray: '#9f9f9f',
  darkgrey: '#3f3f3f',
  black: '#333333',
  white: '#ffffff',
};

const STORE_KEY = 'badge-forge-form:v1';
const COLOR_KEYS = ['ibg', 'lbg', 'rbg'];

const el = {
  icon: $('f-icon'),
  text: $('f-text'),
  data: $('f-data'),
  query: $('f-query'),
  format: $('f-format'),
  h: $('f-h'),
  r: $('f-r'),
  gloss: $('f-gloss'),
  outH: $('o-h'),
  outR: $('o-r'),
  stage: $('stage'),
  preview: $('preview'),
  error: $('stage-error'),
  meta: $('meta'),
  presets: $('presets'),
  ver: $('ver'),
  outUrl: $('out-url'),
  outMd: $('out-md'),
  outHtml: $('out-html'),
  resetBtn: document.querySelector('[data-reset]'),
  zoomBtn: document.querySelector('[data-zoom]'),
};

const state = { seq: 0, zoom: 2, loadingTimer: null };
// 页面所在目录（如 https://vpn.negiao.cn/badge）。资源全用相对路径，
// 同一份代码既能独立跑在根路径，也能挂在 nginx 的 /badge/ 子路径下。
const PAGE_BASE = new URL('.', location.href).href.replace(/\/$/, '');
let badgeBase = PAGE_BASE;
let previewObjectUrl = null;
let renderTimer = null;

/* ── 表单读写 ─────────────────────────────────────────────── */

/** 颜色字段的当前值（以文本输入为准，允许直接粘贴 #rrggbb 或命名色） */
function colorValue(name) {
  return ($(`f-${name}-text`).value || '').trim().toLowerCase();
}

/** 命名色 → 十六进制；非法值回落灰 */
function toHex(value) {
  const raw = String(value || '').trim().toLowerCase();
  if (NAMED_COLORS[raw]) return NAMED_COLORS[raw];
  return /^#[0-9a-f]{6}$/.test(raw) ? raw : DEFAULTS.lbg;
}

/** 表单 → 查询串（省略等于默认值的项） */
function buildQuery() {
  const params = new URLSearchParams();
  const icon = el.icon.value.trim();
  const text = el.text.value.trim();
  const data = el.data.value.trim();
  const query = el.query.value.trim();

  if (icon) params.set('icon', icon);
  if (text) params.set('text', text);
  if (data) params.set('data', data);
  if (query) params.set('query', query);
  if (el.format.value !== DEFAULTS.format) params.set('format', el.format.value);

  COLOR_KEYS.forEach((name) => {
    const value = colorValue(name);
    if (value && value !== DEFAULTS[name]) params.set(name, value);
  });

  if (Number(el.h.value) !== DEFAULTS.h) params.set('h', el.h.value);
  if (Number(el.r.value) !== DEFAULTS.r) params.set('r', el.r.value);
  if (el.gloss.checked !== DEFAULTS.gloss) params.set('gloss', '0');

  return params.toString();
}

/** 当前表单快照（用于持久化） */
function snapshot() {
  return {
    icon: el.icon.value,
    text: el.text.value,
    data: el.data.value,
    query: el.query.value,
    format: el.format.value,
    h: el.h.value,
    r: el.r.value,
    gloss: el.gloss.checked,
    ibg: colorValue('ibg'),
    lbg: colorValue('lbg'),
    rbg: colorValue('rbg'),
  };
}

/** 把快照写回表单（含色块、滑块读数的同步） */
function applyState(saved) {
  if (!saved) return;
  el.icon.value = saved.icon || '';
  el.text.value = saved.text || '';
  el.data.value = saved.data || '';
  el.query.value = saved.query || '';
  el.format.value = saved.format || DEFAULTS.format;
  el.h.value = saved.h || DEFAULTS.h;
  el.r.value = saved.r || DEFAULTS.r;
  el.gloss.checked = saved.gloss !== false;

  COLOR_KEYS.forEach((name) => {
    const value = saved[name] || DEFAULTS[name];
    $(`f-${name}-text`).value = value;
    $(`f-${name}`).value = toHex(value);
  });
  syncReadouts();
}

function syncReadouts() {
  el.outH.textContent = el.h.value;
  el.outR.textContent = el.r.value;
}

function persist() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(snapshot()));
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略 */
  }
}

/** 恢复上次配置；没有存档返回 false */
function restore() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return false;
    applyState(JSON.parse(raw));
    return true;
  } catch {
    return false;
  }
}

/* ── 渲染 ─────────────────────────────────────────────────── */

/** 只在值变化时写入，避免每次输入都把 textarea 的光标顶回开头 */
function setText(node, value) {
  if (node.value !== value) node.value = value;
}

function showError(message) {
  el.error.hidden = false;
  el.error.textContent = message;
}

function hideError() {
  el.error.hidden = true;
  el.error.textContent = '';
}

function setLoading(on) {
  if (on) {
    // 200ms 内返回的请求不显示加载态，避免闪烁
    clearTimeout(state.loadingTimer);
    state.loadingTimer = setTimeout(() => el.stage.classList.add('is-loading'), 200);
  } else {
    clearTimeout(state.loadingTimer);
    el.stage.classList.remove('is-loading');
  }
}

/** 按当前高度与缩放设置预览尺寸（徽章只有 20px，1:1 看不清细节） */
function applyZoom() {
  const height = Number(el.h.value) || DEFAULTS.h;
  el.preview.style.height = `${height * state.zoom}px`;
  if (el.zoomBtn) {
    el.zoomBtn.textContent = `${state.zoom}×`;
    el.zoomBtn.setAttribute('aria-pressed', String(state.zoom > 1));
  }
}

/** 问服务端「这次取数为啥失败」——只在出错时调用，正常路径不多打一次 */
async function explain(suffix) {
  try {
    const response = await fetch(`api/explain${suffix}`, { headers: { Accept: 'application/json' } });
    return await response.json();
  } catch {
    return {};
  }
}

/** 同步三段代码框 + 预览 */
async function render() {
  const seq = ++state.seq;
  const qs = buildQuery();
  const suffix = qs ? `?${qs}` : '';
  const absolute = `${badgeBase}/svg${suffix}`;
  const alt = el.text.value.trim() || 'badge';

  setText(el.outUrl, absolute);
  setText(el.outMd, `![${alt}](${absolute})`);
  setText(el.outHtml, `<img src="${absolute}" alt="${alt}" />`);
  applyZoom();
  persist();

  setLoading(true);
  try {
    const response = await fetch(`svg${suffix}`, {
      headers: { Accept: 'image/svg+xml' },
      cache: 'no-store',
    });
    const svg = await response.text();
    if (seq !== state.seq) return; // 已有更新的请求，丢弃这次结果

    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
    el.preview.src = previewObjectUrl;

    const status = response.headers.get('x-badge-status') || '-';
    el.meta.textContent = `HTTP ${response.status} · ${status} · ${svg.length} 字节`;

    if (status === 'error') {
      showError('取值失败，正在诊断…');
      const info = await explain(suffix);
      if (seq !== state.seq) return;
      showError(`取值失败：${info.reason || '原因未知'}`);
    } else {
      hideError();
    }
  } catch (error) {
    if (seq !== state.seq) return;
    showError(`请求失败：${error.message}`);
    el.meta.textContent = '—';
  } finally {
    if (seq === state.seq) setLoading(false);
  }
}

function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(render, 220);
}

/* ── 交互绑定 ─────────────────────────────────────────────── */

function bindForm() {
  [el.icon, el.text, el.data, el.query].forEach((input) => {
    input.addEventListener('input', scheduleRender);
  });
  el.format.addEventListener('change', scheduleRender);

  el.h.addEventListener('input', () => {
    syncReadouts();
    scheduleRender();
  });
  el.r.addEventListener('input', () => {
    syncReadouts();
    scheduleRender();
  });
  el.gloss.addEventListener('change', scheduleRender);
}

/** 颜色选择器与文本框双向同步 */
function bindColors() {
  COLOR_KEYS.forEach((name) => {
    const picker = $(`f-${name}`);
    const text = $(`f-${name}-text`);
    picker.addEventListener('input', () => {
      text.value = picker.value;
      scheduleRender();
    });
    text.addEventListener('input', () => {
      const hex = toHex(text.value);
      if (/^#[0-9a-f]{6}$/i.test(text.value.trim())) picker.value = hex;
      scheduleRender();
    });
  });
}

/** 把预设写回表单 */
function applyPreset(preset) {
  el.icon.value = preset.icon || '';
  el.text.value = preset.text || '';
  el.data.value = preset.data || '';
  el.query.value = preset.query || '';
  el.format.value = preset.format || DEFAULTS.format;

  COLOR_KEYS.forEach((name) => {
    const hex = toHex(preset[name] || DEFAULTS[name]);
    $(`f-${name}-text`).value = hex;
    $(`f-${name}`).value = hex;
  });
  render();
}

function bindCopy() {
  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const target = $(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(target.value);
      } catch {
        target.select();
        document.execCommand('copy');
      }
      const original = button.textContent;
      button.textContent = '已复制';
      button.classList.add('done');
      setTimeout(() => {
        button.textContent = original;
        button.classList.remove('done');
      }, 1400);
    });
  });
}

/** 主题切换：<html data-theme> 由 head 内联脚本在首帧前写好，这里只负责点击与持久化 */
function bindThemeToggle() {
  const button = document.querySelector('[data-theme-toggle]');
  if (!button) return;
  const label = (theme) => (theme === 'dark' ? '切换到浅色主题' : '切换到深色主题');
  button.setAttribute('aria-label', label(document.documentElement.dataset.theme));
  button.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    button.setAttribute('aria-label', label(next));
    try {
      localStorage.setItem('badge-forge-theme', next);
    } catch {
      /* 忽略 */
    }
  });
}

/** 预览缩放：1× / 2× 轮换 */
function bindZoom() {
  if (!el.zoomBtn) return;
  el.zoomBtn.addEventListener('click', () => {
    state.zoom = state.zoom === 2 ? 1 : 2;
    applyZoom();
  });
}

/** 重置：清存档并回到第一个预设（没有预设则清空表单） */
function bindReset() {
  if (!el.resetBtn) return;
  el.resetBtn.addEventListener('click', () => {
    try {
      localStorage.removeItem(STORE_KEY);
    } catch {
      /* 忽略 */
    }
    if (el.resetBtn.dataset.preset) {
      applyPreset(JSON.parse(el.resetBtn.dataset.preset));
    } else {
      el.icon.value = '';
      el.text.value = '';
      el.data.value = '';
      el.query.value = '';
      el.format.value = DEFAULTS.format;
      el.h.value = DEFAULTS.h;
      el.r.value = DEFAULTS.r;
      el.gloss.checked = DEFAULTS.gloss;
      COLOR_KEYS.forEach((name) => {
        $(`f-${name}-text`).value = DEFAULTS[name];
        $(`f-${name}`).value = DEFAULTS[name];
      });
      syncReadouts();
      render();
    }
  });
}

/** 拉元信息：版本号、对外基址、预设按钮 */
async function loadMeta() {
  try {
    const response = await fetch('api/config');
    const config = await response.json();
    if (config.publicBaseUrl) badgeBase = config.publicBaseUrl.replace(/\/$/, '');
    el.ver.textContent = `v${config.version}`;
    (config.presets || []).forEach((preset) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = preset.name;
      button.addEventListener('click', () => applyPreset(preset));
      el.presets.appendChild(button);
    });
    if (el.resetBtn && config.presets && config.presets.length) {
      el.resetBtn.dataset.preset = JSON.stringify(config.presets[0]);
    }
    return config;
  } catch {
    return null; // 元信息拿不到不影响手动配置
  }
}

/* ── 活动图 ───────────────────────────────────────────────── */

const gEl = {
  user: $('g-user'),
  title: $('g-title'),
  days: $('g-days'),
  width: $('g-width'),
  color: $('g-color-text'),
  bg: $('g-bg-text'),
  point: $('g-point-text'),
  hide: $('g-hide'),
  line: $('g-line'),
  dot: $('g-dot'),
  area: $('g-area'),
  grid: $('g-grid'),
  outDays: $('g-out-days'),
  outWidth: $('g-out-width'),
  outLine: $('g-out-line'),
  outDot: $('g-out-dot'),
  outArea: $('g-out-area'),
  outGrid: $('g-out-grid'),
  stage: $('graph-stage'),
  preview: $('graph-preview'),
  error: $('graph-error'),
  meta: $('graph-meta'),
  presets: $('g-presets'),
  outUrl: $('out-graph-url'),
  outMd: $('out-graph-md'),
  outHtml: $('out-graph-html'),
};

const GRAPH_DEFAULTS = {
  days: 31, width: 1200, color: '#70a5fd', bg: '#1a1b27', point: '#a9b1d6',
  line: 4, dot: 10, area: 10, grid: 30,
};
const GRAPH_STORE = 'badge-forge-graph:v1';
const GRAPH_PRESETS = [
  { name: 'tokyo-night', color: '#70a5fd', bg: '#1a1b27', point: '#a9b1d6' },
  { name: 'GitHub 深色', color: '#40c463', bg: '#0d1117', point: '#c9d1d9' },
  { name: '浅色', color: '#1f6feb', bg: '#ffffff', point: '#57606a' },
  { name: '暖炭', color: '#d97757', bg: '#1b1a17', point: '#f4f0e6' },
];

let graphSeq = 0;
let graphTimer = null;
let graphObjectUrl = null;
let githubEnabled = true;

function syncGraphReadouts() {
  gEl.outDays.textContent = gEl.days.value;
  gEl.outWidth.textContent = gEl.width.value;
  gEl.outLine.textContent = gEl.line.value;
  gEl.outDot.textContent = gEl.dot.value;
  gEl.outArea.textContent = gEl.area.value;
  gEl.outGrid.textContent = gEl.grid.value;
}

function buildGraphQuery() {
  const params = new URLSearchParams();
  const user = gEl.user.value.trim();
  const title = gEl.title.value.trim();

  if (user) params.set('username', user);
  if (Number(gEl.days.value) !== GRAPH_DEFAULTS.days) params.set('days', gEl.days.value);
  if (Number(gEl.width.value) !== GRAPH_DEFAULTS.width) params.set('width', gEl.width.value);
  if (title) params.set('custom_title', title);
  if (gEl.hide.checked) params.set('hide_title', '1');

  // 视觉参数：只输出与默认值不同的项
  [['line', 'line'], ['dot', 'dot'], ['area', 'area'], ['grid', 'grid']].forEach(([key, param]) => {
    if (Number(gEl[key].value) !== GRAPH_DEFAULTS[key]) params.set(param, gEl[key].value);
  });

  ['color', 'bg', 'point'].forEach((key) => {
    const value = gEl[key].value.trim().toLowerCase();
    if (value && value !== GRAPH_DEFAULTS[key]) params.set(key, value);
  });
  return params.toString();
}

function graphSnapshot() {
  return {
    user: gEl.user.value,
    title: gEl.title.value,
    days: gEl.days.value,
    width: gEl.width.value,
    color: gEl.color.value,
    bg: gEl.bg.value,
    point: gEl.point.value,
    hide: gEl.hide.checked,
    line: gEl.line.value,
    dot: gEl.dot.value,
    area: gEl.area.value,
    grid: gEl.grid.value,
  };
}

function applyGraphState(saved) {
  if (!saved) return;
  // user 只在显式传入时才覆盖 —— 否则「先设默认用户名、再套配色」的调用会被清空
  if (saved.user !== undefined) gEl.user.value = saved.user;
  if (saved.title !== undefined) gEl.title.value = saved.title;
  gEl.days.value = saved.days || GRAPH_DEFAULTS.days;
  gEl.width.value = saved.width || GRAPH_DEFAULTS.width;
  gEl.hide.checked = Boolean(saved.hide);
  ['line', 'dot', 'area', 'grid'].forEach((key) => {
    gEl[key].value = saved[key] || GRAPH_DEFAULTS[key];
  });
  ['color', 'bg', 'point'].forEach((key) => {
    const value = saved[key] || GRAPH_DEFAULTS[key];
    gEl[key].value = value;
    $(`g-${key}`).value = toHex(value);
  });
  syncGraphReadouts();
}

function persistGraph() {
  try {
    localStorage.setItem(GRAPH_STORE, JSON.stringify(graphSnapshot()));
  } catch {
    /* 忽略 */
  }
}

function restoreGraph() {
  try {
    const raw = localStorage.getItem(GRAPH_STORE);
    if (!raw) return false;
    applyGraphState(JSON.parse(raw));
    return true;
  } catch {
    return false;
  }
}

function applyGraphPreset(preset) {
  ['color', 'bg', 'point'].forEach((key) => {
    gEl[key].value = preset[key];
    $(`g-${key}`).value = preset[key];
  });
  renderGraph();
}

async function renderGraph() {
  const seq = ++graphSeq;
  const qs = buildGraphQuery();
  const suffix = qs ? `?${qs}` : '';
  const absolute = `${badgeBase}/activity-graph${suffix}`;

  setText(gEl.outUrl, absolute);
  setText(gEl.outMd, `![Contribution Graph](${absolute})`);
  setText(gEl.outHtml, `<img src="${absolute}" alt="Contribution Graph" width="100%" />`);
  persistGraph();

  gEl.error.hidden = true;
  try {
    const response = await fetch(`activity-graph${suffix}`, {
      headers: { Accept: 'image/svg+xml' },
      cache: 'no-store',
    });
    const svg = await response.text();
    if (seq !== graphSeq) return;

    if (graphObjectUrl) URL.revokeObjectURL(graphObjectUrl);
    graphObjectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
    gEl.preview.src = graphObjectUrl;

    const cacheStatus = response.headers.get('x-cache-status') || '-';
    gEl.meta.textContent = `HTTP ${response.status} · ${cacheStatus} · ${svg.length} 字节`;

    // 服务端在「未配 Token / 取数失败」时返回的是说明性 SVG，用这个头区分
    if (cacheStatus === 'ERROR') {
      gEl.error.hidden = false;
      gEl.error.textContent = githubEnabled
        ? '渲染失败：用户名不存在、Token 失效或 GitHub 暂时不可达。'
        : '未配置 GITHUB_TOKEN：在服务器 /etc/badge-forge.env 设置只读 PAT 后重启服务。';
    }
  } catch (error) {
    if (seq !== graphSeq) return;
    gEl.error.hidden = false;
    gEl.error.textContent = `请求失败：${error.message}`;
    gEl.meta.textContent = '—';
  }
}

function scheduleGraphRender() {
  clearTimeout(graphTimer);
  graphTimer = setTimeout(renderGraph, 220);
}

function bindGraph() {
  [gEl.user, gEl.title].forEach((input) => input.addEventListener('input', scheduleGraphRender));
  [gEl.days, gEl.width, gEl.line, gEl.dot, gEl.area, gEl.grid].forEach((input) =>
    input.addEventListener('input', () => {
      syncGraphReadouts();
      scheduleGraphRender();
    }),
  );
  gEl.hide.addEventListener('change', scheduleGraphRender);

  ['color', 'bg', 'point'].forEach((key) => {
    const picker = $(`g-${key}`);
    picker.addEventListener('input', () => {
      gEl[key].value = picker.value;
      scheduleGraphRender();
    });
    gEl[key].addEventListener('input', () => {
      if (/^#[0-9a-f]{6}$/i.test(gEl[key].value.trim())) picker.value = toHex(gEl[key].value);
      scheduleGraphRender();
    });
  });

  GRAPH_PRESETS.forEach((preset) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = preset.name;
    button.addEventListener('click', () => applyGraphPreset(preset));
    gEl.presets.appendChild(button);
  });
}

/* ── Tab 切换 ─────────────────────────────────────────────── */

const TAB_KEY = 'badge-forge-tab';
let currentTab = null;

function switchTab(name, shouldRender = true) {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.setAttribute('aria-selected', String(button.dataset.tab === name));
  });
  document.querySelectorAll('[data-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
  currentTab = name;
  try {
    localStorage.setItem(TAB_KEY, name);
  } catch {
    /* 忽略 */
  }
  if (!shouldRender) return;
  if (name === 'graph') renderGraph();
  else render();
}

function bindTabs() {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
  });
}

/* ── 启动 ─────────────────────────────────────────────────── */

async function boot() {
  bindForm();
  bindColors();
  bindCopy();
  bindThemeToggle();
  bindZoom();
  bindReset();
  bindTabs();
  bindGraph();

  const config = await loadMeta();
  if (config && config.github) githubEnabled = config.github.enabled !== false;

  // 徽章表单：有存档就恢复，否则套第一个预设（applyPreset 内部会渲染一次）
  let badgeRendered = false;
  if (restore()) {
    syncReadouts();
  } else if (config && config.presets && config.presets.length) {
    applyPreset(config.presets[0]);
    badgeRendered = true;
  } else {
    syncReadouts();
  }

  // 活动图表单：有存档就恢复，否则用服务端给的默认值
  if (!restoreGraph()) {
    const defaults = (config && config.github && config.github.chartDefaults) || {};
    gEl.user.value = defaults.username || '';
    applyGraphState({
      days: defaults.days,
      width: defaults.width,
      color: defaults.color,
      bg: defaults.bg,
      point: defaults.point,
    });
  }
  syncGraphReadouts();

  // 回到上次所在的 tab；?tab=graph 可直接深链到活动图
  let tab = 'badge';
  try {
    const fromUrl = new URLSearchParams(location.search).get('tab');
    tab = fromUrl || localStorage.getItem(TAB_KEY) || 'badge';
  } catch {
    /* 忽略 */
  }
  if (tab !== 'badge' && tab !== 'graph') tab = 'badge';
  switchTab(tab, false);
  if (tab === 'graph') renderGraph();
  else if (!badgeRendered) render();
}

boot();
