from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Checagem de vida para o Render. Não expõe segredos nem chama o LLM (custo zero)."""
    settings = request.app.state.settings
    return {
        "status": "ok",
        "app_env": settings.app_env.value,
        "llm_provider": request.app.state.llm.nome,
    }
