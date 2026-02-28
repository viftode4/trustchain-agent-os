"""Mock AutoGen/AG2 agent as FastMCP server."""

from fastmcp import FastMCP
from tc_frameworks.base import FrameworkAdapter


class AutoGenMock(FrameworkAdapter):
    """Simulates an AutoGen/AG2 multi-agent conversation.

    In real AG2:
        groupchat = GroupChat(agents=[planner, coder, reviewer])
        manager = GroupChatManager(groupchat=groupchat)
        manager.initiate_chat(message="Build X")

    Here we expose the group chat capabilities as MCP tools.
    """

    framework_name = "AutoGen/AG2"
    framework_version = "0.11.0"

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("AutoGen Agent")

        @mcp.tool(name="group_chat")
        async def group_chat(task: str, agents: str = "planner,coder,reviewer") -> str:
            """Run a multi-agent group chat to solve a task.
            Simulates AG2's GroupChat with automatic speaker selection.
            """
            agent_list = [a.strip() for a in agents.split(",")]
            lines = [f"[AG2 GroupChat] Task: {task}"]
            lines.append(f"Agents: {', '.join(agent_list)}")
            lines.append(f"Speaker selection: auto")
            lines.append("")
            for i, agent in enumerate(agent_list):
                lines.append(f"  [{agent}]: Contributing to '{task}' (round {i+1})")
            lines.append("")
            lines.append(f"[GroupChat complete, {len(agent_list)} rounds]")
            return "\n".join(lines)

        @mcp.tool(name="code_execution")
        async def code_execution(code: str, language: str = "python") -> str:
            """Execute code in a sandboxed environment.
            Simulates AG2's code execution capability.
            """
            return (
                f"[AG2 CodeExecutor] Executed {language} code.\n"
                f"Code: {code[:100]}...\n"
                f"Output: Code executed successfully.\n"
                f"[DockerCommandLineCodeExecutor]"
            )

        @mcp.tool(name="register_tool_call")
        async def register_tool_call(tool_name: str, arguments: str = "{}") -> str:
            """Call a registered tool through the AG2 function calling mechanism."""
            return (
                f"[AG2 Tool] Called '{tool_name}' with args: {arguments}\n"
                f"Result: Tool executed by executor agent.\n"
                f"[register_function: caller->executor pattern]"
            )

        return mcp

    def get_tool_names(self):
        return ["group_chat", "code_execution", "register_tool_call"]
