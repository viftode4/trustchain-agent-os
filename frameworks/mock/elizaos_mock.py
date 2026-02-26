"""Mock ElizaOS agent as FastMCP server."""

from fastmcp import FastMCP
from frameworks.base import FrameworkAdapter


class ElizaOSMock(FrameworkAdapter):
    """Simulates an ElizaOS agent (TypeScript framework, REST API bridge).

    In real ElizaOS:
        elizaos start --character character.json  # runs on port 3000
        POST /api/messaging/submit  # send messages via REST

    Here we expose the agent's capabilities as MCP tools.
    ElizaOS is the only TS-first framework; others are Python-native.
    """

    framework_name = "ElizaOS"
    framework_version = "1.7.2"

    @property
    def is_python_native(self) -> bool:
        return False  # TypeScript-first, REST API bridge from Python

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("ElizaOS Agent")

        @mcp.tool(name="eliza_message")
        async def eliza_message(content: str, room_id: str = "default") -> str:
            """Send a message to an ElizaOS agent and get a response.
            Simulates POST /api/messaging/submit.
            """
            return (
                f"[ElizaOS] Agent response in room '{room_id}':\n"
                f"Input: {content}\n"
                f"Response: Message processed by ElizaOS agent.\n"
                f"[AgentRuntime: plugin-openai, room={room_id}]"
            )

        @mcp.tool(name="eliza_knowledge_query")
        async def eliza_knowledge_query(query: str) -> str:
            """Query the ElizaOS agent's knowledge base (RAG).
            Simulates the @elizaos/plugin-knowledge integration.
            """
            return (
                f"[ElizaOS Knowledge] Query: {query}\n"
                f"Retrieved 3 relevant documents.\n"
                f"Summary: Knowledge base contains relevant information.\n"
                f"[plugin-knowledge: RAG pipeline]"
            )

        @mcp.tool(name="eliza_solana_action")
        async def eliza_solana_action(action: str, params: str = "{}") -> str:
            """Execute a Solana blockchain action through ElizaOS.
            Simulates @elizaos/plugin-solana integration.
            """
            return (
                f"[ElizaOS Solana] Action: {action}\n"
                f"Params: {params}\n"
                f"Result: Blockchain action simulated.\n"
                f"[plugin-solana: Web3 integration]"
            )

        return mcp

    def get_tool_names(self):
        return ["eliza_message", "eliza_knowledge_query", "eliza_solana_action"]
