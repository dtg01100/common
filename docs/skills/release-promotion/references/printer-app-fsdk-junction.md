# Printer application shared FSDK junction baseline

Part of [release-promotion](../SKILL.md) — the one pin the four FSDK OCI
Printer Applications share, recorded so a re-pin review does not have to
re-derive it. Tracked by
[common#1239](https://github.com/projectbluefin/common/issues/1239), a child of
the printer epic [common#1209](https://github.com/projectbluefin/common/issues/1209)
and the shared-graph contract
[ghostscript-printer-app#20](https://github.com/projectbluefin/ghostscript-printer-app/issues/20)
(now closed).

> Scope note: this covers the **FSDK/junction pin and its stated source
> identity** only. Upstream application-source parity is
> [printer-app-source-baseline.md](./printer-app-source-baseline.md)
> (common#1244); `testing`→`stable` promotion and tag publication are
> [printer-app-promotion.md](./printer-app-promotion.md) (common#1243); the
> release-contract evidence audit is
> [printer-app-evidence-matrix.md](./printer-app-evidence-matrix.md)
> (common#1217). The pin itself is moved in the printer repos (one writer per
> pin) — `common` records the baseline, it does not own the pin.

## The contract in one line

Each appliance repo reaches FSDK through exactly one junction,
`elements/fsdk-containers.bst`, pinned to a **full 40-character commit** of
`projectbluefin/fsdk-containers`. The `track:` line is a fallback for
resolvability only; nothing may build from it, and no repo may add a second
CUPS owner. The `io.projectbluefin.fsdk.version` / `io.projectbluefin.fsdk.ref`
labels in each repo's `elements/oci/<app>.bst` must state the FSDK commit that
the *pinned* `fsdk-containers` commit actually builds on, so the published
image carries the same source identity as the junction.

## Verified baseline as of 2026-09-29

All four repos, on **both** `testing` and `stable`, carry the same junction
commit and the same label pair:

| Repo | `testing` HEAD | `stable` HEAD | `elements/fsdk-containers.bst` `ref` | OCI labels `fsdk.version` / `fsdk.ref` |
|---|---|---|---|---|
| [`ps-printer-app`](https://github.com/projectbluefin/ps-printer-app) | `ec0cfff` | `b1dfb3b` | `8a02f5e18b6d89c5558d2371212a5489e86c3ea2` | `26.08.1` / `b02b59ffe19a49a402f357fd5fcb1d552ebc50d7` |
| [`hplip-printer-app`](https://github.com/projectbluefin/hplip-printer-app) | `12912f5` | `b483227` | `8a02f5e18b6d89c5558d2371212a5489e86c3ea2` | `26.08.1` / `b02b59ffe19a49a402f357fd5fcb1d552ebc50d7` |
| [`gutenprint-printer-app`](https://github.com/projectbluefin/gutenprint-printer-app) | `b6816e0` | `64bc5bc` | `8a02f5e18b6d89c5558d2371212a5489e86c3ea2` | `26.08.1` / `b02b59ffe19a49a402f357fd5fcb1d552ebc50d7` |
| [`ghostscript-printer-app`](https://github.com/projectbluefin/ghostscript-printer-app) | `b72e7b9` | `f667ca7` | `8a02f5e18b6d89c5558d2371212a5489e86c3ea2` | `26.08.1` / `b02b59ffe19a49a402f357fd5fcb1d552ebc50d7` |

Nested source identity, read from the pinned commit rather than from `main`:

```bash
gh api "repos/projectbluefin/fsdk-containers/contents/elements/freedesktop-sdk.bst?ref=8a02f5e18b6d89c5558d2371212a5489e86c3ea2" --jq .content | base64 -d | head -5
```

yields `gitlab:freedesktop-sdk/freedesktop-sdk.git`, `track:
freedesktop-sdk-26.08*`, `ref:
freedesktop-sdk-26.08.1-0-gb02b59ffe19a49a402f357fd5fcb1d552ebc50d7` — which is
exactly the `26.08.1` / `b02b59f` pair the four OCI elements declare. The pin,
the source identity and the image labels agree.

## The preview-SHA concern from common#1239 is resolved upstream

common#1239 was written while the appliance repos still tracked an immutable
**preview** commit of the Ghostscript FSDK work
(`d2ce02dc8965a66df2716266d14e155b1fb02b75`), on the understanding that a
squash merge would produce a different, reviewed commit and that a preview ref
must not be promoted. That is no longer the state of the tree:

- [`ghostscript-printer-app` PR #44](https://github.com/projectbluefin/ghostscript-printer-app/pull/44)
  ("chore(deps): update FSDK to 26.08.1") is **merged** (2026-09-25, merge
  commit `daf785716b1531e89236e6c8a4c6f82737d11e40`), and it replaced the local
  FSDK junction with the shared `fsdk-containers` junction.
- The preview SHA appears in **no** appliance repo today — not in any
  `elements/*.bst`, and not anywhere in the four `testing` trees.
- The overlapping USB-defaults work is not duplicated: PR
  [#39](https://github.com/projectbluefin/ghostscript-printer-app/pull/39) was
  **closed unmerged** and its content landed through #44; the OCI CI cutover
  [#40](https://github.com/projectbluefin/ghostscript-printer-app/pull/40) is
  **merged**. CUPS has exactly one provider — `fsdk-containers:printing/base.bst`
  — in all four repos. The per-repo `patches/cups-dnssd-backend-socket-only.patch`
  copies are referenced only by the retained legacy `snap/`/`rockcraft/`
  recipes for driver provenance; no BuildStream element in the forks applies
  them, and `fsdk-containers` owns the equivalent
  `patches/printing/cups/*.patch` for the built path.

common#1239 has **four** acceptance criteria; this document addresses criteria
1 (pin/labels agreement, no floating branch, no duplicate CUPS owner) and 3
(resolved USB overlap; OCI cutover #40 merged). The remaining criteria stay
open and live in the appliance repos:

- **Criterion 2** — *real image-backed native amd64/arm64 socket-print,
  state-restart and runtime-closure CI for PS, HPLIP, Gutenprint against the
  new pin, with check URLs recorded.* That evidence lives in each appliance
  repo's image epic and PR, not in `common`; this PR does not collect it. If
  criterion 2 is needed before the issue can close, the owner is whichever
  appliance epic the recorded check URLs land in.
- **Criterion 4** — PS public promotion stays blocked on the security sign-off
  tracked at [ps-printer-app#27](https://github.com/projectbluefin/ps-printer-app/issues/27)
  (closed 2026-09-29; the tag-hold ruleset and the documented security review
  gate remain in force until a human clears the release in the appliance repo).
  Source and CI verification of the pin do not authorize a release; see
  [printer-app-promotion.md](./printer-app-promotion.md).

## What remains (not `common` code)

- **The daily junction bump is failing in the appliance repos.** The
  `Update fsdk-containers base` workflow fails in all of them (for example
  [ps #36545192555](https://github.com/projectbluefin/ps-printer-app/actions/runs/36545192555),
  [hplip #36539369982](https://github.com/projectbluefin/hplip-printer-app/actions/runs/36539369982),
  [gutenprint #36542991265](https://github.com/projectbluefin/gutenprint-printer-app/actions/runs/36542991265))
  with a GitHub App call returning `404` / `Token is not set` after the
  deprecated `app-id` input warning — the same missing client-id pattern seen
  in the factory health monitor. So the shared pin is consistent but stale:
  `fsdk-containers` `main` is `ffac3b0d` and its FSDK junction has already
  moved to `26.08.2`. Fixing the App credentials and landing the resulting bump
  is action in the appliance repos (and a secret-availability decision), not a
  change `common` can make.
- **No physical print verification.** Nothing in this document was produced by
  a real printer; all four apps are marked unverified for hardware output.

## Re-deriving this baseline

```bash
# 1. the junction pin and the declared image labels, per repo and branch
for r in ps-printer-app hplip-printer-app gutenprint-printer-app ghostscript-printer-app; do
  for b in testing stable; do
    echo "== $r@$b"
    gh api "repos/projectbluefin/$r/contents/elements/fsdk-containers.bst?ref=$b" --jq .content \
      | base64 -d | grep -E 'track:|ref:'
    gh api "repos/projectbluefin/$r/contents/elements/oci/$r.bst?ref=$b" --jq .content \
      | base64 -d | grep 'io.projectbluefin.fsdk'
  done
done

# 2. the nested FSDK source identity the pinned commit actually builds on
#    (head -8 to reach the ref: line — track:/ref: are line 5 / line 7 at 8a02f5e;
#    grep is the durable shape, head is the human-readable check)
gh api "repos/projectbluefin/fsdk-containers/contents/elements/freedesktop-sdk.bst?ref=<pinned>" \
  --jq .content | base64 -d | grep -E 'track:|ref:'

# 3. no preview/branch-only pin anywhere in the appliance trees.
#    A grep for `ref:` lines that are NOT a full 40-char SHA catches any
#    floating / branch / tag pin (the previous `grep '^elements' | grep -v '\.bst$'`
#    only listed directory names and verified nothing).
for r in ps-printer-app hplip-printer-app gutenprint-printer-app ghostscript-printer-app; do
  gh api "repos/projectbluefin/$r/git/trees/testing?recursive=1" --jq '.tree[].path' \
    | grep '\.bst$' \
    | while read -r p; do
        gh api "repos/projectbluefin/$r/contents/$p?ref=testing" --jq .content \
          | base64 -d | grep -h '^[[:space:]]*ref:' | grep -vE '[0-9a-f]{40}'
      done
done

# 4. the CUPS owner is singular in each repo.
#    The previous read of `elements/printer-app/core-stack.bst` 404s in hplip-printer-app
#    (it uses `runtime-stack.bst`); in the others it returns a comment or an
#    unrelated element. The real CUPS consumers are the cups*.bst refs under
#    `fsdk-containers.bst:freedesktop-sdk.bst:components/`. Grep every element
#    for `cups` and assert each hit is prefixed by `fsdk-containers.bst:` —
#    only one such path means one CUPS owner.
for r in ps-printer-app hplip-printer-app gutenprint-printer-app ghostscript-printer-app; do
  gh api "repos/projectbluefin/$r/git/trees/testing?recursive=1" --jq '.tree[].path' \
    | grep '\.bst$' \
    | while read -r p; do
        body=$(gh api "repos/projectbluefin/$r/contents/$p?ref=testing" --jq .content | base64 -d)
        cups_hits=$(echo "$body" | grep -i 'cups' || true)
        [ -z "$cups_hits" ] && continue
        # every cups reference must resolve through fsdk-containers.bst:freedesktop-sdk.bst:components/cups*.bst
        echo "$cups_hits" | grep -vE 'fsdk-containers\.bst:freedesktop-sdk\.bst:components/cups.*\.bst'
      done
done
```

## Verification checklist before trusting this table again

- [ ] Re-read all four junctions on **both** branches; a daily bump in one repo
      only is a contract violation, not a routine update.
- [ ] Resolve the nested FSDK ref at the pinned `fsdk-containers` commit — never
      at its `main` — before comparing it to the OCI labels.
- [ ] Treat a `ref:` that is not a full 40-character commit (a tag, a branch, or
      a short SHA) as a failure, not as a style nit: it is exactly the
      unreachable-preview case common#1239 was raised about.
- [ ] Do not mark physical print output verified; no hardware is available.

