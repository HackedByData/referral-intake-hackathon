"""The only place that talks to Claude. Structured output, adaptive thinking, refusal handling."""
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """A Claude call that could not produce a usable structured answer."""


class StructuredLLM:
    def __init__(self, client: Any | None = None, model: str | None = None, use_fallbacks: bool = True):
        self._client = client
        self.model = model or get_settings().model
        self.use_fallbacks = use_fallbacks

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(max_retries=3)
        return self._client

    async def parse(self, *, system: str, content: list[dict], output: type[T],
                    effort: str = "high", max_tokens: int = 16000) -> T:
        last_error: Exception | None = None
        for _attempt in range(2):
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output,
                output_config={"effort": effort},
                thinking={"type": "adaptive"},
            )
            if self.use_fallbacks:
                kwargs["betas"] = [FALLBACK_BETA]
                kwargs["fallbacks"] = "default"
            try:
                response = await self.client.beta.messages.parse(**kwargs)
            except ValidationError as e:  # model JSON did not match the schema
                last_error = e
                continue
            except Exception as e:  # server rejected fallbacks on this account: retry without them
                if self.use_fallbacks and "fallback" in str(e).lower():
                    log.warning("Server-side fallbacks rejected; continuing without them: %s", e)
                    self.use_fallbacks = False
                    continue
                raise
            if response.stop_reason == "refusal":
                category = getattr(getattr(response, "stop_details", None), "category", None)
                raise LLMError(f"Model declined the request (category={category})")
            if response.stop_reason == "max_tokens":
                raise LLMError("Model output was truncated (max_tokens)")
            if response.parsed_output is not None:
                usage = getattr(response, "usage", None)
                log.info("claude usage: in=%s out=%s cache_read=%s cache_write=%s",
                         getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                         getattr(usage, "cache_read_input_tokens", None),
                         getattr(usage, "cache_creation_input_tokens", None))
                return response.parsed_output
            last_error = LLMError("Model returned no structured output")
        raise LLMError(f"Structured output failed after retry: no structured output ({last_error})")
