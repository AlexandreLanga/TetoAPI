import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.services.image_analysis import AnalysisServiceError, analyze_images_and_build_response, generate_analysis_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.post("/analyze")
def analyze_image(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...),
    provider: str | None = Form(default=None),
    model: str | None = Form(default=None),
):
    try:
        payload = analyze_images_and_build_response(files, prompt, provider=provider, model=model)
    except AnalysisServiceError as exc:
        logger.error(f"Analysis service error: {exc.message}")
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return JSONResponse(
        content={
            "message": "Análise concluída",
            "result": payload,
            "pdf_url": "/api/analyze/pdf",
        }
    )


@router.post("/analyze/pdf")
def download_pdf(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...),
    provider: str | None = Form(default=None),
    model: str | None = Form(default=None),
):
    try:
        pdf_bytes = generate_analysis_pdf(files, prompt, provider=provider, model=model)
    except AnalysisServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=analysis.pdf"},
    )
