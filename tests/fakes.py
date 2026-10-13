"""A scripted stand-in for the OpenAI client, so agent scenarios are deterministic and offline."""
import json
from types import SimpleNamespace as NS


def tool(name: str, **args):
    return ("tool", name, args)


def text(content: str):
    return ("text", content)


class FakeLLM:
    """Each create() pops the next step: a text reply, or a list of tool calls (one model turn)."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []  # (model, messages) per create()
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, *, model, messages, tools=None, **_):
        self.calls.append((model, [dict(m) for m in messages]))
        step = self.steps.pop(0)
        steps = step if isinstance(step, list) else [step]
        if steps[0][0] == "text":
            msg = NS(content=steps[0][1], tool_calls=None)
        else:
            msg = NS(content="", tool_calls=[
                NS(id=f"call_{len(self.calls)}_{i}", type="function", function=NS(name=n, arguments=json.dumps(a)))
                for i, (_, n, a) in enumerate(steps)
            ])
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=100, completion_tokens=20, total_tokens=120))

    def tool_results(self, call_index: int) -> list[dict]:
        """Tool results new in a given create() call (those after the last assistant message)."""
        msgs = self.calls[call_index][1]
        last = max(i for i, m in enumerate(msgs) if m["role"] != "tool")
        return [json.loads(m["content"]) for m in msgs[last + 1:]]
