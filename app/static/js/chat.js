// Tela de conversa: streaming das respostas, botões de resposta rápida e progresso.
"use strict";

(() => {
  const lista = document.querySelector("[data-conversa]");
  const pensando = document.querySelector("[data-pensando]");
  const form = document.querySelector("[data-compositor]");
  const campo = form.querySelector("textarea");
  const botao = form.querySelector("button");
  const contador = document.querySelector("[data-contador]");
  const rotulo = document.getElementById("progresso-rotulo");
  const trilho = document.querySelector(".progresso-trilho");
  const linha = document.querySelector("[data-progresso-linha]");

  // O que já foi mostrado, para redesenhar a conversa ao voltar do resultado.
  let historico = CF.sessao.conversa();
  let ocupado = false;
  let ultimaMensagem = null;

  function rolarParaFim() {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  function adicionar(tipo, html) {
    const item = document.createElement("li");
    item.className = `mensagem mensagem-${tipo}`;
    item.innerHTML = html;
    lista.appendChild(item);
    return item;
  }

  function mostrarProgresso(percentual) {
    const valor = Math.max(0, Math.min(100, percentual));
    linha.style.width = `${valor}%`;
    trilho.setAttribute("aria-valuenow", String(valor));
    rotulo.textContent = valor === 0 ? "Começando a te conhecer" : `Já te conheço uns ${valor}%`;
  }

  function mostrarOpcoes(item, opcoes) {
    const grupo = document.createElement("div");
    grupo.className = "opcoes";
    grupo.setAttribute("role", "group");
    grupo.setAttribute("aria-label", "Respostas rápidas");
    for (const opcao of opcoes) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "opcao";
      b.textContent = opcao;
      b.addEventListener("click", () => enviar(opcao));
      grupo.appendChild(b);
    }
    item.appendChild(grupo);
  }

  function mostrarTrilhasProntas(nomes) {
    const modelo = document.getElementById("modelo-trilhas-prontas").content.cloneNode(true);
    const ul = modelo.querySelector("[data-lista]");
    for (const nome of nomes) {
      const li = document.createElement("li");
      li.textContent = nome;
      ul.appendChild(li);
    }
    lista.appendChild(modelo);
  }

  function removerOpcoesAntigas() {
    lista.querySelectorAll(".opcoes").forEach((g) => g.remove());
  }

  function redesenhar() {
    for (const m of historico) {
      if (m.tipo === "trilhas") { mostrarTrilhasProntas(m.nomes); continue; }
      const item = adicionar(m.tipo, m.tipo === "voce" ? CF.escapar(m.texto) : CF.paragrafos(m.texto));
      if (m.opcoes && m === historico[historico.length - 1]) mostrarOpcoes(item, m.opcoes);
    }
    const p = historico.findLast ? historico.findLast((m) => m.progresso !== undefined) : null;
    if (p) mostrarProgresso(p.progresso);
  }

  const textoPensando = pensando.querySelector("span:last-child");
  let avisoDemora = null;

  function ocupar(sim) {
    ocupado = sim;
    botao.disabled = sim;
    pensando.hidden = !sim;
    clearTimeout(avisoDemora);
    textoPensando.textContent = "Pensando…";
    // Se o provedor de IA estiver lento, avisa que não travou.
    if (sim) {
      avisoDemora = setTimeout(() => {
        textoPensando.textContent = "Está demorando mais que o normal, mas a resposta está a caminho…";
      }, 8000);
    }
  }

  function erroComRetentativa(texto) {
    const item = adicionar("erro", `<p>${CF.escapar(texto)}</p>`);
    if (ultimaMensagem !== null) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-outline-secondary";
      b.textContent = "Tentar de novo";
      const texto = ultimaMensagem;
      b.addEventListener("click", () => { item.remove(); enviar(texto, { repetir: true }); });
      item.appendChild(b);
    }
  }

  function conversaExpirada(texto) {
    const item = adicionar("erro", `<p>${CF.escapar(texto)}</p>`);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-primary";
    b.textContent = "Começar de novo";
    b.addEventListener("click", () => { CF.sessao.limpar(); window.location.reload(); });
    item.appendChild(b);
  }

  async function conversar(url, corpo) {
    ocupar(true);
    let bolha = null;
    let texto = "";
    let registro = null;
    let progresso;
    rolarParaFim();

    const aoEvento = (tipo, dados) => {
      switch (tipo) {
        case "texto":
          if (!bolha) {
            pensando.hidden = true;
            bolha = adicionar("ia", "");
            registro = { tipo: "ia", texto: "" };
            historico.push(registro);
          }
          texto += dados.texto;
          registro.texto = texto;
          bolha.innerHTML = CF.paragrafos(texto);
          break;
        case "opcoes":
          if (bolha) { mostrarOpcoes(bolha, dados.opcoes); registro.opcoes = dados.opcoes; }
          break;
        case "progresso":
          progresso = dados.percentual;
          mostrarProgresso(progresso);
          break;
        case "aviso":
          adicionar("aviso", CF.paragrafos(dados.texto));
          historico.push({ tipo: "aviso", texto: dados.texto });
          break;
        case "erro":
          erroComRetentativa(dados.texto);
          break;
        case "recomendacao": {
          const nomes = dados.trilhas.map((t) => t.nome);
          mostrarTrilhasProntas(nomes);
          historico.push({ tipo: "trilhas", nomes });
          bolha = null; // o texto seguinte vem numa nova estação
          texto = "";
          break;
        }
        case "sessao":
          CF.sessao.salvarToken(dados.token);
          break;
        default:
          break;
      }
      rolarParaFim();
    };

    try {
      await CF.postarSSE(url, corpo, aoEvento);
    } catch (erro) {
      if (erro.status === 400) conversaExpirada(erro.message);
      else erroComRetentativa(erro.status ? erro.message : "Sem conexão. Confira a internet e tente de novo.");
    } finally {
      if (progresso !== undefined && historico.length) historico[historico.length - 1].progresso = progresso;
      CF.sessao.salvarConversa(historico);
      ocupar(false);
      campo.focus();
    }
  }

  async function enviar(texto, { repetir = false } = {}) {
    const limpo = texto.trim();
    if (!limpo || ocupado) return;
    removerOpcoesAntigas();
    if (!repetir) {
      adicionar("voce", CF.escapar(limpo));
      historico.push({ tipo: "voce", texto: limpo });
    }
    ultimaMensagem = limpo;
    campo.value = "";
    atualizarContador();
    await conversar("/api/chat/mensagem", { token: CF.sessao.token(), texto: limpo });
  }

  function atualizarContador() {
    contador.textContent = `${campo.value.length}/500`;
    campo.style.height = "auto";
    campo.style.height = `${Math.min(campo.scrollHeight, 144)}px`;
    // Só mostra a rolagem quando o texto passa da altura máxima.
    campo.style.overflowY = campo.scrollHeight > 144 ? "auto" : "hidden";
  }

  form.addEventListener("submit", (ev) => { ev.preventDefault(); enviar(campo.value); });
  campo.addEventListener("input", atualizarContador);
  campo.addEventListener("keydown", (ev) => {
    // Enter envia; Shift+Enter quebra a linha.
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); form.requestSubmit(); }
  });

  if (CF.sessao.token() && historico.length) {
    redesenhar();
    rolarParaFim();
  } else {
    CF.sessao.limpar();
    historico = [];
    const codigo = CF.sessao.codigo();
    conversar("/api/chat/iniciar", codigo ? { codigo } : {});
  }
})();
