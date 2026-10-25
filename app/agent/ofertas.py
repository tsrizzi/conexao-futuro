"""Onde cada curso é ofertado na região (seção 4.2). Vem SÓ de data/ofertas_regiao.json.

Para um estudante do 2º ano, o que serve agora é o que aceita quem está cursando o EM
(concomitante); o que pede EM concluído fica para "depois que terminar o EM".
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.agent.prompts import carregar

CAMINHO_OFERTAS = Path(__file__).resolve().parents[2] / "data" / "ofertas_regiao.json"
VALIDADE = timedelta(days=365)

log = logging.getLogger(__name__)


class Oferta(BaseModel):
    curso_id: str
    instituicao: str
    tipo: Literal[
        "Sistema S", "Pública federal", "Pública estadual", "Pública municipal", "Privada"
    ]
    cidade: str
    distancia_km: float | None = None
    forma: Literal["integrado", "concomitante", "subsequente", "EaD"]
    requisito: Literal["cursando EM", "EM concluído"] | None = None
    gratuito: bool | None = None  # null = não se sabe; o agente não afirma gratuidade
    link: str | None = None
    fonte: str = ""
    verificado_em: date | None = None

    def precisa_conferir(self, hoje: date | None = None) -> bool:
        if self.verificado_em is None:
            return True
        return (hoje or date.today()) - self.verificado_em > VALIDADE

    @property
    def serve_agora(self) -> bool:
        if self.requisito:
            return self.requisito == "cursando EM"
        return self.forma == "concomitante"


class OfertasDoCurso(BaseModel):
    agora: list[Oferta] = []  # dá para fazer junto com o EM
    depois: list[Oferta] = []  # depois que terminar o EM
    aviso: str | None = None  # texto fixo quando não há ofertas cadastradas
    conferir: list[str] = []  # instituições com verificação antiga


class Ofertas:
    def __init__(self, ofertas: list[Oferta]) -> None:
        self._por_curso: dict[str, list[Oferta]] = {}
        for oferta in ofertas:
            self._por_curso.setdefault(oferta.curso_id, []).append(oferta)

    @classmethod
    def carregar(cls, ids_validos: set[str], caminho: Path = CAMINHO_OFERTAS) -> "Ofertas":
        if not caminho.exists():
            return cls([])
        validas = []
        for item in json.loads(caminho.read_text(encoding="utf-8") or "[]"):
            try:
                oferta = Oferta.model_validate(item)
            except ValidationError as erro:
                log.warning("Oferta inválida ignorada: %s", erro)
                continue
            if oferta.curso_id not in ids_validos:
                log.warning("Oferta de curso fora do catálogo ignorada: %s", oferta.curso_id)
                continue
            validas.append(oferta)
        return cls(validas)

    def do_curso(self, curso_id: str, hoje: date | None = None) -> OfertasDoCurso:
        ofertas = sorted(
            self._por_curso.get(curso_id, []),
            key=lambda o: o.distancia_km if o.distancia_km is not None else 9999,
        )
        if not ofertas:
            return OfertasDoCurso(aviso=carregar("msg_sem_ofertas"))
        # Integrado ao EM exige começar o EM de novo: não serve para quem está no 2º ano.
        uteis = [o for o in ofertas if o.forma != "integrado"]
        return OfertasDoCurso(
            agora=[o for o in uteis if o.serve_agora],
            depois=[o for o in uteis if not o.serve_agora],
            aviso=None if uteis else carregar("msg_sem_ofertas"),
            conferir=[o.instituicao for o in uteis if o.precisa_conferir(hoje)],
        )
