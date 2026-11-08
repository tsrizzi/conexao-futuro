"""O resultado ("Minhas trilhas") para a tela e para exportar ao NotebookLM.

`para_exibicao` gera o JSON que a interface desenha; `texto_resultado` gera o .txt (o mesmo
texto do botão Copiar). Tudo vem da recomendação validada, nunca do LLM direto.
"""

from typing import Any

from app.agent.ofertas import Oferta
from app.agent.recommender import Recomendacao, Trilha
from app.rag.catalog import Curso

AVISO_IA = (
    "Estas sugestões foram feitas por uma inteligência artificial do projeto Conexão Futuro, "
    "a partir do Catálogo Nacional de Cursos Técnicos (CNCT/MEC). São sugestões para pensar, "
    "não uma decisão."
)


def duracao(curso: Curso) -> str | None:
    """'O curso dura, em média, 1 ano e meio.' -> '1 ano e meio'."""
    for linha in curso.carga_horaria_detalhes.splitlines():
        if "dura, em média," in linha:
            return linha.split("dura, em média,", 1)[1].strip().rstrip(".")
    return None


def o_que_faz(curso: Curso, maximo: int = 3) -> list[str]:
    """Os primeiros itens do perfil profissional ("será habilitado para: - ...")."""
    itens = []
    for linha in curso.perfil_profissional.splitlines():
        if linha.startswith("Para atuação") or linha.startswith("Para a atuação"):
            break
        if linha.startswith("- "):
            itens.append(linha[2:].strip())
    return itens[:maximo]


def onde_trabalha(curso: Curso, maximo: int = 6) -> list[str]:
    linhas = [lin for lin in curso.campo_atuacao.splitlines() if not lin.endswith(":")]
    return linhas[:maximo]


def _oferta(o: Oferta, conferir: list[str]) -> dict[str, Any]:
    return {
        "instituicao": o.instituicao,
        "cidade": o.cidade,
        "distancia_km": o.distancia_km,
        "forma": o.forma,
        "gratuito": o.gratuito,
        "link": o.link,
        "conferir": o.instituicao in conferir,
    }


def _trilha(t: Trilha) -> dict[str, Any]:
    c = t.curso
    return {
        "id": c.id,
        "nome": c.nome,
        "eixo": c.eixo,
        "carga_horaria": c.carga_horaria_min,
        "duracao": duracao(c),
        "porque_combina": t.porque_combina,
        "atencao": t.atencao,
        "o_que_faz": o_que_faz(c),
        "onde_trabalha": onde_trabalha(c),
        "ofertas_agora": [_oferta(o, t.ofertas.conferir) for o in t.ofertas.agora],
        "ofertas_depois": [_oferta(o, t.ofertas.conferir) for o in t.ofertas.depois],
        "aviso_ofertas": t.ofertas.aviso,
    }


def para_exibicao(rec: Recomendacao) -> dict[str, Any]:
    return {
        "resumo_perfil": rec.resumo_perfil,
        "dimensoes": [
            {"nome": d.nome, "evidencias": d.evidencias} for d in rec.dimensoes_dominantes
        ],
        "trilhas": [_trilha(t) for t in rec.trilhas],
        "pense_criticamente": rec.pense_criticamente,
        "incompleta": rec.incompleta,
        "edicao_cnct": rec.edicao_cnct,
    }


def _linhas_oferta(titulo: str, ofertas: list[dict[str, Any]]) -> list[str]:
    if not ofertas:
        return []
    linhas = [f"  {titulo}:"]
    for o in ofertas:
        partes = [o["instituicao"], o["cidade"], o["forma"]]
        if o["distancia_km"] is not None:
            partes.append(f"{o['distancia_km']:g} km")
        linha = "    - " + ", ".join(partes)
        if o["conferir"]:
            linha += " (confira se ainda está aberto)"
        linhas.append(linha)
        if o["link"]:
            linhas.append(f"      {o['link']}")
    return linhas


def texto_resultado(rec: Recomendacao) -> str:
    return texto_de_exibicao(para_exibicao(rec))


def texto_de_exibicao(d: dict[str, Any]) -> str:
    """Texto simples, bem estruturado, pensado para colar ou enviar ao NotebookLM.

    Recebe o mesmo dicionário da tela (também é o que fica salvo para quem tem código).
    """
    linhas = ["MINHAS TRILHAS - Conexão Futuro", "", AVISO_IA, "", "Sobre mim", ""]
    linhas += [f"- {frase}" for frase in d["resumo_perfil"]]
    for dim in d["dimensoes"]:
        evidencias = "; ".join(dim["evidencias"])
        linhas.append(f"- Perfil {dim['nome']}: {evidencias}")
    if d["incompleta"]:
        linhas.append("- (Conversa curta: as sugestões ficam melhores com mais conversa.)")

    for t in d["trilhas"]:
        linhas += ["", "", t["nome"], "=" * len(t["nome"])]
        cabecalho = f"Eixo: {t['eixo']}. Carga horária mínima: {t['carga_horaria']} horas"
        if t["duracao"]:
            cabecalho += f" (dura em média {t['duracao']})"
        linhas += [cabecalho + ".", "", f"Por que combina comigo: {t['porque_combina']}"]
        linhas.append(f"Ponto de atenção: {t['atencao']}")
        if t["o_que_faz"]:
            linhas += ["", "O que esse profissional faz:"]
            linhas += [f"- {item}" for item in t["o_que_faz"]]
        if t["onde_trabalha"]:
            linhas += ["", "Onde trabalha:", "- " + "; ".join(t["onde_trabalha"])]
        linhas += ["", "Onde estudar:"]
        linhas += _linhas_oferta("Dá para fazer junto com o Ensino Médio", t["ofertas_agora"])
        linhas += _linhas_oferta("Depois de terminar o Ensino Médio", t["ofertas_depois"])
        if t["aviso_ofertas"]:
            linhas.append(f"  {t['aviso_ofertas']}")

    linhas += ["", "", "Pense criticamente", ""]
    linhas += [f"- {p}" for p in d["pense_criticamente"]]
    linhas += ["", f"Fonte: Catálogo Nacional de Cursos Técnicos, {d['edicao_cnct']} (MEC)."]
    return "\n".join(linhas) + "\n"
