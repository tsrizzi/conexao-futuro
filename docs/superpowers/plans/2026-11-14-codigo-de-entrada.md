# Código de entrada no site — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O site só é usado com um código de entrada válido, gerado pelo autor numa área `/admin` com início e término.

**Architecture:** Tabela nova `codigo_entrada` (só HMAC do código). `POST /api/entrada` troca o código por um cookie assinado de liberação; uma função única confere cookie + código ainda válido e protege páginas e APIs que gravam ou gastam. `/admin` reaproveita o padrão do painel do professor (argon2, cookie assinado, CSRF, formulários sem JS).

**Tech Stack:** FastAPI, Jinja2, SQLAlchemy 2 + Alembic, argon2-cffi (já instalado), JS puro.

**Spec:** `docs/superpowers/specs/2026-11-14-acesso-controlado-design.md`

## Global Constraints

- Nenhum dado pessoal; o código fica no banco só como HMAC (`hash_do_codigo`) + os 4 últimos caracteres.
- Formato `XXXX-XXXX`, alfabeto sem ambiguidade (o `_ALFABETO_TURMA` de `app/db/codigo.py`: sem 0/O, 1/I/L, 5/S), sem diferença de maiúsculas.
- Cookies: `HttpOnly`, `SameSite=Lax`, `Secure` só com `APP_ENV=production` (mesmo padrão de `app/routes/professor.py`).
- Login do admin: 5 tentativas por IP a cada 10 min. Código de entrada: 10 **erros** por IP a cada 10 min (acertos não contam).
- Liberação dura até o fim do código ou 12 h, o que vier antes; revogar/terminar corta na próxima chamada.
- Datas digitadas no admin são do horário de Brasília (UTC−3 fixo, sem horário de verão; não usar `zoneinfo`, que falha sem `tzdata` no Windows e na imagem slim). Gravadas em UTC.
- Textos ao usuário em português do Brasil; nada de estilo/script inline (CSP); sem dependências novas.
- Abertos sem código: `/privacidade`, `/health`, `/static`, `/professor*`, `/admin*`, `/api/turma/*`, `GET /api/formulario/perguntas`.

## Review Focus

1. Código digitado em minúsculas, com espaço ou sem hífen (`k7mq 3xpt`, `K7MQ3XPT`) deve entrar → teste em Task 1 (`normalizar_codigo_entrada`) e Task 2.
2. Código agendado, encerrado ou revogado deve dar mensagens diferentes e claras, não "código inválido" genérico → Task 2.
3. Código revogado com conversa em andamento: a próxima mensagem é recusada e o navegador volta para a porta com aviso → Task 2 (API) e Task 3 (JS).
4. Cookie de liberação adulterado, de outra chave ou apontando para código apagado → recusado → Task 2.
5. Admin cria código com término antes do início, ou datas vazias/inválidas → erro no formulário, nada gravado → Task 4.

---

### Task 1: Código, tabela e repositório

**Files:**
- Modify: `app/db/codigo.py` (funções novas no fim)
- Modify: `app/db/models.py` (classe `CodigoEntrada`)
- Create: `app/db/migrations/versions/0003_codigo_entrada.py` (revision `"0003"`, down `"0002"`)
- Modify: `app/db/repositorio.py` (seção nova "Código de entrada" + retenção)
- Test: `tests/test_entrada.py`

