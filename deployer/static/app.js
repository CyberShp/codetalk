/**
 * CodeTalk Deployer — Wizard App
 * Vanilla JS ES module, zero dependencies.
 */

const {
  normalizeHealthServices,
} = window.CodeTalkAppHelpers;

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
  hasDeployError: false,
  pendingForceTakeover: false,
};

// Step metadata
const STEP_LABELS = ['选择模式', '环境检查', '参数配置', '确认配置', '部署', '完成'];

// Service -> SSE step keyword mapping (partial match)
const SERVICE_STEP_MAP = {
  frontend:    ['frontend'],
  backend:     ['backend', 'api'],
  postgres:    ['postgres', 'database', 'db'],
  redis:       ['redis', 'cache'],
  deepwiki:    ['deepwiki', 'wiki'],
  gitnexus:    ['gitnexus', 'git'],
  codecompass: ['codecompass', 'compass'],
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
    headerLabel.textContent = `第 ${state.currentStep} 步 / 共 ${state.totalSteps} 步 — ${STEP_LABELS[state.currentStep - 1]}`;
  }

  updateNavButtons();
}

function updateNavButtons() {
  const btnBack = $('#btn-back');
  const btnNext = $('#btn-next');

  // Relabel Next button
  let nextLabel = '下一步';
  if (state.currentStep === 4) nextLabel = '开始部署';
  if (state.currentStep === 6) nextLabel = '打开 CodeTalk';
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
  list.innerHTML = '<div class="checks-loading"><div class="spinner" aria-hidden="true"></div><span>正在运行检查&hellip;</span></div>';
  state.checksHasFail = true;
  updateNavButtons();

  try {
    const res = await fetch(`/api/checks?mode=${state.selectedMode || 'compose'}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderChecks(data.checks || []);
  } catch (err) {
    list.innerHTML = `<div class="check-error">检查运行失败：${escHtml(err.message)}</div>`;
  }
}

function renderChecks(checks) {
  const list = $('#checks-list');

  if (!checks.length) {
    list.innerHTML = '<p class="text-muted">暂无检查项。</p>';
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
  // Workspace path auto-derives repos-path and deepwiki-path
  const workspaceInput = $('#workspace-path');
  const reposPathInput = $('#repos-path');
  const deepwikiPathInput = $('#deepwiki-path');
  if (workspaceInput) {
    workspaceInput.addEventListener('input', () => {
      const ws = workspaceInput.value.replace(/[/\\]+$/, '');
      if (reposPathInput) reposPathInput.value = ws + '/repos';
      if (deepwikiPathInput && !deepwikiPathInput.dataset.userEdited) deepwikiPathInput.value = ws + '/deepwiki-open';
    });
  }
  if (deepwikiPathInput) {
    deepwikiPathInput.addEventListener('input', () => { deepwikiPathInput.dataset.userEdited = '1'; });
  }

  // Show/hide component port panels based on checkbox state
  const installDeepwikiCb = $('#install-deepwiki');
  const installGitnexusCb = $('#install-gitnexus');
  const installCgcCb = $('#install-cgc');
  if (installDeepwikiCb) {
    const syncDeepwikiPorts = () => {
      const ports = $('#deepwiki-ports');
      if (ports) ports.style.display = installDeepwikiCb.checked ? '' : 'none';
    };
    installDeepwikiCb.addEventListener('change', syncDeepwikiPorts);
    syncDeepwikiPorts();
  }
  const embedderSel = $('#deepwiki-embedder-type');
  if (embedderSel) {
    embedderSel.addEventListener('change', () => updateEmbedderFields(embedderSel.value));
  }
  if (installGitnexusCb) {
    installGitnexusCb.addEventListener('change', () => {
      const ports = $('#gitnexus-ports');
      if (ports) ports.style.opacity = installGitnexusCb.checked ? '1' : '0.4';
    });
  }
  if (installCgcCb) {
    installCgcCb.addEventListener('change', () => {
      const ports = $('#cgc-ports');
      if (ports) ports.style.opacity = installCgcCb.checked ? '1' : '0.4';
    });
  }
  updatePortsVisibility();

  // Populate from saved config
  fetch('/api/config')
    .then(r => r.ok ? r.json() : null)
    .then(cfg => {
      if (!cfg) return;
      state.config = { ...state.config, ...cfg };
      if (cfg.workspacePath) {
        ($('#workspace-path') || {}).value = cfg.workspacePath;
        if ($('#repos-path')) $('#repos-path').value = cfg.workspacePath.replace(/[/\\]+$/, '') + '/repos';
      }
      if (installDeepwikiCb && cfg.installDeepwiki !== undefined) {
        installDeepwikiCb.checked = !!cfg.installDeepwiki;
        const ports = $('#deepwiki-ports');
        if (ports) ports.style.display = installDeepwikiCb.checked ? '' : 'none';
      }
      if (cfg.installGitnexus === false && installGitnexusCb) installGitnexusCb.checked = false;
      if (cfg.portDeepwikiApi)       ($('#port-deepwiki-api')       || {}).value = cfg.portDeepwikiApi;
      if (cfg.portDeepwikiUi)        ($('#port-deepwiki-ui')        || {}).value = cfg.portDeepwikiUi;
      if (cfg.deepwikiPath)          ($('#deepwiki-path')           || {}).value = cfg.deepwikiPath;
      if (cfg.deepwikiEmbedderType) {
        const sel = $('#deepwiki-embedder-type');
        if (sel) { sel.value = cfg.deepwikiEmbedderType; updateEmbedderFields(cfg.deepwikiEmbedderType); }
      }
      if (cfg.deepwikiGoogleApiKey) ($('#deepwiki-google-api-key') || {}).value = cfg.deepwikiGoogleApiKey;
      if (cfg.deepwikiOllamaHost)   ($('#deepwiki-ollama-host')    || {}).value = cfg.deepwikiOllamaHost;
      if (cfg.portGitnexus)    ($('#port-gitnexus')     || {}).value  = cfg.portGitnexus;
      if (installCgcCb && cfg.installCgc !== undefined) installCgcCb.checked = !!cfg.installCgc;
      if (cfg.portCgc)         ($('#port-cgc')          || {}).value  = cfg.portCgc;
      if (cfg.dbUser)          ($('#db-user')            || {}).value  = cfg.dbUser;
      if (cfg.dbPassword)      ($('#db-password')        || {}).value  = cfg.dbPassword;
      if (cfg.dbName)          ($('#db-name')            || {}).value  = cfg.dbName;
      if (cfg.reposPath)       ($('#repos-path')         || {}).value  = cfg.reposPath;
      if (cfg.portFrontend)    ($('#port-frontend')      || {}).value  = cfg.portFrontend;
      if (cfg.portBackend)     ($('#port-backend')       || {}).value  = cfg.portBackend;
      if (cfg.portDb)          ($('#port-db')            || {}).value  = cfg.portDb;
      if (cfg.corsOrigins)     ($('#cors-origins')       || {}).value  = cfg.corsOrigins;
    })
    .catch(() => {});
}

function updateEmbedderFields(type) {
  const googleLabel = $('#dw-google-key-label');
  const googleInput = $('#deepwiki-google-api-key');
  const ollamaLabel = $('#dw-ollama-host-label');
  const ollamaInput = $('#deepwiki-ollama-host');
  const showGoogle = type === 'google';
  const showOllama = type === 'ollama';
  if (googleLabel) googleLabel.style.display = showGoogle ? '' : 'none';
  if (googleInput) googleInput.style.display = showGoogle ? '' : 'none';
  if (ollamaLabel) ollamaLabel.style.display = showOllama ? '' : 'none';
  if (ollamaInput) ollamaInput.style.display = showOllama ? '' : 'none';
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

  $$('.service-pill[data-modes]').forEach(pill => {
    const modes = pill.dataset.modes || '';
    pill.style.display = modes.includes(state.selectedMode) ? '' : 'none';
  });
}

function collectConfig() {
  const installDeepwikiCb = $('#install-deepwiki');
  const installGitnexusCb = $('#install-gitnexus');
  const installCgcCb      = $('#install-cgc');
  const cfg = {
    mode:            state.selectedMode,
    workspacePath:   (($('#workspace-path') || {}).value || './workspace').trim(),
    installDeepwiki: installDeepwikiCb ? installDeepwikiCb.checked : true,
    installGitnexus: installGitnexusCb ? installGitnexusCb.checked : true,
    installCgc:      installCgcCb ? installCgcCb.checked : true,
    portDeepwikiApi:       (($('#port-deepwiki-api')       || {}).value || '8091').trim(),
    portDeepwikiUi:        (($('#port-deepwiki-ui')        || {}).value || '3001').trim(),
    deepwikiPath:          (($('#deepwiki-path')           || {}).value || '').trim(),
    deepwikiEmbedderType:   (($('#deepwiki-embedder-type')   || {}).value || 'openai').trim(),
    deepwikiGoogleApiKey:   (($('#deepwiki-google-api-key') || {}).value || '').trim(),
    deepwikiOllamaHost:     (($('#deepwiki-ollama-host')    || {}).value || '').trim(),
    portGitnexus:    (($('#port-gitnexus')     || {}).value || '7100').trim(),
    portCgc:         (($('#port-cgc')          || {}).value || '7072').trim(),
    reposPath:       (($('#repos-path')        || {}).value || './.repos'),
    portFrontend:    (($('#port-frontend')     || {}).value || '3005'),
    portBackend:     (($('#port-backend')      || {}).value || '8100'),
    corsOrigins:     (($('#cors-origins')      || {}).value || ''),
  };
  if (state.selectedMode !== 'native') {
    cfg.dbUser     = (($('#db-user')     || {}).value || '');
    cfg.dbPassword = (($('#db-password') || {}).value || '');
    cfg.dbName     = (($('#db-name')     || {}).value || '');
    cfg.portDb     = (($('#port-db')     || {}).value || '5433');
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
  const MODE_LABELS = { native: '本地原生部署', compose: 'Docker Compose', k8s: 'Kubernetes' };

  const rows = [
    { label: '部署模式',  value: MODE_LABELS[cfg.mode] || cfg.mode },
    { label: '工作目录',  value: cfg.workspacePath || './workspace', mono: true },
  ];

  const components = [];
  if (cfg.installDeepwiki !== false) {
    components.push(`DeepWiki-Open（API :${cfg.portDeepwikiApi || '8091'}，UI :${cfg.portDeepwikiUi || '3001'}）`);
  }
  if (cfg.installGitnexus !== false) {
    components.push(`GitNexus（:${cfg.portGitnexus || '7100'}）`);
  }
  if (cfg.installCgc !== false) {
    components.push(`CGC（:${cfg.portCgc || '7072'}）`);
  }
  rows.push({ label: '安装组件', value: components.length ? components.join('，') : '（无）' });

  if (cfg.mode !== 'native') {
    rows.push(null,
      { label: '数据库用户名', value: cfg.dbUser     || 'codetalks' },
      { label: '数据库密码',   value: maskKey(cfg.dbPassword), mono: true, sensitive: true },
      { label: '数据库名',     value: cfg.dbName     || 'codetalks' },
    );
  }

  rows.push(null, { label: '仓库存储路径', value: cfg.reposPath || './.repos', mono: true });

  if (cfg.mode !== 'k8s') {
    rows.push(null,
      { label: '前端端口',      value: cfg.portFrontend || '3005', mono: true },
      { label: '后端 API 端口', value: cfg.portBackend  || '8100', mono: true },
    );
  }

  if (cfg.corsOrigins) {
    rows.push(null, { label: 'CORS 允许来源', value: cfg.corsOrigins, mono: true });
  }

  $('#review-content').innerHTML = `
    <table class="review-table" aria-label="配置摘要">
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
      敏感信息已脱敏显示。凭证仅保存在本地 <code>deployer-config.json</code>。
    </p>`;
}

// ---------------------------------------------------------------------------
// Step 5: Deploy & Monitor
// ---------------------------------------------------------------------------

function resetDeployUI() {
  $('#deploy-step-name').textContent = '初始化中…';
  setProgress(0, 0);
  $('#terminal-log').innerHTML = '';
  hide($('#deploy-error-banner'));

  const installDeepwiki = state.config.installDeepwiki !== false;
  const installGitnexus = state.config.installGitnexus !== false;

  $$('.service-pill').forEach(p => {
    p.className = 'service-pill pill-pending';
    const modes = p.dataset.modes || '';
    const svc   = p.dataset.service;
    let visible = modes.includes(state.selectedMode);
    if (svc === 'deepwiki' && !installDeepwiki) visible = false;
    if (svc === 'gitnexus' && !installGitnexus) visible = false;
    p.style.display = visible ? '' : 'none';
  });
}

async function startDeploy() {
  resetDeployUI();
  state.deployDone = false;
  state.hasDeployError = false;

  if (state.deployEventSource) {
    state.deployEventSource.close();
    state.deployEventSource = null;
  }

  const forceTakeover = state.pendingForceTakeover;
  state.pendingForceTakeover = false;

  try {
    const res = await fetch('/api/deploy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: state.selectedMode, force_takeover: forceTakeover }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      if (detail && typeof detail === 'object' && detail.conflicts) {
        state.pendingForceTakeover = true;
        const lines = detail.conflicts.map(c =>
          `端口 ${c.port} 被 ${c.process_name}(PID ${c.pid})${c.is_own ? '（本实例）' : ''} 占用`
        );
        showDeployError(`端口冲突 — ${lines.join('；')}。点击「重试」将强制接管。`);
      } else {
        showDeployError((typeof detail === 'string' ? detail : detail?.message) || '启动部署失败');
      }
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
      showDeployError('与部署流的连接已断开');
    }
    es.close();
  };
}

function handleDeployEvent(evt) {
  const { step, status, message, progress } = evt;

  if (step === 'done' && status === 'done') {
    if (state.hasDeployError) return;  // suppress false Complete when prior errors exist
    state.deployDone = true;
    if (state.deployEventSource) {
      state.deployEventSource.close();
      state.deployEventSource = null;
    }
    appendLog('success', '部署完成！');
    setTimeout(() => goToStep(6), 800);
    return;
  }

  if (step === 'done' && status === 'cancelled') {
    state.deployDone = true;
    if (state.deployEventSource) {
      state.deployEventSource.close();
      state.deployEventSource = null;
    }
    appendLog('info', message || '部署已取消');
    return;
  }

  if (step === 'done' && status === 'error') {
    if (state.deployEventSource) {
      state.deployEventSource.close();
      state.deployEventSource = null;
    }
    showDeployError(message || '部署失败');
    return;
  }

  if (step)    $('#deploy-step-name').textContent = formatStepName(step);
  if (progress && typeof progress.current === 'number') setProgress(progress.current, progress.total || 0);
  if (message) appendLog(status === 'error' ? 'error' : status === 'done' ? 'success' : 'info', message);
  if (step && status) updateServicePill(step, status);
  if (status === 'error') {
    state.hasDeployError = true;
    showDeployError(message || '部署过程中发生错误');
  }
}

const STEP_NAMES = {
  check_env:        '检查环境',
  install_backend:  '安装后端',
  install_frontend: '安装前端',
  install_gitnexus: '安装 GitNexus',
  install_deepwiki: '安装 DeepWiki',
  generate_config:  '生成配置文件',
  start_services:   '启动服务',
  health_check:     '健康检查',
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
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
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
  const installDeepwiki = cfg.installDeepwiki !== false;
  const installGitnexus = cfg.installGitnexus !== false;

  const deepwikiUiPort = cfg.portDeepwikiUi || '3001';
  const gitnexusPort   = cfg.portGitnexus   || '7100';

  const NATIVE_URLS = {
    frontend:    'http://localhost:' + (cfg.portFrontend  || '3005'),
    backend:     'http://localhost:' + (cfg.portBackend   || '8100'),
    deepwiki:    installDeepwiki ? 'http://localhost:' + deepwikiUiPort : null,
    gitnexus:    installGitnexus ? 'http://localhost:' + gitnexusPort   : null,
    codecompass: null,
    joern:       null,
  };

  const COMPOSE_URLS = {
    frontend:    'http://localhost:' + (cfg.portFrontend || '3005'),
    backend:     'http://localhost:' + (cfg.portBackend  || '8100'),
    deepwiki:    installDeepwiki ? 'http://localhost:' + deepwikiUiPort : null,
    gitnexus:    installGitnexus ? 'http://localhost:' + gitnexusPort   : null,
    codecompass: 'http://localhost:16251',
    joern:       'http://localhost:8080',
  };

  const K8S_URLS = {
    frontend:    'http://localhost/',
    backend:     'http://localhost/api',
    deepwiki:    null,
    gitnexus:    null,
    codecompass: null,
    joern:       null,
  };

  const urls = mode === 'native' ? NATIVE_URLS : mode === 'k8s' ? K8S_URLS : COMPOSE_URLS;

  // Update each service-url-card
  document.querySelectorAll('.service-url-card[data-service]').forEach(card => {
    const svc = card.getAttribute('data-service');
    const url = urls[svc];
    if (!url) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    card.href = url;
    const urlSpan = card.querySelector('.url-card-url');
    if (urlSpan) urlSpan.textContent = url.replace('http://', '');
  });

  // Hide restart buttons in modes that don't support per-service restart
  const supportsRestart = mode !== 'k8s';
  document.querySelectorAll('.restart-btn').forEach(btn => {
    btn.style.display = supportsRestart ? '' : 'none';
  });

  // Update the "Open CodeTalk" hero button
  const heroBtn = document.querySelector('.complete-actions .btn-primary');
  if (heroBtn) heroBtn.href = urls.frontend;
}

async function restartService(btn) {
  const card = btn.closest('.service-url-card');
  if (!card) return;
  const service = card.getAttribute('data-service');
  if (!service) return;

  btn.disabled = true;
  btn.classList.remove('success', 'error');
  btn.classList.add('spinning');

  try {
    const resp = await fetch(`/api/services/${encodeURIComponent(service)}/restart`, { method: 'POST' });
    btn.classList.remove('spinning');
    if (resp.ok) {
      btn.classList.add('success');
    } else {
      btn.classList.add('error');
    }
  } catch (_) {
    btn.classList.remove('spinning');
    btn.classList.add('error');
  } finally {
    btn.disabled = false;
    setTimeout(() => btn.classList.remove('success', 'error'), 2500);
  }
}

async function runHealthCheck() {
  const resultEl = $('#health-result');
  resultEl.style.display = '';
  resultEl.innerHTML = '<div class="health-loading"><div class="spinner" aria-hidden="true"></div>正在检查服务&hellip;</div>';

  try {
    const res = await fetch('/api/services/health');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderHealthResult(data.services || {});
  } catch (err) {
    resultEl.innerHTML = `<div class="health-error">健康检查失败：${escHtml(err.message)}</div>`;
  }
}

function renderHealthResult(services) {
  const resultEl = $('#health-result');
  const entries = normalizeHealthServices(services);
  if (!entries.length) {
    resultEl.innerHTML = '<p class="text-muted">未返回任何服务数据。</p>';
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
            <span class="health-status">${ok ? '正常' : '异常'}</span>
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
    if (state.currentStep === 3) {
      const dwCb = $('#install-deepwiki');
      const dwPath = $('#deepwiki-path');
      if (dwCb && dwCb.checked && dwPath && !dwPath.value.trim()) {
        dwPath.setCustomValidity('请填写 DeepWiki-Open 源码路径');
        dwPath.reportValidity();
        return;
      }
      if (dwPath) dwPath.setCustomValidity('');
      await saveConfig();
    }
    if (state.currentStep === 6) {
      const frontendUrl = state.selectedMode === 'k8s' ? 'http://localhost/' : 'http://localhost:' + (state.config.portFrontend || '3005');
      window.open(frontendUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    await goToStep(state.currentStep + 1);
  });

  $('#btn-back').addEventListener('click', async () => {
    if (state.currentStep > 1) {
      state.pendingForceTakeover = false;
      await goToStep(state.currentStep - 1);
    }
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
    appendLog('error', '用户已手动停止部署。');
    showDeployError('部署已被停止。');
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
