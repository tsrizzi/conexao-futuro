"""Operações no banco. Funções síncronas (as rotas as chamam fora do event loop)."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.profile import Perfil
from app.db import codigo as codigos
from app.db.models import (
    CodigoEntrada,
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


def criar_turma(
    db: Session, escola: str | None, nome: str, padlet_url: str | None, retencao_dias: int
) -> Turma:
    for _ in range(30):
        codigo = codigos.gerar_codigo_turma(escola, nome)
        if db.scalar(select(Turma.id).where(Turma.codigo_turma == codigo)) is None:
            turma = Turma(
                codigo_turma=codigo,
                escola=escola or None,
                padlet_url=padlet_url or None,
                expira_em=agora() + timedelta(days=retencao_dias),
            )
            db.add(turma)
            db.flush()
            return turma
    raise RuntimeError("Não foi possível gerar um código de turma único")


def turma_existe(db: Session, turma_id: str | None) -> bool:
    return turma_id is not None and db.get(Turma, turma_id) is not None


def turmas_ativas(db: Session) -> list[Turma]:
    turmas = db.scalars(select(Turma).order_by(Turma.criada_em.desc())).all()
    return [t for t in turmas if _como_utc(t.expira_em) >= agora()]


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


# ---- Eventos agregados e painel do professor (seções 4.3 e 5.5) -----------------------
# Nenhum evento guarda o participante: só turma, eixo, curso, tipo e tokens.


def registrar_trilhas(
    db: Session, turma_id: str, trilhas: list[dict[str, Any]], nova_participacao: bool
) -> None:
    if nova_participacao:
        db.add(EventoAgregado(turma_id=turma_id, tipo="participacao"))
    for t in trilhas:
        db.add(EventoAgregado(turma_id=turma_id, tipo="trilha", eixo=t["eixo"], curso_id=t["id"]))


def registrar_uso(db: Session, turma_id: str | None, entrada: int, saida: int) -> None:
    if entrada or saida:
        db.add(
            EventoAgregado(
                turma_id=turma_id, tipo="uso", tokens_entrada=entrada, tokens_saida=saida
            )
        )


def participacoes(db: Session, turma_ids: list[str]) -> dict[str, int]:
    if not turma_ids:
        return {}
    linhas = db.execute(
        select(EventoAgregado.turma_id, func.count())
        .where(EventoAgregado.tipo == "participacao", EventoAgregado.turma_id.in_(turma_ids))
        .group_by(EventoAgregado.turma_id)
    )
    return {turma_id: total for turma_id, total in linhas if turma_id is not None}


def tokens(db: Session, turma_id: str | None = None, todas: bool = False) -> tuple[int, int]:
    """Tokens de uma turma, ou de tudo (com e sem turma) quando todas=True."""
    consulta = select(
        func.coalesce(func.sum(EventoAgregado.tokens_entrada), 0),
        func.coalesce(func.sum(EventoAgregado.tokens_saida), 0),
    ).where(EventoAgregado.tipo == "uso")
    if not todas:
        consulta = consulta.where(EventoAgregado.turma_id == turma_id)
    entrada, saida = db.execute(consulta).one()
    return int(entrada or 0), int(saida or 0)


@dataclass
class PainelTurma:
    participantes: int
    detalhado: bool
    eixos: list[tuple[str, int]] = field(default_factory=list)
    cursos: list[tuple[str, int]] = field(default_factory=list)  # (curso_id, vezes)
    tokens_entrada: int = 0
    tokens_saida: int = 0


def painel_da_turma(
    db: Session, turma_id: str, minimo_detalhe: int, max_cursos: int = 10
) -> PainelTurma:
    """Só contagens. Abaixo do mínimo de participantes, nem eixos nem cursos (seção 8.8)."""
    participantes = participacoes(db, [turma_id]).get(turma_id, 0)
    entrada, saida = tokens(db, turma_id)
    painel = PainelTurma(participantes, participantes >= minimo_detalhe, [], [], entrada, saida)
    if not painel.detalhado:
        return painel
    trilhas = db.execute(
        select(EventoAgregado.eixo, EventoAgregado.curso_id).where(
            EventoAgregado.turma_id == turma_id, EventoAgregado.tipo == "trilha"
        )
    ).all()
    painel.eixos = Counter(e for e, _ in trilhas if e).most_common()
    painel.cursos = Counter(c for _, c in trilhas if c).most_common(max_cursos)
    return painel


# ---- Código de entrada no site ----------------------------------------------------------

Situacao = Literal["agendado", "ativo", "encerrado", "revogado"]


def criar_codigo_entrada(
    db: Session, chave: bytes, rotulo: str | None, inicio: datetime, fim: datetime
) -> tuple[CodigoEntrada, str]:
    """Cria o código e o devolve em claro (só existe neste momento)."""
    for _ in range(20):
        codigo = codigos.gerar_codigo_entrada()
        registro = CodigoEntrada(
            codigo_hash=codigos.hash_do_codigo_entrada(codigo, chave),
            final=codigo[-4:],
            rotulo=rotulo or None,
            inicio_em=inicio,
            fim_em=fim,
        )
        db.add(registro)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue
        return registro, codigo
    raise RuntimeError("Não foi possível gerar um código de entrada único")


def buscar_codigo_entrada(db: Session, texto: str, chave: bytes) -> CodigoEntrada | None:
    codigo = codigos.normalizar_codigo_entrada(texto)
    if codigo is None:
        return None
    return db.scalar(
        select(CodigoEntrada).where(
            CodigoEntrada.codigo_hash == codigos.hash_do_codigo_entrada(codigo, chave)
        )
    )


def situacao_codigo(c: CodigoEntrada, momento: datetime | None = None) -> Situacao:
    momento = momento or agora()
    if c.revogado:
        return "revogado"
    if momento < _como_utc(c.inicio_em):
        return "agendado"
    if momento >= _como_utc(c.fim_em):
        return "encerrado"
    return "ativo"


def listar_codigos_entrada(db: Session) -> list[CodigoEntrada]:
    return list(
        db.scalars(
            select(CodigoEntrada).order_by(CodigoEntrada.criado_em.desc(), CodigoEntrada.id.desc())
        )
    )


def revogar_codigo_entrada(db: Session, codigo_id: int) -> bool:
    registro = db.get(CodigoEntrada, codigo_id)
    if registro is None:
        return False
    registro.revogado = True
    return True


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
        delete(CodigoEntrada).where(CodigoEntrada.fim_em < momento - timedelta(days=retencao_dias)),
    ):
        total += db.execute(alvo).rowcount or 0  # type: ignore[attr-defined]
    return total