**Interfaces:**
- Produces (`app/db/codigo.py`): `gerar_codigo_entrada() -> str` ("XXXX-XXXX"); `normalizar_codigo_entrada(texto: str) -> str | None` (tira espaços/hífens/pontos, maiúsculas, exige 8 caracteres do alfabeto, devolve "XXXX-XXXX"). O hash usa `hash_do_codigo("entrada:" + codigo, chave)` (domínio separado do código do estudante).
- Produces (`models.CodigoEntrada`, tabela `codigo_entrada`): `id: int` PK autoincrement; `codigo_hash: str(64)` unique index; `final: str(4)`; `rotulo: str(80) | None`; `inicio_em`, `fim_em: DateTime(tz)` (fim indexado); `revogado: bool = False`; `usos: int = 0`; `criado_em`.
- Produces (`app/db/repositorio.py`):
  - `criar_codigo_entrada(db, chave: bytes, rotulo: str | None, inicio: datetime, fim: datetime) -> tuple[CodigoEntrada, str]` (repete se colidir, como `criar_participante`)
  - `buscar_codigo_entrada(db, texto: str, chave: bytes) -> CodigoEntrada | None` (qualquer situação; `None` se formato inválido ou não existe)
  - `situacao_codigo(c: CodigoEntrada, momento: datetime | None = None) -> Literal["agendado","ativo","encerrado","revogado"]` (revogado vence; usa `_como_utc`)
  - `listar_codigos_entrada(db) -> list[CodigoEntrada]` (mais novos primeiro)
  - `revogar_codigo_entrada(db, codigo_id: int) -> bool`
  - `limpar_expirados` passa a apagar também `CodigoEntrada` com `fim_em < momento - retencao_dias`.

- [ ] **Step 1: Write the failing tests** em `tests/test_entrada.py`:
  - `test_codigo_entrada_formato`: 200 gerações batem `^[A-Z2-9]{4}-[A-Z2-9]{4}$` e nenhum caractere em `"0O1IL5S"`.
  - `test_normalizar_codigo_entrada` (parametrize): `"k7mq-3xpt"`, `" K7MQ 3XPT "`, `"k7mq3xpt"` → `"K7MQ-3XPT"`; `""`, `"K7MQ-3XP"`, `"K7MQ-3XP0"`, `"K7MQ-3XPTA"` → `None`.
  - `test_banco_guarda_so_hash_e_final`: criar com `CHAVE=b"x"*32`; `codigo not in c.codigo_hash`, `c.final == codigo[-4:]`; `buscar_codigo_entrada(db, codigo.lower(), CHAVE) is c`; com `b"y"*32` → `None`.
  - `test_situacao_codigo`: com `agora()` fixo: início +1 h → `"agendado"`; período em volta de agora → `"ativo"`; fim −1 min → `"encerrado"`; ativo com `revogado=True` → `"revogado"`.
  - `test_retencao_apaga_codigo_antigo`: código com fim há 61 dias some em `limpar_expirados(db, 60)`; com fim há 10 dias fica.

- [ ] **Step 2:** `uv run pytest tests/test_entrada.py -q` → FAIL (ImportError).
- [ ] **Step 3:** Implementar as funções, o modelo e a migração (gerar com `uv run python -m alembic revision --autogenerate -m "codigo de entrada"` contra um SQLite temporário e renomear para `0003_codigo_entrada.py`, ou escrever à mão no estilo de `0002`).
- [ ] **Step 4:** `uv run pytest tests/test_entrada.py tests/test_fase4.py -q` → PASS (inclui `test_migracoes_criam_o_mesmo_esquema_dos_modelos`).
- [ ] **Step 5:** Commit `"Código de entrada: formato, tabela e repositório"`.

### Task 2: A porta no servidor (liberação + APIs protegidas)

**Files:**
- Create: `app/web/entrada.py`
- Create: `app/routes/entrada_api.py`
- Modify: `app/routes/chat_api.py`, `app/routes/acesso_api.py`, `app/main.py` (router + `app.state.limite_entrada = LimitadorTaxa(maximo=10, janela_s=600)`)
- Modify: `tests/conftest.py`
- Test: `tests/test_entrada.py`

