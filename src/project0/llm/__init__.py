"""LLM provider abstraction. Keeps the Anthropic SDK isolated from agent code
so that a future swap to a local model is a configuration change, not a
refactor."""
