# Conexão Futuro — Especificação do Sistema

**Autor:** Tiago Silveira Rizzi · Especialização em Tecnologias para EPT (IFSC/TEDPRO) · Orientador: Prof. Luís Henrique Lindner
**Versão:** 1.0 (24/10/2026)
**Status:** pronta para desenvolvimento. Inclui o levantamento do teste SouTec (seção 7 e `data/soutec_itens.yaml`).

---

## 1. Visão geral

### 1.1 Problema
Estudantes do 2º ano do Ensino Médio da rede pública de Guaramirim (SC) chegam ao fim do ciclo básico sem direcionamento profissional e, em geral, sem saber que existem cursos técnicos gratuitos na região. A ferramenta oficial (SouTec/MEC) pede 72 questões estáticas, o que desengaja os estudantes.

### 1.2 Solução
Um **agente conversacional web** que conversa com o estudante na linguagem dele, descobre interesses, habilidades e valores, e recomenda **trilhas de formação técnica do Catálogo Nacional de Cursos Técnicos (CNCT)**, sempre explicando o porquê de cada sugestão. As respostas ficam ancoradas no catálogo oficial por meio de **RAG** (Retrieval-Augmented Generation), para que o agente não invente cursos.

### 1.3 Contexto de uso (da proposta formativa P3)
Oficina com 3 encontros de 50 min, para uma turma de 25 a 35 estudantes:

| Encontro | Onde | O que o sistema precisa fazer |
|---|---|---|
| 1 | Sala de aula | Apresentar o agente. O estudante pode responder o **formulário de apoio** (opcional), que gera um **código de acesso**. |
| 2 | Laboratório ou celular | **A turma inteira usa ao mesmo tempo.** Quem tem código recupera as respostas e conversa a partir delas; quem não tem conversa do zero. |
| Entre 2 e 3 | Casa (opcional) | Acesso remoto livre, pela internet. |
| 3 | Sala de aula | Roda de conversa. O professor usa o **painel da turma** (agregado e anônimo). O estudante pode exportar o resultado para o NotebookLM. |

### 1.4 Princípios
1. **Sugestão, não veredito.** Toda recomendação vem com justificativa e com um convite à reflexão crítica.
2. **Nada de dados pessoais.** Sem nome, CPF, e-mail ou telefone. O estudante é identificado só por um código aleatório.
3. **Ancorado no catálogo.** O agente só afirma fatos sobre cursos que estejam no CNCT carregado. Se não souber, diz que não sabe.
4. **Linguagem jovem sem infantilizar.** Tom informal, direto e acolhedor, frases curtas, uma pergunta por vez.
5. **Tudo opcional.** O formulário, o código e a exportação são opcionais. O chat funciona sem nada disso.

---

## 2. Decisão crítica: qual LLM usar

> ⚠️ **Os Termos Adicionais da API do Gemini (vigentes desde 23/03/2026) proíbem o uso da API em sites ou apps "direcionados ou com probabilidade de serem acessados por menores de 18 anos".** O público deste projeto tem de 15 a 17 anos. Além disso, no plano gratuito o Google pode usar os dados enviados para treino e revisão humana.
> **Conclusão: o Gemini não pode ser o provedor em produção com os estudantes.**

**Provedor padrão: API da Anthropic (Claude).** A Anthropic permite organizações que atendem menores, desde que cumpram três requisitos:
- informar claramente ao usuário que ele está falando com uma IA;
- implementar salvaguardas próprias (moderação, escopo restrito, monitoramento);
- cumprir a legislação de proteção de crianças e adolescentes e de dados (no Brasil: LGPD art. 14 e ECA).

O sistema implementa os três (seções 8 e 9). O modelo padrão é `claude-haiku-5-5`, que é rápido e barato, configurável por variável de ambiente.

**Camada de LLM trocável:** o código nunca chama um provedor direto. Uma interface única tem adaptadores para:

| Adaptador | Uso |
|---|---|
| `anthropic` | **Padrão em produção.** |
| `openai_compat` | Qualquer API compatível com OpenAI (ex.: provedores de Llama/Qwen hospedados). Verificar os termos de cada provedor sobre menores antes de usar com estudantes. |
| `ollama` | Modelos open source rodando localmente (Llama, Qwen, Gemma). Para desenvolvimento, ou para um servidor próprio no futuro. Não roda em nuvem gratuita (falta RAM/GPU). |
| `gemini` | **Só para desenvolvimento e testes com adultos.** Bloqueado quando `APP_ENV=production`. |

