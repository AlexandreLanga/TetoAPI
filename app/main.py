from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(
    title="TetoAPI",
    version="0.1.0",
    description="API para análise de imagens com GPT via LangChain e geração de PDF.",
)

app.include_router(router)
