"""Tabelas (seção 4.3). NENHUM dado pessoal: sem nome, CPF, e-mail, telefone, gênero, idade.

O texto das conversas nunca é gravado. Fica só o perfil estruturado, a recomendação e as
respostas do formulário (as abertas já com dados pessoais mascarados).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def agora() -> datetime:
    return datetime.now(UTC)


def novo_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Turma(Base):
    __tablename__ = "turma"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    codigo_turma: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    escola: Mapped[str | None] = mapped_column(String(120))
    padlet_url: Mapped[str | None] = mapped_column(String(300))
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Participante(Base):
    __tablename__ = "participante"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    # HMAC do código de acesso (o código em si não é guardado).
    codigo_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    turma_id: Mapped[str | None] = mapped_column(ForeignKey("turma.id", ondelete="SET NULL"))
    formulario_completo: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    respostas: Mapped[list["FormularioResposta"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    perfil: Mapped["PerfilSalvo | None"] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    recomendacoes: Mapped[list["RecomendacaoSalva"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="RecomendacaoSalva.id"
    )


class FormularioResposta(Base):
    __tablename__ = "formulario_resposta"
    __table_args__ = (UniqueConstraint("participante_id", "pergunta_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participante_id: Mapped[str] = mapped_column(
        ForeignKey("participante.id", ondelete="CASCADE"), index=True
    )
    pergunta_id: Mapped[str] = mapped_column(String(40))
    resposta: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class PerfilSalvo(Base):
    __tablename__ = "perfil"

    participante_id: Mapped[str] = mapped_column(
        ForeignKey("participante.id", ondelete="CASCADE"), primary_key=True
    )
    json_perfil: Mapped[dict[str, Any]] = mapped_column(JSON)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, onupdate=agora
    )


class RecomendacaoSalva(Base):
    __tablename__ = "recomendacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participante_id: Mapped[str] = mapped_column(
        ForeignKey("participante.id", ondelete="CASCADE"), index=True
    )
    # Ids dos cursos (para o painel) e o resultado pronto para exibir (porquês incluídos).
    cursos: Mapped[list[str]] = mapped_column(JSON)
    justificativas: Mapped[dict[str, Any]] = mapped_column(JSON)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class EventoAgregado(Base):
    """Painel do professor: sem nenhuma ligação com o participante (seção 4.3)."""

    __tablename__ = "evento_agregado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    turma_id: Mapped[str | None] = mapped_column(
        ForeignKey("turma.id", ondelete="CASCADE"), index=True
    )
    eixo: Mapped[str | None] = mapped_column(String(80))
    curso_id: Mapped[str | None] = mapped_column(String(120))
    # "participacao" (1 por conversa que chegou às trilhas), "trilha" (1 por curso sugerido)
    # ou "uso" (tokens gastos numa resposta; também sem turma, para o custo total).
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    tokens_entrada: Mapped[int | None] = mapped_column(Integer)
    tokens_saida: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora, index=True)
