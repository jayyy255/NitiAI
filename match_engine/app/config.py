"""Runtime configuration, read from environment variables (or a .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../server/.env"), extra="ignore")

    database_url: str = ""
    # Shared secret the Express server sends in X-Internal-Token. Empty disables the check (local dev only).
    match_engine_token: str = ""

    # Models. "fastembed" uses ONNX models; "hash" is a deterministic offline fallback used in tests.
    embedder: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    reranker: str = "fastembed"  # "fastembed" | "none"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_cache_dir: str = ".cache/models"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    rapidapi_key: str = ""
    rapidapi_host: str = "linkedin-job-search-api.p.rapidapi.com"
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "in"

    # Ingestion
    ingest_enabled: bool = False
    ingest_interval_hours: float = 6.0
    ingest_queries: str = "software engineer,data scientist,backend developer,frontend developer,machine learning engineer,devops engineer,data analyst,full stack developer"
    ingest_location: str = "India"
    stale_after_days: int = 21
    max_posting_age_days: int = 45

    # Retrieval
    rrf_k: int = 60
    candidate_pool: int = 100
    rerank_top_n: int = 50

    ltr_weights_path: str = "ltr_weights.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
