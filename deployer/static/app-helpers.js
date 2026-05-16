(function (global) {
  function getDeepWikiApiPort(mode, cfg) {
    const fallback = mode === 'native' ? '8091' : '8001';
    return String((cfg && cfg.portDeepwiki) || fallback);
  }

  function getDeepWikiUiPort(mode, cfg) {
    const fallback = mode === 'native' ? '3001' : '3000';
    return String((cfg && cfg.deepwikiUiPort) || fallback);
  }

  function normalizeHealthServices(services) {
    if (Array.isArray(services)) {
      return services.map((info, index) => [
        info && info.name ? info.name : `service-${index + 1}`,
        info || {},
      ]);
    }
    if (!services || typeof services !== 'object') {
      return [];
    }
    return Object.entries(services);
  }

  const api = {
    getDeepWikiApiPort,
    getDeepWikiUiPort,
    normalizeHealthServices,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  global.CodeTalkAppHelpers = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
