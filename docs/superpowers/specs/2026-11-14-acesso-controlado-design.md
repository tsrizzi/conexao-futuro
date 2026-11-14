# Código de entrada no site

Data: 14/11/2026. Status: aprovado pelo autor em 14/11/2026.

## Por quê

O Conexão Futuro é um TCC e o autor paga a API do LLM do próprio bolso. O site não pode
ficar aberto: só quem recebeu um código liberado pelo autor, dentro de um período, usa o
formulário e o chat. É uma porta de entrada: troca "login e senha" por um código.

## Objetivos

- Primeira tela: "Conexão Futuro — digite o código para usar". Sem código válido, não há
  formulário nem conversa (e nenhuma chamada ao LLM).
- Área de administração (`/admin`), só do autor, com usuário e senha, para gerar códigos com
  **data de início e de término**, ver a lista e revogar.
- O mesmo tipo de código serve para professor e para estudante. Um código pode ser usado por
  várias pessoas (ex.: um para a turma inteira, outro para os colegas do piloto).

## Não objetivos

- Hierarquia admin → professor → turma, códigos por professor, mudanças no painel do
  professor (continua com a senha dele) ou no código da turma (continua opcional).
- Códigos individuais por pessoa, contas, e-mail, segundo fator (pode vir depois).

## Anti-objetivos

- Guardar dado pessoal de quem usa o código.
- Alguém chamar o LLM sem código válido no momento.

## Abordagens consideradas

- **Porta única com código compartilhável (escolhida).** Simples de distribuir e de explicar.
- **Hierarquia admin → professor → turma com código da turma obrigatório.** Proposta antes e
  descartada pelo autor: mais do que o necessário agora.
- **Código individual por estudante.** Descartada (muitos códigos para distribuir).

## Como funciona

### Admin (`/admin`)
- Login com `ADMIN_USUARIO` + `ADMIN_PASSWORD_HASH` (argon2, mesmo script do professor),
  via variáveis/secrets. 5 tentativas por IP a cada 10 min, cookie assinado `HttpOnly`,
  `SameSite=Lax`, `Secure` em produção, CSRF nos formulários (mesmo padrão do painel).
- Criar código: rótulo opcional (ex.: "Turma 2B — oficina", "Piloto com colegas"; a tela avisa
  para não pôr nome de pessoa), início e término (padrão: agora até daqui a 30 dias).
- Lista com situação (agendado, ativo, encerrado, revogado) e quantas vezes foi usado para
  entrar (contagem, sem guardar quem). Botão **revogar**.
- O código aparece por inteiro **só na criação** (com botão de copiar). No banco fica o HMAC
  e os 4 últimos caracteres, para o autor reconhecer na lista.

### Código de entrada
- Formato `XXXX-XXXX` com letras e números sem ambiguidade (sem 0/O, 1/I/L, 5/S). Sem
  diferença de maiúsculas e minúsculas.
- Tabela nova `codigo_entrada`: id, codigo_hash, final (4 caracteres), rotulo, inicio_em,
  fim_em, revogado, usos, criado_em. Migração Alembic nova. Apagado pela retenção (60 dias
  depois do fim).

### A porta
- Página inicial: sem liberação, mostra só o campo do código (o aviso de IA e o link de
  privacidade continuam no rodapé). Com liberação, a página inicial de hoje.
- Código certo → o servidor grava um cookie assinado de liberação (id do código + validade),
  `HttpOnly`, `SameSite=Lax`, `Secure` em produção, que dura até o fim do código ou 12 h, o
  que vier antes.
- `/chat`, `/formulario`, `/codigo` e `/resultado` sem liberação voltam para a página inicial.
- O servidor confere a liberação, e se o código ainda vale (não revogado, dentro do período),
  em toda chamada que grava ou gasta: iniciar conversa, mensagem, formulário, voltar com
  código de acesso, resultado. Revogar ou terminar o período corta na próxima mensagem.
- Força bruta: 50 códigos errados por IP a cada 10 min. Só contam palpites (código bem formado
  que não existe); erro de digitação não conta. Ajustado na revisão final: com 10, uma sala
  inteira atrás do mesmo IP se trancava para fora em segundos. Com 29^8 combinações, 50 segue
  inviável para adivinhar.
- Ficam abertos: aviso de privacidade, `/health`, painel do professor (tem a própria senha) e
  `/admin` (tem login).

## Restrições

- Nenhum dado pessoal; códigos só como HMAC; sem dependências novas; CSP atual.
- Testes existentes que usam o site aberto passam a entrar com um código de teste.

## Deixado para quem implementar

- Layout das telas (seguir o estilo do painel do professor), textos exatos em português do
  Brasil no tom do projeto.