---

## 3. Arquitetura

```
┌──────────────────────── Navegador (celular ou PC) ────────────────────────┐
│  Bootstrap 5 + JS puro · chat com streaming (SSE) · formulário · resultado │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │ HTTPS
┌──────────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI (Python 3.12)                                                      │
│  ├─ routes/  chat · formulario · resultado · professor · health            │
│  ├─ agent/   orquestrador da conversa (estados) + extração de perfil       │
│  ├─ rag/     busca híbrida (embeddings locais + BM25) sobre o CNCT          │
│  ├─ llm/     interface LLMProvider + adaptadores                           │
│  ├─ safety/  filtros de entrada e saída, detecção de dados pessoais, crise │
│  └─ db/      SQLAlchemy → Postgres (produção) / SQLite (desenvolvimento)   │
└───────┬───────────────────────┬────────────────────────────┬───────────────┘
        │                       │                            │
  data/cnct.json         índice vetorial em            API do LLM
  (catálogo processado)  arquivo (numpy), carregado     (Anthropic por padrão)
                         na memória ao iniciar
```

### 3.1 Stack
- **Backend:** Python 3.12, FastAPI, Uvicorn, Jinja2 (templates), SQLAlchemy 2 + Alembic, Pydantic v2.
- **Frontend:** Bootstrap 5.3 (CDN), JavaScript puro, sem framework e sem etapa de build. Mobile-first.
- **RAG:**
  - **Embeddings locais** com `fastembed` (ONNX, leve) e o modelo `paraphrase-multilingual-MiniLM-L12-v2`, que entende português. São locais para que nenhum texto do estudante saia do servidor só para gerar embedding.
  - **BM25** (`rank-bm25`) combinado com os embeddings (busca híbrida).
  - **Índice:** o catálogo tem cerca de 200 cursos, então basta uma matriz numpy salva em arquivo, sem banco vetorial dedicado.
- **Banco:** Postgres gratuito (Neon, região São Paulo) em produção, SQLite local.
- **Hospedagem:** Google Cloud Run (camada gratuita), com deploy automático a cada push no GitHub. Decidido na Fase 6 (nov/2026): o Render gratuito tem 512 MB e o app não sobe neles (medido: pico de ~535 MB ao carregar o modelo de embeddings); o Cloud Run permite 1 GB. Sem uso, o Cloud Run desliga a instância e leva alguns segundos para acordar, por isso **abra o site 5 minutos antes da oficina**. O disco do container é descartável: nada persistente fica nele, só no Postgres.
- **Testes:** pytest, httpx, e Locust para teste de carga.
- **Qualidade de código:** ruff + mypy (modo básico).

### 3.2 Estrutura de pastas
```
conexao-futuro/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── Dockerfile               # imagem de produção (Cloud Run)
├── docs/
│   ├── ESPECIFICACAO.md          ← este arquivo
│   └── AVISO_PRIVACIDADE.md
├── data/
│   ├── raw/                      ← PDF do CNCT (não versionar se for grande)
│   ├── cnct.json                 ← catálogo processado (versionado)
│   ├── cnct_index.npz            ← embeddings (gerado por script)
│   ├── ofertas_regiao.json       ← onde cada curso é ofertado (preenchido à mão)
│   ├── perguntas.yaml            ← formulário curto (18 itens + 3 abertas)
│   ├── soutec_itens.yaml         ← os 72 itens do SouTec classificados (referência)
│   └── dimensoes_eixos.yaml      ← dimensões R/I/A/S/E/C → eixos do CNCT
├── scripts/
│   ├── ingest_cnct.py            ← PDF → cnct.json
│   ├── build_index.py            ← cnct.json → cnct_index.npz
│   └── eval_recomendacoes.py     ← avaliação com perfis fictícios
├── app/
│   ├── main.py
│   ├── config.py                 ← pydantic-settings, lê o .env
│   ├── llm/        (base.py, anthropic.py, openai_compat.py, ollama.py, gemini.py, factory.py)
│   ├── rag/        (catalog.py, retriever.py)
│   ├── agent/      (orchestrator.py, profile.py, prompts/)
│   ├── safety/     (pii.py, moderation.py, crisis.py)
│   ├── db/         (models.py, session.py, migrations/)
│   ├── routes/
│   ├── templates/
│   └── static/     (css/, js/, img/)
└── tests/
```

