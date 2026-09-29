---
name: e2e-ci
version: "1.1"
last_updated: "2026-09-29"
id: e2e-ci
one_line_purpose: Debug pre/post-merge E2E CI for common.
entry_point: docs/skills/e2e-ci/SKILL.md
category: test-authoring
mcp_compliance_level: partial
optimization_status: draft
status: active
dependencies: []
tags: [e2e, testing, ci]
description: >-
  Pre/post-merge E2E CI for common. Use when debugging E2E failures,
  understanding the PR gate flow, or diagnosing masked brew-setup issues.
metadata:
  type: reference
---

# E2E CI

## When to Use

Use when debugging E2E failures, understanding the PR gate flow, diagnosing
masked `brew-setup` issues, or wiring E2E as a gate in a promotion pipeline.

## When Not to Use

Do not use this skill for lab/KubeVirt testing (see
[`../lab-testing/SKILL.md`](../lab-testing/SKILL.md)), or for generic GitHub
Actions authoring unrelated to the `common` test workflows.

## Post-merge E2E

**File:** `.github/workflows/e2e.yml`

- Runs after merges to `main`
- Calls the local `.github/workflows/run-testsuite.yml` wrapper, which centralizes the pinned `projectbluefin/testsuite` SHA
- Validates the common layer against three downstream images:
  - `ghcr.io/projectbluefin/bluefin:latest`
  - `ghcr.io/projectbluefin/bluefin:lts`
  - `ghcr.io/projectbluefin/dakota:testing`
- Uses SSH-mode tests from the runner, so the common suite does not require a full GNOME session

## Pre-merge gate

**File:** `.github/workflows/pr-e2e.yml`

- Runs on PRs to `main` and on `merge_group`
- Builds the PR's `common` layer candidate first
- Composes a downstream test image from `ghcr.io/projectbluefin/bluefin:stable` by overlaying `/system_files/shared` and `/system_files/bluefin`
- Recompiles GSettings schemas in the composed image
- Pushes the composed image to GHCR and runs the local testsuite wrapper with `suites: common`

This is the pre-merge gate for common-layer changes, so regressions can fail before merge instead of waiting for post-merge E2E.
In branch protection today it is still an advisory/non-required signal; `build.yml` remains the required merge check.

Use a stable downstream base for this PR-time compose gate. The moving `:testing`
stream belongs in `promotion-candidate-e2e.yml`; using it here makes unrelated
downstream churn (for example missing CLI tools in the current testing image)
fail `common` PRs that only change the shared layer.

## Promotion-candidate feedback loop

**File:** `.github/workflows/promotion-candidate-e2e.yml`

- Runs weekly on Tuesdays before the downstream Bluefin promotion workflows
- Tests the exact candidate tags used for promotion from common's side:
  - `ghcr.io/projectbluefin/bluefin:testing`
  - `ghcr.io/projectbluefin/bluefin:lts-testing`
- Runs `smoke,common` to add a boot/basic-usage signal on top of the shared-layer checks
- Uses the same local testsuite wrapper as PR/post-merge workflows, so the testsuite SHA stays aligned

This is **not** a full installer gate. It is the smallest safe repo-local improvement common can make without editing downstream image repos or installer pipelines.

## Known CI caveats and quarantines

- `brew-setup.service` is masked in CI, so Homebrew-installed CLI tools are not present unless explicitly provisioned during the job
- `testsuite#210` tracks the `bash -lc` PATH mismatch affecting `zsh`/`fish` checks in the CI user environment
- GNOME Software scenarios are intentionally `@quarantine` after `testsuite#258`; they should not be treated as active software-store coverage
- Bazaar coverage is currently a `@pending` placeholder tied to the same gap
- `ujust report --confirm` scenario (`system_health.feature`) is `@quarantine` — the `--confirm` mode is not implemented in any current image variant; the step skip-detection used the wrong error string. See testsuite PR #259. Re-enable when `report --confirm` lands in the image Justfile.

## Reading a Promotion Candidate E2E failure

The `notify-on-failure` job files a bare `ci: promotion candidate E2E failed` issue with
no diagnosis, so triage starts from the run. Two steps get you the real cause fast —
do not read the raw job log top to bottom, it is ~23k lines and mostly `git fetch` noise.

