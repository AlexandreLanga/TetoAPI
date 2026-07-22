import unittest

from fastapi import UploadFile

from app.core.config import DEFAULT_LLM_PROVIDER, OPENAI_MODEL
from app.services.image_analysis import (
    extract_json_payload,
    get_model_settings,
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

    def test_validate_image_files_requires_three_images(self):
        files = [
            UploadFile(filename="a.jpg", file=__import__("io").BytesIO(b"x"))
            for _ in range(2)
        ]

        with self.assertRaises(ValueError):
            validate_image_files(files)

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
