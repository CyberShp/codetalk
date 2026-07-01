/**
 * CodeTalk Start Panel — Service launcher & monitor.
 * Vanilla JS, zero dependencies.
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // DOM helpers
  // ---------------------------------------------------------------------------

  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };

  function escHtml(str) {
    return String(str != null ? str : '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function errorDetailMessage(detail, fallback) {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
      if (detail.message) return String(detail.message);
      if (detail.error) return String(detail.error);
    }
    return fallback;
  }

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  var isStarting = false;
  var eventSource = null;
  var statusTimer = null;
  var reconnectTimer = null;
  var reconnectDelay = 1000;
  var forceTakeover = false;

  // Services we track — must match data-svc attrs in HTML
  var SERVICES = ['backend', 'frontend', 'gitnexus', 'cgc'];

  var STATUS_LABELS = {
    running:  '运行中',
    stopped:  '已停止',
    error:    '异常',
    starting: '启动中',
  };

  // ---------------------------------------------------------------------------
  // Config — load user-customized ports/addresses
  // ---------------------------------------------------------------------------

  var savedConfig = {};

  function fetchConfig() {
    fetch('/api/config')
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (cfg) {
        if (!cfg) return;
        savedConfig = cfg;
        applyConfigToUI(cfg);
      })
      .catch(function () {});
  }

  function applyConfigToUI(cfg) {
    var backendPort  = cfg.portBackend     || 3004;
    var frontendPort = cfg.portFrontend    || 3003;
    var gitnexusPort = cfg.portGitnexus    || 7100;
    var cgcPort      = cfg.portCgc         || 7072;

    // Update port labels on service cards
    var portBackend  = document.getElementById('port-backend');
    var portFrontend = document.getElementById('port-frontend');
    var portGitnexus = document.getElementById('port-gitnexus');
    var portCgc      = document.getElementById('port-cgc');

    if (portBackend)  portBackend.textContent  = ':' + backendPort;
    if (portFrontend) portFrontend.textContent = ':' + frontendPort;
    if (portGitnexus) portGitnexus.textContent = ':' + gitnexusPort;
    if (portCgc)      portCgc.textContent      = ':' + cgcPort;

    // Update "Open CodeTalk" links
    var frontendUrl = 'http://localhost:' + frontendPort;
    var openLink = $('#open-ct-link');
    if (openLink) openLink.href = frontendUrl;
    var bannerLink = $('#success-ct-link');
    if (bannerLink) bannerLink.href = frontendUrl;

    // Populate config info bar with full addresses
    var bar = $('#config-info-bar');
    if (bar) bar.classList.add('visible');

    setInfoLink('cfg-workspace', null, cfg.workspacePath || './workspace');
    setInfoLink('cfg-frontend-url', 'http://localhost:' + frontendPort);
    setInfoLink('cfg-backend-url', 'http://localhost:' + backendPort);
    setInfoLink('cfg-gitnexus-url', 'http://localhost:' + gitnexusPort);
    setInfoLink('cfg-cgc-url', 'http://localhost:' + cgcPort);

  }

  function setInfoLink(id, url, text) {
    var el = document.getElementById(id);
    if (!el) return;
    if (url) {
      el.innerHTML = '<a href="' + escHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escHtml(text || url) + '</a>';
    } else {
      el.textContent = text || '—';
    }
  }

  // ---------------------------------------------------------------------------
  // Status polling
  // ---------------------------------------------------------------------------

  function fetchStatus() {
    fetch('/api/services/status')
      .then(function (res) {
        if (!res.ok) return;
        return res.json();
      })
      .then(function (data) {
        if (data) updateStatusUI(data);
      })
      .catch(function () {});
  }

  function updateStatusUI(data) {
    var processes = data.processes || {};
    var anyRunning = false;

    SERVICES.forEach(function (svc) {
      var proc = processes[svc];
      var state = 'stopped';
      if (proc) {
        state = proc.running ? 'running' : 'error';
      }
      if (isStarting && state === 'stopped') {
        state = 'starting';
      }
      if (state === 'running') anyRunning = true;

      var dot = $('#dot-' + svc);
      var label = $('#label-' + svc);
      if (dot) {
        dot.className = 'svc-status-dot dot-' + state;
      }
      if (label) {
        label.className = 'svc-status-label label-' + state;
        label.textContent = STATUS_LABELS[state] || state;
      }

      var startBtn = $('[data-svc="' + svc + '"][data-action="start"]');
      var stopBtn = $('[data-svc="' + svc + '"][data-action="stop"]');
      var restartBtn = $('[data-svc="' + svc + '"][data-action="restart"]');
      if (startBtn) startBtn.disabled = (state === 'running' || state === 'starting');
      if (stopBtn) stopBtn.disabled = (state === 'stopped');
      if (restartBtn) restartBtn.disabled = (state !== 'running');
    });

    // Show "Open CodeTalk" button when any service is running
    var openBtn = $('#open-ct-link');
    if (openBtn) {
      openBtn.style.display = anyRunning ? '' : 'none';
    }
  }

  function startPolling() {
    stopPolling();
    fetchStatus();
    statusTimer = setInterval(fetchStatus, 5000);
  }

  function stopPolling() {
    if (statusTimer) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Terminal logging
  // ---------------------------------------------------------------------------

  function appendLog(type, message) {
    var log = $('#terminal-log');
    if (!log) return;
    var nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
    var line = document.createElement('div');
    line.className = 'log-line log-' + type;
    var ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    line.innerHTML =
      '<span class="log-ts">' + escHtml(ts) + '</span>' +
      '<span class="log-msg">' + escHtml(message) + '</span>';
    log.appendChild(line);
    if (nearBottom) log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // SSE stream handling
  // ---------------------------------------------------------------------------

  function openEventStream() {
    closeEventStream();
    var es = new EventSource('/api/deploy/stream');
    eventSource = es;

    es.onmessage = function (evt) {
      var payload;
      try { payload = JSON.parse(evt.data); } catch (e) { return; }
      handleStreamEvent(payload);
    };

    es.onerror = function () {
      es.close();
      eventSource = null;
      if (isStarting) {
        var delay = reconnectDelay;
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        appendLog('error', 'SSE 连接断开，' + (delay / 1000).toFixed(0) + 's 后重连...');
        reconnectTimer = setTimeout(openEventStream, delay);
      }
    };
  }

  function closeEventStream() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function handleStreamEvent(evt) {
    var step = evt.step || '';
    var status = evt.status || '';
    var message = evt.message || '';

    // Done sentinel
    if (step === 'done' && status === 'done') {
      isStarting = false;
      closeEventStream();
      appendLog('success', '所有服务已成功启动');
      showSuccessBanner();
      fetchStatus();
      setButtonsEnabled(true);
      return;
    }

    if (step === 'done' && status === 'cancelled') {
      isStarting = false;
      closeEventStream();
      appendLog('info', message || '操作已取消');
      fetchStatus();
      setButtonsEnabled(true);
      return;
    }

    if (step === 'done' && status === 'error') {
      isStarting = false;
      closeEventStream();
      appendLog('error', message || '启动失败');
      fetchStatus();
      setButtonsEnabled(true);
      return;
    }

    // Regular log events
    if (message) {
      var logType = status === 'error' ? 'error' : status === 'done' ? 'success' : 'info';
      appendLog(logType, message);
    }

    // Refresh status indicators after each event
    fetchStatus();
  }

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  function quickstart() {
    if (isStarting) return;
    isStarting = true;
    reconnectDelay = 1000;
    setButtonsEnabled(false);
    hideSuccessBanner();
    appendLog('info', '正在启动全部服务...');

    // Mark all as starting
    SERVICES.forEach(function (svc) {
      var dot = $('#dot-' + svc);
      var label = $('#label-' + svc);
      if (dot) dot.className = 'svc-status-dot dot-starting';
      if (label) {
        label.className = 'svc-status-label label-starting';
        label.textContent = '启动中';
      }
    });

    var ft = forceTakeover;
    setForceTakeoverMode(false);

    fetch('/api/quickstart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_takeover: ft }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (err) {
            var detail = err.detail;
            if (detail && typeof detail === 'object' && detail.conflicts) {
              setForceTakeoverMode(true);
              var lines = detail.conflicts.map(function (c) {
                return '端口 ' + c.port + ' 被 ' + c.process_name + '(PID ' + c.pid + ')' + (c.is_own ? '（本实例）' : '') + ' 占用';
              });
              throw new Error('端口冲突：' + lines.join('；') + '。请确认这些进程可被关闭，然后点击「强制接管并启动」。');
            }
            throw new Error(typeof detail === 'string' ? detail : (detail && detail.message) || 'HTTP ' + res.status);
          }).catch(function (e) {
            if (e.message) throw e;
            throw new Error('HTTP ' + res.status);
          });
        }
        setForceTakeoverMode(false);
        // Connect to SSE for live logs
        openEventStream();
      })
      .catch(function (e) {
        isStarting = false;
        appendLog('error', '启动请求失败: ' + e.message);
        setButtonsEnabled(true);
        fetchStatus();
      });
  }

  function stopAll() {
    closeEventStream();
    isStarting = false;
    setForceTakeoverMode(false);
    appendLog('info', '正在停止全部服务...');
    hideSuccessBanner();

    fetch('/api/services/stop', { method: 'POST' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        appendLog('success', data.message || '已停止');
      })
      .catch(function (e) {
        appendLog('error', '停止请求失败: ' + e.message);
      })
      .then(function () {
        fetchStatus();
      });
  }

  function serviceAction(btn, name, action) {
    var labels = { start: '启动', stop: '停止', restart: '重启' };
    btn.disabled = true;
    btn.classList.add('loading');
    appendLog('info', name + ' → ' + (labels[action] || action) + '...');
    fetch('/api/services/' + encodeURIComponent(name) + '/' + action, { method: 'POST' })
      .then(function (res) {
        if (!res.ok) {
          return res.json()
            .catch(function () { return {}; })
            .then(function (err) {
              throw new Error(errorDetailMessage(err.detail, 'HTTP ' + res.status));
            });
        }
        return res.json();
      })
      .then(function () {
        btn.classList.remove('loading');
        btn.classList.add('action-success');
        appendLog('success', name + ' ' + (labels[action] || action) + '完成');
      })
      .catch(function (e) {
        btn.classList.remove('loading');
        btn.classList.add('action-error');
        appendLog('error', name + ' ' + (labels[action] || action) + '失败: ' + e.message);
      })
      .then(function () {
        btn.disabled = false;
        setTimeout(function () { btn.classList.remove('action-success', 'action-error'); }, 2500);
        fetchStatus();
      });
  }

  // ---------------------------------------------------------------------------
  // UI helpers
  // ---------------------------------------------------------------------------

  function setButtonsEnabled(enabled) {
    var btnStart = $('#btn-start-all');
    var btnStop = $('#btn-stop-all');
    if (btnStart) btnStart.disabled = !enabled;
    if (btnStop) btnStop.disabled = !enabled;
  }

  function setForceTakeoverMode(enabled) {
    forceTakeover = !!enabled;
    var btnStart = $('#btn-start-all');
    var label = $('#btn-start-label');
    if (btnStart) {
      btnStart.classList.toggle('force-takeover', forceTakeover);
      btnStart.setAttribute(
        'aria-label',
        forceTakeover ? '强制接管并启动全部服务' : '一键启动全部服务'
      );
      btnStart.title = forceTakeover
        ? '将关闭占用端口的进程后重新启动 CodeTalk'
        : '';
    }
    if (label) label.textContent = forceTakeover ? '强制接管并启动' : '一键启动全部';
  }

  function showSuccessBanner() {
    var banner = $('#success-banner');
    if (banner) banner.classList.add('visible');
    var openBtn = $('#open-ct-link');
    if (openBtn) openBtn.style.display = '';
  }

  function hideSuccessBanner() {
    var banner = $('#success-banner');
    if (banner) banner.classList.remove('visible');
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------

  function init() {
    var btnStart = $('#btn-start-all');
    var btnStop = $('#btn-stop-all');
    var clearBtn = $('#clear-log-btn');

    if (btnStart) btnStart.addEventListener('click', quickstart);
    if (btnStop) btnStop.addEventListener('click', stopAll);
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        var log = $('#terminal-log');
        if (log) log.innerHTML = '';
      });
    }

    var grid = $('#service-grid');
    if (grid) {
      grid.addEventListener('click', function (e) {
        var btn = e.target.closest('.svc-btn');
        if (!btn || btn.disabled) return;
        serviceAction(btn, btn.dataset.svc, btn.dataset.action);
      });
    }

    // Load saved config then begin status polling
    fetchConfig();
    startPolling();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
