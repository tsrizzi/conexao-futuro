"""Peças falsas para testar o agente sem LLM real e sem modelo de embeddings."""

from collections.abc import Iterable
from typing import Any

from app.agent.orchestrator import Evento, EventoTexto
from app.rag.catalog import Catalogo
from app.rag.retriever import ResultadoBusca

# Candidatos fixos do catálogo real, de eixos variados.
IDS_CANDIDATOS = [
    "tecnico-em-informatica",
    "tecnico-em-desenvolvimento-de-sistemas",
    "tecnico-em-programacao-de-jogos-digitais",
    "tecnico-em-mecatronica",
    "tecnico-em-eletronica",
    "tecnico-em-design-grafico",
    "tecnico-em-multimidia",
    "tecnico-em-enfermagem",
    "tecnico-em-administracao",
    "tecnico-em-eletromecanica",
    "tecnico-em-automacao-industrial",
    "tecnico-em-redes-de-computadores",
    "tecnico-em-computacao-grafica",
    "tecnico-em-marketing",
    "tecnico-em-seguranca-do-trabalho",
    "tecnico-em-meio-ambiente",
    "tecnico-em-quimica",
    "tecnico-em-gastronomia",
]


class BuscadorFixo:
    def __init__(self, catalogo: Catalogo, ids: Iterable[str] = IDS_CANDIDATOS) -> None:
        self._cursos = [c for i in ids if (c := catalogo.get(i)) is not None]
        self.consultas: list[str] = []

    def buscar(
        self, consulta: str, *, eixos_provaveis: Any = (), k: int = 15
    ) -> list[ResultadoBusca]:
        self.consultas.append(consulta)
        return [
            ResultadoBusca(c, 1.0 - i / 100, 0.0, 0.0, 0.0) for i, c in enumerate(self._cursos[:k])
        ]


def dimensoes(**evidencias: str) -> dict:
    """dimensoes(R="conserta a bike") -> nota 0.8 e a evidência na dimensão R."""
    return {cod: {"nota": 0.8, "evidencias": [ev]} for cod, ev in evidencias.items()}


def analise(sinais: dict | None = None, **perfil: Any) -> dict:
    return {"perfil": perfil, "sinais": sinais or {}}


def escolha(curso_id: str, porque: str = "Você disse que curte isso.") -> dict:
    return {"curso_id": curso_id, "porque_combina": porque, "atencao": "Tem bastante prática."}


def recomendacao(*ids: str) -> dict:
    return {
        "sugestoes": [escolha(i) for i in ids],
        "resumo_perfil": ["Você curte tecnologia.", "Gosta de criar.", "Quer trabalhar logo."],
        "pense_criticamente": ["Você se imagina fazendo isso todo dia?", "O que falta saber?"],
    }


def texto(eventos: list[Evento]) -> str:
    return "".join(e.texto for e in eventos if isinstance(e, EventoTexto))
