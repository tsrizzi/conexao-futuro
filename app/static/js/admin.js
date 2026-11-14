// Área do admin: botão de copiar o código recém-criado.
"use strict";

(() => {
  const caixa = document.querySelector("[data-codigo-novo]");
  if (!caixa) return;
  caixa.focus();
  const valor = caixa.querySelector("[data-codigo-valor]").textContent.trim();
  const status = caixa.querySelector("[data-status-copia]");
  caixa.querySelector("[data-copiar-codigo]").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(valor);
      status.textContent = "Copiado.";
    } catch (e) {
      status.textContent = "Não deu para copiar aqui. Selecione o código e copie à mão.";
    }
  });
})();
