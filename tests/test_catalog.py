"""O cnct.json versionado é válido e coerente com os dados de referência."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.rag.catalog import Catalogo, Curso

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalogo() -> Catalogo:
    return Catalogo.carregar()


def test_catalogo_carrega_e_valida(catalogo: Catalogo) -> None:
    assert len(catalogo) >= 200
    assert catalogo.edicao.startswith("4ª edição")
    assert {c.edicao for c in catalogo.cursos} == {catalogo.edicao}


def test_campos_essenciais_preenchidos(catalogo: Catalogo) -> None:
    for curso in catalogo.cursos:
        assert curso.nome.startswith("Técnico"), curso.id
        assert curso.carga_horaria_min >= 800, curso.id
        assert len(curso.perfil_profissional) >= 100, curso.id
        assert curso.campo_atuacao, curso.id
        assert curso.nome in curso.texto_busca, curso.id


def test_cursos_conhecidos_existem(catalogo: Catalogo) -> None:
    for curso_id in [
        "tecnico-em-informatica",
        "tecnico-em-enfermagem",
        "tecnico-em-mecatronica",
        "tecnico-em-eletromecanica",
        "tecnico-em-seguranca-do-trabalho",
        "tecnico-em-administracao",
    ]:
        assert curso_id in catalogo
    assert "tecnico-em-curso-inventado" not in catalogo
    assert catalogo.get("tecnico-em-curso-inventado") is None


def test_eixos_do_mapeamento_existem_no_catalogo(catalogo: Catalogo) -> None:
    mapa = yaml.safe_load((RAIZ / "data" / "dimensoes_eixos.yaml").read_text(encoding="utf-8"))
    assert set(mapa) == {"R", "I", "A", "S", "E", "C"}
    for dim, dados in mapa.items():
        for eixo in dados["eixos"]:
            assert eixo in catalogo.eixos, f"{dim}: eixo '{eixo}' não existe no cnct.json"


def _curso(curso_id: str = "tecnico-em-x", **extra: object) -> Curso:
    dados = {
        "id": curso_id,
        "nome": "Técnico em X",
        "eixo": "Eixo X",
        "carga_horaria_min": 800,
        "perfil_profissional": "perfil",
        "texto_busca": "texto",
        "edicao": "teste",
    }
    return Curso.model_validate(dados | extra)


def test_ids_repetidos_sao_rejeitados() -> None:
    with pytest.raises(ValueError, match="repetidos"):
        Catalogo([_curso(), _curso()])


def test_id_fora_do_padrao_e_rejeitado() -> None:
    with pytest.raises(ValidationError):
        _curso("Técnico X")


def test_perfil_vazio_e_rejeitado() -> None:
    with pytest.raises(ValidationError):
        _curso(perfil_profissional="   ")
