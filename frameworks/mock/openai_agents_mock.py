"""Mock OpenAI Agents SDK agent as FastMCP server."""

from fastmcp import FastMCP
from frameworks.base import FrameworkAdapter


class OpenAIAgentsMock(FrameworkAdapter):
    """Simulates an OpenAI Agents SDK agent with handoffs.

    In real OpenAI Agents:
        agent = Agent(name="triage", handoffs=[billing, refund])
        result = await Runner.run(agent, "I need a refund")

    Here we expose the agent's capabilities as MCP tools.
    """

    framework_name = "OpenAI Agents SDK"
    framework_version = "0.10.1"

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("OpenAI Agent")

        @mcp.tool(name="triage_request")
        async def triage_request(user_message: str) -> str:
            """Triage a user request and route to the appropriate specialist agent.
            Simulates the OpenAI Agents SDK handoff mechanism.
            """
            # Simulate triage logic
            if any(w in user_message.lower() for w in ["refund", "return", "money back"]):
                specialist = "refund_agent"
            elif any(w in user_message.lower() for w in ["bill", "charge", "invoice"]):
                specialist = "billing_agent"
            else:
                specialist = "general_agent"
            return (
                f"[OpenAI Agents] Triage: routed to {specialist}\n"
                f"Input: '{user_message}'\n"
                f"[Handoff: transfer_to_{specialist}]"
            )

        @mcp.tool(name="run_agent")
        async def run_agent(task: str, agent_name: str = "assistant") -> str:
            """Run a task through an OpenAI agent. Returns the agent's response."""
            return (
                f"[OpenAI Agents] Agent '{agent_name}' completed task.\n"
                f"Task: {task}\n"
                f"Result: Task processed successfully.\n"
                f"[Runner.run completed, model=gpt-4o]"
            )

        @mcp.tool(name="agent_web_search")
        async def agent_web_search(query: str) -> str:
            """Search the web using the OpenAI agent's built-in WebSearchTool."""
            return (
                f"[OpenAI Agents WebSearchTool] Results for '{query}':\n"
                f"1. Relevant result about {query}\n"
                f"2. Additional context on {query}\n"
                f"[HostedMCPTool: web_search]"
            )

        return mcp

    def get_tool_names(self):
        return ["triage_request", "run_agent", "agent_web_search"]
