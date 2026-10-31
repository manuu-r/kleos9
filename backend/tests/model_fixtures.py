"""SDK response objects for adapter tests; nothing here touches the network."""
import json

from google.genai.types import GenerateContentResponse
from openai.types.chat import ChatCompletion
from openai.types.responses import Response


def responses_reply(model, body, searches=0, input_tokens=100, output_tokens=50, cached=0):
    text = body if isinstance(body, str) else json.dumps(body)
    output = [{"type": "web_search_call", "id": f"ws_{n}", "status": "completed",
               "action": {"type": "search", "query": "fixture"}} for n in range(searches)]
    output.append({"type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": text, "annotations": []}]})
    return Response.model_validate({
        "id": "resp_fixture", "object": "response", "created_at": 1, "model": model, "status": "completed",
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "output": output,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens,
                  "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
        "error": None, "incomplete_details": None, "instructions": None, "metadata": {},
        "temperature": 1, "top_p": 1})


def chat_reply(model, body, input_tokens=100, output_tokens=50, cached=0):
    text = body if isinstance(body, str) else json.dumps(body)
    return ChatCompletion.model_validate({
        "id": "chatcmpl_fixture", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens,
                  "prompt_tokens_details": {"cached_tokens": cached}}})


def gemini_reply(model, body, input_tokens=100, output_tokens=50):
    text = body if isinstance(body, str) else json.dumps(body)
    return GenerateContentResponse.model_validate({
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": input_tokens, "candidatesTokenCount": output_tokens,
                          "totalTokenCount": input_tokens + output_tokens}, "modelVersion": model})


def patch_replies(monkeypatch, gateway, provider, model, reply, planning=False):
    """Replace the SDK call for one provider model; returns the list of captured requests."""
    if not planning:
        # These fixtures intentionally return completed SDK objects, never stream chunks.
        gateway.settings.engineering_streaming = False
    agent = (gateway.planning_agents if planning else gateway.agents)[(provider, model)]
    requests = []

    async def respond(*args, **kwargs):
        requests.append(kwargs)
        return reply(len(requests))
    client = agent.model.client
    if provider == "google":
        monkeypatch.setattr(client.aio.models, "generate_content", respond)
    else:
        monkeypatch.setattr((client.responses if provider == "meta" else client.chat.completions), "create", respond)
    return requests


def sdk_event_stream(client, events, cast_to, *, interrupt=None):
    """Use the actual OpenAI SDK SSE decoder without opening a connection."""
    import httpx2
    from openai import AsyncStream

    class Bytes(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for event in events:
                data = json.dumps(event).encode()
                yield b"data: " + data + b"\n\n"
            if interrupt:
                raise interrupt
            yield b"data: [DONE]\n\n"

    response = httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes(),
                               request=httpx2.Request("POST", "https://fixtures.invalid/stream"))
    return AsyncStream(cast_to=cast_to, response=response, client=client)


def responses_stream(client, model, body, output_tokens=50, interrupt=None):
    from openai.types.responses import ResponseStreamEvent

    reply = responses_reply(model, body, output_tokens=output_tokens).model_dump(mode="json")
    text = reply["output"][-1]["content"][0]["text"]
    item = {**reply["output"][-1], "content": []}
    events = [
        {"type": "response.created", "sequence_number": 0,
         "response": {**reply, "status": "in_progress", "output": [], "usage": None}},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": item},
    ]
    for offset in range(0, len(text), 512):
        events.append({"type": "response.output_text.delta", "sequence_number": len(events),
                       "item_id": item["id"], "output_index": 0, "content_index": 0,
                       "delta": text[offset:offset + 512], "logprobs": []})
    if not interrupt:
        events += [
            {"type": "response.output_item.done", "sequence_number": len(events), "output_index": 0,
             "item": reply["output"][-1]},
            {"type": "response.completed", "sequence_number": len(events) + 1, "response": reply},
        ]
    return sdk_event_stream(client, events, ResponseStreamEvent, interrupt=interrupt)


def chat_stream(client, model, body, output_tokens=50, interrupt=None):
    from openai.types.chat import ChatCompletionChunk

    reply = chat_reply(model, body, output_tokens=output_tokens).model_dump(mode="json")
    text = reply["choices"][0]["message"]["content"]
    common = {"id": reply["id"], "object": "chat.completion.chunk", "created": 1, "model": model}
    events = [{**common, "choices": [{"index": 0, "finish_reason": None, "delta": {"role": "assistant"}}]}]
    for offset in range(0, len(text), 512):
        events.append({**common, "choices": [{"index": 0, "finish_reason": None,
                                               "delta": {"content": text[offset:offset + 512]}}]})
    if not interrupt:
        events += [{**common, "choices": [{"index": 0, "finish_reason": "stop", "delta": {}}]},
                   {**common, "choices": [], "usage": reply["usage"]}]
    return sdk_event_stream(client, events, ChatCompletionChunk, interrupt=interrupt)
