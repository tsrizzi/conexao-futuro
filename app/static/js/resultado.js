// Tela "Minhas trilhas": desenha o resultado e cuida de copiar, baixar e imprimir.
"use strict";

(() => {
  const raiz = document.querySelector("[data-resultado]");
  const status = raiz.querySelector("[data-status]");
  const token = CF.sessao.token();
  const codigo = CF.sessao.codigo();
  // Conversa desta aba (token) ou, em outro aparelho, as trilhas salvas no código.
  const pedido = { token, codigo };

  // Cada eixo do CNCT ganha sempre a mesma cor de linha.
  const EIXOS = [
    "Informação e Comunicação", "Ambiente e Saúde", "Produção Cultural e Design",
    "Controle e Processos Industriais", "Gestão e Negócios", "Produção Industrial",
    "Infraestrutura", "Recursos Naturais", "Produção Alimentícia",
    "Desenvolvimento Educacional e Social", "Segurança", "Turismo, Hospitalidade e Lazer",
  ];
  const cor = (eixo) => Math.max(0, EIXOS.indexOf(eixo)) % 6;

  function lista(el, itens) {
    for (const texto of itens) {
      const li = document.createElement("li");
      li.textContent = texto;
      el.appendChild(li);
    }
  }

  function grupoOfertas(titulo, ofertas) {
    const div = document.createElement("div");
    div.className = "oferta-grupo";
    const t = document.createElement("strong");
    t.textContent = titulo;
    div.appendChild(t);
    const ul = document.createElement("ul");
    for (const o of ofertas) {
      const li = document.createElement("li");
      const partes = [o.instituicao, o.cidade, o.forma];
      if (o.distancia_km !== null) partes.push(`${o.distancia_km} km`);
      li.textContent = partes.join(", ") + (o.conferir ? " (confira se ainda está aberto)" : "");
      if (o.link) {
        const a = document.createElement("a");
        a.href = o.link;
        a.rel = "noopener";
        a.target = "_blank";
        a.textContent = " site";
        li.appendChild(a);
      }
      ul.appendChild(li);
    }
    div.appendChild(ul);
    return div;
  }

  function trilha(t) {
    const no = document.getElementById("modelo-trilha").content.cloneNode(true);
    const secao = no.querySelector(".trilha");
    secao.classList.add(`cor-${cor(t.eixo)}`);
    no.querySelector(".trilha-nome").textContent = t.nome;
    let meta = `${t.eixo}. ${t.carga_horaria} horas`;
    if (t.duracao) meta += `, dura em média ${t.duracao}`;
    no.querySelector(".trilha-meta").textContent = `${meta}.`;
    no.querySelector('[data-campo="porque"]').textContent = t.porque_combina;
    lista(no.querySelector('[data-campo="faz"]'), t.o_que_faz);
    no.querySelector('[data-campo="atencao"]').textContent = t.atencao;

    const onde = no.querySelector('[data-campo="onde"]');
    if (t.ofertas_agora.length) onde.appendChild(grupoOfertas("Dá para fazer junto com o Ensino Médio", t.ofertas_agora));
    if (t.ofertas_depois.length) onde.appendChild(grupoOfertas("Depois de terminar o Ensino Médio", t.ofertas_depois));
    if (t.aviso_ofertas) {
      const p = document.createElement("p");
      p.className = "oferta-sem";
      p.textContent = t.aviso_ofertas;
      onde.appendChild(p);
    }
    return no;
  }

  function desenhar(r) {
    lista(raiz.querySelector("[data-resumo]"), r.resumo_perfil);
    const dl = raiz.querySelector("[data-dimensoes]");
    for (const d of r.dimensoes) {
      const dt = document.createElement("dt");
      dt.textContent = `Seu lado ${d.nome}`;
      const dd = document.createElement("dd");
      dd.textContent = d.evidencias.map((e) => `“${e}”`).join(", ");
      dl.append(dt, dd);
    }
    raiz.querySelector("[data-incompleta]").hidden = !r.incompleta;
    const caixa = raiz.querySelector("[data-trilhas]");
    for (const t of r.trilhas) caixa.appendChild(trilha(t));
    if (r.trilhas.length !== 3) {
      raiz.querySelector("#titulo-trilhas").textContent = `${r.trilhas.length} caminhos para você pensar`;
    }
    lista(raiz.querySelector("[data-pense]"), r.pense_criticamente);
    raiz.querySelector("[data-edicao]").textContent = r.edicao_cnct;
    raiz.querySelector("[data-conteudo]").hidden = false;
  }

  function vazio() {
    raiz.querySelector("[data-vazio]").hidden = false;
  }

  async function textoTxt() {
    const resposta = await CF.postarJSON("/api/resultado.txt", pedido);
    return resposta.text();
  }

  async function copiar() {
    try {
      await navigator.clipboard.writeText(await textoTxt());
      status.textContent = "Copiado. Agora é só colar no NotebookLM ou onde quiser.";
    } catch (e) {
      status.textContent = "Não deu para copiar aqui. Use o botão Baixar .txt.";
    }
  }

  async function baixarTxt() {
    try {
      const blob = new Blob([await textoTxt()], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "minhas-trilhas.txt";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
      status.textContent = "Arquivo minhas-trilhas.txt baixado.";
    } catch (e) {
      status.textContent = e.message || "Não deu para baixar. Tente de novo.";
    }
  }

  function baixarPdf() {
    status.textContent = "Na janela que abrir, escolha Salvar como PDF.";
    window.print();
  }

  async function iniciar() {
    raiz.querySelector("[data-copiar]").addEventListener("click", copiar);
    raiz.querySelector("[data-baixar-txt]").addEventListener("click", baixarTxt);
    raiz.querySelector("[data-baixar-pdf]").addEventListener("click", baixarPdf);
    try {
      if (!token && !codigo) { vazio(); return; }
      const resposta = await CF.postarJSON("/api/resultado", pedido);
      desenhar(await resposta.json());
    } catch (e) {
      vazio();
    } finally {
      raiz.querySelector("[data-carregando]").hidden = true;
      raiz.setAttribute("aria-busy", "false");
    }
  }

  iniciar();
})();
