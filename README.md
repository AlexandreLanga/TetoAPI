# Teto
Teto is an AI service for roof image analysis, created to reduce labor for identifying water infiltration and other related issues.

## Estrutura base da API
Esta implementação fornece uma base inicial em Python com:
- FastAPI para exposição de endpoints
- LangChain + GPT para análise de imagens
- Geração de relatório em PDF
- Endpoint para upload de imagem e prompt

## Instalação
```bash
pip install -r requirements.txt
```

## Configuração
Copie o arquivo .env.example para .env e informe sua chave da OpenAI:
```bash
copy .env.example .env
```

## Execução
```bash
uvicorn app.main:app --reload
```

## Endpoint principal
- POST /api/analyze
  - Form-data com:
    - prompt: texto com instruções para a análise
    - file: imagem (JPEG, PNG ou WebP)

A resposta é um arquivo PDF gerado com o resultado da análise.

