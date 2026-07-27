import unittest
from io import BytesIO

from fastapi import UploadFile

from app.core.config import DEFAULT_LLM_PROVIDER, OPENAI_MODEL
from app.services.image_analysis import (
    extract_json_payload,
    get_model_settings,
    normalize_payload,
    validate_image_files,
)


class ExtractJsonPayloadTests(unittest.TestCase):
    def test_extracts_json_from_fenced_block(self):
        content = '''```json
{
  "roof_condition": {
    "score": 90,
    "classification": "Bom",
    "summary": "Excelente conservação"
  },
  "issues": [],
  "maintenance": {
    "priority": "Baixa",
    "inspection_required": false,
    "risk_of_leak": "Baixo",
    "structural_risk": "Baixo"
  },
  "limitations": []
}
```'''

        result = extract_json_payload(content)

        self.assertEqual(result["roof_condition"]["score"], 90)
        self.assertEqual(result["maintenance"]["priority"], "Baixa")

    def test_extracts_json_from_plain_text(self):
        content = '{"roof_condition":{"score":70,"classification":"Regular","summary":"ok"},"issues":[],"maintenance":{"priority":"Média","inspection_required":true,"risk_of_leak":"Médio","structural_risk":"Baixo"},"limitations":["Não foi possível avaliar"]}'

        result = extract_json_payload(content)

        self.assertEqual(result["roof_condition"]["classification"], "Regular")
        self.assertTrue(result["maintenance"]["inspection_required"])

    def test_validate_image_files_accepts_multiple_valid_images(self):
        files = [
            UploadFile(
                filename="a.jpg",
                file=BytesIO(b"x"),
                headers={"content-type": "image/jpeg"},
            )
            for _ in range(2)
        ]

        self.assertEqual(validate_image_files(files), files)

    def test_normalize_payload_adds_coordinate_fields_to_issues(self):
        payload = {
            "roof_condition": {"score": 80, "classification": "Bom", "summary": "ok"},
            "issues": [{"type": "infiltration", "severity": "MÉDIA", "confidence": 85, "location": "centro", "description": "teste"}],
            "maintenance": {"priority": "Média", "inspection_required": True, "risk_of_leak": "Médio", "structural_risk": "Baixo"},
            "limitations": [],
        }

        normalized = normalize_payload(payload)

        self.assertIn("coordinates", normalized["issues"][0])
        self.assertIn("image_name", normalized["issues"][0])
        self.assertEqual(normalized["issues"][0]["coordinates"], {"x": None, "y": None, "width": None, "height": None})
        self.assertIsNone(normalized["issues"][0]["image_name"])

    def test_get_model_settings_prefers_explicit_provider_and_model(self):
        settings = get_model_settings(provider="google", model="gemini-1.5-flash")

        self.assertEqual(settings["provider"], "google")
        self.assertEqual(settings["model"], "gemini-1.5-flash")
        self.assertIn("api_key", settings)

    def test_get_model_settings_defaults_to_openai(self):
        settings = get_model_settings()

        self.assertEqual(settings["provider"], DEFAULT_LLM_PROVIDER)
        self.assertEqual(settings["model"], OPENAI_MODEL if DEFAULT_LLM_PROVIDER == "openai" else settings["model"])


if __name__ == "__main__":
    unittest.main()
