"""Parser do PDF do CNCT, com páginas sintéticas (não precisa do PDF)."""

from app.rag.ingestao import (
    FichaBruta,
    Linha,
    Palavra,
    fichas_do_sumario,
    juntar_linhas,
    linhas_como_itens,
    montar_registros,
    ocupacoes_cbo,
    parse_ficha,
    parse_sumario,
    problemas_da_ficha,
    slug,
)


def linha(top: float, texto: str, x0: float = 50, tamanho: float = 10) -> Linha:
    palavras, x = [], x0
    for p in texto.split():
        palavras.append(Palavra(x, p))
        x += 6 * len(p) + 4
    return Linha(top, palavras, tamanho)


def duas_colunas(top: float, esquerda: str, direita: str) -> Linha:
    a, b = linha(top, esquerda), linha(top, direita, x0=325)
    return Linha(top, a.palavras + b.palavras)


def rodape(n: int) -> Linha:
    return linha(816, f"CATÁLOGO NACIONAL DE CURSOS TÉCNICOS Página {n} de 548", tamanho=6)


# ---- Sumário ---------------------------------------------------------------------------


def test_sumario_com_eixo_area_curso_e_nome_quebrado() -> None:
    paginas = [
        [
            linha(20, "SUMÁRIO"),
            linha(40, "EXPEDIENTE 11"),
            linha(60, "Eixo de Informação e Comunicação 230"),
            linha(80, "Técnico em Informática 236"),
            linha(100, "Técnico em Produção de Materiais Didáticos Bilíngues em"),
            linha(112, "Libras/Língua Portuguesa 240"),
            linha(130, "Eixo de Segurança 300"),
            linha(150, "Técnico em Defesa Civil 301"),
            linha(170, "CURSOS EXCLUÍDOS OU NÃO INCLUÍDOS 310"),
        ]
    ]
    entradas = parse_sumario(paginas)
    assert [(e.tipo, e.nome) for e in entradas][2:4] == [
        ("curso", "Técnico em Informática"),
        (
            "curso",
            "Técnico em Produção de Materiais Didáticos Bilíngues em Libras/Língua Portuguesa",
        ),
    ]

    fichas = fichas_do_sumario(entradas, total_paginas=320)
    assert [(f.nome, f.eixo, f.pagina_inicial, f.pagina_final) for f in fichas] == [
        ("Técnico em Informática", "Informação e Comunicação", 236, 239),
        (
            "Técnico em Produção de Materiais Didáticos Bilíngues em Libras/Língua Portuguesa",
            "Informação e Comunicação",
            240,
            299,
        ),
        ("Técnico em Defesa Civil", "Segurança", 301, 309),
    ]


def test_area_tecnologica_e_reiniciada_a_cada_eixo() -> None:
    entradas = parse_sumario(
        [
            [
                linha(10, "Eixo de Ambiente e Saúde 18"),
                linha(20, "Área Tecnológica Gestão e Promoção da Saúde e Bem-Estar 19"),
                linha(30, "Técnico em Enfermagem 32"),
                linha(40, "Eixo de Produção Alimentícia 291"),
                linha(50, "Técnico em Alimentos 294"),
            ]
        ]
    )
    fichas = fichas_do_sumario(entradas, 400)
    assert fichas[0].area_tecnologica == "Gestão e Promoção da Saúde e Bem-Estar"
    assert fichas[1].area_tecnologica is None


# ---- Ficha -----------------------------------------------------------------------------


def _pagina_1() -> list[Linha]:
    return [
        linha(18, "TÉCNICO EM AGENTE COMUNITÁRIO DE SAÚDE 1200 horas", tamanho=16),
        linha(55, "Perfil Profissional de Conclusão"),
        linha(70, "O Técnico em Agente Comunitário de Saúde será habilitado para:"),
        linha(82, "- Orientar e acompanhar, sob a supervisão de profissional de nível"),
        linha(94, "superior, indivíduos e famílias."),
        linha(106, "- Promover ações nas áreas de vigilância em saúde e ambiental."),
        linha(130, "Carga Horária Mínima"),
        linha(142, "1200 horas"),
        linha(154, "O curso dura, em média, 1 ano e meio."),
        rodape(20),
    ]


