# Copilot Instructions - FabLab Suite

Use the agent workflow defined in `AGENTS.md` and `agents/PROCEDURE_COMPLETE.md`.

## Required behavior

1. Start each task with Context Keeper style snapshot.
2. Implement one improvement at a time.
3. Run validation/tests before proposing commit.
4. Perform risk review before commit.
5. End significant sessions with Release Logger style summary.

## Scope discipline

- If user gives multiple improvements, rank by risk/dependency and execute only the first unless explicitly asked otherwise.
- Avoid unrelated refactors.
- Keep commits scoped and reversible.
