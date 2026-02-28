"""Agent B — A2A specialist agent that performs computations.

Receives tasks via A2A protocol on port 9002, returns computed results.
"""

import math
import re

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message


class ComputeAgent:
    """Simple compute agent that handles math tasks."""

    async def invoke(self, text: str) -> str:
        text_lower = text.lower().strip()

        # Factorial
        m = re.search(r"factorial\s+(?:of\s+)?(\d+)", text_lower)
        if m:
            n = int(m.group(1))
            return f"factorial({n}) = {math.factorial(n)}"

        # Fibonacci
        m = re.search(r"fibonacci\s+(?:of\s+)?(\d+)", text_lower)
        if m:
            n = int(m.group(1))
            a, b = 0, 1
            for _ in range(n):
                a, b = b, a + b
            return f"fibonacci({n}) = {a}"

        # Square
        m = re.search(r"square\s+(?:of\s+)?(\d+)", text_lower)
        if m:
            n = int(m.group(1))
            return f"square({n}) = {n * n}"

        # Fallback: try to evaluate as math expression
        m = re.search(r"(\d+)\s*([+\-*/])\s*(\d+)", text_lower)
        if m:
            a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
            ops = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0}
            result = ops.get(op, 0)
            return f"{a} {op} {b} = {result}"

        return f"I can compute factorial, fibonacci, square, or basic math. Got: {text}"


class ComputeAgentExecutor(AgentExecutor):
    """A2A executor wrapping the compute agent."""

    def __init__(self):
        self.agent = ComputeAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Extract text from the user message.
        # A2A SDK wraps parts in a RootModel — actual data is in part.root
        text = ""
        if context.message and context.message.parts:
            for part in context.message.parts:
                inner = getattr(part, "root", part)
                if hasattr(inner, "text"):
                    text = inner.text
                    break

        result = await self.agent.invoke(text)
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def create_app(port: int = 9002):
    skill = AgentSkill(
        id="compute",
        name="Math Computation",
        description="Computes factorial, fibonacci, square, and basic math",
        tags=["math", "compute"],
        examples=["factorial 10", "fibonacci 8", "square 7", "15 + 27"],
    )

    agent_card = AgentCard(
        name="Compute Agent B",
        description="A specialist agent that performs mathematical computations",
        url=f"http://localhost:{port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )

    handler = DefaultRequestHandler(
        agent_executor=ComputeAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    return server.build()


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=9002)