---

## 4. Dados

### 4.1 CNCT (Catálogo Nacional de Cursos Técnicos)
- **Fonte:** edição em vigor do CNCT (MEC/SETEC), em PDF baixado manualmente para `data/raw/`.
- **Atenção:** a 5ª edição está prevista para dezembro de 2026. Por isso o catálogo é **substituível sem mudar código**: basta rodar os scripts de ingestão e de índice de novo. `cnct.json` guarda o campo `edicao`, que aparece no rodapé do site.
- **Esquema de cada curso** (`cnct.json` → lista):
```json
{
  "id": "tecnico-em-informatica",
  "nome": "Técnico em Informática",
  "eixo": "Informação e Comunicação",
  "carga_horaria_min": 1200,
  "perfil_profissional": "...",
  "campo_atuacao": "...",
  "ocupacoes_cbo": ["..."],
  "infraestrutura": "...",
  "itinerarios": "...",
  "texto_busca": "texto concatenado usado no índice",
  "edicao": "4ª edição (2020)"
}
```
- `ingest_cnct.py`: extrai com `pdfplumber`, valida com Pydantic e gera um relatório de campos vazios ou suspeitos. **Um humano revisa uma amostra de cursos antes de usar.** Se o PDF for difícil de parsear, aceitar também uma planilha CSV montada à mão como entrada alternativa.

### 4.2 Ofertas na região (opcional, mas muito valioso)
O CNCT diz quais cursos existem, mas não onde são ofertados. `ofertas_regiao.json` é preenchido manualmente pelo Tiago. **Fonte mais prática: a tela "Ofertas Próximas" do próprio SouTec**, configurada para Guaramirim (cerca de 120 resultados até 55 km). Ela já traz instituição, forma de oferta, requisito e distância. Depois, cada oferta é conferida no site da instituição (IFSC, SENAI, rede estadual etc.).
```json
[{ "curso_id": "tecnico-em-eletromecanica", "instituicao": "SENAI SC - Guaramirim",
   "tipo": "Sistema S|Pública federal|Pública estadual|Privada",
   "cidade": "Guaramirim", "distancia_km": 1.4,
   "forma": "integrado|concomitante|subsequente|EaD",
   "requisito": "cursando EM|EM concluído", "gratuito": null,
   "link": "https://...", "fonte": "SouTec", "verificado_em": "2026-10-24" }]
```
- A forma de oferta conversa diretamente com o público: para um aluno do 2º ano, as relevantes **agora** são as **concomitantes** (fazer junto com o EM). As subsequentes ficam como "depois que terminar o EM". O resultado deve separar as duas.
- `gratuito` fica `null` quando não se sabe. Nesse caso, o agente não afirma gratuidade.
- Se o arquivo estiver vazio ou o curso não aparecer nele, o agente diz: "não tenho a lista de onde esse curso é ofertado aqui perto; vale perguntar na coordenação ou pesquisar no site do IFSC e do SENAI". **Nunca inventa instituição.**
- Ofertas com `verificado_em` com mais de 12 meses aparecem com o aviso "confira se ainda está aberto".

### 4.3 Banco de dados (tabelas)
| Tabela | Campos principais | Observação |
|---|---|---|
| `turma` | id, codigo_turma (ex.: `GUA-2B-7K`), escola, criada_em, expira_em | Criada pelo professor no painel. |
| `participante` | id, codigo_acesso (ex.: `ONDA-4827`), turma_id (opcional), criado_em, expira_em | **Sem nenhum dado pessoal.** |
| `formulario_resposta` | participante_id, pergunta_id, resposta, criado_em | |
| `perfil` | participante_id, json_perfil, atualizado_em | Perfil estruturado (seção 6.2). |
| `recomendacao` | participante_id, cursos (json), justificativas, criado_em | |
| `evento_agregado` | turma_id, eixo, curso_id, tipo, criado_em | Alimenta o painel do professor, sem ligação com o participante. |

