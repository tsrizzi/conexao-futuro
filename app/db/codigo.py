"""Código de acesso PALAVRA-0000 (seção 4.4).

- Palavra curta em português, de uma lista curada e inofensiva, mais 4 dígitos.
- Sem diferença entre maiúsculas e minúsculas; aceita "onda 4827", "Onda4827", "ONDA-4827".
- No banco fica só o HMAC do código (com a SECRET_KEY): quem tiver uma cópia do banco não
  consegue descobrir os códigos, porque são poucas combinações sem a chave.
"""

import hashlib
import hmac
import re
import secrets

# Palavras curtas, fáceis de ler em voz alta, sem acento (fáceis de digitar no celular),
# sem duplo sentido e sem letras que se confundem com números.
PALAVRAS = (
    "ONDA", "TREM", "LUA", "PIXEL", "SOL", "RIO", "MAR", "NUVEM", "FAROL", "TERRA", "PEDRA",
    "FOLHA", "RAIZ", "PONTE", "TRILHA", "ROTA", "MAPA", "BUSSOLA", "VELA", "BARCO", "AVIAO",
    "FOGUETE", "ROBO", "CHIP", "CABO", "REDE", "SINAL", "RADIO", "TECLA", "TELA", "CANETA",
    "LAPIS", "PAPEL", "LIVRO", "PAGINA", "LETRA", "NOTA", "MUSICA", "VIOLAO", "TAMBOR", "PIANO",
    "FLAUTA", "DANCA", "PALCO", "CENA", "FILME", "FOTO", "COR", "TINTA", "PINCEL", "ARGILA",
    "VIDRO", "METAL", "MOTOR", "RODA", "PEDAL", "FREIO", "BICI", "SKATE", "PATINS", "BOLA",
    "GOL", "QUADRA", "PISTA", "CORRIDA", "SALTO", "TIME", "MEDALHA", "TROFEU", "APITO", "CAFE",
    "MILHO", "ARROZ", "FEIJAO", "PAO", "BOLO", "MEL", "LIMAO", "MANGA", "UVA", "CAJU", "MORANGO",
    "PERA", "AMORA", "FLOR", "IPE", "PINHAO", "BAMBU", "CACTO", "MUSGO", "TUCANO", "ARARA",
    "SABIA", "CORUJA", "PANDA", "LONTRA", "BALEIA", "GOLFINHO", "TATU", "CUTIA", "VENTO",
    "CHUVA", "NEVE", "RAIO", "ESTRELA", "COMETA", "PLANETA", "GALAXIA", "ORBITA", "ECLIPSE",
    "PRISMA", "CRISTAL", "IMA", "MOLA", "ENGRENAGEM", "ALAVANCA", "POLIA", "BOBINA", "LED",
    "BATERIA",
)  # fmt: skip

_RE_CODIGO = re.compile(r"^([A-Z]{2,12})[\s\-_.]*(\d{4})$")


def gerar() -> str:
    return f"{secrets.choice(PALAVRAS)}-{secrets.randbelow(10_000):04d}"


def normalizar(texto: str) -> str | None:
    """Forma canônica PALAVRA-0000, ou None se não tiver o formato de um código."""
    m = _RE_CODIGO.match(texto.strip().upper())
    if not m or m[1] not in PALAVRAS:
        return None
    return f"{m[1]}-{m[2]}"


def hash_do_codigo(codigo: str, chave: bytes) -> str:
    return hmac.new(chave, codigo.encode(), hashlib.sha256).hexdigest()
