from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_env: str = "development"
    log_level: str = "INFO"

    # GitLab
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""
    # Secret token configured on the GitLab webhook (Settings → Webhooks → Secret token).
    # When set, every incoming webhook request must include the matching
    # X-Gitlab-Token header, rejecting forged/replayed events.
    gitlab_webhook_secret: str = ""

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/greenpipe"

    # Carbon Aware SDK
    carbon_aware_sdk_url: str = "http://localhost:5073"
    electricity_maps_api_key: str = ""
    watttime_user: str = ""
    watttime_password: str = ""

    # Auto-deferral policy
    # Mode: "recommend-only" | "approval-required" | "auto-execute"
    greenpipe_defer_mode: str = "recommend-only"
    # Minimum carbon savings (%) to trigger deferral action
    greenpipe_min_savings_pct: float = 20.0
    # Maximum hours a pipeline may be deferred
    greenpipe_max_delay_hours: int = 24
    # Comma-separated branch patterns that must never be deferred
    greenpipe_protected_branches: str = "main,master,release*"
    # Comma-separated environment names that must never be deferred
    greenpipe_protected_envs: str = "production,staging"


settings = Settings()
