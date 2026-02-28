"""Real AutoGen/AG2 adapter — wraps an AG2 group chat as a trust-gated MCP server.

Requires: pip install ag2[openai]

Usage:
    from tc_frameworks.adapters.autogen_adapter import AutoGenAdapter

    adapter = AutoGenAdapter(
        agents_config=[
            {"name": "planner", "system_message": "You create plans."},
            {"name": "coder", "system_message": "You write code."},
        ],
        llm_config={"model": "gpt-4o", "api_key": "sk-..."},
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class AutoGenAdapter(FrameworkAdapter):
    """Wraps a real AG2 multi-agent group chat as a FastMCP server."""

    framework_name = "AutoGen/AG2"
    framework_version = "0.11.0"

    def __init__(
        self,
        agents_config: Optional[List[Dict[str, Any]]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.agents_config = agents_config or [
            {"name": "assistant", "system_message": "You are a helpful assistant."},
        ]
        self.llm_config = llm_config or {"model": "gpt-4o-mini"}

    def _build_agents(self):
        """Build AG2 agents from config."""
        from autogen import ConversableAgent, LLMConfig

        llm_config = LLMConfig(**self.llm_config)
        agents = []
        for cfg in self.agents_config:
            agent = ConversableAgent(
                name=cfg["name"],
                system_message=cfg.get("system_message", ""),
                llm_config=llm_config,
            )
            agents.append(agent)
        return agents

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("AutoGen Agent (Real)")
        adapter = self

        @mcp.tool(name="group_chat_run")
        async def group_chat_run(message: str, max_turns: int = 3) -> str:
            """Run a message through the AG2 group chat."""
            agents = adapter._build_agents()
            if len(agents) < 2:
                result = agents[0].generate_reply(
                    messages=[{"role": "user", "content": message}]
                )
                return str(result)

            from autogen import GroupChat, GroupChatManager, LLMConfig
            llm_config = LLMConfig(**adapter.llm_config)
            groupchat = GroupChat(agents=agents, messages=[], max_round=max_turns)
            manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config)
            agents[0].initiate_chat(manager, message=message, max_turns=max_turns)
            return str(groupchat.messages[-1]["content"] if groupchat.messages else "No response")

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["group_chat_run"]
