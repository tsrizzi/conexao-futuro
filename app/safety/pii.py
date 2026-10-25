"""Mascaramento de dados pessoais ANTES de qualquer envio ao LLM (seção 8.2).

Nada aqui é gravado: o texto mascarado só segue para o LLM e para o histórico da sessão.
"""

import re
from dataclasses import dataclass, field

_FLAGS = re.IGNORECASE


@dataclass(frozen=True)
class _Padrao:
    tipo: str  # como o tipo aparece no aviso ao estudante
    marcador: str
    regex: re.Pattern[str]
    grupo: int = 0  # 0 = mascara o trecho inteiro; n = só o grupo n (ex.: o nome)


# A ordem importa: e-mail antes de @perfil; CEP e CPF antes de telefone.
_PADROES = [
    _Padrao("e-mail", "[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _FLAGS)),
    _Padrao("CPF", "[CPF]", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-?\d{2}\b")),
    _Padrao("CEP", "[CEP]", re.compile(r"\b\d{5}-\d{3}\b")),
    _Padrao(
        "telefone",
        "[TELEFONE]",
        re.compile(r"(?<![\w+])(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?9?\d{4}[\s.-]?\d{4}\b"),
    ),
    # 11 dígitos seguidos que não parecem celular (DDD + 9): CPF sem pontuação.
    _Padrao("CPF", "[CPF]", re.compile(r"\b\d{11}\b")),
    _Padrao(
        "endereço",
        "[ENDEREÇO]",
        re.compile(
            r"\b(?:rua|r\.|avenida|av\.|travessa|rodovia|estrada|alameda|servidão)"
            r"\s+[^\d,.\n]{2,40}?,?\s*(?:n[º°o.]?\s*)?\d{1,5}\b",
            _FLAGS,
        ),
    ),
    _Padrao(
        "nome",
        "[NOME]",
        re.compile(
            r"\b(?:meu nome é|meu nome e|me chamo|pode me chamar de|meu nome:)\s+"
            r"([A-Za-zÀ-ÿ]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]+)*)",
            _FLAGS,
        ),
        grupo=1,
    ),
    _Padrao(
        "perfil de rede social",
        "[REDE SOCIAL]",
        re.compile(r"(?<![\w.@\[])@[A-Za-z0-9_.]{3,30}"),
    ),
]


@dataclass
class ResultadoPII:
    texto: str
    tipos: list[str] = field(default_factory=list)

    @property
    def encontrou(self) -> bool:
        return bool(self.tipos)


def mascarar(texto: str) -> ResultadoPII:
    tipos: list[str] = []
    for padrao in _PADROES:

        def trocar(m: re.Match[str], p: _Padrao = padrao) -> str:
            if p.tipo not in tipos:
                tipos.append(p.tipo)
            if p.grupo:
                inicio, fim = m.span(p.grupo)
                return m.group(0)[: inicio - m.start()] + p.marcador + m.group(0)[fim - m.start() :]
            return p.marcador

        texto = padrao.regex.sub(trocar, texto)
    return ResultadoPII(texto, tipos)


def descrever_tipos(tipos: list[str]) -> str:
    """ "CPF, telefone e nome" — para o aviso gentil ao estudante."""
    if len(tipos) <= 1:
        return "".join(tipos)
    return ", ".join(tipos[:-1]) + " e " + tipos[-1]
