"""Unit tests for each provider's OpenAI-wire <-> native tool-calling
translation. These are the pieces that had zero coverage before the
tool-calling bugfix -- exercised directly here (no network calls), separate
from the end-to-end gateway-level tests in test_gateway.py."""
from __future__ import annotations

from app.providers.anthropic_provider import (
    _extract_anthropic_tool_calls,
    _to_anthropic_message,
    _to_anthropic_tools,
)
from app.providers.gemini_provider import (
    _extract_text,
    _extract_tool_calls,
    _to_gemini_content,
    _to_gemini_tools,
    _tool_call_id_to_name,
)
from app.providers.ollama_provider import (
    _normalize_ollama_tool_calls,
    _prepare_messages_for_ollama,
)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def test_normalize_ollama_tool_calls_encodes_arguments_as_json_string():
    raw = [{"id": "call_1", "function": {"name": "crm_lookup", "arguments": {"identifier": "cust_001"}}}]
    normalized = _normalize_ollama_tool_calls(raw)
    assert normalized[0]["function"]["arguments"] == '{"identifier": "cust_001"}'


def test_normalize_ollama_tool_calls_generates_id_when_missing():
    raw = [{"function": {"name": "crm_lookup", "arguments": {}}}]
    normalized = _normalize_ollama_tool_calls(raw)
    assert normalized[0]["id"].startswith("call_")


def test_normalize_ollama_tool_calls_none_when_no_calls():
    assert _normalize_ollama_tool_calls(None) is None
    assert _normalize_ollama_tool_calls([]) is None


def test_prepare_messages_for_ollama_parses_json_string_arguments_back_to_dict():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "crm_lookup", "arguments": '{"identifier": "cust_001"}'}}
            ],
        }
    ]
    prepared = _prepare_messages_for_ollama(messages)
    assert prepared[0]["content"] == ""  # never null
    assert prepared[0]["tool_calls"][0]["function"]["arguments"] == {"identifier": "cust_001"}


def test_prepare_messages_for_ollama_leaves_plain_messages_untouched():
    messages = [{"role": "user", "content": "hi"}]
    assert _prepare_messages_for_ollama(messages) == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def test_to_anthropic_message_converts_tool_role_to_user_tool_result():
    msg = _to_anthropic_message({"role": "tool", "tool_call_id": "call_1", "content": "Pro plan"})
    assert msg["role"] == "user"
    assert msg["content"][0]["type"] == "tool_result"
    assert msg["content"][0]["tool_use_id"] == "call_1"
    assert msg["content"][0]["content"] == "Pro plan"


def test_to_anthropic_message_converts_assistant_tool_calls_to_tool_use_block():
    msg = _to_anthropic_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "crm_lookup", "arguments": '{"identifier": "cust_001"}'}}
            ],
        }
    )
    assert msg["role"] == "assistant"
    block = msg["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "crm_lookup"
    assert block["input"] == {"identifier": "cust_001"}


def test_to_anthropic_tools_maps_parameters_to_input_schema():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "crm_lookup",
                "description": "desc",
                "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}}},
            },
        }
    ]
    converted = _to_anthropic_tools(tools)
    assert converted[0]["name"] == "crm_lookup"
    assert converted[0]["input_schema"] == {"type": "object", "properties": {"identifier": {"type": "string"}}}


def test_to_anthropic_tools_none_when_no_tools():
    assert _to_anthropic_tools(None) is None
    assert _to_anthropic_tools([]) is None


def test_extract_anthropic_tool_calls_from_tool_use_blocks():
    class FakeBlock:
        def __init__(self, type_, **kwargs):
            self.type = type_
            for k, v in kwargs.items():
                setattr(self, k, v)

    blocks = [
        FakeBlock("text", text="thinking..."),
        FakeBlock("tool_use", id="toolu_1", name="crm_lookup", input={"identifier": "cust_001"}),
    ]
    calls = _extract_anthropic_tool_calls(blocks)
    assert calls[0]["id"] == "toolu_1"
    assert calls[0]["function"]["name"] == "crm_lookup"
    assert calls[0]["function"]["arguments"] == '{"identifier": "cust_001"}'


def test_extract_anthropic_tool_calls_none_when_only_text():
    class FakeBlock:
        type = "text"
        text = "hello"

    assert _extract_anthropic_tool_calls([FakeBlock()]) is None


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


def test_tool_call_id_to_name_maps_across_the_message_list():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "call_1", "function": {"name": "crm_lookup", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "Pro plan"},
    ]
    assert _tool_call_id_to_name(messages) == {"call_1": "crm_lookup"}


