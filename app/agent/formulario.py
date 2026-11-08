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
            f"no formulário, mais ou menos: {p.texto.lower()}"
            for p, v in itens
            if NOTA_FRACA < v < NOTA_FORTE
        ]
        evidencias += [
            f"no formulário, não curte: {p.texto.lower()}" for p, v in itens if v <= NOTA_FRACA
        ]
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


def resumo_do_formulario(respostas: dict[str, str]) -> str:
    """Resumo concreto para o agente abrir a conversa sem inventar (montado pelo código).

    Diz o que mais chegou perto, mesmo quando nada empolgou: "mais ou menos" também é pista.
    """
    formulario = carregar_formulario()
    notas = [
        (p.texto.lower(), int(respostas[p.id]))
        for p in formulario.perguntas
        if p.tipo == "escala" and p.id in respostas
    ]
    linhas = [f"- Respondeu {len(notas)} das 18 atividades da escala de 1 a 5."]
    if notas:
        maior = max(v for _, v in notas)
        topo = [t for t, v in notas if v == maior]
        rotulo = {5: "tudo a ver", 4: "tem a ver", 3: "mais ou menos"}.get(maior)
        if rotulo:
            linhas.append(f"- O que mais chegou perto ({rotulo}): " + "; ".join(topo[:4]) + ".")
        if maior < NOTA_FORTE:
            linhas.append("- Nenhuma atividade foi marcada como 'tem a ver' ou 'tudo a ver'.")
        if maior <= NOTA_FRACA:
            linhas.append("- Todas foram marcadas como 'pouco' ou 'nada a ver'.")
        nada = [t for t, v in notas if v == 1]
        if nada:
            linhas.append(f"- 'Nada a ver' para {len(nada)} atividade(s), por exemplo: {nada[0]}.")
    rotulos_abertas = {
        "aberta_tempo_livre": "No tempo livre",
        "aberta_materias": "Sobre as matérias",
        "aberta_planos": "Depois do EM",
    }
    for pergunta_id, rotulo in rotulos_abertas.items():
        if texto := respostas.get(pergunta_id):
            linhas.append(f'- {rotulo}, escreveu: "{texto}"')
    return "\n".join(linhas)
