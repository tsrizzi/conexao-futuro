"""Orquestrador da conversa (seção 6.1).

BOAS_VINDAS -> (formulário?) -> DESCOBERTA -> CONFIRMA_PERFIL -> RECOMENDA -> EXPLORA ⇄ RECOMENDA
e, a partir de qualquer estado, ACOLHIMENTO quando há sinal de sofrimento emocional.

Para cada mensagem do estudante:
1. limites e filtros sem custo: tamanho, dados pessoais (mascarados), crise por
   palavra-chave, conteúdo impróprio;
2. uma chamada de EXTRAÇÃO (saída estruturada): perfil atualizado + sinais;
3. a transição de estado é decidida pelo CÓDIGO, a partir dos sinais e da cobertura;
4. a resposta é gerada em streaming e passa pelo filtro de saída antes de ser exibida.

A sessão viaja assinada para o navegador (app/web/token.py). Nada do texto da conversa é
gravado no servidor.
"""

import asyncio
import logging
import re
import secrets
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.ofertas import Ofertas
from app.agent.profile import (
    Perfil,
    eixos_validados,
    exemplos_soutec,
    nomes_dimensoes,
)
from app.agent.prompts import carregar
from app.agent.recommender import Buscador, Recomendacao, Recomendador, ficha_para_llm
from app.llm.base import (
    MENSAGEM_SOBRECARGA,
    LLMErro,
    LLMIndisponivel,
    LLMProvider,
    Mensagem,
    Uso,
)
from app.rag.catalog import Catalogo, Curso
from app.safety.crisis import detectar_crise, normalizar
from app.safety.limites import MAX_CARACTERES
from app.safety.moderation import FiltroSaida, entrada_impropria
from app.safety.pii import descrever_tipos, mascarar

log = logging.getLogger(__name__)

TROCAS_MIN = 6  # seção 6.1: de 6 a 12 trocas na descoberta
TROCAS_MAX = 12
MAX_CONFIRMACOES = 2
MAX_TOKENS_RESPOSTA = 600
MAX_TOKENS_EXTRACAO = 1500
HISTORICO_RESPOSTA = 16  # mensagens enviadas ao gerar a resposta
HISTORICO_EXTRACAO = 6  # mensagens enviadas na extração (o perfil já resume o resto)


class Estado(StrEnum):
    BOAS_VINDAS = "boas_vindas"
    DESCOBERTA = "descoberta"
    CONFIRMA_PERFIL = "confirma_perfil"
    RECOMENDA = "recomenda"
    EXPLORA = "explora"
    ACOLHIMENTO = "acolhimento"


DESCRICAO_ESTADO = {
    Estado.DESCOBERTA: "descoberta (o agente está conhecendo o estudante)",
    Estado.CONFIRMA_PERFIL: (
        "confirmação do perfil (o agente acabou de resumir o perfil e perguntou se está certo)"
    ),
    Estado.EXPLORA: "exploração (o estudante já recebeu sugestões de cursos)",
    Estado.ACOLHIMENTO: "acolhimento (o estudante passou por um momento difícil)",
}


# ---- Saída da extração -----------------------------------------------------------------


class Sinais(BaseModel):
    sofrimento_emocional: bool = False
    quer_sugestoes: bool = False
    confirmou_perfil: bool | None = None
    quer_refazer: bool = False
    quer_mais_sugestoes: bool = False
    quer_voltar_orientacao: bool = False
    pergunta_sobre_curso: bool = False
    fora_do_escopo: bool = False


class Analise(BaseModel):
    perfil: Perfil
    sinais: Sinais = Sinais()


# ---- Sessão e eventos ------------------------------------------------------------------


class MensagemSessao(BaseModel):
    papel: Literal["user", "assistant"]
    conteudo: str


class Sessao(BaseModel):
    # Identificador aleatório (limite de taxa). Não identifica a pessoa e não é gravado.
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(12))
    # Quem entrou com código: o perfil e as trilhas são salvos para essa pessoa (Fase 4).
    participante_id: str | None = None
    estado: Estado = Estado.BOAS_VINDAS
    perfil: Perfil = Perfil()
    historico: list[MensagemSessao] = []  # já mascarado; fica só no navegador (token)
    trocas_descoberta: int = 0
    confirmacoes: int = 0
    sugeridos: list[str] = []
    estado_antes_acolhimento: Estado | None = None
    recomendacao: Recomendacao | None = None
    uso: Uso = Uso()


