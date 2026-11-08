// Entrar com o código de acesso (em qualquer aparelho).
"use strict";

(() => {
  const raiz = document.querySelector("[data-entrar-codigo]");
  const form = raiz.querySelector("[data-form-codigo]");
  const campo = form.querySelector("input");
  const erro = form.querySelector("[data-erro]");
  const achou = raiz.querySelector("[data-achou]");

  if (CF.sessao.codigo()) campo.value = CF.sessao.codigo();

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    erro.textContent = "";
    achou.hidden = true;
    const codigo = campo.value.trim();
    if (!codigo) { erro.textContent = "Digite o seu código."; return; }
    try {
      const resposta = await CF.postarJSON("/api/codigo", { codigo });
      const dados = await resposta.json();
      CF.sessao.salvarCodigo(codigo.toUpperCase());
      raiz.querySelector("[data-resumo-codigo]").textContent =
        `Você respondeu ${dados.respondidas} de ${dados.total} perguntas do formulário.`;
      raiz.querySelector("[data-ver-trilhas]").hidden = !dados.tem_trilhas;
      raiz.querySelector("[data-continuar-formulario]").hidden = dados.formulario_completo;
      // Ver trilhas pelo código: esquece a conversa desta aba, se houver outra.
      raiz.querySelector("[data-ver-trilhas]").addEventListener("click", () => CF.sessao.limpar());
      achou.hidden = false;
      achou.focus();
    } catch (e) {
      erro.textContent = e.message;
    }
  });
})();
