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

apiKeyInput.value = localStorage.getItem('bitnet_api_key') || 'your-secret-key-here';
useProxyInput.checked = localStorage.getItem('bitnet_use_proxy') === 'true';

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

function closeStatsDrawer() {
  shell.classList.remove('show-right');
  statsDrawer.setAttribute('aria-hidden', 'true');
  clearInterval(statsTimer);
  statsTimer = null;
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
}

function openSettingsDrawer() {
  shell.classList.remove('show-right');
  statsDrawer.setAttribute('aria-hidden', 'true');
  clearInterval(statsTimer);
  statsTimer = null;
  shell.classList.add('show-left');
  settingsDrawer.setAttribute('aria-hidden', 'false');
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
}

async function loadFrontendConfig() {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) return;
  try {
    const base = getApiBase();
    const response = await fetch(`${base}${apiRoute('/frontend-config')}`, {
      headers: { 'X-API-KEY': apiKey }
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

function renderStats(stats) {
  const gpuCards = (stats.gpus || []).map((gpu) => {
    return `<div class="stats-card"><strong>GPU ${gpu.index}: ${gpu.name}</strong><br/>Util: ${gpu.utilization_percent}%<br/>Mem: ${gpu.memory_used_mb} / ${gpu.memory_total_mb} MB<br/>Temp: ${gpu.temperature_c} C</div>`;
  }).join('');
  const cpuUsage = stats.cpu && stats.cpu.usage_percent != null ? `${stats.cpu.usage_percent}%` : 'warming up';
  const memory = stats.memory
    ? `${stats.memory.used_percent}% (${Math.round(stats.memory.used_bytes / (1024 * 1024))} / ${Math.round(stats.memory.total_bytes / (1024 * 1024))} MB)`
    : 'n/a';

  statsBody.innerHTML = `
    <div class="stats-card"><strong>CPU</strong><br/>Usage: ${cpuUsage}<br/>Cores: ${stats.cpu?.cores ?? 'n/a'}</div>
    <div class="stats-card"><strong>Memory</strong><br/>Used: ${memory}</div>
    ${gpuCards || '<div class="stats-card"><strong>GPU</strong><br/>No NVIDIA GPU stats available.</div>'}
  `;
}

async function refreshStats() {
  if (!frontendConfig || !frontendConfig.stats_enabled) return;
  const apiKey = apiKeyInput.value.trim();
  const base = getApiBase();
  try {
    const response = await fetch(`${base}${apiRoute('/stats')}`, {
      headers: { 'X-API-KEY': apiKey }
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
        'X-API-KEY': apiKey
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
          aiBox.textContent += dataLines.join('\n');
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

loadFrontendConfig();
setStreamingUi(false);