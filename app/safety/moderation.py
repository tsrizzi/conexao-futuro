"""Moderação de entrada e de saída (seção 8.4).

Entrada: recusa pedidos explicitamente impróprios para menores, sem chamar o LLM.
Saída: o texto do LLM passa por um filtro frase a frase ANTES de ser exibido. Frases
com curso inexistente no catálogo, valores em reais, links não confiáveis ou palavrões
são descartadas. O streaming continua, só que por frases inteiras.
"""

import re
from collections.abc import Iterable

from app.safety.crisis import normalizar

# Só o explícito: o resto o LLM recusa pelo prompt do sistema.
_IMPROPRIO = re.compile(
    r"\b(?:porn\w*|nudes?|pelad[ao]s?|putaria|transar|sexo explicito|xvideos|onlyfans"
    r"|comprar (?:arma|droga|maconha|cocaina)|fazer (?:uma )?bomba)\b"
)

_PALAVROES = re.compile(
    r"\b(?:porra|caralho|merda|puta|foder|fodase|foda-se|buceta|viado|arrombad\w*|cuzao)\b"
)
_DINHEIRO = re.compile(r"R\$\s?\d|\b\d+(?:[.,]\d+)?\s?(?:reais|mil reais)\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|net|org)(?:\.br)?\b\S*", re.IGNORECASE)
DOMINIOS_CONFIAVEIS = ("cvv.org.br", "gov.br", "ifsc.edu.br", "sc.senai.br", "senai.br")
_CURSO = re.compile(r"\bT[eé]cnic[oa] em ((?:d[aeo]s? |e )?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^\n]{0,80})")
_FIM_FRASE = re.compile(r"(?<=[.!?…])\s+|\n+")


def entrada_impropria(texto: str) -> bool:
    return bool(_IMPROPRIO.search(normalizar(texto)))


class FiltroSaida:
    """Recebe o texto do LLM em pedaços e libera só frases completas e aprovadas."""

    def __init__(self, nomes_de_cursos: Iterable[str]) -> None:
        self._nomes = sorted({normalizar(n) for n in nomes_de_cursos}, key=len, reverse=True)
        self._buffer = ""
        self.descartadas: list[str] = []

    def alimentar(self, pedaco: str) -> str:
        self._buffer += pedaco
        partes = _FIM_FRASE.split(self._buffer)
        if len(partes) == 1:
            return ""
        # Preserva os separadores originais (espaço ou quebra de linha) entre frases.
        completas_ate = self._buffer.rfind(partes[-1]) if partes[-1] else len(self._buffer)
        completo, self._buffer = self._buffer[:completas_ate], self._buffer[completas_ate:]
        return self._aprovar(completo)

    def finalizar(self) -> str:
        resto, self._buffer = self._buffer, ""
        return self._aprovar(resto)

    def _aprovar(self, trecho: str) -> str:
        saida = []
        pedacos = re.split(r"((?<=[.!?…])\s+|\n+)", trecho)
        # Pares (frase, separador): ao descartar uma frase, o separador dela vai junto.
        for frase, separador in zip(pedacos[::2], [*pedacos[1::2], ""], strict=True):
            if frase.strip() and (motivo := self.motivo_bloqueio(frase)):
                self.descartadas.append(f"{motivo}: {frase.strip()}")
                continue
            saida.append(frase + separador)
        return "".join(saida)

    def motivo_bloqueio(self, frase: str) -> str | None:
        normal = normalizar(frase)
        if _PALAVROES.search(normal):
            return "palavrão"
        if _DINHEIRO.search(frase):
            return "valor em dinheiro"
        for url in _URL.findall(frase):
            if not any(d in url.lower() for d in DOMINIOS_CONFIAVEIS):
                return "link não confiável"
        for m in _CURSO.finditer(frase):
            candidato = normalizar(m.group(0))
            if not any(candidato.startswith(nome) for nome in self._nomes):
                return "curso fora do catálogo"
        return None
