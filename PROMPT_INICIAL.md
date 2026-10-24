# Prompt para colar no Claude Code (primeira mensagem)

Coloque esta pasta como raiz do projeto, abra o Claude Code nela e cole:

---

Leia o CLAUDE.md e a docs/ESPECIFICACAO.md inteira. Depois:

1. Me diga em até 10 linhas o que entendeu do projeto e liste dúvidas ou pontos ambíguos que encontrou na especificação.
2. Proponha o plano da **Fase 0 — Base** (arquivos que vai criar, dependências do pyproject e como vai testar).
3. Espere minha aprovação antes de escrever código.

Ao terminar cada fase: rode testes e lint, mostre os critérios de aceite cumpridos, faça commit e pare para eu revisar.

---

## Antes da Fase 1, providencie
- [ ] O PDF do CNCT (edição em vigor), baixado do portal do MEC, em `data/raw/`.
- [ ] Uma chave da API da Anthropic (console.anthropic.com), com limite de gasto mensal configurado.
- [ ] (Opcional) Prints da tela "Ofertas Próximas" do SouTec, para preencher `data/ofertas_regiao.json`.