- **Não armazenar o texto das conversas.** O histórico fica na sessão do navegador, e o servidor guarda só o perfil estruturado e a recomendação. Opcional: `LOG_CONVERSAS=true` grava logs **anonimizados**, usados só na fase de testes com adultos, nunca com estudantes.
- **Retenção:** códigos e dados expiram em 60 dias (configurável) e são apagados por uma tarefa diária.

### 4.4 Código de acesso
- Formato `PALAVRA-0000`: palavra curta em português, de uma lista curada e inofensiva (ex.: ONDA, TREM, LUA, PIXEL), mais 4 dígitos. É fácil de anotar e de lembrar.
- Sem caracteres ambíguos. Sem diferença entre maiúsculas e minúsculas.
- Proteção contra força bruta: limite de 10 tentativas por IP a cada 10 min.
- Na tela: "Anote seu código! É com ele que você volta pras suas respostas. A gente não pede seu nome."

### 4.5 Código de entrada no site (decidido em 14/11/2026)
O site não fica aberto: o projeto é um TCC e a API do LLM é paga pelo autor. A página inicial
pede um **código de entrada** (`XXXX-XXXX`, sem caracteres ambíguos), gerado pelo autor na
área `/admin` (usuário + senha via `ADMIN_USUARIO` e `ADMIN_PASSWORD_HASH`) com **data de
início e de término**. Um código serve para várias pessoas (ex.: uma turma inteira) e pode
ser revogado a qualquer momento; revogar ou terminar o período corta o acesso na próxima
mensagem. No banco fica só o HMAC, os 4 últimos caracteres e a contagem de usos. Detalhes em
`docs/superpowers/specs/2026-11-14-acesso-controlado-design.md`.

---

## 5. Telas (frontend)

Bootstrap 5, mobile-first, com tema claro e escuro, botões grandes e contraste AA.

0. **Porta de entrada** — sem código de entrada válido (seção 4.5), a página inicial mostra só "Digite o código para usar". As demais telas do estudante voltam para ela.
1. **Início** — título "Conexão Futuro", uma frase sobre o que é e três botões: "Começar conversa", "Responder o formulário antes" e "Já tenho um código". O código da turma é opcional, num campo discreto. Rodapé com "Você está conversando com uma inteligência artificial", link para o aviso de privacidade e a edição do CNCT.
2. **Formulário de apoio** — as perguntas de `perguntas.yaml`, uma por tela (estilo cartão), com barra de progresso, botão "pular" em todas e opção de parar e continuar depois. No final, mostra o código de acesso em destaque, com botão de copiar.
3. **Chat** — balões de mensagem, streaming de texto, **botões de resposta rápida** (chips) quando a pergunta tem opções, além de texto livre. Mostra um indicador de "pensando…" e, no topo, um medidor de progresso da descoberta ("já te conheço uns 60%").
4. **Resultado ("Minhas trilhas")** — um cartão com:
   - um resumo do perfil em 3 linhas;
   - **3 trilhas sugeridas**, cada uma com nome, eixo, "por que combina com você" (ligado ao que o estudante disse), o que faz esse profissional, carga horária e onde estudar (se houver em `ofertas_regiao.json`);
   - **"Pense criticamente"**, com 2 ou 3 perguntas para o estudante avaliar a sugestão;
   - botões: **Copiar**, **Baixar (.txt e .pdf)** (formato pensado para colar no NotebookLM), **Abrir mural da turma (Padlet)** (link configurado por turma) e **Continuar conversando**.
5. **Painel do professor** (`/professor`) — login por senha (variável de ambiente) e criação de turmas. Por turma, mostra:
   - quantos participaram (contagem);
   - eixos e cursos mais sugeridos (gráfico de barras);
   - perguntas para puxar a roda de conversa.
   **Nunca** mostra respostas individuais.
6. **Aviso de privacidade** — linguagem simples, para estudantes e responsáveis.

---

## 6. O agente conversacional