**Interfaces:**
- Consumes: Task 1 (`buscar_codigo_entrada`, `situacao_codigo`, `CodigoEntrada`).
- Produces (`app/web/entrada.py`):
  - `COOKIE_ENTRADA = "cf_entrada"`; `VALIDADE_MAX_S = 12 * 3600`
  - `criar_cookie_entrada(chave: bytes, codigo_id: int, fim: datetime, agora: float | None = None) -> tuple[str, int]` → (valor `"{id}.{exp}.{hmac}"`, max_age em s); `exp = min(fim, agora + 12 h)`; assinatura `HMAC(chave, "entrada|{id}|{exp}")`.
  - `ler_cookie_entrada(valor: str | None, chave: bytes, agora: float | None = None) -> int | None` (id do código, ou `None` se ausente/adulterado/expirado).
  - `codigo_liberado(request: Request) -> CodigoEntrada | None` (lê o cookie e confere no banco que o código existe e está `"ativo"`).
  - `exigir_entrada(request: Request) -> None`: sem liberação → `HTTPException(403, detail=MENSAGEM_PRECISA_CODIGO, headers={"X-Entrada": "necessaria"})`. Mensagens (constantes no módulo): `MENSAGEM_PRECISA_CODIGO = "Para usar o Conexão Futuro, digite o código de entrada."`, `MENSAGEM_AGENDADO = "Este código ainda não começou a valer. Confira a data com quem te passou."`, `MENSAGEM_ENCERRADO = "O período deste código terminou."`, `MENSAGEM_REVOGADO = "Este código foi desativado."`, `MENSAGEM_INVALIDO = "Código não encontrado. Confira se digitou certinho."`.
- Produces (`app/routes/entrada_api.py`): `POST /api/entrada` com `{"codigo": str (max 20)}` → 200 `{"ok": true, "fim_em": iso}` + `Set-Cookie`; situação ≠ ativo → 403 com a mensagem da situação; não existe/formato inválido → 404 `MENSAGEM_INVALIDO`. Só não-encontrado conta no `limite_entrada` (bloqueado → 429 com `MENSAGEM_TENTATIVAS` de `acesso_api`). Acerto faz `usos += 1`. `POST /api/saida` apaga o cookie.
- Protegidas com `exigir_entrada` (chamada no início, antes de qualquer gravação/LLM): `POST /api/chat/iniciar`, `POST /api/chat/mensagem`, `POST /api/resultado`, `POST /api/resultado.txt`, `POST /api/formulario`, `POST /api/codigo`.
- `tests/conftest.py`: `criar_cliente(..., liberado: bool = True)`; com `liberado`, cria um código ativo (agora−1 h até agora+1 dia) no banco do cliente e põe o cookie com `client.cookies.set(COOKIE_ENTRADA, valor)`. Os testes existentes não mudam.

- [ ] **Step 1: Write the failing tests** em `tests/test_entrada.py` (cliente com `liberado=False` salvo indicação):
  - `test_apis_fechadas_sem_codigo` (parametrize nas 6 rotas, corpo mínimo válido): status 403, `headers["x-entrada"] == "necessaria"`, e o `FakeProvider` não registrou chamada (`llm.chamadas == []`).
  - `test_entrar_com_codigo_libera` : `POST /api/entrada {"codigo": codigo.lower().replace("-", " ")}` → 200 e cookie `cf_entrada` com `HttpOnly`; depois `POST /api/chat/iniciar` → 200; `usos == 1` no banco.
  - `test_mensagens_por_situacao` (parametrize agendado/encerrado/revogado → 403 com a mensagem certa; inexistente → 404 `MENSAGEM_INVALIDO`).
  - `test_revogar_corta_conversa_em_andamento`: entra, inicia (pega token), revoga no banco, `POST /api/chat/mensagem` → 403 com `x-entrada`.
  - `test_cookie_adulterado_ou_de_outra_chave`: `ler_cookie_entrada` recusa assinatura trocada, `exp` alterado, chave `b"y"*32`, expirado (`agora` > exp); cookie válido de código apagado do banco → `/api/chat/iniciar` 403.
  - `test_forca_bruta_so_conta_erros`: 30 entradas com código certo seguidas → todas 200; 10 inexistentes → 404; a 11ª (mesmo certa) → 429.
  - `test_liberacao_dura_no_maximo_12h`: `criar_cookie_entrada` com fim em 30 dias → max_age `<= 12*3600`; com fim em 1 h → max_age `<= 3600`.
