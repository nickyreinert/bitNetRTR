const shell = document.getElementById('shell');
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const apiKeyInput = document.getElementById('api-key');
const useProxyInput = document.getElementById('use-proxy');
const modelSelect = document.getElementById('bitnet-model');
const nPredictInput = document.getElementById('bitnet-npredict');
const threadsInput = document.getElementById('bitnet-threads');
const ctxInput = document.getElementById('bitnet-ctx');
const tempInput = document.getElementById('bitnet-temp');

const optionsBtn = document.getElementById('options-btn');
const settingsDrawer = document.getElementById('settings-drawer');
const settingsCloseBtn = document.getElementById('settings-close');
const mainPane = document.querySelector('.main-pane');
const topbar = document.querySelector('.topbar');

const statsBtn = document.getElementById('stats-btn');
const statsDrawer = document.getElementById('stats-drawer');
const statsCloseBtn = document.getElementById('stats-close');
const statsBody = document.getElementById('stats-body');

const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');

let activeController = null;
let frontendConfig = null;
let statsTimer = null;
let waitingTimer = null;

function getOrCreateStorageId(storage, key) {
  let value = storage.getItem(key);
  if (value) return value;

  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    value = window.crypto.randomUUID();
  } else {
    value = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
  storage.setItem(key, value);
  return value;
}

const currentUserId = getOrCreateStorageId(localStorage, 'bitnet_user_id');
const currentSessionId = getOrCreateStorageId(sessionStorage, 'bitnet_session_id');

apiKeyInput.value = localStorage.getItem('bitnet_api_key') || 'your-secret-key-here';
const savedUseProxy = localStorage.getItem('bitnet_use_proxy');
useProxyInput.checked = savedUseProxy === null ? true : savedUseProxy === 'true';

function getApiBase() {
  if (useProxyInput.checked) {
    return '';
  }
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return 'http://localhost:8000';
  }
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

function apiRoute(path) {
  if (useProxyInput.checked) {
    return `/api${path}`;
  }
  return path;
}

function setStatus(text, isWarn = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('warn', isWarn);
}

