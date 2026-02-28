"""Mock CrewAI agent as FastMCP server."""

from fastmcp import FastMCP
from tc_frameworks.base import FrameworkAdapter


class CrewAIMock(FrameworkAdapter):
    """Simulates a CrewAI crew with researcher + writer agents.

    In real CrewAI:
        crew = Crew(agents=[researcher, writer], tasks=[...])
        crew.kickoff()

    Here we expose the crew's capabilities as MCP tools.
    """

    framework_name = "CrewAI"
    framework_version = "1.9.3"

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("CrewAI Agent")

        @mcp.tool(name="research_topic")
        async def research_topic(topic: str, depth: str = "standard") -> str:
            """Research a topic using the CrewAI researcher agent.
            Simulates a crew with sequential process: researcher -> writer.
            """
            return (
                f"[CrewAI Researcher] Analysis of '{topic}' (depth={depth}):\n"
                f"- Key finding 1: {topic} has significant implications\n"
                f"- Key finding 2: Market trends show growth\n"
                f"- Key finding 3: Technical feasibility confirmed\n"
                f"[Process: sequential, agents: researcher->writer]"
            )

        @mcp.tool(name="write_report")
        async def write_report(findings: str, format: str = "markdown") -> str:
            """Generate a report from research findings using the CrewAI writer agent."""
            return (
                f"[CrewAI Writer] Report ({format}):\n"
                f"# Research Report\n"
                f"## Findings\n{findings}\n"
                f"## Conclusion\nBased on analysis, recommend proceeding.\n"
                f"[Crew execution complete]"
            )

        @mcp.tool(name="delegate_task")
        async def delegate_task(task: str, agent_role: str = "analyst") -> str:
            """Delegate a task to a specific agent role within the crew."""
            return (
                f"[CrewAI Delegation] Task '{task}' delegated to {agent_role}.\n"
                f"Result: Task completed by {agent_role} agent.\n"
                f"[allow_delegation=True, process=hierarchical]"
            )

        return mcp

    def get_tool_names(self):
        return ["research_topic", "write_report", "delegate_task"]
