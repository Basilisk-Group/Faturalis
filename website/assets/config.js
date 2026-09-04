// Where the qr-bench app itself is running. This website is only a static
// marketing shell + login gate in front of it. See ../README.md.
//
// Change the default below to point at wherever qr-bench is actually hosted
// (e.g. "https://qr-bench.example.com/" or "http://127.0.0.1:8000/" for a
// local `uv run uvicorn qr_bench.main:app` on the same machine). It can also
// be overridden per-visitor without editing this file, by opening the site
// once with `?app=<url>`. The value is saved in the browser's localStorage
// and reused after that.
(function () {
  var DEFAULT_APP_URL = "http://127.0.0.1:8000/";
  var STORAGE_KEY = "faturalis_app_url";

  var params = new URLSearchParams(window.location.search);
  var fromQuery = params.get("app");
  if (fromQuery) {
    try {
      localStorage.setItem(STORAGE_KEY, fromQuery);
    } catch (e) {
      /* localStorage unavailable (private mode, etc.), fall through */
    }
  }

  var stored = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    /* ignore */
  }

  window.FATURALIS_CONFIG = {
    appUrl: fromQuery || stored || DEFAULT_APP_URL,
  };
})();