function addMessage(role, content = '') {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.textContent = content;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function startWaitingAnimation(aiBox) {
  let dots = 3;
  let direction = 1;
  aiBox.classList.add('waiting');
  aiBox.textContent = '.'.repeat(dots);

  clearInterval(waitingTimer);
  waitingTimer = setInterval(() => {
    if (!aiBox.classList.contains('waiting')) {
      clearInterval(waitingTimer);
      waitingTimer = null;
      return;
    }

    dots += direction;
    if (dots >= 6) {
      dots = 6;
      direction = -1;
    } else if (dots <= 3) {
      dots = 3;
      direction = 1;
    }

    aiBox.textContent = '.'.repeat(dots);
    chat.scrollTop = chat.scrollHeight;
  }, 180);
}

function stopWaitingAnimation(aiBox) {
  if (aiBox && aiBox.classList.contains('waiting')) {
    clearInterval(waitingTimer);
    waitingTimer = null;

    if (/^\.{3,6}$/.test(aiBox.textContent)) {
      aiBox.textContent = '';
    }
    aiBox.classList.remove('waiting');
  }
}

function setStreamingUi(streaming) {
  msgInput.disabled = streaming;
  sendBtn.textContent = streaming ? 'Stop' : 'Send';
  sendBtn.classList.toggle('is-stop', streaming);
  sendBtn.dataset.mode = streaming ? 'stop' : 'send';
}

function upsertNumberLocalStorage(key, value) {
  if (!Number.isFinite(value)) return;
  localStorage.setItem(key, String(value));
}

function parseNum(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return 'n/a';
  return new Intl.NumberFormat().format(Number(value));
}

function formatMetric(value, suffix = '') {
  if (value == null || Number.isNaN(Number(value))) return 'n/a';
  return `${Number(value).toFixed(2)}${suffix}`;
}

function formatCompactDate(timestamp) {
  if (!timestamp) return 'n/a';
  return new Date(timestamp * 1000).toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function summarizeRange(range, suffix = '') {
  if (!range || range.avg == null) return 'n/a';
  return `avg ${formatMetric(range.avg, suffix)} | min ${formatMetric(range.min, suffix)} | max ${formatMetric(range.max, suffix)}`;
}

function stripAssistantNoise(text) {
  let cleaned = String(text || '');
  const patterns = [
    /llama_perf_[^\n]*/gi,
    /llm_perf_[^\n]*/gi,
    /(?:load|sampling|prompt eval|eval|total)\s+time\s*=\s*[^\n]*/gi,
    /\b\d+\s+(?:runs|tokens)\s*\(\s*[\d.]+\s*ms per token,\s*[\d.]+\s*tokens per second\s*\)/gi,
    /\b[\d.]+\s*ms per token,\s*[\d.]+\s*tokens per second\b/gi,
    /^\s*tokens\s*$/gim
  ];
  for (const pattern of patterns) {
    cleaned = cleaned.replace(pattern, '');
  }
  cleaned = cleaned.replace(/\n\s*\n+/g, '\n').replace(/ {2,}/g, ' ').trim();
  return cleaned;
}

function statsHeaders(apiKey) {
  return {
    'X-API-KEY': apiKey,
    'X-User-ID': currentUserId,
    'X-Session-ID': currentSessionId
  };
}

function updateEdgeTabPositions() {
  const buttonHeight = optionsBtn.getBoundingClientRect().height || 60;
  const chatTop = chat.getBoundingClientRect().top;
  const alignedTop = Math.max(32, chatTop + (buttonHeight / 2));

  document.documentElement.style.setProperty(' --edge-tab-top'.trim(), `${alignedTop}px`);

  let optionsOffset = 0;
  let statsOffset = 0;

  if (shell.classList.contains('show-left')) {
    const settingsRect = settingsDrawer.getBoundingClientRect();
    optionsOffset = Math.max(0, settingsRect.left);
  }

  if (shell.classList.contains('show-right')) {
    const statsRect = statsDrawer.getBoundingClientRect();
    statsOffset = Math.max(0, window.innerWidth - statsRect.right);
  }

  document.documentElement.style.setProperty(' --options-tab-offset'.trim(), `${optionsOffset}px`);
  document.documentElement.style.setProperty(' --stats-tab-offset'.trim(), `${statsOffset}px`);
}

function syncLayout() {
  window.requestAnimationFrame(updateEdgeTabPositions);
}

function closeStatsDrawer() {
  shell.classList.remove('show-right');
  statsDrawer.setAttribute('aria-hidden', 'true');
  clearInterval(statsTimer);
  statsTimer = null;
  syncLayout();
}

function openStatsDrawer() {
  if (!frontendConfig || !frontendConfig.stats_enabled) return;
  shell.classList.remove('show-left');
  settingsDrawer.setAttribute('aria-hidden', 'true');
  shell.classList.add('show-right');
  statsDrawer.setAttribute('aria-hidden', 'false');
  refreshStats();
  clearInterval(statsTimer);
  statsTimer = setInterval(refreshStats, 2000);
  syncLayout();
}

function toggleStatsDrawer() {
  if (shell.classList.contains('show-right')) {
    closeStatsDrawer();
  } else {
    openStatsDrawer();
  }
}

function closeSettingsDrawer() {
  shell.classList.remove('show-left');
  settingsDrawer.setAttribute('aria-hidden', 'true');
  syncLayout();
}

function openSettingsDrawer() {
  shell.classList.remove('show-right');
  statsDrawer.setAttribute('aria-hidden', 'true');
  clearInterval(statsTimer);
  statsTimer = null;
  shell.classList.add('show-left');
  settingsDrawer.setAttribute('aria-hidden', 'false');
  syncLayout();
}

function toggleSettingsDrawer() {
  if (shell.classList.contains('show-left')) {
    closeSettingsDrawer();
  } else {
    openSettingsDrawer();
  }
}

function applyFrontendConfig(config) {
  frontendConfig = config;
  const bitnet = config.bitnet;
  const defaults = bitnet.defaults;
  const limits = bitnet.limits;

  modelSelect.innerHTML = '';
  for (const modelPath of bitnet.models) {
    const option = document.createElement('option');
    option.value = modelPath;
    option.textContent = modelPath;
    modelSelect.appendChild(option);
  }

  modelSelect.value = bitnet.models.includes(defaults.model) ? defaults.model : bitnet.models[0];

  nPredictInput.max = String(limits.max_n_predict);
  threadsInput.max = String(limits.max_threads);
  ctxInput.max = String(limits.max_context_size);
  tempInput.max = String(limits.max_temp);

  nPredictInput.value = String(clamp(parseNum(defaults.n_predict, 256), 1, limits.max_n_predict));
  threadsInput.value = String(clamp(parseNum(defaults.threads, 2), 1, limits.max_threads));
  ctxInput.value = String(clamp(parseNum(defaults.ctx_size, 2048), 1, limits.max_context_size));
  tempInput.value = String(clamp(parseNum(defaults.temperature, 0.8), 0, limits.max_temp));

  localStorage.setItem('bitnet_model', modelSelect.value);
  upsertNumberLocalStorage('bitnet_n_predict', Number(nPredictInput.value));
  upsertNumberLocalStorage('bitnet_threads', Number(threadsInput.value));
  upsertNumberLocalStorage('bitnet_ctx_size', Number(ctxInput.value));
  upsertNumberLocalStorage('bitnet_temperature', Number(tempInput.value));

  statsBtn.hidden = !config.stats_enabled;
  if (!config.stats_enabled) {
    closeStatsDrawer();
  }
  syncLayout();
}

async function loadFrontendConfig() {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) return;
  try {
    const base = getApiBase();
    const response = await fetch(`${base}${apiRoute('/frontend-config')}`, {
      headers: statsHeaders(apiKey)
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const config = await response.json();
    applyFrontendConfig(config);
  } catch (error) {
    setStatus(`config error: ${error.message}`, true);
  }
}

function getRuntimeOptions() {
  const defaults = frontendConfig ? frontendConfig.bitnet.defaults : {
    model: modelSelect.value,
    n_predict: 256,
    threads: 2,
    ctx_size: 2048,
    temperature: 0.8
  };
  const limits = frontendConfig ? frontendConfig.bitnet.limits : {
    max_threads: 4,
    max_context_size: 4096,
    max_temp: 0.8,
    max_n_predict: 4096
  };

  const model = modelSelect.value || defaults.model;
  const n_predict = clamp(parseNum(nPredictInput.value, defaults.n_predict), 1, limits.max_n_predict);
  const threads = clamp(parseNum(threadsInput.value, defaults.threads), 1, limits.max_threads);
  const ctx_size = clamp(parseNum(ctxInput.value, defaults.ctx_size), 1, limits.max_context_size);
  const temperature = clamp(parseNum(tempInput.value, defaults.temperature), 0, limits.max_temp);

  localStorage.setItem('bitnet_model', model);
  upsertNumberLocalStorage('bitnet_n_predict', n_predict);
  upsertNumberLocalStorage('bitnet_threads', threads);
  upsertNumberLocalStorage('bitnet_ctx_size', ctx_size);
  upsertNumberLocalStorage('bitnet_temperature', temperature);

  nPredictInput.value = String(n_predict);
  threadsInput.value = String(threads);
  ctxInput.value = String(ctx_size);
  tempInput.value = String(temperature);

  return { model, n_predict, threads, ctx_size, temperature };
}

function renderUsageTable(title, rows) {
  if (!rows || !rows.length) {
    return `<section class="stats-section"><h3>${escapeHtml(title)}</h3><div class="stats-card">No data yet.</div></section>`;
  }

  const body = rows.slice(0, 12).map((row) => `
    <tr>
      <td>${escapeHtml(row.label)}</td>
      <td>${formatNumber(row.messages)}</td>
      <td>${formatNumber(row.sessions)}</td>
      <td>${formatNumber(row.total_tokens)}</td>
      <td>${row.total_time_ms?.avg != null ? formatMetric(row.total_time_ms.avg, ' ms') : 'n/a'}</td>
      <td>${row.eval_tokens_per_second?.avg != null ? formatMetric(row.eval_tokens_per_second.avg) : 'n/a'}</td>
    </tr>
  `).join('');

  return `
    <section class="stats-section">
      <h3>${escapeHtml(title)}</h3>
      <div class="stats-table-wrap">
        <table class="stats-table">
          <thead>
            <tr><th>Period</th><th>Msgs</th><th>Sessions</th><th>Tokens</th><th>Latency</th><th>Tok/s</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderRuntimeTable(title, rows) {
  if (!rows || !rows.length) {
    return `<section class="stats-section"><h3>${escapeHtml(title)}</h3><div class="stats-card">No runtime history yet.</div></section>`;
  }
  const body = rows.slice(0, 12).map((row) => `
    <tr>
      <td>${escapeHtml(row.label)}</td>
      <td>${row.cpu_usage_percent?.avg != null ? formatMetric(row.cpu_usage_percent.avg, '%') : 'n/a'}</td>
      <td>${row.memory_used_percent?.avg != null ? formatMetric(row.memory_used_percent.avg, '%') : 'n/a'}</td>
      <td>${row.gpu_utilization_percent?.avg != null ? formatMetric(row.gpu_utilization_percent.avg, '%') : 'n/a'}</td>
    </tr>
  `).join('');
  return `
    <section class="stats-section">
      <h3>${escapeHtml(title)}</h3>
      <div class="stats-table-wrap">
        <table class="stats-table">
          <thead>
            <tr><th>Period</th><th>CPU avg</th><th>Mem avg</th><th>GPU avg</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderRuntimeSampleTable(title, rows) {
  if (!rows || !rows.length) {
    return `<section class="stats-section"><h3>${escapeHtml(title)}</h3><div class="stats-card">No last-hour runtime samples yet.</div></section>`;
  }
  const body = rows.slice(0, 12).map((row) => `
    <tr>
      <td>${escapeHtml(formatCompactDate(row.timestamp))}</td>
      <td>${row.cpu_usage_percent != null ? formatMetric(row.cpu_usage_percent, '%') : 'n/a'}</td>
      <td>${row.memory_used_percent != null ? formatMetric(row.memory_used_percent, '%') : 'n/a'}</td>
      <td>${row.gpu_utilization_percent != null ? formatMetric(row.gpu_utilization_percent, '%') : 'n/a'}</td>
    </tr>
  `).join('');
  return `
    <section class="stats-section">
      <h3>${escapeHtml(title)}</h3>
      <div class="stats-table-wrap">
        <table class="stats-table">
          <thead>
            <tr><th>Sample</th><th>CPU</th><th>Mem</th><th>GPU</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStats(stats) {
  const runtime = stats.runtime || {};
  const usage = stats.usage || {};
  const latest = usage.latest_chat;
  const gpuCards = (runtime.gpus || []).map((gpu) => {
    return `<div class="stats-card"><strong>GPU ${escapeHtml(gpu.index)}: ${escapeHtml(gpu.name)}</strong><br/>Util: ${formatMetric(gpu.utilization_percent, '%')}<br/>Mem: ${formatNumber(gpu.memory_used_mb)} / ${formatNumber(gpu.memory_total_mb)} MB<br/>Temp: ${formatMetric(gpu.temperature_c, ' C')}</div>`;
  }).join('');
  const cpuUsage = runtime.cpu && runtime.cpu.usage_percent != null ? `${runtime.cpu.usage_percent}%` : 'warming up';
  const memory = runtime.memory
    ? `${runtime.memory.used_percent}% (${Math.round(runtime.memory.used_bytes / (1024 * 1024))} / ${Math.round(runtime.memory.total_bytes / (1024 * 1024))} MB)`
    : 'n/a';

  const latestCard = latest
    ? `<div class="stats-card stats-card-wide"><strong>Latest Reply</strong><br/>${escapeHtml(formatCompactDate(latest.timestamp))}<br/>Model: ${escapeHtml(latest.model || 'n/a')}<br/>Prompt: ${formatNumber(latest.prompt_tokens)} tok | Reply: ${formatNumber(latest.completion_tokens)} tok | Total: ${formatNumber(latest.total_tokens)} tok<br/>Latency: ${formatMetric(latest.total_time_ms, ' ms')}<br/>Prompt speed: ${formatMetric(latest.prompt_tokens_per_second)} tok/s | Reply speed: ${formatMetric(latest.eval_tokens_per_second)} tok/s | Sampling: ${formatMetric(latest.sampling_time_ms, ' ms')}</div>`
    : '<div class="stats-card stats-card-wide"><strong>Latest Reply</strong><br/>No completed chat metrics yet.</div>';

  const totals = usage.totals || {};
  statsBody.innerHTML = `
    <section class="stats-section">
      <h3>Overview</h3>
      <div class="stats-grid">
        <div class="stats-card"><strong>CPU</strong><br/>Usage: ${cpuUsage}<br/>Cores: ${runtime.cpu?.cores ?? 'n/a'}</div>
        <div class="stats-card"><strong>Memory</strong><br/>Used: ${memory}</div>
        <div class="stats-card"><strong>User Totals</strong><br/>Sessions: ${formatNumber(totals.sessions)}<br/>Messages: ${formatNumber(totals.messages)}<br/>Tokens: ${formatNumber(totals.total_tokens)}</div>
        <div class="stats-card"><strong>Token Split</strong><br/>Prompt: ${formatNumber(totals.prompt_tokens)}<br/>Reply: ${formatNumber(totals.completion_tokens)}<br/>User: ${escapeHtml(usage.user_id || 'n/a')}</div>
        ${latestCard}
        ${gpuCards || '<div class="stats-card"><strong>GPU</strong><br/>No NVIDIA GPU stats available.</div>'}
      </div>
    </section>
    ${renderUsageTable('Last Hour Detail', usage.last_hour)}
    ${renderRuntimeSampleTable('Last Hour Runtime', stats.runtime_history?.last_hour)}
    ${renderUsageTable('Daily Usage', usage.daily)}
    ${renderUsageTable('Weekly Usage', usage.weekly)}
    ${renderUsageTable('Monthly Usage', usage.monthly)}
    ${renderRuntimeTable('Runtime Trend', stats.runtime_history?.daily || [])}
    ${renderRuntimeTable('Weekly Runtime', stats.runtime_history?.weekly || [])}
    ${renderRuntimeTable('Monthly Runtime', stats.runtime_history?.monthly || [])}
  `;
}

async function refreshStats() {
  if (!frontendConfig || !frontendConfig.stats_enabled) return;
  const apiKey = apiKeyInput.value.trim();
  const base = getApiBase();
  try {
    const response = await fetch(`${base}${apiRoute('/stats')}`, {
      headers: statsHeaders(apiKey)
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const stats = await response.json();
    renderStats(stats);
  } catch (error) {
    statsBody.innerHTML = `<div class="stats-card">Stats error: ${error.message}</div>`;
  }
}

function stopStream() {
  if (activeController) {
    activeController.abort();
  }
}

async function send() {
  const prompt = msgInput.value.trim();
  const apiKey = apiKeyInput.value.trim();
  if (!prompt || !apiKey) return;

  localStorage.setItem('bitnet_api_key', apiKey);
  localStorage.setItem('bitnet_use_proxy', String(useProxyInput.checked));
  const runtime = getRuntimeOptions();
  msgInput.value = '';
  setStreamingUi(true);

  addMessage('user', prompt);
  const aiBox = addMessage('ai', '');
  let assistantText = '';
  startWaitingAnimation(aiBox);
  setStatus('streaming...');
  activeController = new AbortController();

  try {
    const apiBase = getApiBase();
    const route = apiRoute('/chat');
    const response = await fetch(`${apiBase}${route}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-KEY': apiKey,
        'X-User-ID': currentUserId,
        'X-Session-ID': currentSessionId
      },
      body: JSON.stringify({ prompt, ...runtime }),
      signal: activeController.signal
    });

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new Error(`HTTP ${response.status}: ${text || 'Request failed'}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';

      for (const eventBlock of events) {
        const eventTypeLine = eventBlock.split('\n').find((line) => line.startsWith('event: '));
        const eventType = eventTypeLine ? eventTypeLine.slice(7).trim() : '';
        const dataLines = eventBlock
          .split('\n')
          .filter((line) => line.startsWith('data: '))
          .map((line) => line.slice(6));

        if (eventType === 'error' && dataLines.length) {
          stopWaitingAnimation(aiBox);
          aiBox.textContent += `\n\n[Server Error] ${dataLines.join('\n')}`;
          continue;
        }
        if (eventType === 'status') {
          if (dataLines.length) {
            setStatus(dataLines.join(' '));
          }
          continue;
        }
        if (eventType === 'done') {
          continue;
        }
        if (dataLines.length) {
          stopWaitingAnimation(aiBox);
          assistantText += dataLines.join('\n');
          aiBox.textContent = stripAssistantNoise(assistantText);
          chat.scrollTop = chat.scrollHeight;
        }
      }
    }

    setStatus('done');
  } catch (err) {
    stopWaitingAnimation(aiBox);
    if (err.name === 'AbortError') {
      aiBox.textContent += '\n\n[Stopped]';
      setStatus('stopped');
    } else {
      aiBox.textContent += `\n\n[Error] ${err.message}`;
      setStatus('stream error', true);
    }
  } finally {
    stopWaitingAnimation(aiBox);
    activeController = null;
    setStreamingUi(false);
    msgInput.focus();
  }
}

sendBtn.addEventListener('click', () => {
  if (activeController) {
    stopStream();
  } else {
    send();
  }
});

useProxyInput.addEventListener('change', () => {
  localStorage.setItem('bitnet_use_proxy', String(useProxyInput.checked));
  loadFrontendConfig();
});

apiKeyInput.addEventListener('blur', loadFrontendConfig);

optionsBtn.addEventListener('click', toggleSettingsDrawer);
settingsCloseBtn.addEventListener('click', closeSettingsDrawer);

statsBtn.addEventListener('click', toggleStatsDrawer);
statsCloseBtn.addEventListener('click', closeStatsDrawer);

msgInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    if (activeController) {
      stopStream();
    } else {
      send();
    }
  }
});

window.addEventListener('resize', syncLayout);

loadFrontendConfig();
setStreamingUi(false);
syncLayout();