- [ ] **Step 2:** `uv run pytest tests/test_entrada.py -q` → novos testes FAIL.
- [ ] **Step 3:** Implementar `app/web/entrada.py`, `entrada_api.py`, chamadas de `exigir_entrada`, router e limitador em `main.py`, `conftest.py`.
- [ ] **Step 4:** `uv run pytest -q` → todos PASS (os 253 antigos continuam passando graças a `liberado=True`).
- [ ] **Step 5:** Commit `"Porta de entrada: liberação por código e APIs protegidas"`.

### Task 3: A porta na interface

**Files:**
- Create: `app/templates/entrada.html`, `app/static/js/entrada.js`
- Modify: `app/routes/paginas.py`, `app/static/js/comum.js`, `app/static/css/app.css`
- Modify: `scripts/verificar_interface.py` (o app de demonstração cria um código e a passada entra por ele antes do fluxo atual; acrescenta a captura `"{tema}-0-entrada"` com `checar`)
- Test: `tests/test_entrada.py`

**Interfaces:**
- Consumes: Task 2 (`codigo_liberado`, `POST /api/entrada`, cabeçalho `X-Entrada`).
- `GET /`: sem liberação → `entrada.html` (título "Conexão Futuro", texto "Digite o código para usar", campo `#codigo-entrada` com `autocomplete="off" autocapitalize="characters"`, botão "Entrar", parágrafo de erro `role="alert"`, rodapé padrão com aviso de IA e privacidade). Com liberação → `inicio.html` de hoje.
- `GET /chat|/formulario|/codigo|/resultado` sem liberação → `RedirectResponse("/", 303)`.
- `entrada.js`: envia `POST /api/entrada`; 200 → `location.href = "/"`; erro → mostra `detail`. Se a URL tem `?saiu=1`, mostra "Seu acesso terminou. Digite um código válido para continuar."
- `comum.js`: em `postarSSE` e `postarJSON`, resposta com cabeçalho `X-Entrada: necessaria` → `sessao.limpar()` e `location.href = "/?saiu=1"` (não lança erro para a tela).

- [ ] **Step 1: Write the failing tests:**
  - `test_pagina_inicial_vira_porta`: sem liberação, `GET /` contém `"Digite o código para usar"`, `id="codigo-entrada"` e `"inteligência artificial"`, e **não** contém `"Começar conversa"`; com liberação contém `"Começar conversa"`.
  - `test_paginas_redirecionam_sem_codigo` (parametrize `/chat`, `/formulario`, `/codigo`, `/resultado`): `follow_redirects=False` → 303 para `/`.
  - `test_paginas_abertas_sem_codigo` (parametrize `/privacidade`, `/health`, `/professor`, `/admin`): 200.
  - `test_js_trata_saida`: `/static/js/comum.js` contém `"X-Entrada"` e `"/?saiu=1"`.
- [ ] **Step 2:** `uv run pytest tests/test_entrada.py -q` → FAIL.
- [ ] **Step 3:** Implementar templates, rotas, JS e CSS (estilo da tela de login do professor).
- [ ] **Step 4:** `uv run pytest -q` → PASS; `uv run python scripts/verificar_interface.py` → código de saída 0 (sem rolagem horizontal, axe sem violações, incluindo `light/entrada` e `dark/entrada`).
- [ ] **Step 5:** Commit `"Porta de entrada: tela inicial pede o código"`.

### Task 4: Área do admin