def _pagina_2() -> list[Linha]:
    return [
        linha(18, "TÉCNICO EM AGENTE COMUNITÁRIO DE SAÚDE 1200 horas", tamanho=16),
        duas_colunas(101, "Pré-requisitos para ingresso", "Legislação Profissional"),
        duas_colunas(118, "- Para ingresso no Curso Técnico Concomitante, o", "• Lei nº 11.350"),
        linha(130, "estudante deverá estar cursando o Ensino Médio."),
        linha(258, "Itinerários formativos"),
        linha(275, "Sugestões de qualificação profissional:"),
        linha(287, "- Agente Comunitário de Saúde"),
        linha(299, "Sugestões de verticalização para cursos de graduação:"),
        duas_colunas(563, "Campo de Atuação", "Ocupações CBO Associadas"),
        duas_colunas(580, "Locais e ambientes de trabalho:", "3222-55 - Técnico em Agente"),
        duas_colunas(592, "Sistema Único de Saúde (SUS),", "Comunitário de Saúde"),
        duas_colunas(604, "unidades básicas", "322205 - Técnico de Enfermagem"),
        linha(621, "Infraestrutura Mínima"),
        linha(638, "Biblioteca com acervo físico ou virtual"),
        linha(650, "Laboratório multidisciplinar"),
        linha(690, "Nomeclaturas anteriores"),
        linha(706, "• Agente de Saúde"),
        rodape(21),
    ]


def _ficha() -> FichaBruta:
    return FichaBruta(
        nome="Técnico em Agente Comunitário de Saúde",
        eixo="Ambiente e Saúde",
        area_tecnologica="Gestão e Promoção da Saúde e Bem-Estar",
        pagina_inicial=20,
        pagina_final=21,
    )


def test_ficha_completa_com_duas_colunas() -> None:
    ficha = parse_ficha(_ficha(), [_pagina_1(), _pagina_2()])
    [registro] = montar_registros([ficha], edicao="4ª edição (2020)")

    assert registro["id"] == "tecnico-em-agente-comunitario-de-saude"
    assert registro["carga_horaria_min"] == 1200
    assert registro["perfil_profissional"].splitlines() == [
        "O Técnico em Agente Comunitário de Saúde será habilitado para:",
        "- Orientar e acompanhar, sob a supervisão de profissional de nível superior, "
        "indivíduos e famílias.",
        "- Promover ações nas áreas de vigilância em saúde e ambiental.",
    ]
    assert registro["pre_requisitos"] == (
        "- Para ingresso no Curso Técnico Concomitante, o estudante deverá estar cursando "
        "o Ensino Médio."
    )
    assert "Lei" not in registro["pre_requisitos"]  # coluna da direita separada
    assert registro["campo_atuacao"].splitlines() == [
        "Locais e ambientes de trabalho:",
        "Sistema Único de Saúde (SUS), unidades básicas",
    ]
    assert registro["ocupacoes_cbo"] == [
        "3222-55 - Técnico em Agente Comunitário de Saúde",
        "3222-05 - Técnico de Enfermagem",
    ]
    assert registro["itinerarios"].splitlines()[-1].startswith("Sugestões de verticalização")
    assert registro["infraestrutura"].count("\n") == 1
    assert registro["nomenclaturas_anteriores"] == ["Agente de Saúde"]
    assert "CATÁLOGO NACIONAL" not in registro["texto_busca"]  # rodapé descartado
    assert problemas_da_ficha(ficha, registro) == []