### 6.1 Máquina de estados
```
BOAS_VINDAS → (CARREGA_FORMULARIO?) → DESCOBERTA → CONFIRMA_PERFIL → RECOMENDA → EXPLORA ⇄ RECOMENDA
```
- **BOAS_VINDAS:** apresenta-se como IA, explica em 2 frases o que vai acontecer e avisa: "não precisa me dizer seu nome nem nada pessoal".
- **CARREGA_FORMULARIO:** se houver código com respostas, resume o que já sabe ("vi que você curte X e Y, bora aprofundar?") e pula as perguntas já respondidas.
- **DESCOBERTA:** uma pergunta por vez, de 6 a 12 trocas, cobrindo as dimensões da seção 7. Adapta as perguntas às respostas, sem roteiro rígido. Se o estudante responder curto ("sei lá"), oferece exemplos concretos ou opções.
- **CONFIRMA_PERFIL:** "Deixa eu ver se entendi: …" e o estudante confirma ou corrige.
- **RECOMENDA:** chama o RAG e entrega 3 trilhas (seção 6.3). Mais trilhas só se o estudante pedir.
- **EXPLORA:** responde dúvidas sobre os cursos sugeridos ou outros cursos, sempre com base no catálogo. Permite "refazer" com novos interesses.
- O estudante pode dizer "já quero ver as sugestões" a qualquer momento. Nesse caso, o agente recomenda com o que tem e avisa que a sugestão fica mais certeira com mais conversa.

### 6.2 Perfil estruturado
Depois de cada mensagem do estudante, uma chamada de **extração** (tool use / saída estruturada) atualiza o perfil:
```json
{
  "interesses": ["..."],
  "atividades_que_gosta": ["..."],
  "materias_favoritas": ["..."],
  "materias_dificeis": ["..."],
  "habilidades_percebidas": ["..."],
  "valores_trabalho": ["ajudar pessoas", "estabilidade", "criatividade"],
  "ambiente_preferido": "escritório|ao ar livre|laboratório|oficina|com pessoas|indiferente",
  "restricoes": ["precisa ser perto", "quer trabalhar logo"],
  "dimensoes": {
    "R": {"nota": 0.0, "evidencias": ["disse que conserta a bike sozinho"]},
    "I": {"nota": 0.0, "evidencias": []}, "A": {"nota": 0.0, "evidencias": []},
    "S": {"nota": 0.0, "evidencias": []}, "E": {"nota": 0.0, "evidencias": []},
    "C": {"nota": 0.0, "evidencias": []}
  },
  "eixos_provaveis": [{"eixo": "...", "confianca": 0.0}],
  "cobertura": 0.0
}
```
`cobertura` (de 0 a 1) diz quanto das dimensões já foi coberto, considerando as 6 dimensões da seção 7 mais matérias, valores e restrições. Ela alimenta o medidor de progresso e indica quando o perfil está pronto para recomendar (padrão ≥ 0,7).

### 6.3 Recomendação (RAG)
1. Monta uma consulta a partir do perfil (interesses, atividades, valores e eixos prováveis).
2. Faz a busca híbrida e recupera os **top 15** cursos (peso 0,6 para embeddings e 0,4 para BM25, configurável). Aplica um bônus leve para cursos dos eixos prováveis.
3. Envia ao LLM o perfil e as fichas completas dos 15 cursos. O LLM escolhe 3 e devolve **JSON validado**: `curso_id`, `porque_combina`, `atencao` (um ponto de atenção honesto, ex.: "tem bastante matemática").
4. **Validação anti-alucinação:** todo `curso_id` precisa existir em `cnct.json`. Se não existir, a sugestão é descartada e o pedido é refeito uma vez.
5. Ofertas regionais vêm **só** de `ofertas_regiao.json`, nunca do LLM.
6. Diversidade: as 3 sugestões não podem ser todas do mesmo eixo, a não ser que o perfil seja muito claro (confiança do eixo > 0,8).

### 6.4 Prompts
Ficam em `app/agent/prompts/` (arquivos .md versionados, nunca strings soltas no código). O prompt do sistema deve conter:
- **Papel:** orientador vocacional do projeto Conexão Futuro, que ajuda estudantes do 2º ano a conhecer cursos técnicos.
- **Tom:** português brasileiro informal, frases curtas, sem jargão, sem gírias forçadas e sem infantilizar. Pode usar emojis com moderação (no máximo 1 por mensagem). **Uma pergunta por mensagem.**
- **Escopo:** só orientação profissional e cursos técnicos. Se o assunto for outro, redireciona com gentileza.
- **Verdade:** fatos sobre cursos só a partir das fichas fornecidas. Não promete salário, vaga ou emprego. Diz quando não sabe.
- **Postura:** sugere, não decide. Valoriza qualquer caminho (técnico, faculdade, trabalho) e não diminui as escolhas do estudante. Evita estereótipos de gênero e de classe nas sugestões.
- **Privacidade:** nunca pede nome, escola, endereço, contato ou redes sociais. Se o estudante oferecer esses dados, agradece e diz que não precisa.
- **Saúde emocional:** ver a seção 8.

