import pydantic_settings


class TestSettings(pydantic_settings.BaseSettings):
    DB_HOST: str = "127.0.0.1"
    DB_USER: str = "postgres"
    DB_NAME: str = "postgres"
    DB_PASSWORD: str = "superpassword"  # noqa: S105
    DB_CONNECT_TIMEOUT: int = 1


settings = TestSettings()
