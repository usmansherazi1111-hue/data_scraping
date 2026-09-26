"""Application settings, read from environment variables and an optional JSON file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(os.getenv("B2B_LEADS_DATA_DIR", "b2b_leads/data"))


@dataclass
class ScrapeSettings:
    """Politeness and reliability knobs for the scraper."""

    user_agent: str = "B2BLeadsBot/0.1"
    respect_robots_txt: bool = True
    # Minimum seconds between two requests to the same domain.
    min_delay_seconds: float = 2.0
    # Random extra delay added on top of min_delay, for realistic pacing.
    jitter_seconds: float = 1.5
    max_retries: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 60.0
    timeout_seconds: float = 30.0
    max_concurrent_domains: int = 4


@dataclass
class ProxySettings:
    """User-supplied proxy providers. Leave `urls` empty to connect directly."""

    urls: list[str] = field(default_factory=list)
    strategy: str = "round_robin"  # round_robin | random | least_used
    health_check_url: str = "https://httpbin.org/ip"
    health_check_interval_seconds: float = 300.0
    # Consecutive failures before a proxy is put on cooldown.
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0


@dataclass
class Settings:
    """Top-level settings object."""

    database_url: str = ""
    data_dir: Path = DEFAULT_DATA_DIR
    scrape: ScrapeSettings = field(default_factory=ScrapeSettings)
    proxy: ProxySettings = field(default_factory=ProxySettings)
    llm: dict[str, Any] = field(default_factory=dict)
    # CAN-SPAM requires a valid physical postal address in commercial email.
    sender_postal_address: str = ""
    unsubscribe_base_url: str = "http://localhost:8000/unsubscribe"

    def __post_init__(self) -> None:
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'b2b_leads.db'}"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Build settings from an optional JSON file, then environment overrides.

    Args:
        path: JSON config file. Defaults to ``$B2B_LEADS_CONFIG`` if set.

    Returns:
        The resolved settings.
    """
    raw: dict[str, Any] = {}
    path = path or os.getenv("B2B_LEADS_CONFIG")
    if path and Path(path).exists():
        raw = json.loads(Path(path).read_text(encoding="utf-8"))

    settings = Settings(
        database_url=raw.get("database_url", os.getenv("B2B_LEADS_DATABASE_URL", "")),
        data_dir=Path(raw.get("data_dir", DEFAULT_DATA_DIR)),
        scrape=ScrapeSettings(**raw.get("scrape", {})),
        proxy=ProxySettings(**raw.get("proxy", {})),
        llm=raw.get("llm", {}),
        sender_postal_address=raw.get(
            "sender_postal_address", os.getenv("B2B_LEADS_POSTAL_ADDRESS", "")
        ),
        unsubscribe_base_url=raw.get(
            "unsubscribe_base_url",
            os.getenv("B2B_LEADS_UNSUBSCRIBE_URL", "http://localhost:8000/unsubscribe"),
        ),
    )

    # Proxy URLs can also come from the environment (comma separated) so that
    # provider credentials never need to live in a committed file.
    env_proxies = os.getenv("B2B_LEADS_PROXIES")
    if env_proxies:
        settings.proxy.urls = [p.strip() for p in env_proxies.split(",") if p.strip()]

    if not settings.llm and os.getenv("OPENROUTER_API_KEY"):
        settings.llm = {
            "api_key": os.getenv("OPENROUTER_API_KEY"),
            "base_url": "https://openrouter.ai/api/v1",
            "model": os.getenv("B2B_LEADS_LLM_MODEL", "openai/gpt-oss-20b"),
        }
    elif settings.llm.get("api_key", "").startswith("env:"):
        settings.llm["api_key"] = os.getenv(settings.llm["api_key"][4:], "")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    return settings