---

## 7. Modelo de perfil (baseado no SouTec)

### 7.1 Como o SouTec funciona (levantado nos prints do app, out/2026)
- **Cadastro inicial:** estado e cidade, gênero, idade e escolaridade ("estou cursando o Ensino Médio"…).
- **Teste:** **72 afirmações de atividades**, uma por tela, com foto, avaliadas de 1 (interesse mínimo) a 5 (interesse máximo). Promete "cerca de 3 min".
- **Estrutura:** os itens seguem um **ciclo fixo de 6 dimensões** (A, C, E, I, R, S), com 12 itens cada. É o modelo de interesses de Holland (RIASEC). A transcrição completa e a classificação estão em **`data/soutec_itens.yaml`**.
- **Resultado:** os **2 perfis dominantes** (ex.: 1º Técnico-Operacional, 2º Empreendedor), a descrição de cada um, uma seção "O que significa essa combinação?" e o botão "Ver cursos recomendados".
- **Ofertas próximas:** lista de cursos com instituição (ex.: SENAI SC – Guaramirim), tipo (Sistema S, privada…), forma de oferta (concomitante ou subsequente), requisito ("estar cursando EM" ou "EM concluído"), distância em km e filtro por raio (até 55 km, 120 resultados).

### 7.2 O que o Conexão Futuro faz diferente
| SouTec | Conexão Futuro |
|---|---|
| 72 itens fixos e escala numérica | Conversa de 6 a 12 trocas que cobre as mesmas 6 dimensões |
| Pede gênero e idade | **Não pede nada pessoal** |
| Resultado = 2 rótulos e um texto genérico | Resultado = 3 cursos com o **porquê ligado ao que o estudante disse**, mais pontos de atenção |
| Não dá para perguntar nada | O estudante tira dúvidas sobre os cursos ("o que faz um técnico em mecatrônica?") |

### 7.3 As 6 dimensões
| Código | Nome | Resumo | Eixos do CNCT mais ligados (dica para o RAG, não é regra) |
|---|---|---|---|
| R | Técnico-Operacional | Prática, ferramentas, máquinas, tecnologia, natureza | Controle e Processos Industriais; Produção Industrial; Infraestrutura; Recursos Naturais; Informação e Comunicação (redes, suporte) |
| I | Investigativo | Pesquisar, analisar, ciência | Ambiente e Saúde (análises); Produção Industrial (química); Produção Alimentícia; Informação e Comunicação (desenvolvimento) |
| A | Artístico | Criar, se expressar, design, comunicação | Produção Cultural e Design; Informação e Comunicação (multimídia); Turismo, Hospitalidade e Lazer |
| S | Social | Ajudar, cuidar, ensinar | Ambiente e Saúde; Desenvolvimento Educacional e Social; Segurança |
| E | Empreendedor | Liderar, negociar, vender | Gestão e Negócios; Turismo, Hospitalidade e Lazer |
| C | Organizacional | Organizar dados, rotinas, números, normas | Gestão e Negócios; Informação e Comunicação (banco de dados); Segurança (normas) |

O mapeamento de dimensões para eixos fica em `data/dimensoes_eixos.yaml`, editável. Os nomes dos eixos devem ser conferidos com o `cnct.json` gerado.

