from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Price = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANALYTICS_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    backend: Literal["sqlite", "postgres"] = "sqlite"
    mode: Literal["demo", "llm"] = "demo"
    pg_host: str = "localhost"
    pg_port: int = Field(default=5432, ge=1, le=65535)
    pg_database: str = "analytics"
    pg_user: str = "analytics_reader"
    pg_password: SecretStr = SecretStr("local-reader-demo-only")
    llm_model: str = "gpt-4.1-mini"
    llm_base_url: str | None = None
    llm_output: Literal["tool", "sql"] = "tool"
    llm_timeout_seconds: int = Field(default=25, ge=1, le=90)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    langsmith: bool = False
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(
        default="enterprise-agentic-analytics", validation_alias="LANGSMITH_PROJECT"
    )
    input_usd_per_million: Price | None = None
    output_usd_per_million: Price | None = None
    max_rows: int = Field(default=100, ge=1, le=10_000)
    timeout_ms: int = Field(default=3000, ge=100, le=30_000)
    max_corrections: int = Field(default=2, ge=0, le=3)

    @model_validator(mode="after")
    def check_credentials(self):
        if self.mode == "llm" and not self.openai_api_key and not self.llm_base_url:
            raise ValueError("Set OPENAI_API_KEY or configure an OpenAI-compatible endpoint.")
        if self.langsmith and not self.langsmith_api_key:
            raise ValueError("Set LANGSMITH_API_KEY to enable LangSmith.")
        return self
