# ADR-0001 — the requirements are read out of the code, not written before it

**Date:** 2026-08-15
**Status:** accepted

## Context

This service ran for four months with no tests: no `tests/` directory, no runner, no CI
step. It also had no stated requirements. Both gaps arrived at once, and closing the
second is a precondition for closing the first — a test suite with nothing to trace to
measures only itself.

The honest options were two.

**Write the requirements first, from intent.** Ask what the grid BFF is *for*, state it,
then test against it. This produces a specification that is genuinely independent of the
implementation — and a first run in which most tests fail, with no way to tell a defect
from a requirement nobody ever agreed to. The intent is also not recoverable: the people
who chose these behaviours are available, but four months of small decisions are not
reconstructible by asking.

**Read the requirements out of the code.** State what the service does today, in language
that says why it matters, and pin it. This produces a specification that cannot find a
defect by construction — it agrees with the code — but that does make every subsequent
change visible, because a change now contradicts a written sentence.

`../celine-ai-assistant` faced the same choice in the same week and took the second, for
the same reason.

## Decision

Distil the requirements from the code. Each `REQ-####` states a behaviour the service has
today and that a reader would want to stay true. Each is verified by at least one test
declaring it with `@verifies REQ-####`.

Where reading the code turned up behaviour that is *wrong* rather than merely
undocumented, state the **correct** behaviour as the requirement and mark its test
`xfail(strict=True)` with a characterisation test beside it. REQ-0043 is the only such
case at the time of writing.

Where the code and its own docstrings disagree, the requirement follows what **runs** and
says so.

## Consequences

The suite could not have found a defect it was not looking for, and mostly did not. The
three it did find — REQ-0043, the `JWT_HEADER_NAME` split in REQ-0002, and the always-null
`locale` — surfaced from writing down what the code does and noticing the sentence was
absurd, not from a failing assertion. That is the yield to expect from this method, and it
is why writing the sentence out matters more than the assertion beneath it.

A requirement distilled this way is worth less than one agreed in advance: it cannot
contradict the implementation, so it cannot catch the case where the implementation was
never what anyone wanted. What it *can* do is make the next change visible, which is the
property the repository had none of.

Someone will eventually want to change a behaviour and find a `REQ-####` in the way. The
requirement is not an authority — it is a record of what was true on 2026-08-15. Change it
and its test together, in the same commit, and the record stays honest.
