"""Include the tool-specific suites in the repository's normal discovery."""
from tools.control_deck.test_decisions import DecisionInboxTest, DecisionHTTPTest
from tools.control_deck.test_decisions import RefusalTest as DecisionRefusalTest
from tools.control_deck.test_decisions import SourceBoundTest
from tools.control_deck.test_command_journal import JournalTest
from tools.control_deck.test_command_journal import RefusalTest as JournalRefusalTest
from tools.control_deck.test_operating_context import ContextTest
