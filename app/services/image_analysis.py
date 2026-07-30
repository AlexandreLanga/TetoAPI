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

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover - depende do ambiente
    PILImage = None

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


def build_grounding_context(image_bytes: bytes) -> list[dict[str, Any]]:
    if not image_bytes or PILImage is None:
        return []

    try:
        image = PILImage.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return []

    width, height = image.size
    if width <= 0 or height <= 0:
        return []

    sample_width = max(32, width // 4)
    sample_height = max(32, height // 4)
    sample = image.resize((sample_width, sample_height))
    pixels = list(sample.getdata())
    if not pixels:
        return []

    luminances = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    average_luminance = sum(luminances) / len(luminances)
    candidate_points = [
        (x, y)
        for (x, y), luminance in zip(
            [(x, y) for y in range(sample_height) for x in range(sample_width)],
            luminances,
        )
        if luminance <= average_luminance * 0.75 or luminance >= average_luminance * 1.25
    ]

    if len(candidate_points) < 8:
        return []

    min_x = min(x for x, _ in candidate_points)
    max_x = max(x for x, _ in candidate_points)
    min_y = min(y for _, y in candidate_points)
    max_y = max(y for _, y in candidate_points)

    box_width = max(8, int((max_x - min_x + 1) * (width / sample_width)))
    box_height = max(8, int((max_y - min_y + 1) * (height / sample_height)))
    box_x = max(0, int(min_x * (width / sample_width)))
    box_y = max(0, int(min_y * (height / sample_height)))
    box_x2 = min(width, box_x + box_width)
    box_y2 = min(height, box_y + box_height)

    if box_x2 - box_x <= 0 or box_y2 - box_y <= 0:
        return []

    return [
        {
            "x": box_x,
            "y": box_y,
            "width": box_x2 - box_x,
            "height": box_y2 - box_y,
            "confidence": 0.7,
            "reason": "região visualmente contrastante detectada pela análise local",
        }
    ]


def build_grounding_context_text(image_names: list[str], grounding_contexts: list[list[dict[str, Any]]]) -> str:
    sections: list[str] = []
    for index, image_name in enumerate(image_names):
        contexts = grounding_contexts[index] if index < len(grounding_contexts) else []
        if contexts:
            details = "; ".join(
                f"bbox(x={item['x']}, y={item['y']}, w={item['width']}, h={item['height']}, conf={item['confidence']}) {item['reason']}"
                for item in contexts
            )
            sections.append(f"{index + 1}. {image_name}: {details}")
        else:
            sections.append(f"{index + 1}. {image_name}: nenhuma região candidata confirmada pela análise local")

    return "Contexto de grounding/segmentação local:\n" + "\n".join(sections)


def build_analysis_prompts(prompt: str, image_names: list[str], grounding_contexts: list[list[dict[str, Any]]] | None = None) -> str:
    image_context = "\n".join(f"{index + 1}. {name}" for index, name in enumerate(image_names))
    grounding_text = build_grounding_context_text(image_names, grounding_contexts or [])
    return (
        f"{build_prompt_text(prompt)}\n\nImagens anexadas:\n{image_context}\n\n"
        f"{grounding_text}\n\n"
        "Fluxo de validação visual:\n"
        "1. Identifique o tipo do dano e o contexto visual com base na imagem.\n"
        "2. Use o contexto de grounding/segmentação local como evidência para localizar melhor o objeto ou área suspeita.\n"
        "3. Valide a imagem antes de retornar qualquer resposta.\n"
        "4. Confirme que cada issue listada é realmente visível, suportada e relevante para a imagem.\n"
        "5. Se a imagem estiver desfocada, escura, muito distante, mal iluminada ou não permitir confirmação, marque a limitação e não invente diagnóstico.\n"
        "6. Revise localização, coordenadas, severidade e confiança antes de finalizar.\n"
        "7. Não altere a estrutura do JSON; apenas aprimore a análise com base na validação visual.\n"
        "8. Não retorne texto fora do JSON.\n\n"
        "Ordem de análise: considere cada imagem separadamente e relacione cada problema identificado ao nome da imagem correspondente no campo 'image_name'."
    )


def build_validation_prompt(prompt: str, image_names: list[str], previous_payload: dict[str, Any], grounding_contexts: list[list[dict[str, Any]]] | None = None) -> str:
    image_context = "\n".join(f"{index + 1}. {name}" for index, name in enumerate(image_names))
    grounding_text = build_grounding_context_text(image_names, grounding_contexts or [])
    previous_payload_text = json.dumps(previous_payload, ensure_ascii=False, indent=2)
    return (
        f"{build_prompt_text(prompt)}\n\nImagens anexadas:\n{image_context}\n\n"
        f"{grounding_text}\n\n"
        "Revise a análise anterior antes de responder.\n"
        "- Valide visualmente cada problema sugerido contra as imagens anexadas.\n"
        "- Use o contexto de grounding/segmentação local para afinar a localização dos problemas.\n"
        "- Remova ou reduza problemas que não estejam claramente visíveis.\n"
        "- Aumente a precisão de localização, coordenadas, severidade e confiança quando possível.\n"
        "- Se não houver confirmação visual suficiente, prefira limitar o relato e adicionar restrições nas limitações.\n"
        "- Não altere a estrutura do JSON; apenas refine o conteúdo.\n"
        "- Não retorne texto fora do JSON.\n\n"
        f"Análise anterior para revisão:\n{previous_payload_text}"
    )


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
        grounding_contexts: list[list[dict[str, Any]]] = []
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
            grounding_contexts.append(build_grounding_context(image_bytes))

        image_names = [file.filename or f"imagem_{index + 1}" for index, file in enumerate(files)]
        prompt_text = build_analysis_prompts(prompt, image_names, grounding_contexts)

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
        validated_payload = payload

        validation_prompt_text = build_validation_prompt(prompt, image_names, payload, grounding_contexts)
        validation_content = [
            {"type": "text", "text": validation_prompt_text},
            *image_parts,
        ]
        validation_message = HumanMessage(content=validation_content)
        try:
            validation_response = llm.invoke([validation_message])
            if isinstance(validation_response.content, list):
                validation_text = "\n".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in validation_response.content
                )
            else:
                validation_text = str(validation_response.content)

            validated_payload = extract_json_payload(validation_text)
        except Exception:
            validated_payload = payload

        normalized_payload = normalize_payload(validated_payload)
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
