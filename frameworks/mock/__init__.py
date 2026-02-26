"""Mock MCP servers simulating each agent framework.

These require NO external dependencies — they create FastMCP servers
that behave like each framework's agent would, allowing the testing
ground to demonstrate TrustChain across all 6 ecosystems without
installing any of them.
"""

from frameworks.mock.crewai_mock import CrewAIMock
from frameworks.mock.openai_agents_mock import OpenAIAgentsMock
from frameworks.mock.autogen_mock import AutoGenMock
from frameworks.mock.langgraph_mock import LangGraphMock
from frameworks.mock.google_adk_mock import GoogleADKMock
from frameworks.mock.elizaos_mock import ElizaOSMock

ALL_MOCKS = [
    CrewAIMock,
    OpenAIAgentsMock,
    AutoGenMock,
    LangGraphMock,
    GoogleADKMock,
    ElizaOSMock,
]
