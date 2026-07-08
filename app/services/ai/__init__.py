"""Provider-neutral AI client boundary.

This package exists so feature code (LLM scoring, CV tailoring) depends on
one small completion contract instead of importing a provider directly.
The Ollama adapter is the first implementation; cloud adapters (HNTR-55)
join later behind the same protocol, owning their own auth.
"""
