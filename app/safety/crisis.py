"""Detecção de sofrimento emocional por palavras-chave (seção 8.3).

É a primeira barreira, sem custo e sem latência. A segunda é o sinal
`sofrimento_emocional` que o LLM devolve na extração. Na dúvida, acolhe: um falso
positivo custa uma mensagem de cuidado; um falso negativo pode custar muito mais.
Esses eventos não são gravados em lugar nenhum.
"""

import re

from app.rag.ingestao import sem_acentos

# Expressões do dia a dia que NÃO são crise ("morrendo de rir", "me mata de vergonha").
_EXCECOES = re.compile(
    r"\b(?:morr\w*|mat\w*)\s+(?:de|do|da)\s+"
    r"(?:rir|risada|vergonha|fome|sono|calor|frio|tedio|preguica|saudade|inveja|cansaco|curiosidade|estudar)"
)

_SINAIS = [
    # ideação suicida
    r"\b(?:quero|vou|vontade de|penso em|pensando em|queria|preferia|melhor)\s+"
    r"(?:me\s+)?(?:morrer|sumir|desaparecer|nao existir|me matar)",
    r"\bme matar\b",
    r"\bsuicid",
    r"\btirar (?:a )?minha (?:propria )?vida",
    r"\bacabar com (?:a )?minha vida",
    r"\bacabar com tudo\b",
    r"\bnao (?:quero|aguento) mais viver",
    r"\bnao (?:vejo|tem) (?:mais )?sentido (?:na|em|de) (?:vida|viver)",
    r"\bdesist\w* de (?:tudo|viver|mim)",
    r"\bnao queria ter nascido",
    # autolesão
    r"\bme (?:cortar|cortando|cortei|corto|machucar|machucando|machuquei|machuco)\b",
    r"\bautomutil",
    r"\bauto ?lesao",
    r"\bme (?:ferir|ferindo)\b",
    # desesperança / sofrimento intenso
    r"\bnao aguento mais\b",
    r"\bsem saida\b",
    r"\bme odeio\b",
    r"\bninguem (?:liga|se importa) (?:pra|para|comigo|com mim)",
    # "tô mal" sim; "tô mal em matemática" não.
    r"\b(?:to|tou|estou|ando|me sinto) (?:muito |super |tao )?"
    r"(?:mal|triste|deprimid\w*|vazi\w*|sem esperanca)\b"
    r"(?! (?:em|de|com|na|no) (?:matematica|prova|nota|portugues|fisica|quimica))",
    r"\bdepress",
    r"\bcrise de ansiedade",
    # violência e abuso
    r"\bme (?:bate|batem|espanca\w*)\b",
    r"\b(?:sofro|sofrendo) (?:bullying|abuso|violencia)",
    r"\babus(?:o|aram|ou|ando) de mim",
    r"\bviolencia (?:em|dentro de) casa",
]
_RE_SINAIS = re.compile("|".join(f"(?:{s})" for s in _SINAIS))


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", sem_acentos(texto).lower())


def detectar_crise(texto: str) -> bool:
    limpo = _EXCECOES.sub(" ", normalizar(texto))
    return bool(_RE_SINAIS.search(limpo))
