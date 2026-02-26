"""Mock LangGraph agent as FastMCP server."""

from fastmcp import FastMCP
from frameworks.base import FrameworkAdapter


class LangGraphMock(FrameworkAdapter):
    """Simulates a LangGraph ReAct agent with tool calling.

    In real LangGraph:
        agent = create_react_agent(model, tools)
        result = agent.invoke({"messages": [...]})

    Here we expose the agent's graph capabilities as MCP tools.
    """

    framework_name = "LangGraph"
    framework_version = "1.0.7"

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("LangGraph Agent")

        @mcp.tool(name="react_agent_run")
        async def react_agent_run(query: str) -> str:
            """Run a query through a LangGraph ReAct agent.
            Simulates the think-act-observe loop.
            """
            return (
                f"[LangGraph ReAct] Processing: {query}\n"
                f"  Think: Analyzing the query...\n"
                f"  Act: Selecting appropriate tool...\n"
                f"  Observe: Tool returned results.\n"
                f"  Think: Synthesizing response.\n"
                f"Final: Query processed successfully.\n"
                f"[StateGraph: assistant->tools->assistant->END]"
            )

        @mcp.tool(name="supervisor_delegate")
        async def supervisor_delegate(task: str, worker: str = "researcher") -> str:
            """Delegate a task via the LangGraph supervisor pattern.
            Simulates langgraph-supervisor's create_supervisor.
            """
            return (
                f"[LangGraph Supervisor] Delegating to {worker}.\n"
                f"Task: {task}\n"
                f"Worker '{worker}' completed the task.\n"
                f"Supervisor: Validated results.\n"
                f"[create_supervisor: handoff_tool -> {worker} -> return]"
            )

        @mcp.tool(name="graph_state_query")
        async def graph_state_query(key: str = "messages") -> str:
            """Query the current state of the LangGraph execution graph."""
            return (
                f"[LangGraph State] Key: {key}\n"
                f"State contents: [HumanMessage, AIMessage, ToolMessage]\n"
                f"Checkpoint: InMemorySaver (3 checkpoints)\n"
                f"[MessagesState with ToolNode]"
            )

        return mcp

    def get_tool_names(self):
        return ["react_agent_run", "supervisor_delegate", "graph_state_query"]
