import base64
import json
from io import BytesIO
from typing import Any

from fastapi import UploadFile
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import files
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL
from app.prompts.roof_images import ROOF_INSPECTION_PROMPT


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


def validate_image_files(files: list[UploadFile]) -> list[UploadFile]:
    #if len(files) < 3:
        #raise ValueError("Envie ao menos 3 imagens para análise")

    for file in files:
        if not file.content_type or file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Tipo de arquivo inválido. Envie imagens JPEG, PNG ou WebP.")

    return files


def analyze_images(files: list[UploadFile], prompt: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurada")

    validate_image_files(files)

    llm = ChatOpenAI(model=OPENAI_MODEL, api_key=OPENAI_API_KEY)

    image_parts: list[dict[str, Any]] = []
    for file in files:
        image_bytes = file.file.read()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{file.content_type};base64,{image_base64}"},
            }
        )

    content = [
        {"type": "text", "text": build_prompt_text(prompt)},
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
    payload.setdefault("prompt", prompt)
    return payload


def analyze_images_and_build_response(files: list[UploadFile], prompt: str) -> dict[str, Any]:
    return analyze_images(files, prompt)


def generate_analysis_pdf(files: list[UploadFile], prompt: str) -> bytes:
    payload = analyze_images(files, prompt)
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
