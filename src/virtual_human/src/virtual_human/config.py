"""配置: pydantic-settings + dotenv, 从项目根 .env 读取.

用法:
    from virtual_human.config import settings
    settings.pg_dsn  # postgresql+psycopg://...
"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 位于项目根 (包目录上级)
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL
    pg_host: str = Field(default="127.0.0.1", alias="PGHOST")
    pg_port: int = Field(default=55432, alias="PGPORT")
    pg_user: str = Field(alias="PGUSER")
    pg_password: str = Field(alias="PGPASSWORD")
    pg_database: str = Field(alias="PGDATABASE")

    # LLM (对话 + 骨架生成)
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", alias="VH_LLM_MODEL")

    # RustFS (S3 兼容) 对象存储 —— agent 知识文档
    rustfs_endpoint: str = Field(default="", alias="RUSTFS_ENDPOINT")
    rustfs_access_key: str = Field(default="", alias="RUSTFS_ACCESS_KEY")
    rustfs_secret_key: str = Field(default="", alias="RUSTFS_SECRET_KEY")
    rustfs_bucket: str = Field(default="vh-docs", alias="RUSTFS_BUCKET")
    rustfs_region: str = Field(default="us-east-1", alias="RUSTFS_REGION")

    # Embedding (可选, 默认关闭: 不生成向量、检索不走向量召回)
    embed_enabled: bool = Field(default=False, alias="VH_EMBED_ENABLED")
    embed_model: str = Field(default="text-embedding-3-small", alias="VH_EMBED_MODEL")

    @property
    def has_llm(self) -> bool:
        """是否配置了 LLM 凭证 (决定 generate 走真实 LLM 还是 mock)."""
        return bool(self.openai_api_key)

    @property
    def object_storage_enabled(self) -> bool:
        """是否配置了 RustFS 对象存储 (决定 agent 文档工具是否挂载)."""
        return bool(self.rustfs_endpoint and self.rustfs_access_key)

    @property
    def pg_dsn(self) -> str:
        """psycopg 原生 DSN (key=value 形式)."""
        return (
            f"host={self.pg_host} port={self.pg_port}"
            f" user={self.pg_user} password={self.pg_password}"
            f" dbname={self.pg_database}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 同步 engine URL (psycopg3)."""
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def async_sqlalchemy_url(self) -> str:
        """SQLAlchemy 异步 engine URL (psycopg3 async)."""
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
