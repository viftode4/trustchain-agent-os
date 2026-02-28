"""Mock Google ADK agent as FastMCP server."""

from fastmcp import FastMCP
from tc_frameworks.base import FrameworkAdapter


class GoogleADKMock(FrameworkAdapter):
    """Simulates a Google ADK agent with A2A protocol support.

    In real Google ADK:
        agent = LlmAgent(name="agent", model="gemini-2.0-flash", tools=[...])
        runner = Runner(agent=agent, session_service=InMemorySessionService())

    Here we expose the agent's capabilities as MCP tools.
    """

    framework_name = "Google ADK"
    framework_version = "1.25.1"

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Google ADK Agent")

        @mcp.tool(name="adk_agent_run")
        async def adk_agent_run(message: str) -> str:
            """Run a message through a Google ADK LlmAgent.
            Simulates the Runner.run_async execution loop.
            """
            return (
                f"[Google ADK] Processing: {message}\n"
                f"Model: gemini-2.0-flash\n"
                f"Session: in-memory (user1234)\n"
                f"Response: Message processed successfully.\n"
                f"[Runner.run_async complete]"
            )

        @mcp.tool(name="a2a_send_task")
        async def a2a_send_task(agent_url: str, task: str) -> str:
            """Send a task to a remote agent via the A2A protocol.
            Simulates the Agent-to-Agent JSON-RPC communication.
            """
            return (
                f"[A2A Protocol] SendMessage to {agent_url}\n"
                f"Task: {task}\n"
                f"Status: WORKING -> COMPLETED\n"
                f"Agent Card: {agent_url}/.well-known/agent.json\n"
                f"[JSON-RPC 2.0, HTTP transport]"
            )

        @mcp.tool(name="adk_sub_agent")
        async def adk_sub_agent(task: str, sub_agent: str = "researcher") -> str:
            """Delegate to a sub-agent in the ADK agent hierarchy."""
            return (
                f"[Google ADK] Delegating to sub_agent='{sub_agent}'\n"
                f"Task: {task}\n"
                f"Sub-agent completed: {sub_agent} returned results.\n"
                f"[SequentialAgent pipeline]"
            )

        return mcp

    def get_tool_names(self):
        return ["adk_agent_run", "a2a_send_task", "adk_sub_agent"]
