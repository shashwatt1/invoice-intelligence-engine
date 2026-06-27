"""
app/prompts/__init__.py

Prompt template package for the AI Structuring Service (Sprint 3+).

Prompt templates are defined as Python string constants (not inline strings)
so they can be version-controlled, reviewed in code review, and unit-tested
for expected structure without making live LLM calls.

Planned prompt modules:
    - extraction.py     — System + user prompts for invoice field extraction
    - validation.py     — Prompts for ambiguous field clarification
"""
