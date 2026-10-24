"""Catálogo Nacional de Cursos Técnicos (CNCT) carregado de `data/cnct.json`.

O catálogo é a única fonte de verdade sobre cursos: todo `curso_id` que o sistema
mostra precisa existir aqui. Trocar de edição = rodar os scripts de novo, sem mudar código.
"""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

CAMINHO_PADRAO = Path(__file__).resolve().parents[2] / "data" / "cnct.json"


class Curso(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    nome: str = Field(min_length=5)
    eixo: str = Field(min_length=3)
    area_tecnologica: str | None = None
    carga_horaria_min: int = Field(ge=100, le=10000)
    carga_horaria_detalhes: str = ""
    perfil_profissional: str
    campo_atuacao: str = ""
    ocupacoes_cbo: list[str] = []
    pre_requisitos: str = ""
    infraestrutura: str = ""
    itinerarios: str = ""
    nomenclaturas_anteriores: list[str] = []
    texto_busca: str
    edicao: str
    pagina: int | None = None

    @field_validator("perfil_profissional", "texto_busca")
    @classmethod
    def _nao_vazio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("campo obrigatório vazio")
        return valor


class Catalogo:
    def __init__(self, cursos: list[Curso]) -> None:
        if not cursos:
            raise ValueError("Catálogo vazio")
        self.cursos = cursos
        self._por_id = {c.id: c for c in cursos}
        if len(self._por_id) != len(cursos):
            raise ValueError("Há ids de curso repetidos no catálogo")
        self.edicao = cursos[0].edicao

    @classmethod
    def carregar(cls, caminho: Path = CAMINHO_PADRAO) -> "Catalogo":
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return cls([Curso.model_validate(item) for item in dados])

    def __len__(self) -> int:
        return len(self.cursos)

    def __contains__(self, curso_id: object) -> bool:
        return curso_id in self._por_id

    def get(self, curso_id: str) -> Curso | None:
        return self._por_id.get(curso_id)

    @property
    def eixos(self) -> list[str]:
        return sorted({c.eixo for c in self.cursos})

    def impressao_digital(self) -> str:
        """Hash do conteúdo indexado; detecta índice gerado a partir de outro catálogo."""
        h = hashlib.sha256()
        for c in self.cursos:
            h.update(c.id.encode())
            h.update(c.texto_busca.encode())
        return h.hexdigest()[:16]
