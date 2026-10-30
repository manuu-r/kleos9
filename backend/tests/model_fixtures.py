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