### 7.4 Como entra no sistema
1. **Perfil:** o perfil estruturado (6.2) ganha o campo `dimensoes: {R,I,A,S,E,C}`, com uma nota de 0 a 1 e as **evidências** de cada uma (frases curtas do que o estudante disse). O resultado mostra as 2 dimensões dominantes, como o SouTec, mas explicadas com as palavras do estudante.
2. **Conversa:** o agente faz perguntas abertas sobre situações do dia a dia ("numa tarde livre…", "num trabalho em grupo, qual parte você pega?"). Quando uma dimensão fica sem sinal, usa **2 ou 3 itens do SouTec daquela dimensão como exemplos concretos** ("você curtiria mais montar um circuito, instalar uma rede ou consertar um carro?"). Nunca aplica os 72 em sequência.
3. **Formulário de apoio (curto):** 18 itens (3 por dimensão) escolhidos de `soutec_itens.yaml` e reescritos em linguagem jovem, com escala de 1 a 5 em emojis ou botões, além de 3 perguntas abertas (matérias favoritas, o que já faz no tempo livre, planos após o EM). Leva menos de 3 min. Fica em `data/perguntas.yaml`. A pontuação de cada dimensão é a média normalizada dos seus itens.
4. **Avaliação (fase 7):** os perfis fictícios de teste são gerados com notas por dimensão, e o critério de aceite verifica se as trilhas sugeridas pertencem aos eixos ligados às 2 dimensões dominantes.

Formato de `perguntas.yaml`:
```yaml
- id: f01
  dim: R
  origem_soutec: 5
  texto: "Montar um circuito eletrônico ou um robô"
  tipo: escala        # escala | multipla | aberta
- id: aberta_materias
  dim: null
  texto: "Quais matérias você mais curte na escola? E quais você menos curte?"
  tipo: aberta
```

---

## 8. Segurança e salvaguardas (estudantes de 15 a 17 anos)

1. **Aviso de IA** fixo na tela e na primeira mensagem.
2. **Filtro de dados pessoais na entrada** (`safety/pii.py`): regex para CPF, telefone, e-mail, endereço e nomes após "meu nome é". O dado é mascarado **antes** de ser enviado ao LLM e o estudante recebe um aviso gentil.
3. **Detecção de sofrimento emocional** (`safety/crisis.py`): palavras-chave mais a avaliação do LLM. Se o estudante mencionar tristeza intensa, desesperança ou autolesão, o agente sai do fluxo vocacional, responde com acolhimento e sugere conversar com alguém de confiança na escola, além de informar o **CVV (ligue 188, gratuito, 24h)**. Não continua a orientação até o estudante querer. Esses eventos não são gravados.
4. **Escopo restrito e moderação da saída:** recusa conteúdo impróprio e verifica se a resposta saiu do escopo antes de exibir.
5. **Rate limit por sessão** (ex.: 30 mensagens por 10 min) e limite de 500 caracteres por mensagem.
6. **Segredos** só em variáveis de ambiente. `.env` fica no `.gitignore`.
7. **HTTPS**, cabeçalhos de segurança (CSP, X-Frame-Options), CSRF nos formulários e cookie de sessão `HttpOnly`, `Secure` e `SameSite=Lax`.
8. **Painel do professor** com senha forte e hash (`argon2`). Mostra só dados agregados, nunca individuais. Turmas com menos de 5 participantes não exibem detalhamento, para evitar reidentificação.

## 9. Privacidade e conformidade
- Sem coleta de dados pessoais, nos termos da LGPD (art. 14, dados de crianças e adolescentes), e minimização de dados.
- `docs/AVISO_PRIVACIDADE.md` explica, em linguagem simples, o que é guardado, por quanto tempo, que o texto é processado por um provedor de IA (Anthropic) e que nada identifica o estudante.
- Recomendação para a oficina: termo de ciência para a escola e os responsáveis, assinado antes do Encontro 2. Isso é feito fora do sistema, mas o aviso de privacidade serve de base.
- O provedor de LLM em produção precisa ter política de **não treinar com os dados da API**.

## 10. Desempenho (turma inteira ao mesmo tempo)
- **Meta:** 35 estudantes simultâneos, com primeira palavra da resposta em menos de 3 s e sem erros visíveis.
- **Streaming** (SSE) em todas as respostas do chat.
- **Fila/semáforo** de chamadas ao LLM (concorrência configurável) e **retry com backoff exponencial** para erros 429/529. Se ainda falhar, mostra a mensagem amigável "muita gente conversando agora, tenta de novo em alguns segundos 😉".
- **Prompt caching** (Anthropic) no prompt do sistema, para reduzir custo e latência.
- O modelo de embeddings e o índice são carregados **uma vez** ao iniciar. Uso medido: ~380 MB estável, pico de ~535 MB na partida; o Cloud Run roda com 1 GB.
- **Teste de carga** com Locust: 40 usuários virtuais simulando uma conversa completa.
- **Custo:** registra os tokens consumidos por sessão e mostra o total no painel. Configura também um limite de gasto mensal no console do provedor.

