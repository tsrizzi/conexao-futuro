"""Conversão do PDF do CNCT em fichas de curso (usado por `scripts/ingest_cnct.py`).

O módulo é puro: recebe as páginas já quebradas em linhas e palavras (com posição x),
para poder ser testado sem o PDF. A leitura do PDF com pdfplumber fica no script.

Estrutura do PDF (4ª edição, atualizada em 29/11/2024):
- Sumário (páginas "SUMÁRIO"): "Eixo de X  N", "Área Tecnológica Y  N", "Técnico em Z  N".
  A página do sumário é igual à página física do PDF.
- Cada ficha tem um cabeçalho "TÉCNICO EM Z  1200 horas", seções com títulos fixos e duas
  seções em duas colunas: "Pré-requisitos | Legislação" e "Campo de Atuação | Ocupações CBO".
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

TOPO_CABECALHO = 45.0  # linhas acima disso são o cabeçalho da página
TAMANHO_RODAPE = 8.0  # o rodapé usa fonte 6
X_COLUNA_PADRAO = 300.0


@dataclass(frozen=True)
class Palavra:
    x0: float
    texto: str


@dataclass
class Linha:
    top: float
    palavras: list[Palavra]
    tamanho: float = 10.0

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palavras)


Pagina = list[Linha]


@dataclass
class EntradaSumario:
    tipo: str  # eixo | area | curso | outro
    nome: str
    pagina: int


@dataclass
class FichaBruta:
    nome: str
    eixo: str
    area_tecnologica: str | None
    pagina_inicial: int
    pagina_final: int
    nome_cabecalho: str = ""
    carga_horaria_min: int | None = None
    secoes: dict[str, list[str]] = field(default_factory=dict)


# ---- Utilidades de texto ---------------------------------------------------------------


def sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sem_acentos(texto).lower()).strip("-")


def juntar_linhas(linhas: list[str]) -> str:
    """Desfaz a quebra de linha do PDF, preservando itens de lista e fim de frase."""
    partes: list[str] = []
    for linha in (lin.strip() for lin in linhas):
        if not linha:
            continue
        novo_item = linha.startswith(("- ", "• ", "-\t", "Sugestões ")) or linha == "-"
        if not partes or novo_item or partes[-1].endswith((":", ".", ";")):
            partes.append(linha)
        else:
            partes[-1] += " " + linha
    return "\n".join(partes)


def linhas_como_itens(linhas: list[str]) -> str:
    """Seções sem marcador em que cada linha do PDF é um item (ex.: locais de trabalho)."""
    partes: list[str] = []
    for linha in (lin.strip() for lin in linhas):
        if not linha:
            continue
        # Item quebrado em duas linhas: a continuação começa minúscula ou segue uma vírgula.
        continua = partes and (linha[0].islower() or partes[-1].endswith((",", " e", " de")))
        if continua:
            partes[-1] += " " + linha
        else:
            partes.append(linha)
    return "\n".join(partes)


def itens(texto: str) -> list[str]:
    """Itens de uma lista com marcadores "- " ou "• "."""
    saida = []
    for linha in texto.splitlines():
        item = linha.strip().lstrip("-•").strip()
        if item:
            saida.append(item)
    return saida


# ---- Sumário ---------------------------------------------------------------------------

_RE_ENTRADA = re.compile(r"^(?P<nome>.*\S)\s+(?P<pagina>\d{1,4})$")


def parse_sumario(paginas: list[Pagina]) -> list[EntradaSumario]:
    entradas: list[EntradaSumario] = []
    pendente = ""
    for pagina in paginas:
        for linha in pagina:
            texto = linha.texto.strip()
            if not texto or texto == "SUMÁRIO":
                continue
            m = _RE_ENTRADA.match(f"{pendente} {texto}".strip())
            if not m:
                pendente = f"{pendente} {texto}".strip()  # nome longo quebrado em 2 linhas
                continue
            pendente = ""
            nome, num = m["nome"], int(m["pagina"])
            if nome.startswith("Eixo "):
                nome_eixo = re.sub(r"^Eixo (de |do |da )?", "", nome)
                entradas.append(EntradaSumario("eixo", nome_eixo, num))
            elif nome.startswith("Área Tecnológica "):
                entradas.append(EntradaSumario("area", nome.removeprefix("Área Tecnológica "), num))
            elif nome.startswith("Técnico"):
                entradas.append(EntradaSumario("curso", nome, num))
            else:
                entradas.append(EntradaSumario("outro", nome, num))
    return entradas


def fichas_do_sumario(entradas: list[EntradaSumario], total_paginas: int) -> list[FichaBruta]:
    """Converte o sumário em fichas com intervalo de páginas (até a próxima entrada)."""
    fichas: list[FichaBruta] = []
    eixo: str | None = None
    area: str | None = None
    for i, entrada in enumerate(entradas):
        if entrada.tipo == "eixo":
            eixo, area = entrada.nome, None
        elif entrada.tipo == "area":
            area = entrada.nome
        elif entrada.tipo == "curso":
            if eixo is None:
                raise ValueError(f"Curso sem eixo no sumário: {entrada.nome}")
            proxima = next(
                (e.pagina for e in entradas[i + 1 :] if e.pagina > entrada.pagina),
                total_paginas + 1,
            )
            fichas.append(
                FichaBruta(
                    nome=entrada.nome,
                    eixo=eixo,
                    area_tecnologica=area,
                    pagina_inicial=entrada.pagina,
                    pagina_final=max(entrada.pagina, proxima - 1),
                )
            )
        else:
            eixo, area = None, None  # EXPEDIENTE, CURSOS EXCLUÍDOS etc. encerram o bloco
    return fichas


# ---- Ficha de curso --------------------------------------------------------------------


@dataclass(frozen=True)
class Secao:
    inicio: str  # como o título começa (sem acentos, minúsculo)
    campo: str
    campo_direita: str | None = None  # seções em duas colunas
    palavra_direita: str | None = None


SECOES = [
    Secao("perfil profissional de conclusao", "perfil_profissional"),
    Secao("carga horaria minima", "carga_horaria_detalhes"),
    Secao("pre-requisitos para ingresso", "pre_requisitos", "legislacao", "Legislação"),
    Secao("itinerarios formativos", "itinerarios"),
    Secao("campo de atuacao", "campo_atuacao", "ocupacoes_cbo", "Ocupações"),
    Secao("infraestrutura minima", "infraestrutura"),
    Secao("nomeclaturas anteriores", "nomenclaturas_anteriores"),  # grafia do PDF
    Secao("nomenclaturas anteriores", "nomenclaturas_anteriores"),
]

_RE_HORAS = re.compile(r"(\d[\d.]*)\s*horas", re.IGNORECASE)


def _titulo_de_secao(linha: Linha) -> Secao | None:
    texto = sem_acentos(linha.texto).lower()
    for secao in SECOES:
        # O título é curto: evita confundir uma frase comum que comece igual.
        if texto.startswith(secao.inicio) and len(texto) <= len(secao.inicio) + 30:
            return secao
    return None


def _eh_rodape(linha: Linha) -> bool:
    return linha.tamanho < TAMANHO_RODAPE or linha.texto.startswith(
        "CATÁLOGO NACIONAL DE CURSOS TÉCNICOS Página"
    )


def _mesmo_curso(cabecalho: str, nome: str) -> bool:
    a, b = slug(cabecalho), slug(nome)
    return bool(a) and (a.startswith(b) or b.startswith(a))


def parse_ficha(ficha: FichaBruta, paginas: list[Pagina]) -> FichaBruta:
    """Preenche `ficha.secoes` a partir das páginas do curso.

    `paginas` pode incluir a página em que o próximo curso começa: alguns cursos curtos
    dividem a página (o fim de um e o início do outro). A leitura para no segundo
    "Perfil Profissional de Conclusão" ou numa página cujo cabeçalho é de outro curso.
    """
    secao: Secao | None = None
    x_coluna = X_COLUNA_PADRAO
    secoes: dict[str, list[str]] = {}
    viu_perfil = False

    for n, pagina in enumerate(paginas):
        cabecalho = " ".join(
            _RE_HORAS.sub("", lin.texto).strip() for lin in pagina if lin.top < TOPO_CABECALHO
        ).strip()
        if n == 0:
            for linha in pagina:
                if linha.top < TOPO_CABECALHO:
                    _ler_cabecalho(ficha, linha)
            # Se a página começa com o fim do curso anterior, ignora até o próprio perfil.
            pular_ate_perfil = not _mesmo_curso(cabecalho, ficha.nome)
        elif not _mesmo_curso(cabecalho, ficha.nome):
            break
        for linha in pagina:
            if linha.top < TOPO_CABECALHO or _eh_rodape(linha):
                continue
            nova = _titulo_de_secao(linha)
            if nova and nova.campo == "perfil_profissional":
                if viu_perfil:
                    break  # começou o próximo curso na mesma página
                viu_perfil, pular_ate_perfil = True, False
            if pular_ate_perfil:
                continue
            if nova:
                secao = nova
                if nova.palavra_direita:
                    x_coluna = next(
                        (p.x0 - 5 for p in linha.palavras if p.texto == nova.palavra_direita),
                        X_COLUNA_PADRAO,
                    )
                continue
            if secao is None:
                continue
            if secao.campo_direita:
                esquerda = [p.texto for p in linha.palavras if p.x0 < x_coluna]
                direita = [p.texto for p in linha.palavras if p.x0 >= x_coluna]
                if esquerda:
                    secoes.setdefault(secao.campo, []).append(" ".join(esquerda))
                if direita:
                    secoes.setdefault(secao.campo_direita, []).append(" ".join(direita))
            else:
                secoes.setdefault(secao.campo, []).append(linha.texto)
        else:
            continue
        break  # o break interno (próximo curso) encerra também o laço de páginas

    ficha.secoes = secoes
    if ficha.carga_horaria_min is None:
        detalhes = " ".join(secoes.get("carga_horaria_detalhes", [])[:1])
        if m := _RE_HORAS.search(detalhes):
            ficha.carga_horaria_min = int(m[1].replace(".", ""))
    return ficha


def _ler_cabecalho(ficha: FichaBruta, linha: Linha) -> None:
    texto = linha.texto
    if m := _RE_HORAS.search(texto):
        ficha.carga_horaria_min = ficha.carga_horaria_min or int(m[1].replace(".", ""))
        texto = _RE_HORAS.sub("", texto)
    texto = texto.strip()
    if texto:
        ficha.nome_cabecalho = f"{ficha.nome_cabecalho} {texto}".strip()


# ---- Montagem do registro final --------------------------------------------------------

# Ocupação "3222-55 - Nome" (ou "322255 - Nome") ou família "3171 - Nome".
_RE_CBO = re.compile(r"(?=\b\d{4}(?:-?\d{2})?\s+(?:-\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")


def ocupacoes_cbo(linhas: list[str]) -> list[str]:
    texto = " ".join(linha.strip() for linha in linhas)
    partes = [re.sub(r"\s+", " ", p).strip(" •-") for p in _RE_CBO.split(texto)]
    return [_formatar_cbo(p) for p in partes if re.match(r"\d{4}(-?\d{2})?\s", p)]


def _formatar_cbo(item: str) -> str:
    """Padroniza "351305 - X" como "3513-05 - X"."""
    return re.sub(r"^(\d{4})(\d{2})\b", r"\1-\2", item)


def nomenclaturas(linhas: list[str]) -> list[str]:
    return [i for i in itens(juntar_linhas(linhas)) if i not in {"-", ""}]


def texto_busca(registro: dict) -> str:
    """Texto concatenado usado no índice. Fica de fora o que é burocrático (legislação)."""
    partes = [
        registro["nome"],
        f"Eixo {registro['eixo']}",
        f"Área {registro['area_tecnologica']}" if registro.get("area_tecnologica") else "",
        registro["perfil_profissional"],
        f"Campo de atuação: {registro['campo_atuacao']}" if registro["campo_atuacao"] else "",
        "Ocupações: " + "; ".join(registro["ocupacoes_cbo"]) if registro["ocupacoes_cbo"] else "",
        "Também chamado de: " + "; ".join(registro["nomenclaturas_anteriores"])
        if registro.get("nomenclaturas_anteriores")
        else "",
    ]
    return "\n".join(p for p in partes if p)


def montar_registros(fichas: list[FichaBruta], edicao: str) -> list[dict]:
    contagem = Counter(slug(f.nome) for f in fichas)
    registros = []
    for f in fichas:
        base = slug(f.nome)
        # Nomes repetidos (ex.: cursos do Eixo Militar em Combate Aéreo e Naval).
        curso_id = base if contagem[base] == 1 else f"{base}-{slug(f.area_tecnologica or f.eixo)}"
        s = f.secoes
        registro = {
            "id": curso_id,
            "nome": f.nome,
            "eixo": f.eixo,
            "area_tecnologica": f.area_tecnologica,
            "carga_horaria_min": f.carga_horaria_min or 0,
            "carga_horaria_detalhes": linhas_como_itens(s.get("carga_horaria_detalhes", [])),
            "perfil_profissional": juntar_linhas(s.get("perfil_profissional", [])),
            "campo_atuacao": linhas_como_itens(s.get("campo_atuacao", [])),
            "ocupacoes_cbo": ocupacoes_cbo(s.get("ocupacoes_cbo", [])),
            "pre_requisitos": juntar_linhas(s.get("pre_requisitos", [])),
            "infraestrutura": linhas_como_itens(s.get("infraestrutura", [])),
            "itinerarios": juntar_linhas(s.get("itinerarios", [])),
            "nomenclaturas_anteriores": nomenclaturas(s.get("nomenclaturas_anteriores", [])),
            "edicao": edicao,
            "pagina": f.pagina_inicial,
        }
        registro["texto_busca"] = texto_busca(registro)
        registros.append(registro)
    return registros


def problemas_da_ficha(ficha: FichaBruta, registro: dict) -> list[str]:
    """Campos vazios ou suspeitos, para o relatório de qualidade."""
    problemas = []
    if not _mesmo_curso(ficha.nome_cabecalho, ficha.nome):
        problemas.append(f"cabeçalho da página ('{ficha.nome_cabecalho}') difere do sumário")
    if not ficha.carga_horaria_min:
        problemas.append("carga horária não encontrada")
    for campo in ("perfil_profissional", "campo_atuacao", "infraestrutura", "itinerarios"):
        if not registro[campo].strip():
            problemas.append(f"{campo} vazio")
    if len(registro["perfil_profissional"]) < 200:
        problemas.append("perfil_profissional muito curto (< 200 caracteres)")
    texto_cbo = " ".join(ficha.secoes.get("ocupacoes_cbo", []))
    sem_cbo_no_pdf = texto_cbo.strip() in {"", "-"} or "não classificada" in texto_cbo
    if not registro["ocupacoes_cbo"] and not sem_cbo_no_pdf:
        problemas.append(f"nenhuma ocupação CBO reconhecida (texto: '{texto_cbo[:80]}')")
    if not registro["pre_requisitos"].strip():
        problemas.append("pre_requisitos vazio")
    return problemas
