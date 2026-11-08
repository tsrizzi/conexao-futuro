"""Markdown mínimo -> HTML seguro, para páginas de texto como o aviso de privacidade.

Suporta só o que esses documentos usam: títulos (#, ##, ###), parágrafos, listas com "-",
**negrito** e [links](https://...). Todo o texto é escapado antes.
"""

import re
from html import escape

from markupsafe import Markup

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)")
_NEGRITO = re.compile(r"\*\*(.+?)\*\*")


def _inline(texto: str) -> str:
    texto = escape(texto, quote=False)
    texto = _NEGRITO.sub(r"<strong>\1</strong>", texto)
    return _LINK.sub(r'<a href="\2">\1</a>', texto)


def para_html(markdown: str) -> Markup:
    blocos: list[str] = []
    lista: list[str] = []
    paragrafo: list[str] = []

    def fechar() -> None:
        if paragrafo:
            blocos.append(f"<p>{_inline(' '.join(paragrafo))}</p>")
            paragrafo.clear()
        if lista:
            blocos.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in lista) + "</ul>")
            lista.clear()

    for linha in markdown.splitlines():
        linha = linha.rstrip()
        if not linha:
            fechar()
        elif m := re.match(r"^(#{1,3})\s+(.*)", linha):
            fechar()
            nivel = len(m[1])
            blocos.append(f"<h{nivel}>{_inline(m[2])}</h{nivel}>")
        elif linha.startswith("- "):
            if paragrafo:
                fechar()
            lista.append(linha[2:])
        elif lista and linha.startswith("  "):
            lista[-1] += " " + linha.strip()
        else:
            if lista:
                fechar()
            paragrafo.append(linha.strip())
    fechar()
    return Markup("\n".join(blocos))
