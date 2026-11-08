"""Formulário de apoio (seção 7.4.3): perguntas, validação e conversão em perfil.

Nada de LLM aqui: o perfil sai das respostas por regra simples (custo zero, instantâneo).
A conversa depois refina, pulando o que o formulário já respondeu.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.agent.profile import CODIGOS, Dimensao, Perfil

CAMINHO = Path(__file__).resolve().parents[2] / "data" / "perguntas.yaml"
MAX_CARACTERES_ABERTA = 500
NOTA_FORTE = 4  # "tem a ver" ou mais vira evidência positiva
NOTA_FRACA = 2  # "pouco a ver" ou menos vira evidência de desinteresse


class Pergunta(BaseModel):
    id: str
    dim: str | None
    tipo: Literal["escala", "aberta"]
    texto: str
    origem_soutec: int | None = None
    exemplo: str | None = None


class OpcaoEscala(BaseModel):
    valor: int
    emoji: str
    rotulo: str


class Formulario(BaseModel):
    escala: list[OpcaoEscala]
    perguntas: list[Pergunta]

    def get(self, pergunta_id: str) -> Pergunta | None:
        return next((p for p in self.perguntas if p.id == pergunta_id), None)


@lru_cache
def carregar_formulario(caminho: Path = CAMINHO) -> Formulario:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    return Formulario.model_validate(dados)


def validar_respostas(respostas: dict[str, str]) -> dict[str, str]:
    """Só perguntas que existem; escala de 1 a 5; texto aberto limpo e limitado."""
    formulario = carregar_formulario()
    validas: dict[str, str] = {}
    for pergunta_id, bruto in respostas.items():
        pergunta = formulario.get(pergunta_id)
        valor = " ".join(str(bruto).split())
        if pergunta is None or not valor:
            continue
        if pergunta.tipo == "escala":
            if valor in {"1", "2", "3", "4", "5"}:
                validas[pergunta_id] = valor
        else:
            validas[pergunta_id] = valor[:MAX_CARACTERES_ABERTA]
    return validas


def respondidas(respostas: dict[str, str]) -> int:
    return len(validar_respostas(respostas))


def perfil_do_formulario(respostas: dict[str, str]) -> Perfil:
    """Nota de cada dimensão = média normalizada dos itens respondidos ((média-1)/4)."""
    formulario = carregar_formulario()
    perfil = Perfil()
    for codigo in CODIGOS:
        itens = [
            (p, int(respostas[p.id]))
            for p in formulario.perguntas
            if p.dim == codigo and p.tipo == "escala" and p.id in respostas
        ]
        if not itens:
            continue
        media = sum(v for _, v in itens) / len(itens)
        evidencias = [
            f"no formulário, curte: {p.texto.lower()}" for p, v in itens if v >= NOTA_FORTE
        ]
        evidencias += [
            f"no formulário, não curte: {p.texto.lower()}" for p, v in itens if v <= NOTA_FRACA
        ]
        if not evidencias:
            evidencias = [f"no formulário, mais ou menos: {itens[0][0].texto.lower()}"]
        setattr(
            perfil.dimensoes,
            codigo,
            Dimensao(nota=round((media - 1) / 4, 2), evidencias=evidencias),
        )
    # Abertas entram como estão (já mascaradas); a conversa organiza depois.
    if texto := respostas.get("aberta_tempo_livre"):
        perfil.atividades_que_gosta = [texto]
    if texto := respostas.get("aberta_materias"):
        perfil.materias_favoritas = [texto]
    if texto := respostas.get("aberta_planos"):
        perfil.restricoes = [f"planos depois do EM: {texto}"]
    perfil.calcular_cobertura()
    return perfil
