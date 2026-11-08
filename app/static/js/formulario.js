// Formulário de apoio: uma pergunta por cartão, "pular" em todas, parar e continuar depois.
"use strict";

(() => {
  const raiz = document.querySelector("[data-formulario]");
  const cartoes = [...raiz.querySelectorAll("[data-pergunta]")];
  const total = cartoes.length;
  const rotulo = raiz.querySelector("[data-progresso-rotulo]");
  const trilho = raiz.querySelector("[data-progresso-trilho]");
  const linha = raiz.querySelector("[data-progresso-linha]");
  const erro = raiz.querySelector("[data-erro]");
  const CHAVE = "cf_formulario_rascunho";

  let atual = 0;
  let enviando = false; // evita envio duplo (toque duplo no "Terminar")
  let respostas = {};
  try { respostas = JSON.parse(sessionStorage.getItem(CHAVE) || "{}"); } catch (e) { respostas = {}; }

  function guardarRascunho() {
    try { sessionStorage.setItem(CHAVE, JSON.stringify(respostas)); } catch (e) { /* ignorado */ }
  }

  function lerCartao(cartao) {
    const id = cartao.dataset.pergunta;
    if (cartao.dataset.tipo === "escala") {
      const marcado = cartao.querySelector("input:checked");
      if (marcado) respostas[id] = marcado.value;
    } else {
      const texto = cartao.querySelector("textarea").value.trim();
      if (texto) respostas[id] = texto; else delete respostas[id];
    }
    guardarRascunho();
  }

  function preencher() {
    for (const cartao of cartoes) {
      const valor = respostas[cartao.dataset.pergunta];
      if (valor === undefined) continue;
      if (cartao.dataset.tipo === "escala") {
        const opcao = cartao.querySelector(`input[value="${valor}"]`);
        if (opcao) opcao.checked = true;
      } else {
        cartao.querySelector("textarea").value = valor;
      }
    }
  }

  function mostrar(indice) {
    atual = Math.max(0, Math.min(indice, total - 1));
    cartoes.forEach((c, i) => { c.hidden = i !== atual; });
    rotulo.textContent = `Pergunta ${atual + 1} de ${total}`;
    trilho.setAttribute("aria-valuenow", String(atual + 1));
    linha.style.width = `${(atual / (total - 1)) * 100}%`;
    const alvo = cartoes[atual].querySelector("input:checked, input, textarea");
    if (alvo) alvo.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }

  async function enviar(completo) {
    if (enviando) return;
    enviando = true;
    raiz.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    erro.textContent = "";
    const corpo = { respostas, completo, codigo: CF.sessao.codigo(), codigo_turma: CF.sessao.turma() };
    try {
      const resposta = await CF.postarJSON("/api/formulario", corpo);
      const dados = await resposta.json();
      if (dados.codigo) CF.sessao.salvarCodigo(dados.codigo);
      try { sessionStorage.removeItem(CHAVE); } catch (e) { /* ignorado */ }
      mostrarCodigo(dados, completo);
    } catch (e) {
      erro.textContent = e.message || "Não deu para salvar agora. Tente de novo.";
    } finally {
      enviando = false;
      raiz.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  }

  function mostrarCodigo(dados, completo) {
    raiz.querySelector("[data-etapa-perguntas]").hidden = true;
    const etapa = raiz.querySelector("[data-etapa-codigo]");
    etapa.hidden = false;
    raiz.querySelector("[data-codigo]").textContent = CF.sessao.codigo();
    if (!completo) {
      raiz.querySelector("[data-titulo-codigo]").textContent = "Respostas guardadas";
    }
    if (dados.acolhimento) {
      const caixa = raiz.querySelector("[data-acolhimento]");
      caixa.innerHTML = CF.paragrafos(dados.acolhimento);
      caixa.hidden = false;
    }
    etapa.focus();
  }

  raiz.addEventListener("click", (ev) => {
    const botao = ev.target.closest("button");
    if (!botao || enviando) return;
    const cartao = botao.closest("[data-pergunta]");
    if (botao.hasAttribute("data-voltar")) { lerCartao(cartao); mostrar(atual - 1); }
    else if (botao.hasAttribute("data-pular")) {
      delete respostas[cartao.dataset.pergunta];
      guardarRascunho();
      if (atual === total - 1) enviar(true); else mostrar(atual + 1);
    } else if (botao.hasAttribute("data-proxima")) {
      lerCartao(cartao);
      if (atual === total - 1) enviar(true); else mostrar(atual + 1);
    } else if (botao.hasAttribute("data-parar")) {
      lerCartao(cartoes[atual]);
      enviar(false);
    } else if (botao.hasAttribute("data-copiar-codigo")) {
      const status = raiz.querySelector("[data-status-codigo]");
      navigator.clipboard.writeText(CF.sessao.codigo())
        .then(() => { status.textContent = "Código copiado."; })
        .catch(() => { status.textContent = "Não deu para copiar. Anote à mão."; });
    }
  });

  async function iniciar() {
    const continuar = new URLSearchParams(window.location.search).has("continuar");
    if (continuar && CF.sessao.codigo()) {
      // Voltou com código: traz o que já foi respondido e vai para a primeira em branco.
      try {
        const r = await CF.postarJSON("/api/codigo", { codigo: CF.sessao.codigo() });
        respostas = { ...(await r.json()).respostas, ...respostas };
      } catch (e) {
        erro.textContent = e.message;
      }
    } else if (!continuar) {
      CF.sessao.esquecerCodigo(); // formulário novo = código novo
    }
    preencher();
    const primeiraEmBranco = cartoes.findIndex((c) => respostas[c.dataset.pergunta] === undefined);
    mostrar(continuar && primeiraEmBranco >= 0 ? primeiraEmBranco : 0);
  }

  iniciar();
})();
