"""Operações no banco. Funções síncronas (as rotas as chamam fora do event loop)."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.profile import Perfil
from app.db import codigo as codigos
from app.db.models import (
    EventoAgregado,
    FormularioResposta,
    Participante,
    PerfilSalvo,
    RecomendacaoSalva,
    Turma,
    agora,
)


def _como_utc(momento: datetime) -> datetime:
    # O SQLite devolve datas sem fuso; tudo é gravado em UTC.
    return momento if momento.tzinfo else momento.replace(tzinfo=agora().tzinfo)


# ---- Participante e código -------------------------------------------------------------


def criar_participante(
    db: Session, chave: bytes, retencao_dias: int, turma_id: str | None = None
) -> tuple[Participante, str]:
    """Cria o participante e devolve o código (que só existe neste momento, em claro)."""
    for _ in range(20):  # colisão é rara (~1,2 milhão de combinações)
        codigo = codigos.gerar()
        participante = Participante(
            codigo_hash=codigos.hash_do_codigo(codigo, chave),
            turma_id=turma_id,
            expira_em=agora() + timedelta(days=retencao_dias),
        )
        db.add(participante)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue
        return participante, codigo
    raise RuntimeError("Não foi possível gerar um código único")


def buscar_por_codigo(db: Session, codigo_digitado: str, chave: bytes) -> Participante | None:
    codigo = codigos.normalizar(codigo_digitado)
    if codigo is None:
        return None
    participante = db.scalar(
        select(Participante).where(
            Participante.codigo_hash == codigos.hash_do_codigo(codigo, chave)
        )
    )
    if participante is None or _como_utc(participante.expira_em) < agora():
        return None
    return participante


def buscar_turma(db: Session, codigo_turma: str | None) -> Turma | None:
    if not codigo_turma:
        return None
    turma = db.scalar(select(Turma).where(Turma.codigo_turma == codigo_turma.strip().upper()))
    if turma is None or _como_utc(turma.expira_em) < agora():
        return None
    return turma


# ---- Formulário ------------------------------------------------------------------------


def salvar_respostas(db: Session, participante: Participante, respostas: dict[str, str]) -> None:
    existentes = {r.pergunta_id: r for r in participante.respostas}
    for pergunta_id, valor in respostas.items():
        if pergunta_id in existentes:
            existentes[pergunta_id].resposta = valor
        else:
            participante.respostas.append(
                FormularioResposta(pergunta_id=pergunta_id, resposta=valor)
            )


def respostas_de(participante: Participante) -> dict[str, str]:
    return {r.pergunta_id: r.resposta for r in participante.respostas}


# ---- Perfil e recomendação -------------------------------------------------------------


def salvar_perfil(db: Session, participante_id: str, perfil: Perfil) -> None:
    dados = perfil.model_dump(mode="json")
    salvo = db.get(PerfilSalvo, participante_id)
    if salvo is None:
        db.add(PerfilSalvo(participante_id=participante_id, json_perfil=dados))
    else:
        salvo.json_perfil = dados
        salvo.atualizado_em = agora()


def perfil_de(participante: Participante) -> Perfil | None:
    if participante.perfil is None:
        return None
    return Perfil.model_validate(participante.perfil.json_perfil)


def salvar_recomendacao(db: Session, participante_id: str, exibicao: dict[str, Any]) -> None:
    db.add(
        RecomendacaoSalva(
            participante_id=participante_id,
            cursos=[t["id"] for t in exibicao["trilhas"]],
            justificativas=exibicao,
        )
    )


def ultima_recomendacao(participante: Participante) -> dict[str, Any] | None:
    if not participante.recomendacoes:
        return None
    return participante.recomendacoes[-1].justificativas


# ---- Retenção (seção 4.3) --------------------------------------------------------------


def limpar_expirados(db: Session, retencao_dias: int, momento: datetime | None = None) -> int:
    """Apaga participantes, turmas e eventos vencidos. Devolve quantos registros saíram."""
    momento = momento or agora()
    total = 0
    for alvo in (
        delete(Participante).where(Participante.expira_em < momento),
        delete(EventoAgregado).where(
            EventoAgregado.criado_em < momento - timedelta(days=retencao_dias)
        ),
        delete(Turma).where(Turma.expira_em < momento),
    ):
        total += db.execute(alvo).rowcount or 0  # type: ignore[attr-defined]
    return total
