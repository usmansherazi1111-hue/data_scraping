"""Wires settings, database, proxy pool and fetcher together."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .campaigns import OutboxSender, Sender, SmtpSender
from .config import Settings, load_settings
from .db import init_db, make_engine
from .scraping.fetcher import PoliteFetcher
from .scraping.proxy_pool import ProxyPool


@dataclass
class AppContext:
    settings: Settings
    sessions: sessionmaker[Session]
    pool: ProxyPool
    fetcher: PoliteFetcher
    _sender: Sender | None = field(default=None, repr=False)

    @classmethod
    def build(cls, config_path: str | Path | None = None, settings: Settings | None = None) -> AppContext:
        settings = settings or load_settings(config_path)
        sessions = init_db(make_engine(settings.database_url))
        p = settings.proxy
        pool = ProxyPool(
            p.urls,
            strategy=p.strategy,
            failure_threshold=p.failure_threshold,
            cooldown_seconds=p.cooldown_seconds,
            health_check_url=p.health_check_url,
        )
        return cls(settings, sessions, pool, PoliteFetcher(settings.scrape, pool))

    @property
    def sender(self) -> Sender:
        """SMTP when ``SMTP_HOST`` is set, otherwise a dry-run outbox folder."""
        if self._sender is None:
            import os

            self._sender = (
                SmtpSender()
                if os.getenv("SMTP_HOST")
                else OutboxSender(self.settings.data_dir / "outbox")
            )
        return self._sender
