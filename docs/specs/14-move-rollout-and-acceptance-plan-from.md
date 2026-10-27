# Spec for #14: move rollout and acceptance plan from README into docs/

Issue: <https://github.com/seandavi/gh-seanbot/issues/14> (part of tracking issue #1).

## Goal

`AGENTS.md` says `README.md` is the installation and command reference and deployment/acceptance steps live in `docs/`. Today `README.md` carries the full rollout plan. Move that plan into a new `docs/rollout.md`, leave the README with status, install, commands, capabilities, CI pointer, ADR list, and links, and repoint every link that names the README as the rollout plan or acceptance checklist.

## Non-goals

- No code, test, policy-file, CLI, workflow, or dependency change. Package APIs are not touched, so none needed verification.
- No rewrite of the plan's substance. The moved text stays the same apart from the edits listed under "Edits to moved text".
- No ADR status change. All three ADRs stay `proposed`; no ADR body changes.
- No change to `.pi/crucible.json`. It is orchestration config, not project documentation (see Risks).
- No live App verification. This change proves nothing about App identity, permissions, or repository rules.

## Design

### Where the plan goes: new `docs/rollout.md`

The issue allows either a new `docs/rollout.md` or merging into `docs/operations.md`. Use **`docs/rollout.md`**:

- `docs/operations.md` describes the implemented CLI's credential boundary, the Actions smoke workflow, and offline evidence. The rollout plan is the proposed setup and the live acceptance target. Keeping them in separate files keeps "what is implemented" apart from "what is planned", which is the same split `AGENTS.md` asks for between implemented and proposed decisions.
- `docs/operations.md` already points at the acceptance checklist as a separate artifact ("run the full … acceptance checklist"), so a separate file fits the existing structure.

### Policy file, CLI, minting, failure modes

Not applicable. No policy keys, CLI arguments, exit codes, output, token minting/request/check/revocation order, or failure handling change. The fail-closed behaviour in `docs/operations.md` and ADR 0002/0003 is unchanged.

### `docs/rollout.md` content

The file starts with:

```markdown
# Rollout and acceptance plan

This is the proposed setup, rollout, and live acceptance plan for the shared agent GitHub App. The [ADRs](adr/README.md) record why the architecture takes this shape; [operations](operations.md) describes what the implemented CLI and Actions workflow actually do and what offline tests cover. Nothing here is a claim that deployment or acceptance is complete; see [tracking issue #1](https://github.com/seandavi/gh-seanbot/issues/1).
```

Then it carries over these README sections in order, keeping their `##`/`###` heading text exactly so that anchors stay stable:

1. `## Goal` (README lines 59–63)
2. `## Identity boundaries` (lines 65–77, including the table and its legend)
3. `## Proposed conventions` with `### Commits` and `### PRs and comments` (lines 79–108)
4. `## Setup and rollout` with `### 1. Decide policy` … `### 6. Roll out incrementally` (lines 110–172)
5. `## Rollback` (lines 174–176)
6. `## Primary references to verify during implementation` (lines 178–186)

#### Edits to moved text

Only these edits are allowed:

- **Link paths.** In the last paragraph of "Primary references", change `[operations](docs/operations.md)` to `[operations](operations.md)`. No other relative links exist in the moved sections; the external `https://` links stay as they are.
- **Self-reference.** In that same paragraph, change "The original rollout plan above remains the acceptance target" to "This plan remains the acceptance target".
- **Stale refresh claim in step 3.** README step 3, item 4 says the seam "Refreshes tokens before expiry". The implemented slice deliberately does not refresh. Per ADR 0003 and `docs/operations.md`, it mints a fresh token for each invocation and stops the command before expiry. Moving the step unannotated would republish a claim that contradicts the code. Keep the list item and add this paragraph directly after the numbered list in step 3, before the "Installation tokens are short-lived…" paragraph:

  ```markdown
  The implemented local CLI does not refresh tokens: each invocation mints a fresh token and stops the command before expiry ([ADR 0003](adr/0003-reuse-actions-auth-and-bound-local-operations.md), [operations](operations.md)). Renewal for long-running sessions remains open in [#7](https://github.com/seandavi/gh-seanbot/issues/7).
  ```

  This records that the evidence has changed. It does not accept or reject the plan item.

### `README.md` after the change

Keep these unchanged: the title, tagline (line 3), `**Status:**` paragraph (line 5), the whole `## Install and use` section (lines 7–41, which covers commands, endpoint rules, capabilities, and lifecycle/hygiene), and the ADR bullet list (lines 51–55).

Change:

- **Rename `## CI and rollout` to `## CI, rollout, and acceptance`** and add this as the section's first paragraph, before the existing CI paragraph:

  ```markdown
  The [rollout and acceptance plan](docs/rollout.md) covers identity boundaries, proposed commit and footer conventions, setup steps, the live acceptance checklist, and rollback.
  ```

  Keep the existing CI paragraph and tracking-issue paragraph unchanged.
- **Replace line 57** ("This document remains the setup and rollout plan; the ADRs record why the proposed architecture takes this shape.") with:

  ```markdown
  The ADRs record why the proposed architecture takes this shape; the [rollout plan](docs/rollout.md) tracks setup and verification.
  ```

