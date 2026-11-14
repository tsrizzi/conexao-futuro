# Deploy: Google Cloud Run + Neon

Passo a passo pelo console, sem instalar nada além do que o projeto já usa. Cada push no
`main` do GitHub gera uma nova versão do site sozinho.

**Por que Cloud Run e não Render:** o app usa ~380 MB parado e tem pico de ~535 MB ao carregar
o modelo de embeddings. O Render gratuito (e o pago de US$ 7) tem 512 MB: o app não sobe. O
Cloud Run deixa escolher 1 GB e só cobra o tempo atendendo requisições.

> **Custos para conferir antes** (não deu para confirmar os números atuais na documentação):
> a camada gratuita do Cloud Run (vCPU-segundos, GiB-segundos e requisições por mês), se a
> região `southamerica-east1` entra nela, e o armazenamento grátis do Artifact Registry (a
> imagem tem ~1 GB). Em qualquer caso, o **alerta de orçamento** do passo 3 avisa antes de
> virar gasto.

---

## 1. Anthropic (o LLM de produção)

O Gemini é **proibido** em produção (os termos não permitem menores de 18; o app se recusa a
iniciar). Use a Anthropic.

1. Em [platform.claude.com](https://platform.claude.com), compre crédito (a conta precisa de
   saldo) e crie uma chave de API.
2. Em **Settings → Spend limits**, defina um limite mensal baixo (ex.: US$ 10).
3. Confira o tier em **Usage → Limits**. No tier Start, o Haiku 4.5 aceita 1.000 requisições
   por minuto: sobra para uma turma. Contas novas podem começar num tier de avaliação com
   limites menores; faça um teste com a conta uns dias antes da oficina.

Custo medido: ~US$ 0,07 por conversa completa no Haiku 4.5 (~US$ 2,50 por turma de 35).

## 2. Banco: Neon (Postgres gratuito)

1. Crie uma conta em [neon.tech](https://neon.tech) e um projeto na região **AWS São Paulo
   (sa-east-1)**.
2. Em **Connect**, copie a connection string **direta** (desmarque "Connection pooling"; o
   host não pode ter `-pooler`). Ela começa com `postgresql://` e termina com
   `?sslmode=require`. O app aceita esse formato como está.
3. Não precisa criar tabelas: o app aplica as migrações (Alembic) sozinho ao iniciar.

O Neon gratuito desliga o banco depois de alguns minutos parado e religa na primeira consulta
(~1 s). O app já reconecta sozinho.

## 3. Google Cloud: projeto e proteção de gasto

1. Em [console.cloud.google.com](https://console.cloud.google.com), crie um projeto (ex.:
   `conexao-futuro`) e vincule a conta de faturamento.
2. **Faturamento → Orçamentos e alertas → Criar orçamento**: R$ 10/mês, com alertas em 50%,
   90% e 100%.
3. Ative as APIs: **Cloud Run**, **Cloud Build**, **Artifact Registry** e **Secret Manager**
   (o console oferece ativar cada uma na primeira vez que você abre).

## 4. Segredos (Secret Manager)

Gere os dois valores locais:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
uv run python scripts/gerar_hash_professor.py                          # hash da senha do painel
```

Em **Secret Manager → Criar secret**, crie um secret para cada linha (cole só o valor, sem
aspas):

| Secret | Valor |
|---|---|
| `anthropic-api-key` | a chave do passo 1 |
| `secret-key` | o `SECRET_KEY` gerado acima |
| `professor-password-hash` | o hash (o texto que começa com `$argon2id$`) |
| `database-url` | a connection string do Neon |

**Atenção:** a `SECRET_KEY` não pode mudar depois que os estudantes tiverem códigos. Ela
protege os códigos de acesso no banco; se mudar, os códigos antigos deixam de funcionar.

## 5. Cloud Run: o serviço

**Cloud Run → Implantar contêiner → Serviço → "Implantar continuamente de um repositório"**:

1. **Configurar o Cloud Build:** conecte o GitHub, escolha `tsrizzi/conexao-futuro`, branch
   `^main$`, tipo de build **Dockerfile** (caminho `/Dockerfile`).
2. **Nome:** `conexao-futuro`. **Região:** `southamerica-east1` (São Paulo).
3. **Autenticação:** permitir acesso público (os estudantes entram sem login).
4. **Faturamento:** baseado em solicitações (CPU só durante as requisições).
5. **Escalonamento:** mínimo **0**, máximo **2** instâncias (o máximo também segura o gasto).
6. **Contêiner** (aba "Contêineres, volumes, rede, segurança"):
   - Porta: **8080**
   - Memória: **1 GiB**. CPU: **1**. Marque **Aumento de CPU na inicialização**.
   - Tempo limite da solicitação: **300** s. Simultaneidade máxima: **80**.
   - **Variáveis de ambiente:**

     | Nome | Valor |
     |---|---|
     | `LLM_PROVIDER` | `anthropic` |
     | `LLM_MODEL` | `claude-haiku-4-5` |
     | `LLM_MAX_CONCURRENCY` | `20` |

     (`APP_ENV=production` e `PROXY_SALTOS=1` já vêm na imagem.)
   - **Secrets como variáveis de ambiente** (versão `latest`):

     | Variável | Secret |
     |---|---|
     | `ANTHROPIC_API_KEY` | `anthropic-api-key` |
     | `SECRET_KEY` | `secret-key` |
     | `PROFESSOR_PASSWORD_HASH` | `professor-password-hash` |
     | `DATABASE_URL` | `database-url` |

     Se o console avisar que a conta de serviço não tem acesso, aceite conceder o papel
     **Acessador de secrets do Secret Manager**.
7. **Criar.** O primeiro build leva uns 5 minutos (baixa dependências e o modelo).

### Conferir

- `https://SEU-SERVICO.run.app/health` responde `"app_env":"production"` e
  `"llm_provider":"anthropic"`.
- Abra o site, converse até as trilhas e entre em `/professor` com a senha.
- Se não subir: **Cloud Run → serviço → Registros**. Os erros comuns têm mensagem clara
  (`SECRET_KEY` curta, Gemini em produção, banco inacessível).

## 6. Teste de carga (sem gastar tokens)

Uma revisão de teste, com o LLM falso, que não recebe o tráfego dos estudantes:

1. **Editar e implantar nova revisão**: troque `LLM_PROVIDER` para `fake` e acrescente
   `LLM_FAKE_ATRASO_S=0.1` (imita a demora de um LLM real). **Desmarque** "Disponibilizar
   esta revisão imediatamente" e dê a tag `carga`. Ela ganha uma URL própria
   (`https://carga---SEU-SERVICO.run.app`).
2. Rode na sua máquina (40 estudantes, 5 minutos):

   ```bash
   uv run locust -f scripts/carga/locustfile.py --host https://carga---SEU-SERVICO.run.app \
       --users 40 --spawn-rate 4 --run-time 5m --headless
   ```

   Critério: **0 falhas** e "mensagem (1ª palavra)" perto de 2 s (o atraso imitado).
3. Apague a tag `carga` em **Revisões** depois do teste.

Resultado local (container com 1 GB e 1 vCPU, nov/2026): 0 falhas, primeira palavra em
2,12 s com atraso imitado de 2,1 s, memória estável em ~380 MB.

## 7. No dia da oficina

- Abra o site uns 5 minutos antes: sem uso, a instância desliga e leva ~10 s para acordar.
- Se quiser zero espera durante a aula, ponha **mínimo de instâncias = 1** só naquele
  período (cobra a instância parada) e volte para 0 depois.
- Confira o saldo da Anthropic e o alerta de orçamento do Google.
