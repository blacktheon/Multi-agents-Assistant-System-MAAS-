"""Exceptions raised by the orchestrator trust boundary."""


class RoutingError(Exception):
    """Raised when routing invariants are violated.

    Examples: a non-Manager agent tries to delegate, an AgentResult has both
    or neither of reply_text/delegate_to set, delegate_to points at an
    unknown agent, etc.
    """
