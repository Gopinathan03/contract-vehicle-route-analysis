from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "abtagent_local"
    db_user: str = "postgres"
    db_password: str = ""
    db_schema: str = "dbo"
    hub_station_codes: str = "CBETR,SALTR,TPJTR,MASTR"
    load_to_capacity_factor: float | None = None
    good_utilization_min_pct: float = 70
    attention_utilization_min_pct: float = 40
    attention_max_travel_hours: float = 12
    poor_max_travel_hours: float = 18

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def station_codes(self) -> tuple[str, str, str, str]:
        codes = tuple(part.strip().upper() for part in self.hub_station_codes.split(",") if part.strip())
        if len(codes) != 4:
            raise ValueError("HUB_STATION_CODES must contain exactly four comma-separated station codes")
        return codes  # type: ignore[return-value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
