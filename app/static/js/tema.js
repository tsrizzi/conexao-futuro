// Aplica o tema antes de desenhar a página (evita piscar). Carregado no <head>.
(function () {
  var salvo = null;
  try { salvo = localStorage.getItem("cf_tema"); } catch (e) { /* armazenamento bloqueado */ }
  var escuro = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-bs-theme", salvo || (escuro ? "dark" : "light"));
})();
