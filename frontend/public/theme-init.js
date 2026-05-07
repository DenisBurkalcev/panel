// Applied synchronously in <head> before paint to avoid a flash of the wrong
// theme. Mirrors the logic in src/theme.ts. Lives as an external file rather
// than an inline <script> so the panel's CSP can stay strict
// (script-src 'self', no 'unsafe-inline').
(function () {
  try {
    var stored = localStorage.getItem("fpk-theme");
    var prefersLight =
      !stored &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches;
    if (stored === "light" || prefersLight) {
      document.documentElement.classList.add("light");
    }
  } catch (_) {
    /* localStorage may be disabled (e.g. private mode); ignore. */
  }
})();
