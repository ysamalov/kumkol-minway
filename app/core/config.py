from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── База данных ────────────────────────────────────────────────────────────
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "uto"

    # ── OpenRouter AI (optional) ───────────────────────────────────────────────
    openrouter_api_key: str = ""
    openrouter_model: str = "mistralai/mistral-7b-instruct"

    # ── Скорость техники ───────────────────────────────────────────────────────
    default_speed_kmh: float = 40.0        # fallback если скорость не вычислена из снапшотов
    min_speed_kmh: float = 1.0             # минимальная допустимая скорость (фильтр выбросов)
    max_speed_kmh: float = 150.0           # максимальная допустимая скорость (фильтр выбросов)

    # ── Рекомендации ──────────────────────────────────────────────────────────
    top_n_recommendations: int = 3

    # ── Смены ─────────────────────────────────────────────────────────────────
    shift_hours: int = 12
    day_shift_start: int = 8
    night_shift_start: int = 20

    # ── SLA дедлайны (часов от planned_start) ─────────────────────────────────
    sla_high_hours: float = 2.0
    sla_medium_hours: float = 5.0
    sla_low_hours: float = 12.0

    # ── Веса скоринга (сумма = 1.0) ───────────────────────────────────────────
    score_w_dist: float = 0.35
    score_w_wait: float = 0.25
    score_w_idle: float = 0.10
    score_w_prio: float = 0.20
    score_w_compat: float = 0.10
    score_dist_reference_km: float = 50.0

    # ── Мультизадача ──────────────────────────────────────────────────────────
    multitask_max_time_minutes: int = 480
    multitask_max_detour_ratio: float = 1.3

    # ── VRP solver ────────────────────────────────────────────────────────────
    vrp_default_time_limit_sec: int = 10
    vrp_medium_tw_cap_minutes: int = 480
    vrp_penalty_high: int = 100_000
    vrp_penalty_medium: int = 50_000
    vrp_penalty_low: int = 10_000

    # ── Лимиты задач ──────────────────────────────────────────────────────────
    max_multitask_tasks: int = 200       # макс. заявок для /api/multitask
    max_vrp_tasks: int = 200             # макс. заявок для /api/optimize

    # ── VRP техника ───────────────────────────────────────────────────────────
    vrp_max_vehicles: int = 30           # топ-N ближайших машин для OR-Tools
    vrp_max_group_size: int = 5          # макс. заявок в одной мультизадаче
    vrp_max_distance_km: float = 150.0   # машины дальше этого порога игнорируются

    # ── Таймауты (секунды) ────────────────────────────────────────────────────
    timeout_distance_matrix: float = 20.0   # построение матрицы расстояний
    timeout_route: float = 5.0              # /api/route
    timeout_grouping: float = 15.0          # мультизадача группировка
    timeout_recommend: float = 10.0         # /api/recommendations

    # ── Граф дорог ────────────────────────────────────────────────────────────
    road_graph_cache_size: int = 8192    # LRU-кэш кратчайших путей

    # ── Аналитика скоростей ───────────────────────────────────────────────────
    analytics_speed_floor: float = 0.3  # порог «машина стоит» (км/ч)
    analytics_speed_ceil: float = 130.0 # верхний фильтр выбросов скорости

    # ── Совместимость техники ─────────────────────────────────────────────────
    # True  — строгая: машина не назначается на несовместимый тип работ
    # False — мягкая:  несовместимость штрафуется в скоре, но не блокирует
    strict_compatibility: bool = False

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()