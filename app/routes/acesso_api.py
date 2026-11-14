"""API do formulário de apoio e do código de acesso (seções 4.4 e 7.4.3).

Rotas síncronas: o FastAPI as roda num pool de threads, sem travar o chat.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.formulario import (
    carregar_formulario,
    perfil_do_formulario,
    validar_respostas,
)
from app.agent.prompts import carregar
from app.db import repositorio
from app.db.models import Participante
from app.safety.crisis import detectar_crise
from app.safety.pii import mascarar
from app.web.entrada import exigir_entrada
from app.web.rede import ip_do_cliente

router = APIRouter(prefix="/api")

MENSAGEM_CODIGO_INVALIDO = (
    "Não encontrei esse código. Confira se digitou certinho (ex.: ONDA-4827). "
    "Os códigos valem por 60 dias."
)
MENSAGEM_TENTATIVAS = (
    "Muitas tentativas seguidas. Por segurança, espere uns minutos antes de tentar de novo."
)


class PedidoFormulario(BaseModel):
    respostas: dict[str, str] = Field(default_factory=dict, max_length=40)
    completo: bool = False
    codigo: str | None = Field(default=None, max_length=30)
    codigo_turma: str | None = Field(default=None, max_length=20)


class PedidoCodigo(BaseModel):
    codigo: str = Field(max_length=30)


def ip(request: Request) -> str:
    return ip_do_cliente(request)


def participante_do_codigo(request: Request, db: Session, codigo: str) -> Participante:
    """Busca pelo código, com proteção contra força bruta (seção 4.4).

    Só as tentativas ERRADAS contam: no máximo 10 por IP a cada 10 min. A turma inteira
    costuma sair pelo mesmo IP da escola, e quem digita o código certo não pode gastar o
    limite dos colegas. Para quem tenta adivinhar, quase toda tentativa é errada.
    """
    limite = request.app.state.limite_codigo
    chave = ip(request)
    if limite.bloqueado(chave):
        raise HTTPException(status_code=429, detail=MENSAGEM_TENTATIVAS)
    participante = repositorio.buscar_por_codigo(db, codigo, request.app.state.chave)
    if participante is None:
        limite.registrar(chave)
        raise HTTPException(status_code=404, detail=MENSAGEM_CODIGO_INVALIDO)
    return participante


def _limpar_abertas(respostas: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Mascara dados pessoais; resposta com sinal de crise não é gravada."""
    formulario = carregar_formulario()
    limpas: dict[str, str] = {}
    crise = False
    for pergunta_id, valor in respostas.items():
        pergunta = formulario.get(pergunta_id)
        if pergunta is not None and pergunta.tipo == "aberta":
            valor = mascarar(valor).texto
            if detectar_crise(valor):
                crise = True
                continue
        limpas[pergunta_id] = valor
    return limpas, crise


@router.get("/formulario/perguntas")
def perguntas() -> dict[str, Any]:
    return carregar_formulario().model_dump()


@router.post("/formulario")
def salvar_formulario(request: Request, pedido: PedidoFormulario) -> dict[str, Any]:
    exigir_entrada(request)
    respostas, crise = _limpar_abertas(validar_respostas(pedido.respostas))
    estado = request.app.state
    with estado.sessoes.begin() as db:
        codigo_novo = None
        if pedido.codigo:
            participante = participante_do_codigo(request, db, pedido.codigo)
        else:
            if not estado.limite_criacao.permitir(ip(request)):
                raise HTTPException(status_code=429, detail=MENSAGEM_TENTATIVAS)
            turma = repositorio.buscar_turma(db, pedido.codigo_turma)
            participante, codigo_novo = repositorio.criar_participante(
                db,
                estado.chave,
                estado.settings.retencao_dias,
                turma_id=turma.id if turma else None,
            )
        repositorio.salvar_respostas(db, participante, respostas)
        db.flush()
        todas = repositorio.respostas_de(participante)
        participante.formulario_completo = participante.formulario_completo or pedido.completo
        repositorio.salvar_perfil(db, participante.id, perfil_do_formulario(todas))
        total = len(carregar_formulario().perguntas)
        return {
            "codigo": codigo_novo,
            "respondidas": len(todas),
            "total": total,
            "formulario_completo": participante.formulario_completo,
            "acolhimento": carregar("msg_acolhimento") if crise else None,
        }


@router.post("/codigo")
def entrar_com_codigo(request: Request, pedido: PedidoCodigo) -> dict[str, Any]:
    exigir_entrada(request)
    with request.app.state.sessoes.begin() as db:
        participante = participante_do_codigo(request, db, pedido.codigo)
        respostas = repositorio.respostas_de(participante)
        return {
            "respostas": respostas,
            "respondidas": len(respostas),
            "total": len(carregar_formulario().perguntas),
            "formulario_completo": participante.formulario_completo,
            "tem_trilhas": bool(participante.recomendacoes),
        }
