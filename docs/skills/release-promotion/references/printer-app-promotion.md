# Printer application testing-to-stable promotion proof

Part of [release-promotion](../SKILL.md) — end-to-end evidence that the
printer-application `testing`→`stable` promotion fast-forwards protected
`stable` with `github.token` and that immutable version-tag publication stays
signature/SBOM/provenance verified. Tracked by
[common#1243](https://github.com/projectbluefin/common/issues/1243), a child of
the printer epic [common#1209](https://github.com/projectbluefin/common/issues/1209).

> Scope note: this covers **promotion** (moving a verified `testing` commit
> onto `stable`). Upstream source-code baseline and lag live in
> [printer-app-source-baseline.md](./printer-app-source-baseline.md)
> (tracked by common#1244). Do not duplicate that ownership here.

## How the promotion works

Each printer-app repo ships a `promote-stable.yml` triggered only by
`workflow_dispatch` with a single required input, `testing_sha`, and
top-level `permissions: {}` — there is no push or schedule trigger:

1. Rebuild the exact `testing_sha` on **both** native architectures
   (x86_64 on `ubuntu-24.04`, aarch64 on `ubuntu-24.04-arm`) and run the
   repo's OCI image verification.
2. On success, the `promote` job (`contents: write`) checks that `testing_sha`
   is still `origin/testing` HEAD and that `stable` is its ancestor, then
   fast-forwards protected `stable` to it using the workflow's `github.token`,
   under a `*-stable-promotion` concurrency group (`cancel-in-progress: false`).

Publication is a separate workflow: pushing a `v*` tag cut from `stable` runs
the repo's `registry-actions.yml`, which
publishes signed `<VERSION>-<arch>` images and a `<VERSION>` multiarch GHCR
index with SPDX SBOM + SLSA provenance.

## Empirical verification (as of 2026-09-25)

All four promotion workflows were run by `workflow_dispatch` on 2026-09-25 and
succeeded end to end (both `Verify OCI image` jobs and `promote`). In each
repo `stable` HEAD is exactly the dispatched `testing_sha`, and that SHA
carries successful runs of the branch's required status contexts:

| Repo | Promote run | Promoted SHA (`stable` HEAD) | Required contexts on SHA |
|---|---|---|---|
| [`ghostscript-printer-app`](https://github.com/projectbluefin/ghostscript-printer-app) | [`36174096890`](https://github.com/projectbluefin/ghostscript-printer-app/actions/runs/36174096890) | `f667ca71158e81ee3baf5ecf035347f6e69aa162` | `FSDK (x86_64)`, `FSDK (aarch64)` |
| [`hplip-printer-app`](https://github.com/projectbluefin/hplip-printer-app) | [`36173271598`](https://github.com/projectbluefin/hplip-printer-app/actions/runs/36173271598) | `b483227a1ae1804948015eaa4c2a10becef1348c` | `FSDK image and socket-sink (x86_64)`, `(aarch64)` |
| [`gutenprint-printer-app`](https://github.com/projectbluefin/gutenprint-printer-app) | [`36180365312`](https://github.com/projectbluefin/gutenprint-printer-app/actions/runs/36180365312) (earlier: `36173275304` → `70bf8eb`) | `64bc5bcbc53acfa700056494e4330ff609aee7f2` | `Native image and socket print (x86_64)`, `(aarch64)` |
| [`ps-printer-app`](https://github.com/projectbluefin/ps-printer-app) | [`36175534054`](https://github.com/projectbluefin/ps-printer-app/actions/runs/36175534054) | `b1dfb3b64758457500337acbd56b2f0b90de214c` | `FSDK (x86_64)`, `FSDK (aarch64)` |

`stable` in every repo keeps branch protection with force-push and deletion
disabled and `enforce_admins` on, so each landing was a fast-forward with no
bypass. `testing` has since moved ahead of `stable` in all four repos; that is
normal — promoting a newer `testing` SHA is the same `workflow_dispatch`.

**Branch-protection finding:** the `github.token` push to protected `stable`
succeeded in all four repos. No least-privilege GitHub App or reviewed-stable-PR
fallback was required. The protected push is not a blocker for this family.

### Immutable version-tag publication stays verified

`v*` tags cut from the promoted `stable` SHAs published successfully:

| Repo | Tag | Release run | GHCR index digest |
|---|---|---|---|
| Ghostscript | `v10.07.1-2` (`f667ca7`) | `36176032004` | `sha256:82487bd81925b824f16d79a50b4237230d00429fca7761454299a8a4393368cc` |
| HPLIP | `v3.26.4` (`b483227`) | `36176022427` | `sha256:1f81f507ce603f19eebb83fdcdc5b7de7bc7f52f728e9626c2c1224ea7477de8` |
| Gutenprint | `v5.3.6-4.1` (`64bc5bc`) | `36181332762` | `sha256:3ca46b65bba16e258d7f93582beb9ccdf71a8b4e450b9d01f8cb545a945b93a1` |

Each index digest, pulled with no registry credentials, passes the checks the
release audit [common#1217](https://github.com/projectbluefin/common/issues/1217)
calls for: keyless `cosign verify` against the repo's workflow identity exits
0; `oras discover` lists SPDX SBOM (`application/vnd.spdx+json`) and sigstore
bundle referrers; `gh attestation verify` (keyless, post-2026-06-11) exits 0
with an `https://slsa.dev/provenance/v1` predicate. Promotion never rewrites a
published tag.

## Hard holds — do not bypass

- **PostScript is under a security-review hold.**
  [ps-printer-app#27](https://github.com/projectbluefin/ps-printer-app/issues/27)
  ("Clear upstream security review before PS release") is **open**
  (`area/release`, `needs-decision`, `security`). Per common#1243 do **not**
  create a PS `v*` tag or relax its tag hold (tag ruleset "Hold PS v* releases
  pending PPD upload security sign-off", `creation` blocked on `refs/tags/v*`)
  until #27 clears. `testing`→`stable` promotion is not held: #27 itself
  requires the fail-closed approval gate to reach `stable` before the tag hold
  lifts. PS has no `v*` tag and no GHCR image.

## What remains (human action, not common code)

- **PostScript release:** blocked on ps-printer-app#27.
- Later promotions: a human dispatches `promote-stable.yml` with the current
  `testing` SHA in the target repo. This is an operational action in those
  repos, not a change `common` can make.

No `common` workflow or config change is required — the promotion lives in the
printer-app repos and is proven for all four.