def test_to_gemini_content_converts_tool_role_to_user_function_response():
    id_to_name = {"call_1": "crm_lookup"}
    content = _to_gemini_content({"role": "tool", "tool_call_id": "call_1", "content": "Pro plan"}, id_to_name)
    assert content.role == "user"
    fr = content.parts[0].function_response
    assert fr.name == "crm_lookup"
    assert fr.response == {"result": "Pro plan"}


def test_to_gemini_content_converts_assistant_tool_calls_to_function_call_part():
    content = _to_gemini_content(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "crm_lookup", "arguments": '{"identifier": "cust_001"}'}}
            ],
        },
        id_to_name={},
    )
    assert content.role == "model"
    fc = content.parts[0].function_call
    assert fc.name == "crm_lookup"
    assert dict(fc.args) == {"identifier": "cust_001"}


def test_to_gemini_content_maps_assistant_role_to_model():
    content = _to_gemini_content({"role": "assistant", "content": "hi"}, id_to_name={})
    assert content.role == "model"


def test_to_gemini_content_returns_none_for_system_role():
    assert _to_gemini_content({"role": "system", "content": "be nice"}, id_to_name={}) is None


def test_to_gemini_tools_maps_parameters_directly():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "crm_lookup",
                "description": "desc",
                "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}}},
            },
        }
    ]
    converted = _to_gemini_tools(tools)
    decl = converted[0].function_declarations[0]
    assert decl.name == "crm_lookup"
    # google-genai parses `parameters` into its own typed `Schema` object
    # rather than storing the raw dict verbatim (uppercasing the JSON-schema
    # type names in the process) -- dump it back to a plain dict to compare
    # semantically instead of asserting on the wrapper type directly.
    assert decl.parameters.model_dump(exclude_none=True, mode="json") == {
        "type": "OBJECT",
        "properties": {"identifier": {"type": "STRING"}},
    }


def test_to_gemini_tools_none_when_no_tools():
    assert _to_gemini_tools(None) is None
    assert _to_gemini_tools([]) is None


def test_extract_tool_calls_from_function_call_parts():
    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class FakeFunctionCall:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, content):
            self.content = content

    candidate = FakeCandidate(
        FakeContent([FakePart(text="thinking..."), FakePart(function_call=FakeFunctionCall("crm_lookup", {"identifier": "cust_001"}))])
    )
    calls = _extract_tool_calls(candidate)
    assert calls[0]["function"]["name"] == "crm_lookup"
    assert calls[0]["function"]["arguments"] == '{"identifier": "cust_001"}'


def test_extract_tool_calls_encodes_thought_signature_into_id_when_present():
    """Regression test for a real incident: Gemini's "thinking" models attach
    an opaque thought_signature to a function-call part and reject a later
    turn that replays the call without it (400 INVALID_ARGUMENT). The
    OpenAI-wire tool_calls shape has no field for it, so it's smuggled
    inside `id` -- see the matching decode test below."""

    class FakePart:
        def __init__(self, function_call=None, thought_signature=None):
            self.text = None
            self.function_call = function_call
            self.thought_signature = thought_signature

    class FakeFunctionCall:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, content):
            self.content = content

    candidate = FakeCandidate(
        FakeContent([FakePart(function_call=FakeFunctionCall("crm_lookup", {}), thought_signature=b"sig-bytes")])
    )
    calls = _extract_tool_calls(candidate)
    call_id = calls[0]["id"]
    assert "::" in call_id
    import base64

    _, _, sig_b64 = call_id.partition("::")
    assert base64.b64decode(sig_b64) == b"sig-bytes"


def test_to_gemini_content_decodes_thought_signature_from_id_suffix():
    import base64

    sig_b64 = base64.b64encode(b"sig-bytes").decode("ascii")
    content = _to_gemini_content(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_1::{sig_b64}",
                    "function": {"name": "crm_lookup", "arguments": "{}"},
                }
            ],
        },
        id_to_name={},
    )
    assert content.parts[0].thought_signature == b"sig-bytes"


def test_to_gemini_content_leaves_thought_signature_unset_without_suffix():
    content = _to_gemini_content(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "crm_lookup", "arguments": "{}"}}],
        },
        id_to_name={},
    )
    assert content.parts[0].thought_signature is None


def test_extract_tool_calls_none_when_only_text():
    class FakePart:
        text = "hello"
        function_call = None

    class FakeContent:
        parts = [FakePart()]

    class FakeCandidate:
        content = FakeContent()

    assert _extract_tool_calls(FakeCandidate()) is None


def test_extract_text_concatenates_text_parts():
    class FakePart:
        def __init__(self, text):
            self.text = text

    class FakeContent:
        parts = [FakePart("Hello, "), FakePart("world")]

    class FakeCandidate:
        content = FakeContent()

    assert _extract_text(FakeCandidate()) == "Hello, world"