@dataclass(frozen=True)
class EventoTexto:
    texto: str


@dataclass(frozen=True)
class EventoOpcoes:
    opcoes: list[str]


@dataclass(frozen=True)
class EventoProgresso:
    cobertura: float

    @property
    def percentual(self) -> int:
        return round(self.cobertura * 100)


@dataclass(frozen=True)
class EventoRecomendacao:
    recomendacao: Recomendacao


@dataclass(frozen=True)
class EventoAviso:
    texto: str


@dataclass(frozen=True)
class EventoErro:
    texto: str


@dataclass(frozen=True)
class EventoEstado:
    estado: Estado


Evento = (
    EventoTexto
    | EventoOpcoes
    | EventoProgresso
    | EventoRecomendacao
    | EventoAviso
    | EventoErro
    | EventoEstado
)


# ---- Botões de resposta rápida ---------------------------------------------------------

_RE_OPCOES = re.compile(r"\[\[\s*op[cç][oõ]es\s*:(.*?)\]\]", re.IGNORECASE | re.DOTALL)


@dataclass
class SeparadorOpcoes:
    """Tira do texto a linha `[[opcoes: a | b]]`, que vira botões e não é exibida."""

    _buffer: str = ""
    opcoes: list[str] = field(default_factory=list)

    def alimentar(self, pedaco: str) -> str:
        self._buffer += pedaco
        for m in _RE_OPCOES.finditer(self._buffer):
            self.opcoes = [o.strip()[:40] for o in m.group(1).split("|") if o.strip()][:4]
        self._buffer = _RE_OPCOES.sub("", self._buffer)
        inicio = self._buffer.find("[[")
        if inicio == -1:
            # Um "[" solto no fim pode ser o começo do marcador: segura até o próximo pedaço.
            corte = len(self._buffer) - 1 if self._buffer.endswith("[") else len(self._buffer)
        else:
            corte = inicio
        livre, self._buffer = self._buffer[:corte], self._buffer[corte:]
        return livre

    def finalizar(self) -> str:
        resto, self._buffer = self._buffer, ""
        return "" if resto.startswith("[[") else resto  # marcador incompleto é descartado


# ---- Orquestrador ----------------------------------------------------------------------


