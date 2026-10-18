from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    database_url: str = ""

    chat_model: str = "gemini-3.5-flash"
    fallback_chat_model: str = "gemini-3.1-flash-lite"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768

    top_k: int = 6
    min_score: float = 0.55
    history_turns: int = 6

    max_message_chars: int = 1000
    rate_limit_per_min: int = 20
    show_demo_logins: bool = True
    admin_username: str = ""
    admin_password: str = ""
    admin_name: str = "Syed Usman Ali"

    hr_contact: str = "hr@zakheera.ai (Extension 2200) or the HR Helpdesk on the ZakheeraHR portal"


settings = Settings()
