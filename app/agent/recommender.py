"""Recomendação de trilhas (seção 6.3): RAG -> LLM escolhe -> validação anti-alucinação.

Regras garantidas pelo código, não pelo prompt:
- todo `curso_id` precisa existir no catálogo E estar entre as fichas enviadas ao LLM;
- com problema, o pedido é refeito UMA vez, explicando o erro;
- diversidade: sempre pelo menos 2 eixos (a especificação permite 3 do mesmo eixo quando o
  perfil é "muito claro"; aqui a regra é mais rígida de propósito: uma opção de outra área
  estimula a comparação, e a "clareza" viria da confiança que o próprio LLM declara);
- ofertas regionais vêm só de `ofertas_regiao.json`, nunca do LLM.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from app.agent.ofertas import Ofertas, OfertasDoCurso
from app.agent.profile import Perfil, nomes_dimensoes
from app.agent.prompts import carregar
from app.llm.base import LLMErro, LLMProvider, LLMRespostaInvalida, Mensagem, Uso
from app.rag.catalog import Catalogo, Curso
from app.rag.retriever import ResultadoBusca

log = logging.getLogger(__name__)

CANDIDATOS = 15
OUTROS_EIXOS_MINIMO = 3  # candidatos de outras áreas garantidos entre os 15
MAX_TOKENS_RECOMENDACAO = 1500


class Buscador(Protocol):
    def buscar(
        self,
        consulta: str,
        *,
        eixos_provaveis: dict[str, float] = ...,
        k: int = 15,
    ) -> list[ResultadoBusca]: ...


# ---- Saída do LLM ----------------------------------------------------------------------


class Escolha(BaseModel):
    curso_id: str = Field(description="id exatamente como aparece na ficha")
    porque_combina: str = Field(description="1 ou 2 frases ligadas ao que o estudante disse")
    atencao: str = Field(description="um ponto de atenção honesto, tirado da ficha")


class SaidaRecomendacao(BaseModel):
    sugestoes: list[Escolha]
    resumo_perfil: list[str] = Field(description="3 frases curtas sobre o estudante")
    pense_criticamente: list[str] = Field(description="2 ou 3 perguntas para refletir")


# ---- Resultado -------------------------------------------------------------------------


class Trilha(BaseModel):
    curso: Curso
    porque_combina: str
    atencao: str
    ofertas: OfertasDoCurso


class DimensaoDominante(BaseModel):
    codigo: str
    nome: str
    evidencias: list[str]


class Recomendacao(BaseModel):
    trilhas: list[Trilha]
    resumo_perfil: list[str]
    pense_criticamente: list[str]
    dimensoes_dominantes: list[DimensaoDominante]
    incompleta: bool  # recomendou antes da cobertura mínima
    edicao_cnct: str


# ---- Recomendador ----------------------------------------------------------------------


def ficha_para_llm(curso: Curso) -> str:
    """Ficha enxuta: o que ajuda a escolher e justificar, sem burocracia (menos tokens)."""
    ocupacoes = "; ".join(curso.ocupacoes_cbo) or "-"
    detalhes = curso.carga_horaria_detalhes.splitlines()
    duracao = detalhes[1:2]
    estagio = next((d for d in detalhes if "estágio" in d.lower()), "")
    return (
        f"## curso_id: {curso.id}\n"
        f"Nome: {curso.nome} | Eixo: {curso.eixo} | {curso.carga_horaria_min} horas"
        f"{' | ' + duracao[0] if duracao else ''}\n"
        # Inclui o trecho "são fundamentais", de onde saem os pontos de atenção (matérias etc.).
        f"Perfil profissional: {curso.perfil_profissional[:2200]}\n"
        + (f"Estágio: {estagio}\n" if estagio else "")
        + f"Campo de atuação: {curso.campo_atuacao[:500]}\n"
        f"Ocupações: {ocupacoes}"
    )


class Recomendador:
    def __init__(
        self,
        llm: LLMProvider,
        buscador: Buscador,
        catalogo: Catalogo,
        ofertas: Ofertas,
        *,
        sistema: str,
        cobertura_minima: float = 0.7,
    ) -> None:
        self._llm = llm
        self._buscador = buscador
        self._catalogo = catalogo
        self._ofertas = ofertas
        self._sistema = sistema
        self._cobertura_minima = cobertura_minima

    async def recomendar(
        self,
        perfil: Perfil,
        *,
        ja_sugeridos: Sequence[str] = (),
        quantidade: int = 3,
        uso: Uso | None = None,
    ) -> Recomendacao | None:
        uso = uso if uso is not None else Uso()
        candidatos = await self._candidatos(perfil, ja_sugeridos)
        if not candidatos:
            return None
        ids_candidatos = {c.id for c in candidatos}
        eixos = {e.eixo: e.confianca for e in perfil.eixos_provaveis}

        aviso = ""
        escolhas: list[Escolha] = []
        saida: SaidaRecomendacao | None = None
        for tentativa in range(2):
            try:
                saida = await self._pedir(perfil, candidatos, ja_sugeridos, quantidade, aviso, uso)
            except LLMRespostaInvalida as erro:
                log.warning("Recomendação com JSON inválido (tentativa %d): %s", tentativa, erro)
                aviso = "A resposta anterior não seguiu o formato pedido."
                continue
            escolhas, problemas = self._validar(
                saida, ids_candidatos, ja_sugeridos, quantidade, eixos
            )
            if not problemas:
                break
            log.warning("Recomendação com problemas (tentativa %d): %s", tentativa, problemas)
            aviso = "Corrija estes problemas da resposta anterior: " + "; ".join(problemas)

        if saida is None or not escolhas:
            return None
        escolhas = self._garantir_diversidade(escolhas, candidatos, eixos, perfil)
        return Recomendacao(
            trilhas=[self._trilha(e) for e in escolhas],
            resumo_perfil=saida.resumo_perfil[:3],
            pense_criticamente=saida.pense_criticamente[:3],
            dimensoes_dominantes=self._dominantes(perfil),
            incompleta=perfil.cobertura < self._cobertura_minima,
            edicao_cnct=self._catalogo.edicao,
        )

    async def _candidatos(self, perfil: Perfil, ja_sugeridos: Sequence[str]) -> list[Curso]:
        consulta = perfil.consulta_busca() or "cursos técnicos para estudantes do Ensino Médio"
        eixos = {e.eixo: e.confianca for e in perfil.eixos_provaveis}
        # A busca é CPU (embeddings): roda fora do event loop para não travar as outras sessões.
        resultados = await asyncio.to_thread(
            self._buscador.buscar,
            consulta,
            eixos_provaveis=eixos,
            k=CANDIDATOS * 2 + len(ja_sugeridos),
        )
        todos = [r.curso for r in resultados if r.curso.id not in ja_sugeridos]
        candidatos = todos[:CANDIDATOS]
        # Garante opções de outras áreas, para a regra de diversidade ter com o que trabalhar.
        principal = candidatos[0].eixo if candidatos else None
        outros = [c for c in candidatos if c.eixo != principal]
        faltam = [c for c in todos[CANDIDATOS:] if c.eixo != principal]
        extras = faltam[: max(0, OUTROS_EIXOS_MINIMO - len(outros))]
        return candidatos[: CANDIDATOS - len(extras)] + extras

    async def _pedir(
        self,
        perfil: Perfil,
        candidatos: list[Curso],
        ja_sugeridos: Sequence[str],
        quantidade: int,
        aviso: str,
        uso: Uso,
    ) -> SaidaRecomendacao:
        instrucoes = carregar(
            "recomendacao",
            quantidade=quantidade,
            ja_sugeridos=", ".join(ja_sugeridos) or "nenhum",
            aviso_correcao=f"\n# ATENÇÃO\n{aviso}\n" if aviso else "",
            perfil=perfil.para_prompt(),
            fichas="\n\n".join(ficha_para_llm(c) for c in candidatos),
        )
        pedido = [Mensagem("user", "Escolha as trilhas para este estudante.")]
        resposta = await self._llm.extract(
            [self._sistema, instrucoes],
            pedido,
            SaidaRecomendacao,
            max_tokens=MAX_TOKENS_RECOMENDACAO,
        )
        uso.somar(resposta.uso)
        return resposta.dados

    def _validar(
        self,
        saida: SaidaRecomendacao,
        ids_candidatos: set[str],
        ja_sugeridos: Sequence[str],
        quantidade: int,
        eixos: dict[str, float],
    ) -> tuple[list[Escolha], list[str]]:
        validas: list[Escolha] = []
        problemas: list[str] = []
        for escolha in saida.sugestoes:
            cid = escolha.curso_id.strip()
            if cid not in self._catalogo or cid not in ids_candidatos:
                problemas.append(f"'{cid}' não é um curso_id das fichas")
            elif cid in ja_sugeridos or cid in {v.curso_id for v in validas}:
                problemas.append(f"'{cid}' repetido")
            elif not escolha.porque_combina.strip():
                problemas.append(f"'{cid}' sem porque_combina")
            else:
                validas.append(escolha.model_copy(update={"curso_id": cid}))
        validas = validas[:quantidade]
        if len(validas) < quantidade:
            problemas.append(f"eram {quantidade} cursos válidos, vieram {len(validas)}")
        if self._mesmo_eixo(validas):
            problemas.append("todos os cursos são do mesmo eixo; inclua um de outro eixo")
        return validas, problemas

    def _mesmo_eixo(self, escolhas: list[Escolha]) -> bool:
        if len(escolhas) < 2:
            return False
        return len({self._curso(e.curso_id).eixo for e in escolhas}) == 1

    def _garantir_diversidade(
        self,
        escolhas: list[Escolha],
        candidatos: list[Curso],
        eixos: dict[str, float],
        perfil: Perfil,
    ) -> list[Escolha]:
        """Se mesmo depois da nova tentativa vierem todas do mesmo eixo, troca a última."""
        if not self._mesmo_eixo(escolhas):
            return escolhas
        eixo = self._curso(escolhas[0].curso_id).eixo
        alternativa = next((c for c in candidatos if c.eixo != eixo), None)
        if alternativa is None:
            return escolhas
        interesse = (perfil.interesses or perfil.atividades_que_gosta or ["o que você contou"])[0]
        troca = Escolha(
            curso_id=alternativa.id,
            porque_combina=carregar("msg_trilha_alternativa", interesse=interesse),
            atencao="É de outra área: vale ler a descrição com calma antes de decidir.",
        )
        return [*escolhas[:-1], troca]

    def _trilha(self, escolha: Escolha) -> Trilha:
        return Trilha(
            curso=self._curso(escolha.curso_id),
            porque_combina=escolha.porque_combina.strip(),
            atencao=escolha.atencao.strip(),
            ofertas=self._ofertas.do_curso(escolha.curso_id),
        )

    def _curso(self, curso_id: str) -> Curso:
        curso = self._catalogo.get(curso_id)
        if curso is None:  # impossível depois da validação; protege contra regressões
            raise LLMErro(f"curso_id fora do catálogo: {curso_id}")
        return curso

    @staticmethod
    def _dominantes(perfil: Perfil) -> list[DimensaoDominante]:
        nomes = nomes_dimensoes()
        return [
            DimensaoDominante(
                codigo=c, nome=nomes[c]["nome"], evidencias=perfil.dimensoes.get(c).evidencias
            )
            for c in perfil.dimensoes_dominantes()
        ]
