"""PDF (ou CSV) do CNCT -> data/cnct.json + data/cnct_relatorio.md.

Uso:
    uv run --group ingest python scripts/ingest_cnct.py data/raw/<arquivo>.pdf
    uv run python scripts/ingest_cnct.py data/raw/<planilha>.csv --edicao "5ª edição (2026)"

Depois de gerar, revise a amostra de cursos do relatório antes de usar com estudantes.
"""

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pydantic import ValidationError  # noqa: E402

from app.rag.catalog import Catalogo, Curso  # noqa: E402
from app.rag.ingestao import (  # noqa: E402
    FichaBruta,
    Linha,
    Pagina,
    Palavra,
    fichas_do_sumario,
    montar_registros,
    parse_ficha,
    parse_sumario,
    problemas_da_ficha,
    texto_busca,
)

SAIDA_JSON = RAIZ / "data" / "cnct.json"
SAIDA_RELATORIO = RAIZ / "data" / "cnct_relatorio.md"
TAMANHO_AMOSTRA = 6


# ---- Leitura do PDF --------------------------------------------------------------------


def _linhas_da_pagina(pagina) -> Pagina:  # type: ignore[no-untyped-def]
    palavras = pagina.extract_words(extra_attrs=["size"], keep_blank_chars=False)
    palavras.sort(key=lambda w: (round(w["top"]), w["x0"]))
    linhas: list[Linha] = []
    for w in palavras:
        if linhas and abs(w["top"] - linhas[-1].top) <= 3:
            linhas[-1].palavras.append(Palavra(w["x0"], w["text"]))
        else:
            linhas.append(Linha(w["top"], [Palavra(w["x0"], w["text"])], w["size"]))
    for linha in linhas:
        linha.palavras.sort(key=lambda p: p.x0)
    return linhas


def _edicao(texto_capa: str) -> str:
    edicao = re.search(r"(\d+ª)\s*edição", texto_capa)
    publicado = re.search(r"Publicado em:\s*\d{2}/\d{2}/(\d{4})", texto_capa)
    alterado = re.search(r"Alteração de conteúdo em:\s*(\d{2}/\d{2}/\d{4})", texto_capa)
    texto = f"{edicao[1] if edicao else '?'} edição"
    detalhes = [publicado[1] if publicado else None]
    if alterado:
        detalhes.append(f"atualizada em {alterado[1]}")
    detalhes_validos = [d for d in detalhes if d]
    return f"{texto} ({', '.join(detalhes_validos)})" if detalhes_validos else texto


def ler_pdf(caminho: Path) -> tuple[list[dict], dict[str, list[str]]]:
    import pdfplumber

    with pdfplumber.open(caminho) as pdf:
        paginas = [_linhas_da_pagina(p) for p in pdf.pages]
        edicao = _edicao(pdf.pages[0].extract_text() or "")

    sumario = [p for p in paginas if p and p[0].texto.strip() == "SUMÁRIO"]
    fichas: list[FichaBruta] = fichas_do_sumario(parse_sumario(sumario), len(paginas))
    for ficha in fichas:
        # Inclui a página seguinte: o curso pode terminar na página em que o próximo começa.
        parse_ficha(ficha, paginas[ficha.pagina_inicial - 1 : ficha.pagina_final + 1])

    registros = montar_registros(fichas, edicao)
    problemas = {r["id"]: problemas_da_ficha(f, r) for f, r in zip(fichas, registros, strict=True)}
    return registros, {k: v for k, v in problemas.items() if v}


# ---- Leitura do CSV (alternativa montada à mão) ----------------------------------------

CAMPOS_LISTA = {"ocupacoes_cbo", "nomenclaturas_anteriores"}


def ler_csv(caminho: Path, edicao: str) -> tuple[list[dict], dict[str, list[str]]]:
    """Colunas = campos de `Curso`; listas separadas por ';'. `texto_busca` é gerado."""
    registros = []
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        for linha in csv.DictReader(arquivo):
            registro: dict = {k: (v or "").strip() for k, v in linha.items() if k}
            for campo in CAMPOS_LISTA:
                registro[campo] = [i.strip() for i in registro.get(campo, "").split(";") if i]
            registro["carga_horaria_min"] = int(registro.get("carga_horaria_min") or 0)
            registro["edicao"] = registro.get("edicao") or edicao
            registro.setdefault("campo_atuacao", "")
            registro["texto_busca"] = registro.get("texto_busca") or texto_busca(registro)
            registros.append(registro)
    return registros, {}


# ---- Validação e relatório -------------------------------------------------------------


def validar(registros: list[dict]) -> tuple[list[Curso], dict[str, str]]:
    cursos, invalidos = [], {}
    for registro in registros:
        try:
            cursos.append(Curso.model_validate(registro))
        except ValidationError as erro:
            invalidos[registro.get("id", "?")] = str(erro).replace("\n", " ")
    return cursos, invalidos


def relatorio(
    origem: Path,
    cursos: list[Curso],
    problemas: dict[str, list[str]],
    invalidos: dict[str, str],
) -> str:
    por_eixo: dict[str, int] = {}
    for c in cursos:
        por_eixo[c.eixo] = por_eixo.get(c.eixo, 0) + 1

    linhas = [
        "# Relatório de qualidade do CNCT",
        "",
        f"- Origem: `{origem.name}`",
        f"- Edição: {cursos[0].edicao if cursos else '?'}",
        f"- Cursos válidos: **{len(cursos)}**",
        f"- Cursos rejeitados na validação: **{len(invalidos)}**",
        f"- Cursos com alertas: **{len(problemas)}**",
        "",
        "## Cursos por eixo",
        "",
        "| Eixo | Cursos |",
        "|---|---|",
        *(f"| {eixo} | {n} |" for eixo, n in sorted(por_eixo.items())),
        "",
        "## Rejeitados (não entram no cnct.json)",
        "",
        *(f"- `{cid}`: {erro}" for cid, erro in invalidos.items()),
        *([] if invalidos else ["Nenhum."]),
        "",
        "## Alertas (entram, mas merecem conferência)",
        "",
        *(f"- `{cid}`: {'; '.join(lista)}" for cid, lista in sorted(problemas.items())),
        *([] if problemas else ["Nenhum."]),
        "",
        "## Amostra para revisão humana",
        "",
        "Confira estes cursos contra o PDF (página indicada) antes de usar com estudantes.",
        "",
    ]
    for c in random.Random(42).sample(cursos, min(TAMANHO_AMOSTRA, len(cursos))):
        linhas += [
            f"### {c.nome} (`{c.id}`, p. {c.pagina})",
            "",
            f"- Eixo: {c.eixo} · Área: {c.area_tecnologica or '-'} · {c.carga_horaria_min} horas",
            f"- Ocupações CBO: {'; '.join(c.ocupacoes_cbo) or '-'}",
            f"- Perfil (início): {c.perfil_profissional[:300]}…",
            f"- Campo de atuação (início): {c.campo_atuacao[:200]}…",
            "",
        ]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("arquivo", type=Path, help="PDF do CNCT ou CSV montado à mão")
    parser.add_argument("--edicao", default="", help="obrigatório para CSV sem coluna edicao")
    args = parser.parse_args()

    if args.arquivo.suffix.lower() == ".csv":
        registros, problemas = ler_csv(args.arquivo, args.edicao)
    else:
        registros, problemas = ler_pdf(args.arquivo)

    cursos, invalidos = validar(registros)
    Catalogo(cursos)  # confere ids únicos e catálogo não vazio

    dados = [c.model_dump() for c in cursos]
    SAIDA_JSON.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    SAIDA_RELATORIO.write_text(
        relatorio(args.arquivo, cursos, problemas, invalidos), encoding="utf-8"
    )
    print(f"{len(cursos)} cursos -> {SAIDA_JSON.relative_to(RAIZ)}")
    print(f"{len(invalidos)} rejeitados, {len(problemas)} com alertas")
    print(f"Relatório: {SAIDA_RELATORIO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
