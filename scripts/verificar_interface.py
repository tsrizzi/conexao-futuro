# Ferramenta de desenvolvimento, roda uma vez: chamadas bloqueantes dentro de async são ok.
# ruff: noqa: ASYNC110, ASYNC210, ASYNC220, ASYNC240
"""Verifica a interface num celular de 360 px, com Chrome headless (Fase 3).

Sobe o app com um LLM FALSO roteirizado (custo zero e determinístico), percorre o fluxo
(início -> conversa -> botões -> trilhas -> resultado) nos temas claro e escuro e:
- salva capturas de tela em .cache/capturas/;
- roda o axe-core (o motor de acessibilidade usado pelo Lighthouse);
- confere se há rolagem horizontal (quebra de layout em 360 px).

Uso:
    uv run python scripts/verificar_interface.py
"""

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
os.environ["LLM_PROVIDER"] = "fake"

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from app.agent.ofertas import Ofertas  # noqa: E402
from app.agent.orchestrator import Orquestrador  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm.fake import FakeProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rag.catalog import Catalogo  # noqa: E402
from tests.agente_fake import BuscadorFixo, analise, dimensoes, recomendacao  # noqa: E402

CHROMES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
AXE = "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js"
SAIDA = RAIZ / ".cache" / "capturas"
LARGURA, ALTURA = 360, 780