```bash
RUN=<run id>
# 1. Which jobs failed and at which step.
gh api repos/projectbluefin/common/actions/runs/$RUN/jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | .name, (.steps[] | select(.conclusion=="failure") | .name)'

# 2. results.json from the metadata artifact has the per-step assertion messages.
#    `gh run download` is not available to the agent; fetch + unzip via the API.
curl -sL -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/projectbluefin/common/actions/artifacts/<id>/zip" -o a.zip
python3 -c "import zipfile;zipfile.ZipFile('a.zip').extractall('a')"
```

Then walk `results.json` for `steps[].result.error_message`. `gh run view --log-failed`
alone is not enough: the behave runner container's stdout is not in the job log, so
assertion text only exists in the artifact.

### Triage order for the 2026-09-29 run (#1285)

Not every failing scenario is fixable in `common`. Split them by owner before starting:

| Failure | Owner | Notes |
|---|---|---|
| `ujust bios-info` / `logs-this-boot` / `check-local-overrides` / shared-scripts scenario all exit 1 with `error: unknown start of token '.'` on `default.just:33` | **common** | See "Justfiles must not contain Go-template braces" below. A single unparseable justfile takes down every recipe in it. |
| `flatpak remotes --system` → `opening repo /var/lib/flatpak/repo: No such file or directory` on `lts-testing` | bluefin-lts | Missing system Flatpak installation in the LTS image, not the shared layer. |
| `GNOME extension "..." is enabled` with `state=6` / `state=99` | testsuite | The suite's own `local.d/00-ci-testing` dconf write replaces `enabled-extensions` with `['unsafe-mode@bluefin-test']`, so per-extension `ExtensionState` assertions cannot pass. See [`../dconf-consistency.md`](../dconf-consistency.md). |

## Justfiles must not contain Go-template braces

`just` lexes a recipe body before handing it to the shell, and a Podman/Docker Go
template placeholder (`--format "{{.Repository}}"`) is not a valid token. One of them
aborts the parse of the **entire** file:

```
error: unknown start of token '.'
 ——▶ default.just:33:60
```

Every recipe in that file then exits non-zero, including ones that have nothing to do
with the line that broke. It even fires inside a `#` comment, because comments in a
recipe body are lexed too.

Do not "fix" it by writing `{{{{`. That was an escape for a Jinja-style consumer which
existed while `default.just` shipped as the `aurorafin-shared` submodule
(`a917e93`, inlined by `434daa4`). Nothing de-escapes it now, so it is inert — and if
any consumer ever does, the file becomes unparseable. `tests/test_justfile_syntax.bats`
gates all three shapes: no `{{`-prefixed token, no `{{{{`, and every `*.just` under
`system_files/` must parse with `just --list` both as written and after
`{{{{`→`{{` de-escaping.

For a prune preview, the default `podman image ls` table already shows repository, tag,
image ID and size — reach for that before reaching for `--format`.

## Testsuite SHA pin

`common/.github/workflows/run-testsuite.yml` pins the testsuite SHA for all repo-local callers. When the pin lags behind `main`, quarantined scenarios may run and cause spurious failures. `common` has Renovate configured (`renovate.json`) but the testsuite SHA pin may need manual updates when testsuite fixes land — check `chore(deps): update` Renovate PRs.

## Red Flags

- Using `:testing` as the compose base for `pr-e2e.yml` (causes false failures from unrelated downstream churn).
- A `workflow_run` trigger fix pushed only to `testing` (default-branch constraint means it has no effect until it reaches `main`).
- The `promote-to-testing` job running on branches other than `main` (double-promotion risk).
- An unanchored `--certificate-identity-regexp` wildcard in cosign verify.
- A `--format` Go template in any `*.just` recipe (see "Justfiles must not contain Go-template braces").

## Verification

- [ ] `pr-e2e.yml` uses a stable (not `:testing`) base image.
- [ ] `run-testsuite.yml` SHA pin matches the testsuite commit being relied on.
- [ ] Promotion `promote-to-testing` job is gated on `head_branch == 'main'`.
- [ ] cosign identity regexp is anchored with `^...$`.
- [ ] `bats tests/test_justfile_syntax.bats` passes if any `*.just` changed.

## References

| File | Description |
|---|---|
| [`references/promotion-patterns.md`](references/promotion-patterns.md) | `promote-to-testing` job pattern, TOCTOU guard, cosign verify anchoring, and cosign install on GHA runners. |
| [`references/never-stall-design.md`](references/never-stall-design.md) | Promotion gate never-stall design: E2E on testing branch, feedback trigger, jq selector, and bluefin bootstrap. |
