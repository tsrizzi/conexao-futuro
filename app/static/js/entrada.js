// Porta de entrada: troca o código por uma liberação (cookie) e abre a página inicial.
"use strict";

(() => {
  const form = document.querySelector("[data-form-entrada]");
  const campo = form.querySelector("input");
  const erro = form.querySelector("[data-erro]");
  const botao = form.querySelector("button");

  if (new URLSearchParams(window.location.search).has("saiu")) {
    erro.textContent = "Seu acesso terminou. Digite um código válido para continuar.";
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const codigo = campo.value.trim();
    if (!codigo) {
      erro.textContent = "Digite o código.";
      campo.focus();
      return;
    }
    botao.disabled = true;
    erro.textContent = "";
    try {
      await CF.postarJSON("/api/entrada", { codigo });
      window.location.href = "/";
    } catch (e) {
      erro.textContent = e.message || "Não deu para entrar agora. Tente de novo.";
      campo.focus();
    } finally {
      botao.disabled = false;
    }
  });
})();