class Orquestrador:
    def __init__(
        self,
        llm: LLMProvider,
        catalogo: Catalogo,
        buscador: Buscador,
        ofertas: Ofertas,
        *,
        cobertura_minima: float = 0.7,
        eixos_fora_da_busca: Iterable[str] = ("Militar",),
    ) -> None:
        self._llm = llm
        self._catalogo = catalogo
        self._buscador = buscador
        self._sistema = carregar("sistema")
        self._cobertura_minima = cobertura_minima
        self._eixos_validos = set(catalogo.eixos) - set(eixos_fora_da_busca)
        self._nomes_cursos = [c.nome for c in catalogo.cursos]
        self._recomendador = Recomendador(
            llm,
            buscador,
            catalogo,
            ofertas,
            sistema=self._sistema,
            cobertura_minima=cobertura_minima,
        )

    @property
    def catalogo(self) -> Catalogo:
        return self._catalogo

    @property
    def total_cursos_sugeriveis(self) -> int:
        return sum(1 for c in self._catalogo.cursos if c.eixo in self._eixos_validos)

    # ---- Entrada -----------------------------------------------------------------------

    async def iniciar(
        self,
        sessao: Sessao,
        perfil_formulario: Perfil | None = None,
        resumo_formulario: str = "",
    ) -> AsyncIterator[Evento]:
        """Primeira mensagem: sempre avisa que é uma IA (regra 4)."""
        if sessao.estado != Estado.BOAS_VINDAS:
            return
        sessao.estado = Estado.DESCOBERTA
        yield EventoEstado(sessao.estado)
        if perfil_formulario is None:
            texto = carregar("msg_boas_vindas")
            sessao.historico.append(MensagemSessao(papel="assistant", conteudo=texto))
            yield EventoTexto(texto)
            yield EventoProgresso(sessao.perfil.cobertura)
            return
        # CARREGA_FORMULARIO: parte do que o formulário já revelou.
        perfil_formulario.eixos_provaveis = eixos_validados(perfil_formulario, self._eixos_validos)
        perfil_formulario.calcular_cobertura()
        sessao.perfil = perfil_formulario
        # "Pula as perguntas já respondidas": com o formulário, bastam ~3 trocas a mais.
        if perfil_formulario.cobertura >= 0.5:
            sessao.trocas_descoberta = TROCAS_MIN - 3
        intro = carregar("msg_boas_vindas_formulario")
        sessao.historico.append(MensagemSessao(papel="assistant", conteudo=intro))
        yield EventoTexto(intro + "\n\n")
        yield EventoProgresso(sessao.perfil.cobertura)
        instrucoes = carregar(
            "estado_formulario",
            resumo=resumo_formulario or "(sem resumo)",
            lacunas=self._lista(sessao.perfil.lacunas()[:4]),
            perfil=sessao.perfil.para_prompt(),
        )
        async for evento in self._responder_llm(sessao, instrucoes, pedido="Vamos começar."):
            yield evento

    async def responder(self, sessao: Sessao, texto: str) -> AsyncIterator[Evento]:
        texto = texto.strip()
        if not texto:
            return
        if sessao.estado == Estado.BOAS_VINDAS:
            sessao.estado = Estado.DESCOBERTA
        if len(texto) > MAX_CARACTERES:
            yield EventoAviso(carregar("msg_mensagem_longa", limite=MAX_CARACTERES))
            return

        pii = mascarar(texto)
        if pii.encontrou:
            yield EventoAviso(carregar("msg_dados_pessoais", tipos=descrever_tipos(pii.tipos)))
        texto = pii.texto

        if detectar_crise(texto):
            sessao.historico.append(MensagemSessao(papel="user", conteudo=texto))
            async for evento in self._acolher(sessao):
                yield evento
            return
        if entrada_impropria(texto):
            yield EventoTexto(carregar("msg_improprio"))  # o texto impróprio não vai ao LLM
            return

        sessao.historico.append(MensagemSessao(papel="user", conteudo=texto))
        try:
            analise = await self._analisar(sessao)
        except LLMIndisponivel:
            sessao.historico.pop()
            yield EventoErro(MENSAGEM_SOBRECARGA)
            return
        except LLMErro as erro:
            log.warning("Extração falhou; segue sem atualizar o perfil: %s", type(erro).__name__)
            analise = Analise(perfil=sessao.perfil)
        sinais = analise.sinais

        if sinais.sofrimento_emocional:
            async for evento in self._acolher(sessao):
                yield evento
            return
        if sessao.estado != Estado.ACOLHIMENTO:
            # Em acolhimento, nada do que foi dito entra no perfil.
            self._atualizar_perfil(sessao, analise.perfil)
            yield EventoProgresso(sessao.perfil.cobertura)

        perguntou_de_curso = sinais.pergunta_sobre_curso and not (
            sinais.quer_sugestoes or sinais.quer_mais_sugestoes or sinais.quer_refazer
        )
        if perguntou_de_curso and sessao.estado in (Estado.DESCOBERTA, Estado.CONFIRMA_PERFIL):
            # Dúvida sobre curso antes da hora: responde com as fichas do CNCT (nunca de
            # memória) e continua na mesma etapa.
            instrucoes = await self._instrucoes_explora(sessao, texto)
            async for evento in self._responder_llm(sessao, instrucoes):
                yield evento
            return

        match sessao.estado:
            case Estado.DESCOBERTA:
                fluxo = self._descoberta(sessao, sinais)
            case Estado.CONFIRMA_PERFIL:
                fluxo = self._confirmacao(sessao, sinais)
            case Estado.EXPLORA | Estado.RECOMENDA:
                fluxo = self._exploracao(sessao, sinais, texto)
            case Estado.ACOLHIMENTO:
                fluxo = self._acolhimento(sessao, sinais, texto)
            case _:
                fluxo = self._descoberta(sessao, sinais)
        async for evento in fluxo:
            yield evento

    # ---- Estados -----------------------------------------------------------------------

    async def _descoberta(self, sessao: Sessao, sinais: Sinais) -> AsyncIterator[Evento]:
        sessao.trocas_descoberta += 1
        if sinais.quer_sugestoes:
            if sessao.perfil.cobertura < self._cobertura_minima:
                yield EventoAviso(carregar("msg_poucos_dados"))
            async for evento in self._recomendar(sessao):
                yield evento
            return
        pronto = sessao.perfil.cobertura >= self._cobertura_minima
        if (pronto and sessao.trocas_descoberta >= TROCAS_MIN) or (
            sessao.trocas_descoberta >= TROCAS_MAX
        ):
            sessao.estado = Estado.CONFIRMA_PERFIL
            yield EventoEstado(sessao.estado)
            async for evento in self._responder_llm(sessao, self._instrucoes_confirma(sessao)):
                yield evento
            return
        async for evento in self._responder_llm(sessao, self._instrucoes_descoberta(sessao)):
            yield evento

    async def _confirmacao(self, sessao: Sessao, sinais: Sinais) -> AsyncIterator[Evento]:
        if sinais.confirmou_perfil is True or sinais.quer_sugestoes:
            async for evento in self._recomendar(sessao):
                yield evento
            return
        sessao.confirmacoes += 1
        if sessao.confirmacoes >= MAX_CONFIRMACOES:
            async for evento in self._recomendar(sessao):
                yield evento
            return
        # Corrigiu algo: o perfil já foi atualizado pela extração; resume de novo.
        async for evento in self._responder_llm(sessao, self._instrucoes_confirma(sessao)):
            yield evento

    async def _exploracao(
        self, sessao: Sessao, sinais: Sinais, texto: str
    ) -> AsyncIterator[Evento]:
        if sinais.quer_refazer:
            sessao.estado = Estado.DESCOBERTA
            sessao.trocas_descoberta = TROCAS_MIN - 2  # mais 2 perguntas e já confirma
            sessao.confirmacoes = 0
            yield EventoEstado(sessao.estado)
            async for evento in self._responder_llm(sessao, self._instrucoes_descoberta(sessao)):
                yield evento
            return
        if sinais.quer_mais_sugestoes or sinais.quer_sugestoes:
            async for evento in self._recomendar(sessao, mais=True):
                yield evento
            return
        instrucoes = await self._instrucoes_explora(sessao, texto)
        async for evento in self._responder_llm(sessao, instrucoes):
            yield evento

    async def _acolhimento(
        self, sessao: Sessao, sinais: Sinais, texto: str
    ) -> AsyncIterator[Evento]:
        if not sinais.quer_voltar_orientacao:
            async for evento in self._responder_llm(sessao, carregar("estado_acolhimento")):
                yield evento
            return
        sessao.estado = sessao.estado_antes_acolhimento or Estado.DESCOBERTA
        sessao.estado_antes_acolhimento = None
        yield EventoEstado(sessao.estado)
        if sessao.estado == Estado.EXPLORA:
            instrucoes = await self._instrucoes_explora(sessao, texto)
        elif sessao.estado == Estado.CONFIRMA_PERFIL:
            instrucoes = self._instrucoes_confirma(sessao)
        else:
            sessao.estado = Estado.DESCOBERTA
            instrucoes = self._instrucoes_descoberta(sessao)
        async for evento in self._responder_llm(sessao, instrucoes):
            yield evento

    async def _acolher(self, sessao: Sessao) -> AsyncIterator[Evento]:
        """Sai do fluxo vocacional (seção 8.3). Nada disso é gravado ou agregado."""
        if sessao.estado == Estado.ACOLHIMENTO:
            # Já está acolhendo: responde com cuidado, sem repetir o texto fixo.
            async for evento in self._responder_llm(sessao, carregar("estado_acolhimento")):
                yield evento
            return
        sessao.estado_antes_acolhimento = sessao.estado
        sessao.estado = Estado.ACOLHIMENTO
        yield EventoEstado(sessao.estado)
        texto = carregar("msg_acolhimento")
        sessao.historico.append(MensagemSessao(papel="assistant", conteudo=texto))
        yield EventoTexto(texto)

    async def _recomendar(self, sessao: Sessao, *, mais: bool = False) -> AsyncIterator[Evento]:
        estado_anterior = sessao.estado
        sessao.estado = Estado.RECOMENDA
        yield EventoEstado(sessao.estado)
        try:
            recomendacao = await self._recomendador.recomendar(
                sessao.perfil, ja_sugeridos=sessao.sugeridos if mais else (), uso=sessao.uso
            )
        except LLMIndisponivel:
            sessao.estado = estado_anterior
            yield EventoErro(MENSAGEM_SOBRECARGA)
            return
        except LLMErro as erro:
            log.warning("Recomendação falhou: %s", type(erro).__name__)
            recomendacao = None

        if recomendacao is None:
            sessao.estado = Estado.EXPLORA if mais else Estado.DESCOBERTA
            texto = carregar("msg_sem_trilhas")
            sessao.historico.append(MensagemSessao(papel="assistant", conteudo=texto))
            yield EventoEstado(sessao.estado)
            yield EventoTexto(texto)
            return

        ids = [t.curso.id for t in recomendacao.trilhas]
        sessao.sugeridos = [*sessao.sugeridos, *(i for i in ids if i not in sessao.sugeridos)]
        sessao.recomendacao = recomendacao
        sessao.estado = Estado.EXPLORA
        texto = carregar("msg_trilhas", quantidade=len(ids))
        nomes = ", ".join(t.curso.nome for t in recomendacao.trilhas)
        # O histórico registra quais foram as trilhas, para o LLM saber do que se fala depois.
        sessao.historico.append(
            MensagemSessao(papel="assistant", conteudo=f"{texto}\n(Trilhas sugeridas: {nomes})")
        )
        yield EventoRecomendacao(recomendacao)
        yield EventoEstado(sessao.estado)
        yield EventoTexto(texto)

    # ---- LLM ---------------------------------------------------------------------------

    async def _analisar(self, sessao: Sessao) -> Analise:
        instrucoes = carregar(
            "extracao",
            eixos=self._lista(sorted(self._eixos_validos)),
            perfil=sessao.perfil.para_prompt(),
            estado=DESCRICAO_ESTADO.get(sessao.estado, sessao.estado.value),
        )
        mensagens = self._mensagens_para_llm(sessao, HISTORICO_EXTRACAO)
        resposta = await self._llm.extract(
            instrucoes, mensagens, Analise, max_tokens=MAX_TOKENS_EXTRACAO
        )
        sessao.uso.somar(resposta.uso)
        return resposta.dados

    async def _responder_llm(
        self, sessao: Sessao, instrucoes: str, *, pedido: str | None = None
    ) -> AsyncIterator[Evento]:
        mensagens = self._mensagens_para_llm(sessao, HISTORICO_RESPOSTA)
        if pedido and (not mensagens or mensagens[-1].papel != "user"):
            mensagens.append(Mensagem("user", pedido))
        stream = self._llm.stream(
            [self._sistema, instrucoes], mensagens, max_tokens=MAX_TOKENS_RESPOSTA
        )
        separador = SeparadorOpcoes()
        filtro = FiltroSaida(self._nomes_cursos)
        exibido: list[str] = []
        try:
            async for pedaco in stream:
                if seguro := filtro.alimentar(separador.alimentar(pedaco)):
                    exibido.append(seguro)
                    yield EventoTexto(seguro)
        except LLMIndisponivel:
            yield EventoErro(MENSAGEM_SOBRECARGA)
            return
        finally:
            sessao.uso.somar(stream.uso)
        if resto := filtro.alimentar(separador.finalizar()) + filtro.finalizar():
            exibido.append(resto)
            yield EventoTexto(resto)
        if filtro.descartadas:
            log.warning("Filtro de saída descartou %d frase(s)", len(filtro.descartadas))

        texto = "".join(exibido).strip()
        if not texto:
            texto = carregar("msg_reformular")
            yield EventoTexto(texto)
        if separador.opcoes:
            yield EventoOpcoes(separador.opcoes)
        sessao.historico.append(MensagemSessao(papel="assistant", conteudo=texto))

    @staticmethod
    def _mensagens_para_llm(sessao: Sessao, limite: int) -> list[Mensagem]:
        """Últimas mensagens, começando pelo estudante e sem papéis repetidos em sequência."""
        recentes = sessao.historico[-limite:]
        while recentes and recentes[0].papel == "assistant":
            recentes = recentes[1:]
        mensagens: list[Mensagem] = []
        for m in recentes:
            if mensagens and mensagens[-1].papel == m.papel:
                mensagens[-1] = Mensagem(m.papel, f"{mensagens[-1].conteudo}\n\n{m.conteudo}")
            else:
                mensagens.append(Mensagem(m.papel, m.conteudo))
        return mensagens

    # ---- Instruções por estado ---------------------------------------------------------

    def _instrucoes_descoberta(self, sessao: Sessao) -> str:
        perfil = sessao.perfil
        foco, codigo = perfil.proximo_foco(sessao.trocas_descoberta)
        exemplos = ""
        if codigo:
            nome = nomes_dimensoes()[codigo]["nome"]
            itens = "; ".join(exemplos_soutec(codigo, semente=sessao.trocas_descoberta))
            exemplos = (
                f"Atividades da dimensão {nome} (do teste SouTec) para usar como exemplos "
                f"concretos ou como botões, reescritas em linguagem jovem: {itens}. "
                f"Nunca aplique um questionário."
            )
        return carregar(
            "estado_descoberta",
            foco=foco,
            exemplos_soutec=exemplos,
            lacunas=self._lista(perfil.lacunas()) or "- quase nada; aprofunde o que já sabe",
            perfil=perfil.para_prompt(),
        )

    def _instrucoes_confirma(self, sessao: Sessao) -> str:
        return carregar("estado_confirma", perfil=sessao.perfil.para_prompt())

    def _cursos_citados(self, texto: str) -> list[Curso]:
        """Cursos citados pelo nome ("o que faz um técnico em design gráfico?")."""
        normal = normalizar(texto)
        citados = [
            c
            for c in self._catalogo.cursos
            if (nucleo := normalizar(re.sub(r"^Técnico (em )?", "", c.nome)))
            and re.search(rf"\b{re.escape(nucleo)}\b", normal)  # "canto" não casa "encanto"
        ]
        # "Design Gráfico" contém "Design": fica com os nomes mais específicos primeiro.
        return sorted(citados, key=lambda c: -len(c.nome))[:2]

    async def _instrucoes_explora(self, sessao: Sessao, texto: str) -> str:
        sugeridos: list[Curso] = [
            c for cid in sessao.sugeridos if (c := self._catalogo.get(cid)) is not None
        ]
        citados = self._cursos_citados(texto)
        resultados = await asyncio.to_thread(self._buscador.buscar, texto, k=3)
        candidatos = [*citados, *sugeridos[-3:], *(r.curso for r in resultados)]
        unicos = list({c.id: c for c in candidatos}.values())[:6]
        fichas = "\n\n".join(ficha_para_llm(c) for c in unicos)
        return carregar(
            "estado_explora",
            sugeridos=", ".join(c.nome for c in sugeridos) or "nenhuma",
            perfil=sessao.perfil.para_prompt(),
            fichas=fichas or "(nenhuma ficha encontrada)",
        )

    # ---- Perfil ------------------------------------------------------------------------

    def _atualizar_perfil(self, sessao: Sessao, novo: Perfil) -> None:
        """Usa o perfil devolvido pelo LLM, sem deixar que ele 'esqueça' o que já se sabia."""
        antigo = sessao.perfil
        for campo in (
            "interesses",
            "atividades_que_gosta",
            "materias_favoritas",
            "materias_dificeis",
            "habilidades_percebidas",
            "valores_trabalho",
            "restricoes",
        ):
            if not getattr(novo, campo) and getattr(antigo, campo):
                setattr(novo, campo, getattr(antigo, campo))
        for codigo in ("R", "I", "A", "S", "E", "C"):
            if not novo.dimensoes.get(codigo).coberta and antigo.dimensoes.get(codigo).coberta:
                setattr(novo.dimensoes, codigo, antigo.dimensoes.get(codigo))
        if novo.ambiente_preferido is None:
            novo.ambiente_preferido = antigo.ambiente_preferido
        novo.eixos_provaveis = eixos_validados(novo, self._eixos_validos)
        novo.calcular_cobertura()
        sessao.perfil = novo

    @staticmethod
    def _lista(itens: Iterable[str]) -> str:
        return "\n".join(f"- {i}" for i in itens)
