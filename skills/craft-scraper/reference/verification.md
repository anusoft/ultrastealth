# Plan contract + verification

The plan defines the contract; verification is done with your own abilities
(`Read` on PNGs, asserting on emitted data). No external API required.

## `plan.md`

Write `out/craft/<task_id>/plan.md`:

```markdown
# Task
<verbatim task description, including the target URL>

# Parameters
| name   | type | source phrase from task | default   | allowed / format       |
|--------|------|-------------------------|-----------|------------------------|
| <arg>  | str  | "..."                   | "<value>" | <format / allowed set> |

# Critical Points
- [ ] CP1: <constraint / required datum — independently verifiable>
- [ ] CP2: ...
```

Rules:
- Every `# Parameters` row → a function argument **and** a CLI `--flag` whose
  default equals the concrete task value. Running with no args reproduces the
  task. Fixed-for-site values (start URL, selectors, endpoint path) are NOT
  parameters.
- One CP per independently verifiable requirement. Numeric/date/quantity/unit
  CPs are **exact** — broadening is a failure. Ranking CPs ("cheapest",
  "latest") must reference the site's actual sort/filter or sort the API
  response by the actual field. A required final datum is its own CP.

## Verification

### Path A (scrapling-js — assert on data)
- Run `bun run <script>.js`; capture output.
- For each CP, cite a concrete check on the emitted JSON/output:
  - expected row/section/page counts (compare to the API's total-count field);
  - required fields present and correctly typed in saved records;
  - filters/params reflected in results (category id matches, price/date bounds
    respected);
  - `--resume` skips already-downloaded files on a second run.
- Tick a CP only when the data concretely proves it. Be harsh on empty or
  suspiciously-short results.

### Path B (Ultrastealth — `Read` the screenshots)
- For each CP, identify the screenshot and/or log line that evidences it and
  `Read` the PNG.
- Confirm the evidence is **unambiguous**: filter/selected state visibly applied
  (not hidden behind a closed drawer); values match exactly; sort applied via the
  site control; required submit/apply action visibly taken; final datum legibly
  displayed.
- Tick a CP only with concrete evidence. Be harsh on partial, occluded, or
  ambiguous states.

## Completion gate

Declare done only when ALL are true:
1. `plan.md` has both `# Parameters` and `# Critical Points`.
2. The emitted script defines one reusable function (or a clean `run()`),
   supports `--help`, and is side-effect-free at import.
3. Every `# Parameters` row maps 1:1 to a function arg and a `--flag` whose
   default is the concrete task value.
4. A no-arg run reproduced the task; every CP is ticked with cited evidence
   (data assertions for Path A, screenshots/log for Path B).
5. The user has seen the final datum/row count **and** the `--help` output.

If any is false: diagnose the specific issue, fix the script (preserving the CLI
shape), re-run (Path B: in a fresh `run_<id+1>/`), and re-verify.
