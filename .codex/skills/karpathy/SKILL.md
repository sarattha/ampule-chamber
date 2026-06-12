---
name: karpathy
description: Apply Karpathy-inspired engineering discipline when writing, reviewing, or refactoring code. Use to keep changes simple, assumption-aware, tightly scoped, and verified against explicit success criteria.
---

# Karpathy

Use this skill to counter common LLM coding failure modes: premature
implementation, speculative abstractions, broad rewrites, and vague completion
criteria. It is adapted from the public Karpathy guidelines skill and tuned for
Relayna Gateway work.

## Operating Stance

- Think first, then edit.
- Prefer the smallest correct change over a clever general solution.
- Keep every changed line tied to the user's request.
- Surface uncertainty early instead of hiding it in code.
- Verify the result with checks that prove the requested behavior.

## Workflow

1. Define the request.
   - Restate the concrete behavior or artifact being changed.
   - Identify assumptions that affect the implementation.
   - If two interpretations would produce materially different behavior, stop
     and ask.

2. Bound the change.
   - Read the relevant code before choosing an approach.
   - Follow existing crate ownership, module boundaries, and local style.
   - Do not refactor, reformat, rename, or clean unrelated code.

3. Choose the simplest implementation.
   - Add no features the user did not ask for.
   - Avoid new abstractions for one caller or one use case.
   - Avoid configurability, compatibility shims, feature flags, or migrations
     unless the compatibility boundary actually requires them.
   - Prefer direct replacement for unreleased branch-local interfaces.

4. Edit surgically.
   - Touch only files needed for the requested outcome.
   - Preserve surrounding style, naming, and error-shape conventions.
   - Remove only unused imports, variables, helpers, or tests made obsolete by
     the current change.
   - Mention unrelated dead code or design issues in the handoff; do not fix
     them unless asked.

5. Verify deliberately.
   - Convert the request into a testable success condition.
   - Add or update focused tests when behavior changes.
   - Run the smallest useful check while iterating.

## Review Questions

Ask these before marking work complete:

- What exact user request does each changed line serve?
- Did I add an abstraction, option, or fallback that is not needed yet?
- Did I preserve released compatibility boundaries, or document why they do not
  apply?
- Is the verification strong enough to catch the bug or behavior change this
  work targets?
- Did I leave unrelated user or branch-local work untouched?

## Handoff Expectations

In the final response, report:

- What changed, in concrete terms.
- What verification ran, including failures or skipped checks.
- Any compatibility decision that materially shaped the implementation.

## Attribution

Inspired by Andrej Karpathy's public observations on LLM coding pitfalls and
the MIT-licensed `karpathy-guidelines` skill from
`multica-ai/andrej-karpathy-skills`.
