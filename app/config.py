"""Runtime settings, read from the environment (and .env) on every call so tests can override them."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    model: str
    data_dir: Path
    packets_dir: Path | None
    hold_ttl_hours: float
    ocr_concurrency: int
    rules_concurrency: int
    criteria_path: Path
    seed_mode: str = "poc"  # poc | demo | none

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"


def get_settings() -> Settings:
    packets = os.getenv("PACKETS_DIR")
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("MODEL", "claude-opus-5-5"),
        data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))),
        packets_dir=Path(packets) if packets else None,
        hold_ttl_hours=float(os.getenv("HOLD_TTL_HOURS", "4")),
        ocr_concurrency=int(os.getenv("OCR_CONCURRENCY", "8")),
        rules_concurrency=int(os.getenv("RULES_CONCURRENCY", "8")),
        criteria_path=ROOT / "criteria" / "admission-criteria.json",
        seed_mode=os.getenv("SEED_MODE", "poc").strip().lower(),
    )
