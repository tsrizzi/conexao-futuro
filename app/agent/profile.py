"""Perfil estruturado do estudante (seção 6.2) e dados de referência das dimensões.

O LLM propõe o perfil; o código valida, limita o tamanho e calcula a `cobertura`,
que alimenta o medidor de progresso e decide quando dá para recomendar.
"""

import random
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DADOS = Path(__file__).resolve().parents[2] / "data"
CODIGOS = ("R", "I", "A", "S", "E", "C")
Codigo = Literal["R", "I", "A", "S", "E", "C"]
Ambiente = Literal[
    "escritório", "ao ar livre", "laboratório", "oficina", "com pessoas", "indiferente"
]

MAX_ITENS = 10
MAX_CARACTERES_ITEM = 100


def _limitar_lista(valores: list[str]) -> list[str]:
    """Remove vazios e repetidos, corta itens longos e limita a quantidade."""
    vistos: dict[str, str] = {}
    for valor in valores:
        item = " ".join(str(valor).split())[:MAX_CARACTERES_ITEM]
        if item and item.lower() not in vistos:
            vistos[item.lower()] = item
    return list(vistos.values())[-MAX_ITENS:]


def _limitar_nota(valor: object) -> float:
    try:
        return min(max(float(valor), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class Dimensao(BaseModel):
    nota: float = 0.0
    evidencias: list[str] = []

    _nota = field_validator("nota", mode="before")(lambda v: _limitar_nota(v))
    _evidencias = field_validator("evidencias", mode="before")(lambda v: _limitar_lista(v or []))

    @property
    def coberta(self) -> bool:
        return bool(self.evidencias)


class Dimensoes(BaseModel):
    R: Dimensao = Dimensao()
    I: Dimensao = Dimensao()  # noqa: E741 - código RIASEC oficial
    A: Dimensao = Dimensao()
    S: Dimensao = Dimensao()
    E: Dimensao = Dimensao()
    C: Dimensao = Dimensao()

    def get(self, codigo: str) -> Dimensao:
        dimensao: Dimensao = getattr(self, codigo)
        return dimensao


class EixoProvavel(BaseModel):
    eixo: str
    confianca: float = Field(default=0.0)

    _confianca = field_validator("confianca", mode="before")(lambda v: _limitar_nota(v))


class Perfil(BaseModel):
    interesses: list[str] = []
    atividades_que_gosta: list[str] = []
    materias_favoritas: list[str] = []
    materias_dificeis: list[str] = []
    habilidades_percebidas: list[str] = []
    valores_trabalho: list[str] = []
    ambiente_preferido: Ambiente | None = None
    restricoes: list[str] = []
    dimensoes: Dimensoes = Dimensoes()
    eixos_provaveis: list[EixoProvavel] = []
    cobertura: float = 0.0

    _listas = field_validator(
        "interesses",
        "atividades_que_gosta",
        "materias_favoritas",
        "materias_dificeis",
        "habilidades_percebidas",
        "valores_trabalho",
        "restricoes",
        mode="before",
    )(lambda v: _limitar_lista(v or []))

    @field_validator("ambiente_preferido", mode="before")
    @classmethod
    def _ambiente_valido(cls, valor: object) -> object:
        opcoes = Ambiente.__args__  # type: ignore[attr-defined]
        return valor if valor in opcoes else None

    # ---- Cobertura (seção 6.2) ---------------------------------------------------------

    def itens_cobertos(self) -> dict[str, bool]:
        """As 6 dimensões + matérias, valores e restrições."""
        itens = {f"dimensao_{c}": self.dimensoes.get(c).coberta for c in CODIGOS}
        itens["materias"] = bool(self.materias_favoritas or self.materias_dificeis)
        itens["valores"] = bool(self.valores_trabalho)
        itens["restricoes"] = bool(self.restricoes)
        return itens

    def calcular_cobertura(self) -> float:
        itens = self.itens_cobertos()
        self.cobertura = round(sum(itens.values()) / len(itens), 2)
        return self.cobertura

    # ---- Leitura -----------------------------------------------------------------------

    def dimensoes_dominantes(self, n: int = 2) -> list[str]:
        com_sinal = [c for c in CODIGOS if self.dimensoes.get(c).nota > 0]
        return sorted(com_sinal, key=lambda c: -self.dimensoes.get(c).nota)[:n]

    def dimensao_menos_conhecida(self) -> str | None:
        sem_sinal = [c for c in CODIGOS if not self.dimensoes.get(c).coberta]
        return sem_sinal[0] if sem_sinal else None

    def _lacunas_de_dimensao(self) -> list[tuple[str, str]]:
        nomes = nomes_dimensoes()
        return [
            (c, f"interesse na dimensão {nomes[c]['nome']} ({nomes[c]['resumo']})")
            for c in CODIGOS
            if not self.dimensoes.get(c).coberta
        ]

    def _outras_lacunas(self) -> list[str]:
        faltam = []
        if not (self.materias_favoritas or self.materias_dificeis):
            faltam.append("matérias da escola de que gosta e de que não gosta")
        if not self.valores_trabalho:
            faltam.append("o que valoriza num trabalho")
        if not self.restricoes:
            faltam.append("restrições (ex.: precisa ser perto, quer trabalhar logo) ou se não há")
        return faltam

    def lacunas(self) -> list[str]:
        return [texto for _, texto in self._lacunas_de_dimensao()] + self._outras_lacunas()

    def proximo_foco(self, rodada: int) -> tuple[str, str | None]:
        """O que a próxima pergunta deve sondar: (descrição, código da dimensão ou None).

        Alterna entre dimensões sem sinal e os outros itens (matérias, valores, restrições),
        para a conversa não ficar presa no que o estudante já contou.
        """
        dimensoes = self._lacunas_de_dimensao()
        outras = self._outras_lacunas()
        if outras and (rodada % 2 == 0 or not dimensoes):
            return outras[0], None
        if dimensoes:
            codigo, texto = dimensoes[rodada // 2 % len(dimensoes)]
            return texto, codigo
        return "aprofundar o que o estudante já contou, pedindo um exemplo concreto", None

    def consulta_busca(self) -> str:
        """Consulta para o RAG (seção 6.3.1): interesses, atividades, valores e evidências."""
        partes = [
            *self.interesses,
            *self.atividades_que_gosta,
            *self.habilidades_percebidas,
            *self.materias_favoritas,
            *self.valores_trabalho,
        ]
        if self.ambiente_preferido and self.ambiente_preferido != "indiferente":
            partes.append(f"trabalhar {self.ambiente_preferido}")
        for codigo in self.dimensoes_dominantes():
            partes += self.dimensoes.get(codigo).evidencias
        return ". ".join(partes)

    def para_prompt(self) -> str:
        """Versão compacta (menos tokens) para os prompts."""
        return self.model_dump_json(exclude={"cobertura"}, exclude_defaults=True) or "{}"


# ---- Dados de referência ---------------------------------------------------------------


@lru_cache
def _soutec() -> dict:
    dados: dict = yaml.safe_load((DADOS / "soutec_itens.yaml").read_text(encoding="utf-8"))
    return dados


def nomes_dimensoes() -> dict[str, dict[str, str]]:
    dimensoes: dict[str, dict[str, str]] = _soutec()["dimensoes"]
    return dimensoes


def exemplos_soutec(codigo: str, quantidade: int = 3, semente: int = 0) -> list[str]:
    """2 ou 3 itens do SouTec da dimensão, para virar exemplo concreto na conversa."""
    itens = [i["texto"] for i in _soutec()["itens"] if i["dim"] == codigo and i["texto"]]
    return random.Random(f"{codigo}-{semente}").sample(itens, min(quantidade, len(itens)))


@lru_cache
def mapa_dimensoes_eixos() -> dict[str, list[str]]:
    dados = yaml.safe_load((DADOS / "dimensoes_eixos.yaml").read_text(encoding="utf-8"))
    return {codigo: list(info["eixos"]) for codigo, info in dados.items()}


def eixos_validados(perfil: Perfil, eixos_validos: set[str]) -> list[EixoProvavel]:
    """Descarta eixos inventados; se não sobrar nenhum, deduz das dimensões dominantes."""
    vistos: dict[str, EixoProvavel] = {}
    for e in perfil.eixos_provaveis:
        if e.eixo in eixos_validos and e.eixo not in vistos:
            vistos[e.eixo] = e
    if vistos:
        return sorted(vistos.values(), key=lambda e: -e.confianca)[:3]
    deduzidos: dict[str, float] = {}
    for codigo in perfil.dimensoes_dominantes():
        nota = perfil.dimensoes.get(codigo).nota
        if nota < 0.5:
            continue
        for eixo in mapa_dimensoes_eixos().get(codigo, []):
            if eixo in eixos_validos:
                deduzidos[eixo] = max(deduzidos.get(eixo, 0.0), round(nota * 0.5, 2))
    return [EixoProvavel(eixo=e, confianca=c) for e, c in list(deduzidos.items())[:3]]
