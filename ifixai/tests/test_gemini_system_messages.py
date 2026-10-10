"""All system frames survive Gemini's single-instruction request format."""

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("instructions", [["First frame", "Second frame"], ["Only frame"], []])
async def test_sdk_request_preserves_system_frames_in_order(monkeypatch, instructions):
    pytest.importorskip("google.generativeai")
    glm = pytest.importorskip("google.ai.generativelanguage")
    from ifixai.providers import gemini

    seen = []

    class OwnedTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def generate_content(self, request, **kwargs):
            seen.append("\n".join(part.text for part in request.system_instruction.parts))
            assert request.contents[0].role == "user"
            assert request.contents[0].parts[0].text == "Question"
            return glm.GenerateContentResponse(candidates=[glm.Candidate(
                content=glm.Content(parts=[glm.Part(text="Owned reply")]),
                finish_reason=glm.Candidate.FinishReason.STOP,
            )])

    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", lambda **kwargs: OwnedTransport())
    messages = [ChatMessage(role="system", content=text) for text in instructions]
    messages.append(ChatMessage(role="user", content="Question"))
    result = await gemini.GeminiProvider().send_message(messages,
        ProviderConfig(provider="gemini", api_key="synthetic-owned-key", max_retries=0))
    assert result == "Owned reply"
    assert seen == ["\n".join(instructions)]
