#!/usr/bin/env bats
# Justfile syntax gate.
#
# `just` lexes a recipe body before handing it to the shell, and a Go-template
# placeholder such as podman's `--format "{{.Repository}}"` is not a valid token.
# A single one aborts the parse of the WHOLE file with
#
#     error: unknown start of token '.'
#     --> default.just:33:60
#
# which takes down every unrelated recipe in the same justfile. That is exactly
# how the 2026-09-29 Promotion Candidate E2E failed: `default.just` could not be
# parsed on bluefin:lts-testing, so `ujust bios-info`, `ujust logs-this-boot`,
# `ujust check-local-overrides` and the shared-scripts scenario all exited 1.
#
# Two gates, because the two failure modes are different:
#   1. no `*.just` file may contain an adjacent-brace placeholder at all
#   2. every `*.just` file must actually parse with `just --list`
#
# Gate 1 is the one that catches a regression at authoring time. Gate 2 is the
# backstop: it fails on any other way of breaking a justfile, and it also proves
# the fix by running against the exact bytes the image ships.
#
# Run: bats tests/test_justfile_syntax.bats

setup() {
    REPO_ROOT="${BATS_TEST_DIRNAME}/.."
    JUSTFILES="$(cd "${REPO_ROOT}" && find system_files bluefin-branding -name '*.just' -type f 2>/dev/null | sort)"
    export REPO_ROOT JUSTFILES
}

have_just() {
    command -v just >/dev/null 2>&1
}

@test "justfile gate: test fixtures were discovered" {
    [ -n "${JUSTFILES}" ]
    # The file that regressed in #1285 must be part of the gate.
    run grep -Fx "system_files/shared/usr/share/ublue-os/just/default.just" <<< "${JUSTFILES}"
    [ "${status}" -eq 0 ]
}

@test "justfile gate: no justfile contains a Go-template brace placeholder" {
    # `{{` is what just refuses to lex. Recipe-level interpolations
    # (`{{ justfile() }}`, `{{ args }}`, `{{ ACTION }}`) are spelled with a
    # space and are legal, so match a brace immediately followed by `.`, a
    # letter, `_` or `/` — the shape a Go template placeholder always has.
    run grep -RnE '\{\{[./A-Za-z_]' $(cat <<< "${JUSTFILES}" | grep -v '^#')
    [ "${status}" -ne 0 ]
}

@test "justfile gate: no justfile ships doubled-brace template escaping" {
    # `{{{{` / `}}}}` is leftover escaping for a Jinja-style consumer that no
    # longer exists. It is harmless to `just` itself but misleading, and it is
    # the shape that turns into a hard parse error the moment any consumer
    # de-escapes it. See a917e93 and 434daa4.
    run grep -Rn '{{{{' $(cat <<< "${JUSTFILES}" | grep -v '^#')
    [ "${status}" -ne 0 ]
}

@test "justfile gate: every justfile parses with just --list" {
    if ! have_just; then
        skip "just is not installed in this environment"
    fi
    local failed=0 f out
    while IFS= read -r f; do
        if ! out="$(cd "${REPO_ROOT}" && just --justfile "${f}" --list 2>&1)"; then
            printf 'PARSE FAIL %s\n%s\n' "${f}" "${out}" >&2
            failed=1
        fi
    done <<< "${JUSTFILES}"
    [ "${failed}" -eq 0 ]
}

@test "justfile gate: a de-escaped justfile still parses" {
    # Reproduces the shipped condition from #1285: a consumer that collapses
    # `{{{{`/`}}}}` down to `{{`/`}}` turns the old `clean-system` line into an
    # unparseable justfile, and `just` then refuses to load *any* recipe from it.
    if ! have_just; then
        skip "just is not installed in this environment"
    fi
    local failed=0 f out collapsed
    while IFS= read -r f; do
        collapsed="$(mktemp -d)/$(basename "${f}")"
        sed -e 's/{{{{/{{/g' -e 's/}}}}/}}/g' "${REPO_ROOT}/${f}" > "${collapsed}"
        if ! out="$(just --justfile "${collapsed}" --list 2>&1)"; then
            printf 'DE-ESCAPED PARSE FAIL %s\n%s\n' "${f}" "${out}" >&2
            failed=1
        fi
        rm -f "${collapsed}"
    done <<< "${JUSTFILES}"
    [ "${failed}" -eq 0 ]
}

@test "justfile gate: default.just keeps its recipes after the format fix" {
    if ! have_just; then
        skip "just is not installed in this environment"
    fi
    local listing
    listing="$(cd "${REPO_ROOT}" && just --justfile system_files/shared/usr/share/ublue-os/just/default.just --list 2>&1)"
    # Every recipe the E2E suites invoke by name, plus the one the fix touched.
    local recipe
    for recipe in clean-system check-local-overrides logs-this-boot logs-last-boot bios-info; do
        run grep -Eq "^    ${recipe}([[:space:]]|$)" <<< "${listing}"
        [ "${status}" -eq 0 ]
    done
}