def app_demo() -> Any:
    perfil: dict[str, Any] = {
        "interesses": ["games", "desenhar"],
        "materias_favoritas": ["artes", "matemática"],
        "dimensoes": dimensoes(A="desenha personagens", R="montou um PC com o primo"),
    }
    llm = FakeProvider(
        respostas=[
            "Massa! Desenhar personagens pede bastante criatividade. "
            "E na escola, qual matéria você mais curte? "
            "[[opcoes: Matemática | Artes | Educação Física]]",
            "Boa combinação. Você já montou ou consertou alguma coisa, tipo um PC ou uma bike?",
        ]
        * 3,
        estruturados=[
            analise(**perfil | {"materias_favoritas": []}),
            analise(**perfil),
            analise({"quer_sugestoes": True}, **perfil),
            recomendacao(
                "tecnico-em-programacao-de-jogos-digitais",
                "tecnico-em-design-grafico",
                "tecnico-em-mecatronica",
            ),
        ]
        * 3,
    )
    catalogo = Catalogo.carregar()
    orq = Orquestrador(llm, catalogo, BuscadorFixo(catalogo), Ofertas([]))
    config = Settings(_env_file=None, llm_provider="fake", secret_key="d" * 32)  # type: ignore[call-arg, arg-type]
    return create_app(config, llm=llm, orquestrador=orq)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class CDP:
    """Cliente mínimo do Chrome DevTools Protocol."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.n = 0

    async def __call__(self, metodo: str, **params: Any) -> dict[str, Any]:
        self.n += 1
        meu = self.n
        await self.ws.send(json.dumps({"id": meu, "method": metodo, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == meu:
                if "error" in msg:
                    raise RuntimeError(f"{metodo}: {msg['error']}")
                resultado: dict[str, Any] = msg.get("result", {})
                return resultado

    async def js(self, expressao: str) -> Any:
        r = await self(
            "Runtime.evaluate", expression=expressao, awaitPromise=True, returnByValue=True
        )
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS: {r['exceptionDetails']}")
        return r["result"].get("value")

    async def esperar(self, condicao: str, limite: float = 15) -> None:
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            if await self.js(f"Boolean({condicao})"):
                return
            await asyncio.sleep(0.2)
        raise TimeoutError(condicao)

    async def abrir(self, url: str) -> None:
        await self("Page.navigate", url=url)
        await self.esperar("document.readyState === 'complete'")
        await asyncio.sleep(0.6)  # fontes

    async def captura(self, nome: str, inteira: bool = True) -> Path:
        r = await self("Page.captureScreenshot", format="png", captureBeyondViewport=inteira)
        caminho = SAIDA / f"{nome}.png"
        caminho.write_bytes(base64.b64decode(r["data"]))
        return caminho


async def checar(cdp: CDP, nome: str, achados: dict[str, Any]) -> None:
    transbordo = await cdp.js(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    violacoes = await cdp.js(
        f"""new Promise((ok) => {{
          const rodar = () => axe.run(document, {{ runOnly: ['wcag2a','wcag2aa','best-practice'] }})
            .then(r => ok(r.violations.map(v => ({{
              id: v.id, impacto: v.impact, alvos: v.nodes.length,
              exemplo: v.nodes[0].target.join(' '), resumo: v.help}}))));
          if (window.axe) return rodar();
          const s = document.createElement('script'); s.src = '{AXE}'; s.onload = rodar;
          document.head.appendChild(s);
        }})"""
    )
    achados[nome] = {"rolagem_horizontal_px": transbordo, "axe": violacoes}


LIVRE = "!document.querySelector('.botao-enviar').disabled"


def enviar(texto: str) -> str:
    """JS que digita no campo e envia o formulário do chat."""
    return (
        "{ const c = document.querySelector('textarea'); "
        f"c.value = {json.dumps(texto)}; c.dispatchEvent(new Event('input')); "
        "document.querySelector('[data-compositor]').requestSubmit(); }"
    )


def mensagens_ia(minimo: int) -> str:
    return f"document.querySelectorAll('.mensagem-ia').length >= {minimo} && {LIVRE}"


async def percorrer(cdp: CDP, base: str, tema: str, achados: dict[str, Any]) -> None:
    preferencia = [{"name": "prefers-color-scheme", "value": tema}]
    await cdp("Emulation.setEmulatedMedia", features=preferencia)
    await cdp.abrir(f"{base}/")
    await cdp.js("localStorage.clear(); sessionStorage.clear()")
    await cdp.abrir(f"{base}/")
    await cdp.captura(f"{tema}-1-inicio")
    await checar(cdp, f"{tema}/inicio", achados)

    await cdp.abrir(f"{base}/chat")
    await cdp.esperar(mensagens_ia(1))
    await cdp.js(enviar("jogo videogame e desenho uns personagens"))
    await cdp.esperar("document.querySelector('.opcao')")
    await cdp.captura(f"{tema}-2-chat-botoes", inteira=False)
    await checar(cdp, f"{tema}/chat", achados)
    await cdp.js("document.querySelector('.opcao').click()")
    await cdp.esperar(mensagens_ia(3))
    await cdp.js(enviar("já quero ver as sugestões"))
    await cdp.esperar(f"document.querySelector('.mensagem-trilhas') && {LIVRE}")
    await cdp.captura(f"{tema}-3-chat-trilhas", inteira=False)

    await cdp.abrir(f"{base}/resultado")
    await cdp.esperar("!document.querySelector('[data-conteudo]').hidden")
    await cdp.captura(f"{tema}-4-resultado")
    await checar(cdp, f"{tema}/resultado", achados)


async def principal() -> int:
    chrome = next((c for c in CHROMES if Path(c).exists()), shutil.which("chrome"))
    if not chrome:
        print("Chrome/Edge não encontrado.")
        return 1
    SAIDA.mkdir(parents=True, exist_ok=True)

    porta_app = porta_livre()
    servidor = uvicorn.Server(uvicorn.Config(app_demo(), port=porta_app, log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()
    while not servidor.started:
        await asyncio.sleep(0.1)

    porta_cdp = porta_livre()
    perfil = tempfile.mkdtemp(prefix="cf-chrome-")
    processo = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--remote-debugging-port={porta_cdp}",
            f"--user-data-dir={perfil}",
            "--no-first-run",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    achados: dict[str, Any] = {}
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{porta_cdp}/json") as r:
                    alvo = next(t for t in json.load(r) if t["type"] == "page")
                break
            except (OSError, StopIteration):
                await asyncio.sleep(0.2)
        async with websockets.connect(alvo["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            cdp = CDP(ws)
            await cdp("Page.enable")
            await cdp("Page.setBypassCSP", enabled=True)  # só para injetar o axe-core
            await cdp(
                "Emulation.setDeviceMetricsOverride",
                width=LARGURA,
                height=ALTURA,
                deviceScaleFactor=2,
                mobile=True,
            )
            for tema in ("light", "dark"):
                await percorrer(cdp, f"http://127.0.0.1:{porta_app}", tema, achados)
    finally:
        processo.terminate()
        servidor.should_exit = True
        shutil.rmtree(perfil, ignore_errors=True)

    problemas = 0
    for pagina, a in achados.items():
        print(f"\n{pagina}: rolagem horizontal = {a['rolagem_horizontal_px']} px")
        problemas += a["rolagem_horizontal_px"] > 0
        for v in a["axe"]:
            problemas += 1
            print(f"  [{v['impacto']}] {v['id']} ({v['alvos']}x): {v['resumo']} -> {v['exemplo']}")
        if not a["axe"]:
            print("  axe-core: nenhuma violação")
    print(f"\nCapturas em {SAIDA.relative_to(RAIZ)}")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
