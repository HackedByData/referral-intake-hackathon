import pytest
from pydantic import BaseModel, ValidationError

from app.llm import FALLBACK_BETA, LLMError, StructuredLLM
from tests.fakes import fake_client, fake_response


class Out(BaseModel):
    answer: str


def _validation_error() -> ValidationError:
    try:
        Out.model_validate({})
    except ValidationError as e:
        return e
    raise AssertionError


async def test_returns_parsed_output_and_sends_required_params():
    client = fake_client([fake_response(Out(answer="ok"))])
    llm = StructuredLLM(client=client, model="claude-opus-5-5")
    out = await llm.parse(system="sys", content=[{"type": "text", "text": "hi"}], output=Out, effort="low")
    assert out.answer == "ok"
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "low"}
    assert call["output_format"] is Out
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


async def test_refusal_raises():
    llm = StructuredLLM(client=fake_client([fake_response(None, stop_reason="refusal", category="bio")]))
    with pytest.raises(LLMError, match="declined"):
        await llm.parse(system="s", content=[], output=Out)


async def test_truncation_raises():
    llm = StructuredLLM(client=fake_client([fake_response(None, stop_reason="max_tokens")]))
    with pytest.raises(LLMError, match="truncated"):
        await llm.parse(system="s", content=[], output=Out)


async def test_retries_once_on_validation_error():
    client = fake_client([_validation_error(), fake_response(Out(answer="second"))])
    out = await StructuredLLM(client=client).parse(system="s", content=[], output=Out)
    assert out.answer == "second" and len(client.beta.messages.calls) == 2


async def test_gives_up_after_two_bad_outputs():
    client = fake_client([fake_response(None), fake_response(None)])
    with pytest.raises(LLMError, match="no structured output"):
        await StructuredLLM(client=client).parse(system="s", content=[], output=Out)


async def test_without_fallbacks_omits_beta_params():
    client = fake_client([fake_response(Out(answer="ok"))])
    await StructuredLLM(client=client, use_fallbacks=False).parse(system="s", content=[], output=Out)
    call = client.beta.messages.calls[0]
    assert "fallbacks" not in call and "betas" not in call
