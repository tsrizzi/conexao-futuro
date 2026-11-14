"""Teste de carga (seção 10): uma turma inteira conversando ao mesmo tempo.

Cada usuário virtual faz o que um estudante faz: abre o site, começa a conversa, manda
algumas mensagens no ritmo de quem digita no celular e abre o resultado. Mede o tempo até a
1ª palavra (meta: < 3 s) e conta como falha qualquer erro que o estudante veria.

Sem custo: rode contra uma revisão com LLM_PROVIDER=fake e LLM_FAKE_ATRASO_S=0.1, que imita a
demora de um LLM real. Com o LLM de verdade cada conversa custa tokens (veja docs/DEPLOY.md).

Uso (40 usuários, entrando 4 por segundo, por 5 minutos):
    uv run locust -f scripts/carga/locustfile.py --host https://SEU-APP.run.app \\
        --users 40 --spawn-rate 4 --run-time 5m --headless
Variáveis opcionais: CARGA_PAUSA_MIN / CARGA_PAUSA_MAX (segundos entre mensagens),
CARGA_TURMA (código da turma, para alimentar o painel de teste).
"""

import json
import os
import random
import time
from typing import Any

from locust import HttpUser, between, task

PAUSA = (float(os.environ.get("CARGA_PAUSA_MIN", 5)), float(os.environ.get("CARGA_PAUSA_MAX", 15)))
TURMA = os.environ.get("CARGA_TURMA") or None
FALAS = [
    "gosto de jogar videogame e de desenhar personagens",
    "na escola eu curto matemática e artes",
    "já montei um computador com meu primo",
    "prefiro trabalhar em equipe, mas com tempo pra pensar sozinho",
    "queria algo que desse pra fazer perto de casa",
    "já quero ver as sugestões",
]


class Estudante(HttpUser):
    wait_time = between(*PAUSA)

    def on_start(self) -> None:
        self.token: str | None = None

    def _conversar(self, nome: str, url: str, corpo: dict[str, Any]) -> None:
        """POST com resposta em streaming (SSE). Registra a 1ª palavra e a resposta inteira."""
        inicio = time.perf_counter()
        primeira: float | None = None
        tipo = ""
        erro = None
        with self.client.post(
            url, json=corpo, stream=True, name=nome, catch_response=True, timeout=60
        ) as resposta:
            if resposta.status_code != 200:
                resposta.failure(f"HTTP {resposta.status_code}")
                return
            for linha in resposta.iter_lines(decode_unicode=True):
                if linha.startswith("event: "):
                    tipo = linha[7:]
                elif linha.startswith("data: "):
                    dados = json.loads(linha[6:])
                    if tipo == "texto" and primeira is None:
                        primeira = time.perf_counter() - inicio
                    elif tipo == "erro":
                        erro = dados.get("texto", "erro")
                    elif tipo == "sessao":
                        self.token = dados["token"]
            if erro:
                resposta.failure(f"erro visível: {erro}")
            elif self.token is None:
                resposta.failure("resposta sem sessão")
            else:
                resposta.success()
        if primeira is not None:
            self.environment.events.request.fire(
                request_type="SSE",
                name=f"{nome} (1ª palavra)",
                response_time=primeira * 1000,
                response_length=0,
                exception=None,
                context={},
            )

    @task
    def conversa_completa(self) -> None:
        self.client.get("/", name="página inicial")
        self.client.get("/chat", name="página do chat")
        self.token = None
        self._conversar("iniciar", "/api/chat/iniciar", {"codigo_turma": TURMA} if TURMA else {})
        for fala in FALAS[: random.randint(4, len(FALAS))]:
            if self.token is None:
                return
            time.sleep(random.uniform(*PAUSA))
            self._conversar("mensagem", "/api/chat/mensagem", {"token": self.token, "texto": fala})
        self.client.get("/resultado", name="página do resultado")
