---
applyTo: "**"
---

Follow the workflow in `AGENTS.md` and `agents/PROCEDURE_COMPLETE.md` for all tasks.

Mandatory sequence:
1. Produce a compact context snapshot first.
2. Execute one improvement at a time.
3. Run validation/tests before commit proposal.
4. Run a risk-focused review before commit.
5. Close significant sessions with a release-style summary.

Scope and safety:
- If multiple improvements are requested, rank by dependency/risk and execute only the first unless explicitly asked to continue.
- Avoid unrelated refactors.
- Keep changes minimal, scoped, and reversible.
- Never include unrelated files in commits.