def test_pagina_compartilhada_entre_dois_cursos() -> None:
    """Curso curto termina na página em que o próximo começa (caso do Eixo Militar)."""
    p1 = [
        linha(18, "TÉCNICO EM MERGULHO 1200 horas", tamanho=16),
        linha(55, "Perfil Profissional de Conclusão"),
        linha(70, "O Técnico em Mergulho será habilitado para:"),
        linha(200, "Infraestrutura Mínima"),
        linha(212, "Piscina"),
    ]
    p2 = [
        linha(18, "TÉCNICO EM MERGULHO 1200 horas", tamanho=16),
        linha(55, "Tanques de mergulho"),
        linha(70, "Nomeclaturas anteriores"),
        linha(82, "-"),
        linha(100, "Perfil Profissional de Conclusão"),
        linha(112, "O Técnico em Operação de Radar será habilitado para:"),
        linha(124, "- Operar o sistema de radares."),
    ]
    p3 = [
        linha(18, "TÉCNICO EM OPERAÇÃO DE SONAR 1200 horas", tamanho=16),
        linha(55, "Perfil Profissional de Conclusão"),
    ]
    mergulho = FichaBruta("Técnico em Mergulho", "Militar", "Combate Naval", 536, 536)
    radar = FichaBruta("Técnico em Operação de Radar", "Militar", "Combate Naval", 537, 537)

    parse_ficha(mergulho, [p1, p2])
    parse_ficha(radar, [p2, p3])

    assert mergulho.secoes["infraestrutura"] == ["Piscina", "Tanques de mergulho"]
    assert "Radar" not in " ".join(sum(mergulho.secoes.values(), []))
    assert radar.secoes["perfil_profissional"] == [
        "O Técnico em Operação de Radar será habilitado para:",
        "- Operar o sistema de radares.",
    ]


def test_ids_repetidos_ganham_a_area() -> None:
    fichas = [
        FichaBruta("Técnico em Material Bélico", "Militar", "Combate Aéreo", 1, 1),
        FichaBruta("Técnico em Material Bélico", "Militar", "Combate Naval", 2, 2),
    ]
    for f in fichas:
        f.secoes = {"perfil_profissional": ["x"]}
    ids = [r["id"] for r in montar_registros(fichas, "e")]
    assert ids == [
        "tecnico-em-material-belico-combate-aereo",
        "tecnico-em-material-belico-combate-naval",
    ]


# ---- Utilidades ------------------------------------------------------------------------


def test_slug() -> None:
    assert slug("Técnico em Açúcar e Álcool") == "tecnico-em-acucar-e-alcool"
    assert (
        slug("Técnico em Turismo, Hospitalidade/Lazer") == "tecnico-em-turismo-hospitalidade-lazer"
    )


def test_juntar_linhas_preserva_itens() -> None:
    assert juntar_linhas(["- item um que", "continua", "- item dois."]) == (
        "- item um que continua\n- item dois."
    )


def test_linhas_como_itens_junta_continuacao() -> None:
    assert linhas_como_itens(["Indústrias com linhas,", "aeroespaciais", "Laboratórios"]) == (
        "Indústrias com linhas, aeroespaciais\nLaboratórios"
    )


def test_cbo_formatos() -> None:
    assert ocupacoes_cbo(["3171 - Técnicos de Desenvolvimento", "de Sistemas", "3172 - Outro"]) == [
        "3171 - Técnicos de Desenvolvimento de Sistemas",
        "3172 - Outro",
    ]
    assert ocupacoes_cbo(["351305 - Técnico em Administração"]) == [
        "3513-05 - Técnico em Administração"
    ]
    assert ocupacoes_cbo(["3115-05 Técnico em Controle de Meio Ambiente"]) == [
        "3115-05 Técnico em Controle de Meio Ambiente"
    ]
    assert ocupacoes_cbo(["Ocupação ainda não classificada"]) == []
    assert ocupacoes_cbo(["-"]) == []


def test_entrada_alternativa_em_csv(tmp_path) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from ingest_cnct import ler_csv, validar

    planilha = tmp_path / "cnct.csv"
    planilha.write_text(
        "id,nome,eixo,carga_horaria_min,perfil_profissional,ocupacoes_cbo\n"
        'tecnico-em-x,Técnico em X,Eixo X,1200,"Faz X e Y.","3171 - A; 3172 - B"\n',
        encoding="utf-8",
    )
    registros, _ = ler_csv(planilha, edicao="5ª edição (2026)")
    cursos, invalidos = validar(registros)
    assert invalidos == {}
    assert cursos[0].ocupacoes_cbo == ["3171 - A", "3172 - B"]
    assert cursos[0].edicao == "5ª edição (2026)"
    assert "Técnico em X" in cursos[0].texto_busca