## 11. Configuração (`.env.example`)
```
APP_ENV=development            # development | production
LLM_PROVIDER=anthropic         # anthropic | openai_compat | ollama | gemini (gemini bloqueado em production)
LLM_MODEL=claude-haiku-5-5
ANTHROPIC_API_KEY=
OPENAI_COMPAT_BASE_URL=
OPENAI_COMPAT_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
GEMINI_API_KEY=
LLM_MAX_CONCURRENCY=8
DATABASE_URL=sqlite:///./dev.db
SECRET_KEY=
PROFESSOR_PASSWORD_HASH=
RETENCAO_DIAS=60
COBERTURA_MINIMA=0.7
LOG_CONVERSAS=false
```

## 12. Plano de desenvolvimento (fases para o Claude Code)

Cada fase termina com testes passando e um commit. **Não avançar sem cumprir os critérios de aceite.**

| Fase | Entrega | Critérios de aceite |
|---|---|---|
| **0 — Base** | Estrutura de pastas, `pyproject`, config, `/health`, camada `llm/` com adaptadores `anthropic` e `ollama`, mais um adaptador falso (`fake`) para testes | `pytest` verde. Trocar de provedor muda só o `.env`. O provedor `gemini` falha ao iniciar com `APP_ENV=production`. |
| **1 — Catálogo** | `ingest_cnct.py`, `build_index.py`, `rag/retriever.py` | `cnct.json` validado e com relatório de qualidade. Para 10 consultas de teste ("gosto de computador", "quero cuidar de pessoas"…), o curso esperado aparece no top 15. |
| **2 — Agente (sem interface)** | Orquestrador, extração de perfil, recomendação, prompts, `safety/` | Conversa completa via CLI ou teste. 100% dos `curso_id` recomendados existem no catálogo. CPF e telefone digitados são mascarados. Uma mensagem de crise dispara o fluxo de acolhimento. |
| **3 — Interface de chat** | Telas de início, chat (streaming e chips) e resultado, com exportação .txt/.pdf | Funciona num celular de 360 px. A exportação abre corretamente no NotebookLM. Lighthouse de acessibilidade ≥ 90. |
| **4 — Formulário e código** | Formulário de apoio, geração e recuperação de código, Postgres/SQLite, Alembic, limpeza por retenção | O código recupera as respostas em outro dispositivo. O conteúdo do chat não é gravado. A força bruta é bloqueada. |
| **5 — Painel do professor** | Login, turmas, gráficos agregados, link do Padlet por turma | Nenhuma tela mostra dados individuais. Turmas com menos de 5 participantes não exibem detalhamento. |
| **6 — Deploy** | `Dockerfile`, Cloud Run, Postgres no Neon, `docs/DEPLOY.md`, teste de carga (Locust) | Site no ar com HTTPS. Teste de carga com 40 usuários sem erro visível. |
| **7 — Avaliação de qualidade** | `eval_recomendacoes.py` com 20 perfis fictícios e o resultado esperado | ≥ 80% dos perfis recebem ao menos 1 trilha do eixo esperado. Revisão manual do tom em 5 conversas. |
| **8 — Piloto** | Teste com 3 a 5 adultos (colegas e orientador) antes da escola | Ajustes de linguagem incorporados. Checklist da oficina pronto. |

## 13. Fora do escopo da v1
- Login com conta (Google ou gov.br).
- Voz (fala e escuta).
- Integração automática com o Padlet ou o NotebookLM (na v1 é só link e exportação).
- Ofertas regionais buscadas automaticamente da internet.

## 14. Checklist do dia da oficina
- [ ] Abrir o site 5 min antes, para acordar o servidor.
- [ ] Criar a turma no painel e anotar o código no quadro.
- [ ] Testar o acesso no Wi-Fi da escola (alguns firewalls bloqueiam sites novos).
- [ ] Conferir o saldo e o limite de gasto da API.
- [ ] Ter o formulário impresso para quem preferir papel.
