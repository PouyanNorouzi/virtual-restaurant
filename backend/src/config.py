"""Backend configuration, sourced from environment variables (see
docker-compose.yml). All settings have sane local-dev defaults except the
broker password, which must always be supplied explicitly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    broker_host: str = "localhost"
    broker_port: int = 1883
    broker_username: str = "backend"
    broker_password: str

    num_tables: int = 4
    min_delay_seconds: float = 10.0
    max_delay_seconds: float = 30.0
    max_food_name_len: int = 200
    max_pending_per_table: int = 5

    # Total ordering+eating budget before a seated (not-yet-finished) session
    # is eligible for eviction, but only once someone else actually needs the
    # table - see SeatingService's dawdle sweep.
    max_dining_seconds: float = 300.0
    eviction_warning_grace_seconds: float = 20.0
    dawdle_check_interval_seconds: float = 5.0

    reconnect_initial_backoff_seconds: float = 1.0
    reconnect_max_backoff_seconds: float = 30.0

    log_level: str = "INFO"
