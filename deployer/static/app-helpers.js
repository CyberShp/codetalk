(function (global) {
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
    normalizeHealthServices,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  global.CodeTalkAppHelpers = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
