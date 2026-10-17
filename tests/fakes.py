"""Test doubles for StructuredLLM."""
from types import SimpleNamespace


class FakeLLM:
    """Same interface as app.llm.StructuredLLM. handler(output_type, system, content) -> instance or Exception."""

    model = "fake-model"

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def parse(self, *, system, content, output, effort="high", max_tokens=16000):
        self.calls.append({"system": system, "content": content, "output": output, "effort": effort})
        result = self.handler(output, system, content)
        if isinstance(result, Exception):
            raise result
        return result


class FakeMessages:
    """Stands in for client.beta.messages: returns queued responses or raises queued exceptions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(responses) -> SimpleNamespace:
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(responses)))


def fake_response(parsed, stop_reason="end_turn", category=None) -> SimpleNamespace:
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason,
                           stop_details=SimpleNamespace(category=category) if category else None)
