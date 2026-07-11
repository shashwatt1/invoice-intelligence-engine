"""
app/prompts/__init__.py

Prompt template package for the AI Structuring Service.

Prompt templates are defined as versioned Python constants (not inline
strings) so they can be version-controlled, reviewed in code review, and
unit-tested for expected structure without making live LLM calls.

Modules:
    - invoice_extraction.py — Versioned system/user prompts for invoice
      field extraction (see get_prompt / ACTIVE_VERSION)
"""

from app.prompts.invoice_extraction import ACTIVE_VERSION, PromptTemplate, get_prompt

__all__ = ["ACTIVE_VERSION", "PromptTemplate", "get_prompt"]
