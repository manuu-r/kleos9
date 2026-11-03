"""Fake-provider helpers: a specialist turn offers every tool as its own function plus `finish` and `block`."""


def provider_name(body):
    """The schema name a scripted provider keys its replies on; 'Action' marks a specialist turn."""
    tools = body.get('tools', [])
    if any(tool['function']['name'] == 'complete_task' for tool in tools):
        return 'Action'
    return tools[0]['function']['name'] if tools else None


def function_call(name, payload):
    """Turn a scripted Action payload into the function call a provider would return for it."""
    if name != 'Action':
        return name, payload
    if payload['action'] == 'tool':
        return payload['tool'].replace('.', '__'), payload.get('arguments', {})
    return ('complete_task' if payload['action'] == 'finish' else 'block_task'), {'summary': payload.get('summary', '')}
