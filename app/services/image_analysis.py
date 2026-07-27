import base64
import json
import os
from io import BytesIO
from typing import Any

from fastapi import UploadFile
from langchain_core.messages import HumanMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.core.config import (
    ALLOWED_IMAGE_TYPES,
    DEFAULT_LLM_PROVIDER,
    GOOGLE_API_KEY,
    GOOGLE_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from app.prompts.roof_images import ROOF_INSPECTION_PROMPT


class AnalysisServiceError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def map_analysis_error(exc: Exception) -> AnalysisServiceError:
    if isinstance(exc, AnalysisServiceError):
        return exc

    error_text = str(exc).lower()

    if isinstance(exc, RateLimitError):
        return AnalysisServiceError(
            "Limite de consumo excedido. Aguarde alguns instantes e tente novamente.",
            status_code=429,
        )

    if isinstance(exc, APITimeoutError) or isinstance(exc, TimeoutError):
        return AnalysisServiceError(
            "A requisição à IA excedeu o tempo limite. Tente novamente.",
            status_code=504,
        )

    if isinstance(exc, APIConnectionError) or isinstance(exc, ConnectionError):
        return AnalysisServiceError(
            "Não foi possível conectar ao serviço de análise. Verifique sua conexão ou a chave da API.",
            status_code=502,
        )

    if isinstance(exc, APIStatusError):
        return AnalysisServiceError(
            f"Erro retornado pela API de IA: {exc}",
            status_code=502,
        )

    if isinstance(exc, json.JSONDecodeError):
        return AnalysisServiceError(
            "A resposta do modelo não veio em um formato JSON válido.",
            status_code=502,
        )

    if "quota" in error_text or "resource_exhausted" in error_text:
        return AnalysisServiceError(
            "A chave do provedor Google excedeu a quota de uso. Verifique o plano e billing da conta.",
            status_code=429,
        )

    if "not_found" in error_text or "model" in error_text and "not found" in error_text:
        return AnalysisServiceError(
            "O modelo escolhido no provedor Google não está disponível para essa chave/API. Use um modelo válido.",
            status_code=404,
        )

    if isinstance(exc, ValueError):
        return AnalysisServiceError(str(exc), status_code=400)

    if hasattr(exc, "status_code"):
        return AnalysisServiceError(
            f"Erro do provedor de IA: {exc}",
            status_code=getattr(exc, "status_code", 500),
        )

    return AnalysisServiceError(
        "Erro inesperado ao processar a análise. Tente novamente mais tarde.",
        status_code=500,
    )


def extract_json_payload(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        text = content.strip()
    else:
        text = str(content).strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    return json.loads(text)


def build_prompt_text(prompt: str) -> str:
    return f"{prompt}\n\nInstruções de análise:\n{ROOF_INSPECTION_PROMPT}"


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    issues = normalized.get("issues") or []
    if not isinstance(issues, list):
        issues = []

    normalized_issues: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue

        coords = issue.get("coordinates")
        if not isinstance(coords, dict):
            coords = {}

        normalized_issue = dict(issue)
        normalized_issue["image_name"] = issue.get("image_name") or None
        normalized_issue["coordinates"] = {
            "x": coords.get("x"),
            "y": coords.get("y"),
            "width": coords.get("width"),
            "height": coords.get("height"),
        }
        normalized_issues.append(normalized_issue)

    normalized["issues"] = normalized_issues
    return normalized


def get_model_settings(provider: str | None = None, model: str | None = None) -> dict[str, str]:
    provider_name = (provider or DEFAULT_LLM_PROVIDER or "openai").strip().lower()

    if provider_name == "google":
        return {
            "provider": "google",
            "model": (model or GOOGLE_MODEL or "gemini-1.5-flash").strip(),
            "api_key": (os.getenv("GOOGLE_API_KEY", GOOGLE_API_KEY) or "").strip(),
        }

    return {
        "provider": "openai",
        "model": (model or OPENAI_MODEL or "gpt-4o-mini").strip(),
        "api_key": (os.getenv("OPENAI_API_KEY", OPENAI_API_KEY) or "").strip(),
    }


def build_llm(provider: str | None = None, model: str | None = None):
    settings = get_model_settings(provider=provider, model=model)
    provider_name = settings["provider"]
    model_name = settings["model"]
    api_key = settings["api_key"]

    if provider_name == "google":
        if not api_key:
            raise AnalysisServiceError("GOOGLE_API_KEY não configurada", status_code=500)

        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)

    if not api_key:
        raise AnalysisServiceError("OPENAI_API_KEY não configurada", status_code=500)

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model_name, api_key=api_key)


def validate_image_files(files: list[UploadFile]) -> list[UploadFile]:
    for file in files:
        if not file.content_type or file.content_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError("Tipo de arquivo inválido. Envie imagens JPEG, PNG ou WebP.")

    return files


def analyze_images(
    files: list[UploadFile],
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    try:
        settings = get_model_settings(provider=provider, model=model)
        validate_image_files(files)

        llm = build_llm(provider=settings["provider"], model=settings["model"])

        image_parts: list[dict[str, Any]] = []
        for file in files:
            image_bytes = file.file.read()
            if not image_bytes:
                raise AnalysisServiceError("Uma ou mais imagens estão vazias.", status_code=400)

            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{file.content_type};base64,{image_base64}"},
                }
            )

        image_names = [file.filename or f"imagem_{index + 1}" for index, file in enumerate(files)]
        image_context = "\n".join(
            f"{index + 1}. {name}" for index, name in enumerate(image_names)
        )
        prompt_text = f"{build_prompt_text(prompt)}\n\nImagens anexadas:\n{image_context}\n\nOrdem de análise: considere cada imagem separadamente e relacione cada problema identificado ao nome da imagem correspondente no campo 'image_name'."

        content = [
            {"type": "text", "text": prompt_text},
            *image_parts,
        ]
        message = HumanMessage(content=content)
        response = llm.invoke([message])

        if isinstance(response.content, list):
            analysis_text = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response.content
            )
        else:
            analysis_text = str(response.content)

        payload = extract_json_payload(analysis_text)
        normalized_payload = normalize_payload(payload)
        normalized_payload.setdefault("prompt", prompt)
        normalized_payload.setdefault("provider", settings["provider"])
        normalized_payload.setdefault("model", settings["model"])
        return normalized_payload
    except Exception as exc:
        raise map_analysis_error(exc) from exc


def analyze_images_and_build_response(
    files: list[UploadFile],
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return analyze_images(files, prompt, provider=provider, model=model)


def generate_analysis_pdf(
    files: list[UploadFile],
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
) -> bytes:
    payload = analyze_images(files, prompt, provider=provider, model=model)
    return build_pdf_from_payload(payload)


def build_pdf_from_payload(payload: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, 760, "Relatório de Análise")
    pdf.drawString(40, 740, "Prompt: " + str(payload.get("prompt", "")))

    summary = json.dumps(payload, ensure_ascii=False, indent=2)
    text = pdf.beginText(40, 720)
    text.textLines(summary.splitlines())
    pdf.drawText(text)

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()
