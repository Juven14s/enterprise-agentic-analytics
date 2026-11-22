FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install '.[ui]'
RUN useradd --create-home analytics
USER analytics
EXPOSE 8000 8501
CMD ["uvicorn", "analytics_agent.api:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "16"]