**Files:**
- Modify: `app/config.py` (`admin_usuario: str | None = None`, `admin_password_hash: SecretStr | None = None`), `.env.example`
- Modify: `app/web/professor_sessao.py`: `criar_cookie`/`ler_cookie` ganham `escopo: str = "prof"` (entra na assinatura, para um cookie de professor nunca valer como admin)
- Create: `app/routes/admin.py`, `app/templates/admin_login.html`, `app/templates/admin_codigos.html`
- Modify: `app/main.py` (router + `app.state.limite_admin = LimitadorTaxa(maximo=5, janela_s=600)`), `app/static/css/app.css`
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: Task 1 (repositório), `professor_sessao` (hash, cookie, CSRF), helpers de formulário de `app/routes/professor.py` (`_formulario`, `_pagina`, `_ir`, `_cookie_seguro` — mover para um módulo comum `app/web/formularios.py` se precisar importar de dois lugares).
- Rotas: `GET /admin` (login ou painel), `POST /admin/entrar` (`usuario`, `senha`, `csrf` double-submit como o professor; usuário comparado com `hmac.compare_digest`), `POST /admin/sair`, `POST /admin/codigos` (`rotulo`, `inicio`, `fim`, `csrf`), `POST /admin/codigos/{id}/revogar`. Cookie `cf_admin`, `path="/admin"`, 8 h, escopo `"admin"`.
- Sem `ADMIN_USUARIO` ou `ADMIN_PASSWORD_HASH`: tela de login desabilitada explicando como configurar (como o painel do professor sem senha).
- Datas: campos `datetime-local` (padrão: agora e agora+30 dias, horário de Brasília); `BRT = timezone(timedelta(hours=-3))`; `fim <= inicio` ou vazio → 400 com "O término precisa ser depois do início."; rótulo até 80 caracteres.
- Depois de criar: a página mostra o código inteiro **uma vez** num destaque com botão Copiar (JS em `app/static/js/admin.js`, `navigator.clipboard`) e o aviso "Anote agora: ele não aparece de novo." A lista mostra rótulo, `•••• ` + final, início/fim em horário de Brasília, situação e usos, com botão Revogar (só se agendado/ativo).
- Aviso fixo perto do rótulo: "Não coloque nome de pessoa: use turma, escola ou finalidade."

- [ ] **Step 1: Write the failing tests** em `tests/test_admin.py` (hash de `"senha-forte-do-admin"`, usuário `"autor"`):
  - `test_admin_pede_login` (sem cookie mostra `type="password"` e `name="usuario"`, `no-store`, `noindex`).
  - `test_login_admin_errado_e_forca_bruta` (usuário errado → 401; senha errada → 401; 6ª tentativa → 429).
  - `test_admin_fechado_sem_configuracao` (sem as variáveis: mensagem com `ADMIN_PASSWORD_HASH`, botão desabilitado).
  - `test_cookie_professor_nao_vale_como_admin` (cookie criado com escopo `"prof"` e o mesmo hash não é aceito por `ler_cookie(..., escopo="admin")`).
  - `test_cria_codigo_mostra_uma_vez_e_funciona_na_porta`: cria com rótulo "Piloto"; a resposta contém um código `XXXX-XXXX`; um `GET /admin` seguinte **não** contém o código inteiro, contém o final; outro cliente sem liberação entra com ele em `/api/entrada` → 200.
  - `test_datas_invalidas` (fim antes do início → 400 com a mensagem; nada gravado).
  - `test_revogar` (revoga; situação "revogado" na lista; `/api/entrada` com ele → 403 `MENSAGEM_REVOGADO`).
  - `test_post_admin_exige_csrf` (sem/errado → 403, nada gravado).
- [ ] **Step 2:** `uv run pytest tests/test_admin.py -q` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q`, `uv run ruff check .`, `uv run mypy app` → tudo limpo.
- [ ] **Step 5:** Commit `"Admin: login e geração de códigos de entrada com período"`.

### Task 5: Documentação e deploy

**Files:**
- Modify: `docs/DEPLOY.md` (secrets `ADMIN_USUARIO` e `ADMIN_PASSWORD_HASH`, comandos de uma linha para o Cloud Shell, como entrar em `/admin` e gerar o primeiro código), `README.md` (seção curta "Código de entrada e admin"), `docs/ESPECIFICACAO.md` (nota na seção 4.4 e na lista de telas: porta de entrada com código; decisão de 14/11/2026), `docs/AVISO_PRIVACIDADE.md` (o código de entrada não identifica ninguém; guarda-se só a contagem de usos).

- [ ] **Step 1:** Atualizar os quatro documentos.
- [ ] **Step 2:** `uv run pytest -q` e `uv run python scripts/verificar_interface.py` → verdes.
- [ ] **Step 3:** Commit `"Docs: código de entrada e área do admin"` e `git push`.