- **Delete** README lines 59–186 (from `## Goal` to the end of the file). They now live in `docs/rollout.md`.

### `docs/adr/README.md`

- Line 3: change `the [rollout plan](../../README.md)` to `the [rollout plan](../rollout.md)`.
- Line 15: change "remain proposed conventions in the rollout plan" to "remain [proposed conventions](../rollout.md#proposed-conventions) in the rollout plan".

### `docs/operations.md`

- Line 51: change "run the full README acceptance checklist on the actual installation" to "run the full [acceptance checklist](rollout.md#5-verify-in-a-test-repository) on the actual installation". The anchor is GitHub's slug for `### 5. Verify in a test repository`.

### ADR files

No change. A grep for `README`/`rollout` in `docs/adr/0001*`, `0002*`, and `0003*` finds only a generic phrase in ADR 0003 ("the broader rollout or attribution policy") that does not link to the README.

## Acceptance criteria

- [ ] `docs/rollout.md` exists. Its headings, in order, are: `# Rollout and acceptance plan`, `## Goal`, `## Identity boundaries`, `## Proposed conventions`, `### Commits`, `### PRs and comments`, `## Setup and rollout`, `### 1. Decide policy` through `### 6. Roll out incrementally`, `## Rollback`, `## Primary references to verify during implementation`.
- [ ] Apart from the intro paragraph and the three edits listed under "Edits to moved text", the body of `docs/rollout.md` matches the removed README lines 59–186 word for word.
- [ ] `README.md` has none of these headings: `## Goal`, `## Identity boundaries`, `## Proposed conventions`, `## Setup and rollout`, `## Rollback`, `## Primary references`.
- [ ] `README.md` keeps `## Install and use` unchanged and has `## CI, rollout, and acceptance` and `## Architecture decisions`. It links to `docs/rollout.md` at least once, and the phrase "This document remains the setup and rollout plan" is gone.
- [ ] No file outside `docs/specs/` and `.pi/` calls the README the rollout plan or acceptance checklist. `grep -rn "README acceptance\|rollout plan\](../../README" --exclude-dir=.git --exclude-dir=.venv .` returns nothing.
- [ ] `docs/adr/README.md` links to `../rollout.md`. `docs/operations.md` links to `rollout.md#5-verify-in-a-test-repository`.
- [ ] Every relative Markdown link (including `#anchor` fragments) in `README.md`, `docs/rollout.md`, `docs/operations.md`, and `docs/adr/README.md` resolves to an existing file or heading.
- [ ] The step 3 paragraph about the implemented CLI not refreshing tokens is present in `docs/rollout.md`, with links to ADR 0003, `operations.md`, and #7.
- [ ] No ADR file changes, and no file under `src/`, `tests/`, `examples/`, `.github/`, or `pyproject.toml` changes. `git diff --stat origin/main` lists only `README.md`, `docs/rollout.md`, `docs/operations.md`, `docs/adr/README.md`, and this spec.
- [ ] `just check` passes (as a regression guard only; no code changes).

## Test plan

Run these from the worktree root. Nothing new is committed for testing; the link check is a one-off stdlib script.

1. Moved-text fidelity:
   ```sh
   git show origin/main:README.md | sed -n '59,186p' > "$TMPDIR/old-plan.md"
   diff "$TMPDIR/old-plan.md" <(sed -n '/^## Goal$/,$p' docs/rollout.md)
   ```
   The diff must show only the step 3 paragraph that was added and the two edits to the "Primary references" paragraph.
2. Removed headings:
   ```sh
   grep -nE '^## (Goal|Identity boundaries|Proposed conventions|Setup and rollout|Rollback|Primary references)' README.md
   ```
   This must print nothing (exit 1).
3. Stale references: the `grep` command in the acceptance criteria must print nothing.
4. Link and anchor check. Run `python3 -I` on a script kept outside the repo. The script extracts `](target)` links from the four Markdown files, skips `http(s):` and `mailto:` targets, resolves each path relative to the file that contains it, and checks that the target exists. For `#fragment`, it slugs the target file's headings the way GitHub does (lowercase; drop characters other than letters, digits, spaces, and hyphens; turn spaces into hyphens) and checks that the fragment is among them. It must report zero failures.
5. `just check` passes.
6. Scope check: `git diff --stat origin/main` lists only the five files named in the acceptance criteria.

## Risks

- **`.pi/crucible.json` goes stale.** Its `design` string describes `README.md` as holding the "rollout plan". It is orchestration config, outside the project docs, so this spec does not change it. The supervisor should update it to name `docs/rollout.md` in a separate change.
- **External links to README anchors break.** Links to anchors such as `README.md#setup-and-rollout` from outside the repo (issues, other repos, or memory) would stop working. Issues #3, #4, #5, and #7 do not link to README sections; outside links were not audited.
- **The step 3 note is an interpretation.** It adds one factual sentence instead of moving the text verbatim. The alternative is to republish a claim that contradicts ADR 0003 and the code, which this repo's review gate treats as a stale doc.
- **Anchor slugs.** The `5-verify-in-a-test-repository` slug assumes GitHub's current heading-slug rules. Step 4 of the test plan checks it locally, but GitHub's own rendering was not checked.
- **PyPI long description.** `pyproject.toml` uses `README.md` as the package readme, so the published long description gets shorter. That is intended.
