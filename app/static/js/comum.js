// Funções comuns: tema, armazenamento da sessão, texto seguro e leitura de SSE via POST.
"use strict";

const CF = (() => {
  const CHAVE_TOKEN = "cf_token";
  const CHAVE_CONVERSA = "cf_conversa";
  const CHAVE_TURMA = "cf_turma";
  const CHAVE_CODIGO = "cf_codigo";

  // sessionStorage some quando a aba fecha: é ali que a conversa mora (nada no servidor).
  function ler(chave) {
    try { return sessionStorage.getItem(chave); } catch (e) { return null; }
  }
  function gravar(chave, valor) {
    try { sessionStorage.setItem(chave, valor); } catch (e) { /* modo privado etc. */ }
  }
  function apagar(chave) {
    try { sessionStorage.removeItem(chave); } catch (e) { /* idem */ }
  }

  const sessao = {
    token: () => ler(CHAVE_TOKEN),
    salvarToken: (t) => gravar(CHAVE_TOKEN, t),
    conversa() {
      try { return JSON.parse(ler(CHAVE_CONVERSA) || "[]"); } catch (e) { return []; }
    },
    salvarConversa: (itens) => gravar(CHAVE_CONVERSA, JSON.stringify(itens)),
    turma: () => ler(CHAVE_TURMA),
    salvarTurma: (c) => gravar(CHAVE_TURMA, c),
    // Código de acesso (formulário): só nesta aba; em outro aparelho, digita de novo.
    codigo: () => ler(CHAVE_CODIGO),
    salvarCodigo: (c) => gravar(CHAVE_CODIGO, c),
    limpar() { apagar(CHAVE_TOKEN); apagar(CHAVE_CONVERSA); },
    esquecerCodigo: () => apagar(CHAVE_CODIGO),
  };

  // Texto vindo do servidor vira HTML seguro: escapa tudo e só então aplica **negrito**.
  function escapar(texto) {
    return String(texto)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function paragrafos(texto) {
    return escapar(texto.trim())
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .split(/\n{2,}/)
      .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  async function erroDaResposta(resposta) {
    try {
      const corpo = await resposta.json();
      return corpo.detail || "Algo deu errado. Tente de novo.";
    } catch (e) {
      return "Algo deu errado. Tente de novo.";
    }
  }

  // EventSource só faz GET; aqui lemos o stream de um POST e separamos os eventos.
  async function postarSSE(url, corpo, aoEvento) {
    const resposta = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(corpo),
    });
    if (!resposta.ok) {
      const erro = new Error(await erroDaResposta(resposta));
      erro.status = resposta.status;
      throw erro;
    }
    const leitor = resposta.body.getReader();
    const decodificador = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await leitor.read();
      if (done) break;
      buffer += decodificador.decode(value, { stream: true });
      let fim;
      while ((fim = buffer.indexOf("\n\n")) !== -1) {
        const bloco = buffer.slice(0, fim);
        buffer = buffer.slice(fim + 2);
        let tipo = "message";
        let dados = "";
        for (const linha of bloco.split("\n")) {
          if (linha.startsWith("event: ")) tipo = linha.slice(7);
          else if (linha.startsWith("data: ")) dados += linha.slice(6);
        }
        aoEvento(tipo, dados ? JSON.parse(dados) : {});
      }
    }
  }

  async function postarJSON(url, corpo) {
    const resposta = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    if (!resposta.ok) {
      const erro = new Error(await erroDaResposta(resposta));
      erro.status = resposta.status;
      throw erro;
    }
    return resposta;
  }

  // Tema claro/escuro.
  function alternarTema() {
    const raiz = document.documentElement;
    const novo = raiz.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    raiz.setAttribute("data-bs-theme", novo);
    try { localStorage.setItem("cf_tema", novo); } catch (e) { /* ignorado */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-alternar-tema]").forEach((b) => b.addEventListener("click", alternarTema));

    // "Começar conversa" no início sempre abre uma conversa nova, sem código.
    document.querySelectorAll("[data-nova-conversa]").forEach((link) =>
      link.addEventListener("click", () => { sessao.limpar(); sessao.esquecerCodigo(); }));
    // Conversa nova, mas partindo das respostas do código.
    document.querySelectorAll("[data-conversar-com-codigo]").forEach((link) =>
      link.addEventListener("click", () => sessao.limpar()));

    // Código da turma (opcional), guardado só nesta aba.
    const formTurma = document.querySelector("[data-form-turma]");
    if (formTurma) {
      const campo = formTurma.querySelector("input");
      const status = formTurma.querySelector("[data-turma-status]");
      if (sessao.turma()) campo.value = sessao.turma();
      formTurma.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const codigo = campo.value.trim().toUpperCase();
        sessao.salvarTurma(codigo);
        status.textContent = codigo ? `Código ${codigo} salvo.` : "Código removido.";
      });
    }
  });

  return { sessao, escapar, paragrafos, postarSSE, postarJSON };
})();
