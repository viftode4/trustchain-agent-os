"""Real CrewAI adapter — wraps a CrewAI crew as a trust-gated MCP server.

Requires: pip install crewai

Usage:
    from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter

    adapter = CrewAIAdapter(
        crew_config={
            "agents": [
                {"role": "Researcher", "goal": "Research topics", "backstory": "Expert researcher"},
            ],
            "tasks": [
                {"description": "Research {topic}", "expected_output": "Summary", "agent_role": "Researcher"},
            ],
        },
        llm_model="ollama/llama3",  # No API key needed with Ollama
        llm_base_url="http://localhost:11434",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class CrewAIAdapter(FrameworkAdapter):
    """Wraps a real CrewAI crew as a FastMCP server for TrustChain integration.

    The crew's tasks become MCP tools. When called through the TrustChain
    gateway, each tool call gets trust-verified and bilaterally recorded.
    """

    framework_name = "CrewAI"
    framework_version = "1.9.3"

    def __init__(
        self,
        crew_config: Dict[str, Any],
        llm_model: str = "openai/gpt-4o-mini",
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
    ):
        self.crew_config = crew_config
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url
        self.llm_api_key = llm_api_key
        self._crew = None  # Cached crew instance

    def _build_crew(self):
        """Build a CrewAI crew from config."""
        from crewai import Agent, Crew, LLM, Process, Task

        llm_kwargs = {"model": self.llm_model}
        if self.llm_base_url:
            llm_kwargs["base_url"] = self.llm_base_url
        if self.llm_api_key:
            llm_kwargs["api_key"] = self.llm_api_key
        llm = LLM(**llm_kwargs)

        agents = {}
        for a_cfg in self.crew_config.get("agents", []):
            agents[a_cfg["role"]] = Agent(
                role=a_cfg["role"],
                goal=a_cfg["goal"],
                backstory=a_cfg.get("backstory", ""),
                llm=llm,
                allow_delegation=a_cfg.get("allow_delegation", False),
            )

        tasks = []
        for t_cfg in self.crew_config.get("tasks", []):
            agent = agents.get(t_cfg.get("agent_role", ""))
            tasks.append(Task(
                description=t_cfg["description"],
                expected_output=t_cfg["expected_output"],
                agent=agent,
            ))

        return Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
        )

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("CrewAI Agent (Real)")
        adapter = self

        @mcp.tool(name="crew_kickoff")
        async def crew_kickoff(inputs: dict = {}) -> str:
            """Run the CrewAI crew with the given inputs."""
            if adapter._crew is None:
                adapter._crew = adapter._build_crew()
            result = await adapter._crew.kickoff_async(inputs=inputs)
            return str(result)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["crew_kickoff"]
