"""IP do cliente, para os limites de tentativa (seções 4.4 e 8.5).

Atrás de um proxy (Cloud Run, por exemplo), `request.client.host` é o IP do proxy, e todo
mundo pareceria a mesma pessoa. Cada proxy confiável acrescenta o IP que viu no fim do
cabeçalho X-Forwarded-For; o que vem antes pode ter sido inventado pelo próprio cliente.
Por isso, com PROXY_SALTOS=N, o IP real é o N-ésimo a partir da direita.
"""

from fastapi import Request


def ip_do_cliente(request: Request) -> str:
    direto = request.client.host if request.client else "desconhecido"
    saltos: int = request.app.state.settings.proxy_saltos
    if saltos <= 0:
        return direto
    cadeia = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
    cadeia = [p for p in cadeia if p]
    return cadeia[-saltos] if len(cadeia) >= saltos else direto
