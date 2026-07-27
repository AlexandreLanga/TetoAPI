# TetoAPI

API FastAPI para analisar imagens de telhados com modelos de IA e gerar um relatório estruturado ou um PDF. A análise identifica apenas evidências visuais; ela não substitui uma inspeção técnica presencial.

## Requisitos

- Python 3.10 ou superior
- Uma chave de API da OpenAI ou do Google Gemini

## Instalação e configuração

Crie e ative um ambiente virtual, instale as dependências e copie o arquivo de configuração:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha no `.env` a chave do provedor que será utilizado. Por padrão, a API usa OpenAI:

```dotenv
DEFAULT_LLM_PROVIDER=openai
OPENAI_API_KEY=sua_chave
OPENAI_MODEL=gpt-4o-mini
```

Para Gemini, defina `DEFAULT_LLM_PROVIDER=google` e configure `GOOGLE_API_KEY`. Os valores de `provider` e `model` enviados no formulário substituem os padrões apenas naquela requisição.

## Execução

```powershell
py -m uvicorn app.main:app --port 8080
```

Com o servidor ativo, a documentação interativa fica em `http://127.0.0.1:8000/docs`.

## Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/api/health` | Confirma que a API está disponível. |
| `POST` | `/api/analyze` | Retorna a análise em JSON. |
| `POST` | `/api/analyze/pdf` | Retorna a análise em um arquivo PDF. |

Os endpoints de análise aceitam `multipart/form-data` com:

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `prompt` | Sim | Instruções adicionais para a análise. |
| `files` | Sim | Uma ou mais imagens JPEG, PNG ou WebP. Repita o campo para enviar várias imagens. |
| `provider` | Não | `openai` ou `google`. |
| `model` | Não | Nome do modelo do provedor escolhido. |

Exemplo para obter JSON:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/analyze `
  -F "prompt=Verifique sinais de infiltração" `
  -F "files=@C:\imagens\telhado-1.jpg" `
  -F "files=@C:\imagens\telhado-2.jpg" `
  -F "provider=openai"
```

Para baixar o PDF, altere a rota para `/api/analyze/pdf` e adicione `-o analysis.pdf` ao comando.

## Respostas e erros

`/api/analyze` retorna um objeto com `message`, `result` e `pdf_url`. O campo `result` inclui a condição geral do telhado, problemas encontrados, prioridades de manutenção e limitações da análise. Cada problema pode conter `image_name` e coordenadas aproximadas.

Erros de validação retornam `400`; limites de uso, `429`; indisponibilidade do provedor, `502`; e tempo excedido, `504`.

## Desenvolvimento

Execute os testes com:

```powershell
python -m unittest discover -s tests -v
```

Mantenha segredos exclusivamente no `.env`, que não é versionado. As regras de análise e o contrato JSON esperado pelo modelo estão em `app/prompts/roof_images.py`.
