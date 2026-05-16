/**
 * CodeTalk Deployer — Wizard App
 * Vanilla JS ES module, zero dependencies.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  currentStep: 1,
  totalSteps: 6,
  selectedMode: null,        // 'native' | 'compose' | 'k8s'
  checksHasFail: true,
  config: {},                // collected form data
  deployJobId: null,
  deployEventSource: null,
  deployDone: false,
};

// Provider label map
const PROVIDER_LABELS = {
  openai:    { label: 'OpenAI API Key',    placeholder: 'sk-…',              hint: 'Your OpenAI API key — stored locally, never sent to third parties' },
  anthropic: { label: 'Anthropic API Key', placeholder: 'sk-ant-…',          hint: 'Your Anthropic API key — stored locally, never sent to third parties' },
  google:    { label: 'Google API Key',    placeholder: 'AIza…',             hint: 'Your Google AI Studio API key — stored locally, never sent to third parties' },
  ollama:    { label: 'Ollama Base URL',   placeholder: 'http://localhost:11434',  hint: 'Local Ollama server URL — no API key required' },
};

// Step metadata
const STEP_LABELS = ['Choose Mode', 'Prerequisites', 'Configuration', 'Review', 'Deploy', 'Complete'];

// Service -> SSE step keyword mapping (partial match)
const SERVICE_STEP_MAP = {
  frontend:    ['frontend'],
  backend:     ['backend', 'api'],
  postgres:    ['postgres', 'database', 'db'],
  redis:       ['redis', 'cache'],
  deepwiki:    ['deepwiki', 'wiki'],
  gitnexus:    ['gitnexus', 'git'],
  codecompass: ['codecompass', 'compass'],
  zoekt:       ['zoekt', 'search'],
};

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $  = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function show(el) { if (el) el.style.display = ''; }
function hide(el) { if (el) el.style.display = 'none'; }

// ---------------------------------------------------------------------------
// Step navigation
// ---------------------------------------------------------------------------

function updateStepVisibility() {
  $$('.step').forEach(s => {
    const n = parseInt(s.dataset.step, 10);
    s.classList.toggle('step-active', n === state.currentStep);
    s.setAttribute('aria-hidden', n !== state.currentStep ? 'true' : 'false');
  });

  $$('.step-dot').forEach(dot => {
    const n = parseInt(dot.dataset.step, 10);
    dot.classList.toggle('active', n === state.currentStep);
    dot.classList.toggle('completed', n < state.currentStep);
  });

  const headerLabel = $('#header-step-label');
  if (headerLabel) {
    headerLabel.textContent = `Step ${state.currentStep} of ${state.totalSteps} — ${STEP_LABELS[state.currentStep - 1]}`;
  }

  updateNavButtons();
}

function updateNavButtons() {
  const btnBack = $('#btn-back');
  const btnNext = $('#btn-next');

  // Relabel Next button
  let nextLabel = 'Next';
  if (state.currentStep === 4) nextLabel = 'Deploy';
  if (state.currentStep === 6) nextLabel = 'Open CodeTalk';
  const nextArrow = btnNext.querySelector('svg');
  btnNext.textContent = nextLabel + ' ';
  if (nextArrow) btnNext.appendChild(nextArrow);

  // Back disabled on step 1
  btnBack.disabled = state.currentStep === 1;

  // Hide nav on deploy/complete steps
  if (state.currentStep === 5 || state.currentStep === 6) {
    hide(btnNext);
    hide(btnBack);
  } else {
    show(btnNext);
    show(btnBack);
  }

  // Per-step enable rules
  switch (state.currentStep) {
    case 1:  btnNext.disabled = !state.selectedMode; break;
    case 2:  btnNext.disabled = state.checksHasFail; break;
    case 3:
    case 4:  btnNext.disabled = false; break;
    default: btnNext.disabled = false;
  }
}

async function goToStep(n) {
  if (n < 1 || n > state.totalSteps) return;
  state.currentStep = n;
  await onStepEnter(n);
  updateStepVisibility();
}

async function onStepEnter(step) {
  switch (step) {
    case 2: await runChecks();  break;
    case 4: renderReview();     break;
    case 5: await startDeploy(); break;
    case 6: updateServiceUrls(); break;
    default: break;
  }
}

// ---------------------------------------------------------------------------
// Step 1: Mode selection
// ---------------------------------------------------------------------------

function initModeCards() {
  $$('.mode-card').forEach(card => {
    card.addEventListener('click', () => {
      state.selectedMode = card.dataset.mode;
      $$('.mode-card').forEach(c => {
        c.classList.toggle('selected', c === card);
        c.setAttribute('aria-checked', c === card ? 'true' : 'false');
      });
      updatePortsVisibility();
      updateNavButtons();
    });
  });
}

// ---------------------------------------------------------------------------
// Step 2: Prerequisites
// ---------------------------------------------------------------------------

async function runChecks() {
  const list = $('#checks-list');
  list.innerHTML = '<div class="checks-loading"><div class="spinner" aria-hidden="true"></div><span>Running checks…</span></div>';
  state.checksHasFail = true;
  updateNavButtons();

  try {
    const res = await fetch(`/api/checks?mode=${state.selectedMode || 'compose'}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderChecks(data.checks || []);
  } catch (err) {
    list.innerHTML = `<div class="check-error">Failed to run checks: ${escHtml(err.message)}</div>`;
  }
}

function renderChecks(checks) {
  const list = $('#checks-list');

  if (!checks.length) {
    list.innerHTML = '<p class="text-muted">No checks to run.</p>';
    state.checksHasFail = false;
    updateNavButtons();
    return;
  }

  state.checksHasFail = checks.some(c => c.status === 'fail');

  const ICONS = {
    pass: `<svg class="check-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>`,
    fail: `<svg class="check-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>`,
    warn: `<svg class="check-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
  };

  list.innerHTML = checks.map(c => {
    const statusKey = c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : 'warn';
    return `
      <div class="check-item check-${statusKey}" role="listitem">
        ${ICONS[statusKey]}
        <div class="check-body">
          <span class="check-name">${escHtml(c.name)}</span>
          ${c.message ? `<span class="check-msg">${escHtml(c.message)}</span>` : ''}
          ${c.fix     ? `<span class="check-fix">${escHtml(c.fix)}</span>` : ''}
        </div>
      </div>`;
  }).join('');

  updateNavButtons();
}

// ---------------------------------------------------------------------------
// Step 3: Configuration
// ---------------------------------------------------------------------------

function initConfigForm() {
  const providerSel  = $('#llm-provider');
  const apiKeyInput  = $('#api-key');
  const apiKeyLabel  = $('#api-key-label');
  const apiKeyHint   = $('#api-key-hint');
  const apiKeyToggle = $('#api-key-toggle');
  const eyeOpen      = apiKeyToggle.querySelector('.eye-open');
  const eyeClosed    = apiKeyToggle.querySelector('.eye-closed');

  providerSel.addEventListener('change', () => {
    const info = PROVIDER_LABELS[providerSel.value] || PROVIDER_LABELS.openai;
    apiKeyLabel.textContent  = info.label;
    apiKeyInput.placeholder  = info.placeholder;
    apiKeyHint.textContent   = info.hint;
    const apiKeyGroup = $('#api-key-group');
    apiKeyGroup.style.opacity = providerSel.value === 'ollama' ? '0.5' : '1';
    apiKeyInput.required = providerSel.value !== 'ollama';
  });

  apiKeyToggle.addEventListener('click', () => {
    const visible = apiKeyInput.type === 'text';
    apiKeyInput.type = visible ? 'password' : 'text';
    eyeOpen.style.display   = visible ? ''     : 'none';
    eyeClosed.style.display = visible ? 'none' : '';
    apiKeyToggle.setAttribute('aria-label', visible ? 'Show key' : 'Hide key');
  });

  updatePortsVisibility();

  // Populate from saved config
  fetch('/api/config')
    .then(r => r.ok ? r.json() : null)
    .then(cfg => {
      if (!cfg || !cfg.llmProvider) return;
      providerSel.value = cfg.llmProvider;
      providerSel.dispatchEvent(new Event('change'));
      if (cfg.apiKey)       apiKeyInput.value           = cfg.apiKey;
      if (cfg.dbUser)       $('#db-user').value          = cfg.dbUser;
      if (cfg.dbPassword)   $('#db-password').value      = cfg.dbPassword;
      if (cfg.dbName)       $('#db-name').value           = cfg.dbName;
      if (cfg.reposPath)    $('#repos-path').value        = cfg.reposPath;
      if (cfg.portFrontend) $('#port-frontend').value     = cfg.portFrontend;
      if (cfg.portBackend)  $('#port-backend').value      = cfg.portBackend;
      if (cfg.portDeepwiki) $('#port-deepwiki').value     = cfg.portDeepwiki;
      if (cfg.portDb)       $('#port-db').value           = cfg.portDb;
      if (cfg.portGitnexus) $('#port-gitnexus').value      = cfg.portGitnexus;
      if (cfg.corsOrigins)  $('#cors-origins').value      = cfg.corsOrigins;
    })
    .catch(() => {});
}

function updatePortsVisibility() {
  const portsSection = $('#ports-section');
  if (portsSection) {
    portsSection.style.display = state.selectedMode === 'k8s' ? 'none' : '';
  }
  const dbSection = $('#db-section');
  if (dbSection) {
    dbSection.style.display = state.selectedMode === 'native' ? 'none' : '';
  }
  const portDbGroup = $('#port-db-group');
  if (portDbGroup) {
    portDbGroup.style.display = state.selectedMode === 'native' ? 'none' : '';
  }
  const portGitnexusGroup = $('#port-gitnexus-group');
  if (portGitnexusGroup) {
    portGitnexusGroup.style.display = (state.selectedMode === 'native' || state.selectedMode === 'compose') ? '' : 'none';
  }

  $$('.service-pill[data-modes]').forEach(pill => {
    const modes = pill.dataset.modes || '';
    pill.style.display = modes.includes(state.selectedMode) ? '' : 'none';
  });
}

function collectConfig() {
  const cfg = {
    mode:          state.selectedMode,
    llmProvider:   $('#llm-provider').value,
    apiKey:        $('#api-key').value,
    reposPath:     $('#repos-path').value,
    portFrontend:  $('#port-frontend').value,
    portBackend:   $('#port-backend').value,
    portDeepwiki:  $('#port-deepwiki').value,
    portGitnexus:  $('#port-gitnexus').value,
    corsOrigins:   $('#cors-origins').value,
  };
  if (state.selectedMode !== 'native') {
    cfg.dbUser     = $('#db-user').value;
    cfg.dbPassword = $('#db-password').value;
    cfg.dbName     = $('#db-name').value;
    cfg.portDb     = $('#port-db').value;
  }
  return cfg;
}

async function saveConfig() {
  state.config = collectConfig();
  try {
    await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.config),
    });
  } catch (err) {
    console.warn('Config save failed:', err);
  }
}

// ---------------------------------------------------------------------------
// Step 4: Review
// ---------------------------------------------------------------------------

function maskKey(val) {
  if (!val || val.length < 8) return '••••••••';
  return val.slice(0, 4) + '••••••••';
}

function renderReview() {
  const cfg = state.config;
  const providerInfo = PROVIDER_LABELS[cfg.llmProvider] || PROVIDER_LABELS.openai;

  const MODE_LABELS = { native: 'Native (Local)', compose: 'Docker Compose', k8s: 'Kubernetes' };
  const rows = [
    { label: 'Deployment mode', value: MODE_LABELS[cfg.mode] || cfg.mode },
    { label: 'LLM Provider',    value: ((cfg.llmProvider || 'openai')[0].toUpperCase() + (cfg.llmProvider || 'openai').slice(1)) },
    { label: providerInfo.label, value: cfg.apiKey ? maskKey(cfg.apiKey) : '(not set)', mono: true, sensitive: true },
  ];

  if (cfg.mode !== 'native') {
    rows.push(null,
      { label: 'DB Username',     value: cfg.dbUser     || 'codetalks' },
      { label: 'DB Password',     value: maskKey(cfg.dbPassword), mono: true, sensitive: true },
      { label: 'DB Name',         value: cfg.dbName     || 'codetalks' },
    );
  }

  rows.push(null, { label: 'Repositories path', value: cfg.reposPath || './.repos', mono: true });

  if (cfg.mode === 'native') {
    rows.push(null,
      { label: 'Frontend port',    value: cfg.portFrontend  || '3005', mono: true },
      { label: 'Backend API port', value: cfg.portBackend   || '8100', mono: true },
      { label: 'GitNexus port',    value: cfg.portGitnexus  || '7100', mono: true },
    );
  } else if (cfg.mode !== 'k8s') {
    rows.push(null,
      { label: 'Frontend port',    value: cfg.portFrontend || '3005',  mono: true },
      { label: 'Backend API port', value: cfg.portBackend  || '8100',  mono: true },
      { label: 'DeepWiki port',    value: cfg.portDeepwiki || '18001', mono: true },
      { label: 'PostgreSQL port',  value: cfg.portDb       || '5433',  mono: true },
      { label: 'GitNexus port',    value: cfg.portGitnexus || '7100',  mono: true },
    );
  }

  if (cfg.corsOrigins) {
    rows.push(null, { label: 'CORS origins', value: cfg.corsOrigins, mono: true });
  }

  $('#review-content').innerHTML = `
    <table class="review-table" aria-label="Configuration summary">
      <tbody>
        ${rows.map(row => {
          if (!row) return '<tr class="review-divider"><td colspan="2"></td></tr>';
          return `<tr>
            <th class="review-key" scope="row">${escHtml(row.label)}</th>
            <td class="review-val${row.mono ? ' font-mono' : ''}${row.sensitive ? ' review-sensitive' : ''}">${escHtml(row.value)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    <p class="review-notice">
      <svg viewBox="0 0 20 20" fill="currentColor" class="review-notice-icon" aria-hidden="true"><path fill-rule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clip-rule="evenodd"/></svg>
      Sensitive values are masked. Credentials are stored locally in <code>deployer-config.json</code>.
    </p>`;
}

// ---------------------------------------------------------------------------
// Step 5: Deploy & Monitor
// ---------------------------------------------------------------------------

function resetDeployUI() {
  $('#deploy-step-name').textContent = 'Initializing…';
  setProgress(0, 0);
  $('#terminal-log').innerHTML = '';
  hide($('#deploy-error-banner'));
  $$('.service-pill').forEach(p => {
    p.className = 'service-pill pill-pending';
    const modes = p.dataset.modes || '';
    p.style.display = modes.includes(state.selectedMode) ? '' : 'none';
  });
}

async function startDeploy() {
  resetDeployUI();
  state.deployDone = false;

  if (state.deployEventSource) {
    state.deployEventSource.close();
    state.deployEventSource = null;
  }

  try {
    const res = await fetch('/api/deploy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: state.selectedMode }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      showDeployError(err.detail || 'Failed to start deployment');
      return;
    }

    const data = await res.json();
    state.deployJobId = data.job_id;
    openEventStream();
  } catch (err) {
    showDeployError(err.message);
  }
}

function openEventStream() {
  const es = new EventSource('/api/deploy/stream');
  state.deployEventSource = es;

  es.onmessage = evt => {
    let payload;
    try { payload = JSON.parse(evt.data); } catch { return; }
    handleDeployEvent(payload);
  };

  es.onerror = () => {
    if (!state.deployDone) {
      showDeployError('Connection to deployment stream lost');
    }
    es.close();
  };
}

function handleDeployEvent(evt) {
  const { step, status, message, progress } = evt;

  if (step === 'done' && status === 'done') {
    state.deployDone = true;
    if (state.deployEventSource) {
      state.deployEventSource.close();
      state.deployEventSource = null;
    }
    appendLog('success', 'Deployment complete!');
    setTimeout(() => goToStep(6), 800);
    return;
  }

  if (step)    $('#deploy-step-name').textContent = formatStepName(step);
  if (progress && typeof progress.current === 'number') setProgress(progress.current, progress.total || 0);
  if (message) appendLog(status === 'error' ? 'error' : status === 'done' ? 'success' : 'info', message);
  if (step && status) updateServicePill(step, status);
  if (status === 'error') showDeployError(message || 'An error occurred during deployment');
}

const STEP_NAMES = {
  check_env: 'Checking prerequisites',
  install_backend: 'Installing backend',
  install_frontend: 'Installing frontend',
  install_gitnexus: 'Installing GitNexus',
  generate_config: 'Generating config',
  start_services: 'Starting services',
  health_check: 'Health check',
};

function formatStepName(step) {
  return STEP_NAMES[step] || step.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function setProgress(current, total) {
  const pct  = total > 0 ? Math.round((current / total) * 100) : 0;
  const fill  = $('#deploy-progress-fill');
  const track = $('#deploy-progress-track');
  const text  = $('#deploy-progress-text');
  fill.style.width = pct + '%';
  track.setAttribute('aria-valuenow', pct);
  text.textContent = total > 0 ? `${current} / ${total}` : '';
}

function appendLog(type, message) {
  const log  = $('#terminal-log');
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  line.innerHTML = `<span class="log-ts">${escHtml(ts)}</span><span class="log-msg">${escHtml(message)}</span>`;
  log.appendChild(line);
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function updateServicePill(stepName, status) {
  const stepLower = stepName.toLowerCase();
  $$('.service-pill').forEach(pill => {
    const keywords = SERVICE_STEP_MAP[pill.dataset.service] || [pill.dataset.service];
    if (keywords.some(kw => stepLower.includes(kw))) {
      pill.className = 'service-pill ' + pillClass(status);
    }
  });
}

function pillClass(status) {
  if (status === 'running') return 'pill-running';
  if (status === 'done')    return 'pill-done';
  if (status === 'error')   return 'pill-error';
  return 'pill-pending';
}

function showDeployError(msg) {
  const banner = $('#deploy-error-banner');
  $('#deploy-error-msg').textContent = ' ' + msg;
  show(banner);
}

// ---------------------------------------------------------------------------
// Step 6: Complete
// ---------------------------------------------------------------------------

function updateServiceUrls() {
  const cfg = state.config;
  const mode = state.selectedMode;

  const deepwikiInstalled = $('#deepwiki-success') && $('#deepwiki-success').style.display !== 'none';
  const NATIVE_URLS = {
    frontend:    'http://localhost:' + (cfg.portFrontend  || '3005'),
    backend:     'http://localhost:' + (cfg.portBackend   || '8100'),
    gitnexus:    'http://localhost:' + (cfg.portGitnexus  || '7100'),
    deepwiki:    deepwikiInstalled ? 'http://localhost:' + (cfg.portDeepwiki || '8091') : null,
    codecompass: null,
    joern:       null,
    zoekt:       null,
  };

  const COMPOSE_URLS = {
    frontend:    'http://localhost:' + (cfg.portFrontend || '3005'),
    backend:     'http://localhost:' + (cfg.portBackend  || '8000'),
    deepwiki:    'http://localhost:' + (cfg.portDeepwiki || '8001'),
    gitnexus:    'http://localhost:' + (cfg.portGitnexus || '7100'),
    codecompass: 'http://localhost:16251',
    joern:       'http://localhost:8080',
    zoekt:       'http://localhost:6070',
  };

  const K8S_URLS = {
    frontend:    'http://localhost/',
    backend:     'http://localhost/api',
    deepwiki:    null,
    gitnexus:    null,
    codecompass: null,
    joern:       null,
    zoekt:       null,
  };

  const urls = mode === 'native' ? NATIVE_URLS : mode === 'k8s' ? K8S_URLS : COMPOSE_URLS;

  // Update each service-url-card
  document.querySelectorAll('.service-url-card[data-service]').forEach(card => {
    const svc = card.getAttribute('data-service');
    const url = urls[svc];
    if (!url) {
      card.style.display = 'none';  // hide inaccessible services in K8s
      return;
    }
    card.style.display = '';
    card.href = url;
    const urlSpan = card.querySelector('.url-card-url');
    if (urlSpan) urlSpan.textContent = url.replace('http://', '');
  });

  // Update the "Open CodeTalk" hero button
  const heroBtn = document.querySelector('.complete-actions .btn-primary');
  if (heroBtn) heroBtn.href = urls.frontend;

  showDeepWikiSection();
}

async function runHealthCheck() {
  const resultEl = $('#health-result');
  resultEl.style.display = '';
  resultEl.innerHTML = '<div class="health-loading"><div class="spinner" aria-hidden="true"></div>Checking services…</div>';

  try {
    const res = await fetch('/api/services/health');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderHealthResult(data.services || {});
  } catch (err) {
    resultEl.innerHTML = `<div class="health-error">Health check failed: ${escHtml(err.message)}</div>`;
  }
}

function renderHealthResult(services) {
  const resultEl = $('#health-result');
  const entries = Object.entries(services);
  if (!entries.length) {
    resultEl.innerHTML = '<p class="text-muted">No service data returned.</p>';
    return;
  }

  resultEl.innerHTML = `
    <div class="health-grid">
      ${entries.map(([name, info]) => {
        const ok = info.healthy || info.status === 'healthy' || info.status === 'ok';
        return `
          <div class="health-item ${ok ? 'health-ok' : 'health-bad'}">
            <span class="health-dot"></span>
            <span class="health-name">${escHtml(name)}</span>
            <span class="health-status">${ok ? 'Healthy' : 'Unhealthy'}</span>
          </div>`;
      }).join('')}
    </div>`;
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;')
    .replace(/'/g,  '&#39;');
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function attachNavHandlers() {
  $('#btn-next').addEventListener('click', async () => {
    if (state.currentStep === 3) await saveConfig();
    if (state.currentStep === 6) {
      const frontendUrl = state.selectedMode === 'k8s' ? 'http://localhost/' : 'http://localhost:' + (state.config.portFrontend || '3005');
      window.open(frontendUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    await goToStep(state.currentStep + 1);
  });

  $('#btn-back').addEventListener('click', async () => {
    if (state.currentStep > 1) await goToStep(state.currentStep - 1);
  });

  document.addEventListener('keydown', e => {
    const tag = document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const btnNext = $('#btn-next');
    const btnBack = $('#btn-back');
    if (e.key === 'Enter' && !btnNext.disabled && state.currentStep !== 5 && state.currentStep !== 6) {
      btnNext.click();
    }
    if (e.key === 'Escape' && !btnBack.disabled) {
      btnBack.click();
    }
  });
}

function attachDeployHandlers() {
  $('#stop-btn').addEventListener('click', async () => {
    if (state.deployEventSource) {
      state.deployEventSource.close();
      state.deployEventSource = null;
    }
    try { await fetch('/api/deploy/stop', { method: 'POST' }); } catch {}
    appendLog('error', 'Deployment stopped by user.');
    showDeployError('Deployment was stopped.');
  });

  $('#retry-btn').addEventListener('click', async () => {
    hide($('#deploy-error-banner'));
    await startDeploy();
  });

  $('#clear-log-btn').addEventListener('click', () => {
    $('#terminal-log').innerHTML = '';
  });
}

function attachStep6Handlers() {
  $('#health-check-btn').addEventListener('click', () => runHealthCheck());
  initDeepWikiSupplement();
}

function initDeepWikiSupplement() {
  const section = $('#deepwiki-supplement');
  const btn = $('#deepwiki-install-btn');
  if (!section || !btn) return;

  btn.addEventListener('click', () => startDeepWikiInstall());
}

function showDeepWikiSection() {
  const section = $('#deepwiki-supplement');
  if (section && state.selectedMode === 'native') {
    section.style.display = '';
  }
}

async function startDeepWikiInstall() {
  const pathInput = $('#deepwiki-path');
  const deepwikiPath = (pathInput.value || '').trim();
  if (!deepwikiPath) {
    pathInput.focus();
    return;
  }

  const btn = $('#deepwiki-install-btn');
  const logWrap = $('#deepwiki-install-log');
  const logEl = $('#deepwiki-log');
  const form = $('#deepwiki-form');
  const successEl = $('#deepwiki-success');

  btn.disabled = true;
  btn.textContent = 'Installing...';
  logWrap.style.display = '';
  logEl.innerHTML = '';
  hide(successEl);

  try {
    const res = await fetch('/api/deploy/supplement/deepwiki', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deepwikiPath: deepwikiPath }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      dwAppendLog('error', err.detail || 'Failed to start DeepWiki install');
      btn.disabled = false;
      btn.textContent = 'Install DeepWiki';
      return;
    }

    const es = new EventSource('/api/deploy/stream');
    es.onmessage = evt => {
      let payload;
      try { payload = JSON.parse(evt.data); } catch { return; }

      if (payload.step === 'done' && payload.status === 'done') {
        es.close();
        hide(form);
        hide(logWrap);
        successEl.style.display = '';
        $('#deepwiki-success-msg').textContent = 'DeepWiki installed and running';
        updateServiceUrls();
        return;
      }

      if (payload.message) {
        dwAppendLog(payload.status === 'error' ? 'error' : payload.status === 'done' ? 'success' : 'info', payload.message);
      }
      if (payload.status === 'error') {
        btn.disabled = false;
        btn.textContent = 'Retry Install';
        es.close();
      }
    };
    es.onerror = () => {
      dwAppendLog('error', 'Connection lost');
      btn.disabled = false;
      btn.textContent = 'Retry Install';
      es.close();
    };
  } catch (err) {
    dwAppendLog('error', err.message);
    btn.disabled = false;
    btn.textContent = 'Install DeepWiki';
  }
}

function dwAppendLog(type, message) {
  const logEl = $('#deepwiki-log');
  if (!logEl) return;
  const nearBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 50;
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  line.innerHTML = `<span class="log-ts">${escHtml(ts)}</span><span class="log-msg">${escHtml(message)}</span>`;
  logEl.appendChild(line);
  if (nearBottom) logEl.scrollTop = logEl.scrollHeight;
}

function attachRecheckHandler() {
  $('#recheck-btn').addEventListener('click', () => runChecks());
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

function init() {
  updateStepVisibility();
  initModeCards();
  initConfigForm();
  attachNavHandlers();
  attachDeployHandlers();
  attachStep6Handlers();
  attachRecheckHandler();
}

document.addEventListener('DOMContentLoaded', init);
