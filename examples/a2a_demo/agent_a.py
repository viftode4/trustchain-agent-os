"""Agent A — A2A coordinator agent that delegates to Agent B.

Receives tasks via A2A protocol on port 9001, forwards them to Agent B,
and returns the result. In production, outbound calls would go through
the TrustChain sidecar proxy — here we call Agent B directly.
"""

from uuid import uuid4

import httpx
import uvicorn
from a2a.client import A2ACardResolver, A2AClient
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    MessageSendParams,
    SendMessageRequest,
)
from a2a.utils import new_agent_text_message

AGENT_B_URL = "http://localhost:9002"


class CoordinatorAgent:
    """Coordinator that delegates compute tasks to Agent B."""

    def __init__(self, agent_b_url: str = AGENT_B_URL):
        self.agent_b_url = agent_b_url

    async def invoke(self, text: str) -> str:
        async with httpx.AsyncClient() as httpx_client:
            # Resolve Agent B's card
            resolver = A2ACardResolver(
                httpx_client=httpx_client,
                base_url=self.agent_b_url,
            )
            agent_b_card = await resolver.get_agent_card()

            # Create A2A client for Agent B
            client = A2AClient(
                httpx_client=httpx_client,
                agent_card=agent_b_card,
            )

            # Send the task to Agent B
            request = SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(
                    message={
                        "role": "user",
                        "parts": [{"kind": "text", "text": text}],
                        "messageId": uuid4().hex,
                    }
                ),
            )

            response = await client.send_message(request)
            resp_data = response.model_dump(mode="json", exclude_none=True)

            # Extract the text result from Agent B's response
            result_obj = resp_data.get("result", {})
            # Handle both Task and Message result types
            if "status" in result_obj:
                # Task result — look for message in status
                messages = result_obj.get("status", {}).get("message", {})
                parts = messages.get("parts", []) if isinstance(messages, dict) else []
            elif "parts" in result_obj:
                # Direct message result
                parts = result_obj.get("parts", [])
            else:
                parts = []

            for part in parts:
                if part.get("kind") == "text":
                    return f"[via Agent B] {part['text']}"

            return f"[via Agent B] (no text in response: {resp_data})"


class CoordinatorAgentExecutor(AgentExecutor):
    """A2A executor wrapping the coordinator agent."""

    def __init__(self):
        self.agent = CoordinatorAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
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


def create_app(port: int = 9001):
    skill = AgentSkill(
        id="coordinate",
        name="Task Coordinator",
        description="Coordinates tasks by delegating to specialist agents",
        tags=["coordinator", "delegate"],
        examples=["factorial 10", "compute fibonacci 8"],
    )

    agent_card = AgentCard(
        name="Coordinator Agent A",
        description="A coordinator agent that delegates computation to specialist agents",
        url=f"http://localhost:{port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )

    handler = DefaultRequestHandler(
        agent_executor=CoordinatorAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    return server.build()


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=9001)
