import pytest

from app.safety.crisis import detectar_crise
from app.safety.limites import LimitadorTaxa
from app.safety.moderation import FiltroSaida, entrada_impropria
from app.safety.pii import descrever_tipos, mascarar

# ---- Dados pessoais --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado", "tipos"),
    [
        ("meu cpf é 123.456.789-09", "meu cpf é [CPF]", ["CPF"]),
        ("cpf 12345678909", "cpf [CPF]", ["CPF"]),
        ("me liga (47) 99988-7766", "me liga [TELEFONE]", ["telefone"]),
        ("zap 47999887766", "zap [TELEFONE]", ["telefone"]),
        ("fixo +55 47 3370-1234", "fixo [TELEFONE]", ["telefone"]),
        ("ana.souza@gmail.com", "[EMAIL]", ["e-mail"]),
        ("me segue no @ana_souza", "me segue no [REDE SOCIAL]", ["perfil de rede social"]),
        ("CEP 89270-000", "CEP [CEP]", ["CEP"]),
        ("moro na Rua das Flores, 123", "moro na [ENDEREÇO]", ["endereço"]),
        ("Meu nome é Ana Paula da Silva", "Meu nome é [NOME]", ["nome"]),
        ("me chamo joão", "me chamo [NOME]", ["nome"]),
    ],
)
def test_mascara_dados_pessoais(texto: str, esperado: str, tipos: list[str]) -> None:
    resultado = mascarar(texto)
    assert resultado.texto == esperado
    assert resultado.tipos == tipos


@pytest.mark.parametrize(
    "texto",
    [
        "tenho 16 anos e estou no 2º ano",
        "o curso tem 1200 horas e dura 1 ano e meio",
        "em 2026 quero fazer o técnico",
        "tirei 10 em matemática e 9,5 em física",
        "gosto de jogar com 4 amigos às 19h",
    ],
)
def test_nao_mascara_numeros_comuns(texto: str) -> None:
    assert mascarar(texto).texto == texto


def test_descrever_tipos() -> None:
    assert descrever_tipos(["CPF"]) == "CPF"
    assert descrever_tipos(["CPF", "telefone", "nome"]) == "CPF, telefone e nome"


# ---- Crise -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "quero morrer",
        "às vezes penso em sumir",
        "tenho vontade de me matar",
        "não aguento mais",
        "eu me corto quando fico mal",
        "não vejo sentido na vida",
        "tô muito triste ultimamente",
        "acho que tenho depressão",
        "meu pai me bate",
        "Quero Morrer!!!",
    ],
)
def test_detecta_sinais_de_crise(texto: str) -> None:
    assert detectar_crise(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "to morrendo de rir",
        "morri de vergonha na apresentação",
        "tô mal em matemática",
        "gosto de matar a fome das pessoas cozinhando",
        "quero cuidar de pessoas",
        "curto jogos de tiro",
    ],
)
def test_nao_dispara_em_expressoes_comuns(texto: str) -> None:
    assert not detectar_crise(texto)


# ---- Moderação -------------------------------------------------------------------------


def test_entrada_impropria() -> None:
    assert entrada_impropria("me manda nudes")
    assert entrada_impropria("onde comprar maconha")
    assert not entrada_impropria("quero ajudar dependentes químicos")


def _filtrar(pedacos: list[str]) -> tuple[str, FiltroSaida]:
    filtro = FiltroSaida(["Técnico em Enfermagem", "Técnico em Informática para Internet"])
    saida = "".join(filtro.alimentar(p) for p in pedacos) + filtro.finalizar()
    return saida, filtro


def test_filtro_libera_frases_validas_mesmo_quebradas_em_pedacos() -> None:
    saida, filtro = _filtrar(["Que legal! O Técnico em Enf", "ermagem cuida de pessoas.\nE aí?"])
    assert saida == "Que legal! O Técnico em Enfermagem cuida de pessoas.\nE aí?"
    assert filtro.descartadas == []


def test_filtro_descarta_curso_inventado_dinheiro_e_link() -> None:
    saida, filtro = _filtrar(
        [
            "Olha só. O Técnico em Astronauta voa. Ganha R$ 3.000 por mês. ",
            "Veja em www.cursosbaratos.com. Mas o Técnico em Informática para Internet existe. ",
            "Ligue 188 ou veja cvv.org.br.",
        ]
    )
    assert saida == (
        "Olha só. Mas o Técnico em Informática para Internet existe. Ligue 188 ou veja cvv.org.br."
    )
    assert [d.split(":")[0] for d in filtro.descartadas] == [
        "curso fora do catálogo",
        "valor em dinheiro",
        "link não confiável",
    ]


def test_filtro_nao_libera_frase_incompleta_antes_do_fim() -> None:
    filtro = FiltroSaida([])
    assert filtro.alimentar("Uma frase sem ponto") == ""
    assert filtro.finalizar() == "Uma frase sem ponto"


# ---- Limites ---------------------------------------------------------------------------


def test_limitador_de_taxa_com_janela_deslizante() -> None:
    agora = [0.0]
    limite = LimitadorTaxa(maximo=3, janela_s=600, relogio=lambda: agora[0])
    assert [limite.permitir("s1") for _ in range(4)] == [True, True, True, False]
    assert limite.permitir("s2")  # outra sessão não é afetada
    agora[0] = 601
    assert limite.permitir("s1")
    limite.limpar_inativos()
