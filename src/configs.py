from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    domain_name: str = "サポート対象サービス"
    assistant_role: str = "問い合わせ対応エージェント"
    knowledge_label: str = "製品ドキュメント"
    faq_label: str = "FAQ"
    max_challenge_count: int = 3

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )
