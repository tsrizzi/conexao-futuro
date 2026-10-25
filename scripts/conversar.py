"""Conversa com o agente pelo terminal (Fase 2: sem interface web).

Uso:
    uv run python scripts/conversar.py                 # provedor do .env
    uv run python scripts/conversar.py --roteiro       # conversa automática com falas prontas

Comandos durante a conversa: /perfil, /custo, /sair.
Nada da conversa é gravado. USO SÓ COM ADULTOS em testes (não com estudantes).
"""

import argparse
import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.agent.factory import criar_orquestrador  # noqa: E402
from app.agent.orchestrator import (  # noqa: E402
    Evento,
    EventoAviso,
    EventoErro,
    EventoEstado,
    EventoOpcoes,
    EventoProgresso,
    EventoRecomendacao,
    EventoTexto,
    Sessao,
)
from app.agent.recommender import Recomendacao  # noqa: E402
from app.config import get_settings  # noqa: E402

# US$ por milhão de tokens (entrada, saída), para estimar o custo da conversa.
PRECOS = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5": (2.0, 10.0)}

ROTEIRO = [
    "oi",
    "jogo videogame e às vezes desenho uns personagens",
    "gosto de artes e de matemática, mas odeio química",
    "no grupo eu sempre faço os slides e a parte visual",
    "acho importante ser algo criativo e que dê pra trabalhar logo",
    "tenho que estudar perto de casa, não tenho como ir longe",
    "prefiro ficar no computador do que com muita gente",
    "isso mesmo",
    "o que faz um técnico em design gráfico?",
]


def mostrar_recomendacao(rec: Recomendacao) -> None:
    print("\n" + "=" * 70 + "\n MINHAS TRILHAS\n" + "=" * 70)
    for linha in rec.resumo_perfil:
        print(f"  {linha}")
    for d in rec.dimensoes_dominantes:
        print(f"  • {d.nome}: {'; '.join(d.evidencias)}")
    for i, t in enumerate(rec.trilhas, 1):
        print(f"\n {i}. {t.curso.nome} ({t.curso.eixo}, {t.curso.carga_horaria_min} h)")
        print(f"    Por que combina: {t.porque_combina}")
        print(f"    Atenção: {t.atencao}")
        for o in t.ofertas.agora:
            print(f"    Agora: {o.instituicao} ({o.cidade}, {o.forma})")
        for o in t.ofertas.depois:
            print(f"    Depois do EM: {o.instituicao} ({o.cidade}, {o.forma})")
        if t.ofertas.aviso:
            print(f"    Onde estudar: {t.ofertas.aviso}")
    print("\n Pense criticamente:")
    for p in rec.pense_criticamente:
        print(f"  - {p}")
    print(f"\n CNCT {rec.edicao_cnct}" + (" · perfil incompleto" if rec.incompleta else ""))
    print("=" * 70)


def mostrar(evento: Evento) -> None:
    match evento:
        case EventoTexto(texto):
            print(texto, end="", flush=True)
        case EventoOpcoes(opcoes):
            print("\n   " + "  ".join(f"[{o}]" for o in opcoes), end="")
        case EventoProgresso() as p:
            print(f"\n   (já te conheço uns {p.percentual}%)", end="")
        case EventoRecomendacao(rec):
            mostrar_recomendacao(rec)
        case EventoAviso(texto):
            print(f"\n   ⚠ {texto}")
        case EventoErro(texto):
            print(f"\n   ✖ {texto}")
        case EventoEstado(estado):
            print(f"\n   · estado: {estado.value}", end="")


def custo(sessao: Sessao, modelo: str) -> str:
    u = sessao.uso
    texto = (
        f"tokens: {u.input_tokens} entrada, {u.output_tokens} saída, "
        f"{u.cache_read_tokens} lidos do cache"
    )
    if modelo in PRECOS:
        entrada, saida = PRECOS[modelo]
        dolares = (u.input_tokens * entrada + u.output_tokens * saida) / 1_000_000
        texto += f" ≈ US$ {dolares:.4f}"
    return texto


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--roteiro", action="store_true", help="usa falas prontas")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    settings = get_settings()
    print(f"Provedor: {settings.llm_provider.value} · modelo: {settings.llm_model}")
    orquestrador = criar_orquestrador(settings)
    sessao = Sessao()

    print("\nIA: ", end="")
    async for evento in orquestrador.iniciar(sessao):
        mostrar(evento)

    falas = iter(ROTEIRO) if args.roteiro else None
    while True:
        if falas is not None:
            fala = next(falas, "/sair")
            print(f"\n\nVocê: {fala}")
        else:
            try:
                fala = (await asyncio.to_thread(input, "\n\nVocê: ")).strip()
            except (EOFError, KeyboardInterrupt):
                fala = "/sair"
        if fala == "/sair":
            break
        if fala == "/perfil":
            print(sessao.perfil.model_dump_json(indent=1, exclude_defaults=True))
            continue
        if fala == "/custo":
            print(custo(sessao, settings.llm_model))
            continue
        print("IA: ", end="")
        async for evento in orquestrador.responder(sessao, fala):
            mostrar(evento)

    print(f"\n\nFim. {custo(sessao, settings.llm_model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
