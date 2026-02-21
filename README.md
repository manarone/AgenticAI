![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/manarone/AgenticAI?utm_source=oss&utm_medium=github&utm_campaign=manarone%2FAgenticAI&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

# AgenticAI

Project repository for AgenticAI.

## CI/CD

This repo is configured to deploy to Coolify after CI passes on `main`.

Required GitHub configuration:

- Repository variable `COOLIFY_API_BASE` (example: `http://10.100.0.7:8000/api/v1`)
- Repository variable `COOLIFY_APP_UUID` (Coolify app UUID)
- Repository secret `COOLIFY_TOKEN` (Coolify API token)
