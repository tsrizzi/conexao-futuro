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
import unicodedata

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


# ---- Código da turma (ex.: GUA-2B-7K) ---------------------------------------------------

# Sem 0/O, 1/I/L e 5/S: o código vai para o quadro e é copiado à mão.
_ALFABETO_TURMA = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
# Palavras que não ajudam a reconhecer a escola ("EEB Prof. Fulano" vira FUL).
_PALAVRAS_GENERICAS = {
    "E", "EE", "EEB", "EEF", "EEM", "EEEM", "EMEF", "CE", "IF", "IFSC", "ESCOLA", "ESTADUAL",
    "MUNICIPAL", "COLEGIO", "CENTRO", "EDUCACIONAL", "INSTITUTO", "FEDERAL", "ENSINO",
    "MEDIO", "BASICA", "DE", "DA", "DO", "DAS", "DOS", "PROF", "PROFA", "PROFESSOR",
    "PROFESSORA", "DR", "DONA", "EDUCACAO", "FUNDAMENTAL", "TECNICO", "TECNICA", "SENAI", "SESI",
}  # fmt: skip
_RE_CODIGO_TURMA = re.compile(r"^[A-Z]{2,3}-[A-Z0-9]{1,4}-[A-Z0-9]{2}$")


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    ).upper()


def prefixo_escola(escola: str | None) -> str:
    palavras = re.findall(r"[A-Z]+", _sem_acento(escola or ""))
    for palavra in palavras:
        if palavra not in _PALAVRAS_GENERICAS and len(palavra) >= 3:
            return palavra[:3]
    return "CF"


def nome_da_turma(turma: str) -> str | None:
    """'2º B' -> '2B'. Só letras e números, até 4 caracteres."""
    limpo = re.sub(r"[^A-Z0-9]", "", _sem_acento(turma.replace("º", "").replace("ª", "")))
    return limpo[:4] or None


def gerar_codigo_turma(escola: str | None, turma: str) -> str:
    nome = nome_da_turma(turma) or "T"
    sufixo = "".join(secrets.choice(_ALFABETO_TURMA) for _ in range(2))
    return f"{prefixo_escola(escola)}-{nome}-{sufixo}"


def codigo_turma_valido(texto: str) -> bool:
    return bool(_RE_CODIGO_TURMA.match(texto.strip().upper()))


# ---- Código de entrada no site (ex.: K7MQ-3XPT) ------------------------------------------
# Gerado pelo admin; quem tem um código válido usa o site. Mesmo alfabeto sem ambiguidade
# do código da turma. No banco vai o HMAC de "entrada:" + código (domínio separado do
# código do estudante).

_RE_CODIGO_ENTRADA = re.compile(rf"^[{_ALFABETO_TURMA}]{{8}}$")


def gerar_codigo_entrada() -> str:
    letras = "".join(secrets.choice(_ALFABETO_TURMA) for _ in range(8))
    return f"{letras[:4]}-{letras[4:]}"


def normalizar_codigo_entrada(texto: str) -> str | None:
    """Forma canônica XXXX-XXXX, ou None. Aceita minúsculas, espaços, pontos e sem hífen."""
    limpo = re.sub(r"[\s\-_.]", "", texto).upper()
    if not _RE_CODIGO_ENTRADA.match(limpo):
        return None
    return f"{limpo[:4]}-{limpo[4:]}"


def hash_do_codigo_entrada(codigo: str, chave: bytes) -> str:
    return hash_do_codigo("entrada:" + codigo, chave)
