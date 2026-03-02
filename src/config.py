from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_env: str = "development"
    log_level: str = "INFO"

    # GitLab
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/greenpipe"

    # Carbon Aware SDK
    carbon_aware_sdk_url: str = "http://localhost:5073"
    electricity_maps_api_key: str = ""
    watttime_user: str = ""
    watttime_password: str = ""


settings = Settings()
