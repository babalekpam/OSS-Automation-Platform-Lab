# ADR 0003: AI output is advisory and deterministic by default

**Status:** accepted. `oss_lab.ai` modules run without an LLM (statistical or rule-based). An optional
`llm` callable can rewrite summaries or refine generated tests. Nothing AI-generated changes platform or
network state; generated tests are drafts marked "REVIEW BEFORE MERGE". Models report precision/recall
against labeled (currently synthetic) history, so the figures show the method, not production accuracy.
