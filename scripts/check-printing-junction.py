#!/usr/bin/env python3
"""Check the shared printing base junction of the printer application forks.

The three printer forks -- ps-printer-app, hplip-printer-app and
gutenprint-printer-app -- reach the shared printing base (patched FSDK CUPS,
cups-filters, libcupsfilters, libppd, ghostscript, mutool, avahi-printing,
PAPPL and pappl-retrofit) only through one BuildStream junction::

    elements/fsdk-containers.bst             pins fsdk-containers by commit
    fsdk-containers elements/freedesktop-sdk.bst at that commit
                                             pins FSDK as
                                             freedesktop-sdk-<version>-<n>-g<ref>

and each advertises that nested pin as two hand-written labels in its OCI
element::

    io.projectbluefin.fsdk.version   the freedesktop-sdk point release
    io.projectbluefin.fsdk.ref       the freedesktop-sdk commit

common#1246 asks for one reviewed proposal owner for that junction across the
three forks, an atomic update of the junction and the packaged FSDK metadata,
and a check that the two agree before promotion. This script is the
cross-repository half of that: it answers three questions about any fork
checkout, so one contract has one owner instead of three divergent copies of it.

1. Is the junction pinned to a full commit? A branch, a tag or a floating
   ``track:`` target is not an immutable base, and the old digest must stay
   resolvable for rollback.
2. Do the OCI labels describe the FSDK release the pinned fsdk-containers
   commit actually builds on? Drift here means the image claims a base it was
   not built from.
3. Is there exactly one proposal owner for the junction? The reviewed daily
   updater (``.github/workflows/update-base.yml``) and a Renovate manager over
   ``elements/fsdk-containers.bst`` would both propose the same line, which is
   exactly the "Renovate or a scheduled reviewed updater, not both" rule. Both
   count as an owner: zero owners and two owners are violations, and choosing
   *which* of the two owns the junction stays a review decision, so a fork
   carrying only a Renovate manager passes this check.

Every check fails closed. An unpinned or ambiguous junction, a missing label, a
ref that is not a release-tagged FSDK pin, a failed fetch, an unreadable
``renovate.json`` or a junction nothing proposes all exit non-zero, because
"could not read the state" is never "the state is correct".

Three limits are reported rather than assumed away. Only the fork's own
``renovate.json`` is parsed, so a custom manager defined in an inherited preset
(the forks extend ``local>projectbluefin/renovate-config``) is invisible here
and an ``extends`` list is printed as a note. Nothing in CI runs this yet: it
is a tool to run against a fork checkout, not a gate, until a workflow invokes
it. And the pinned commit is fetched directly rather than tested for
reachability from ``fsdk-containers`` ``stable``, so common#1246's first
criterion -- track a reviewed stable release, not a floating branch head -- is
only half covered here while all three forks still ``track: main``.

Usage::

    scripts/check-printing-junction.py /path/to/ps-printer-app [...]
    scripts/check-printing-junction.py --repo hplip-printer-app   # from GitHub

``--fsdk-remote`` points the nested-pin lookup at another remote (the tests use
a scratch repository) and ``--no-fetch`` skips it, leaving only checks 1 and 3.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

LABEL_VERSION = "io.projectbluefin.fsdk.version"
LABEL_REF = "io.projectbluefin.fsdk.ref"

JUNCTION = "elements/fsdk-containers.bst"
UPDATER = ".github/workflows/update-base.yml"
RENOVATE = "renovate.json"
FSDK_CONTAINERS_URL = "https://github.com/projectbluefin/fsdk-containers.git"
FSDK_FREEDESKTOP_SDK = "elements/freedesktop-sdk.bst"

# The forks, and the OCI element that carries the labels in each.
FORKS = {
    "ps-printer-app": "elements/oci/ps-printer-app.bst",
    "hplip-printer-app": "elements/oci/hplip-printer-app.bst",
    "gutenprint-printer-app": "elements/oci/gutenprint-printer-app.bst",
}

COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REF_LINE = re.compile(r"^[ \t]*ref:[ \t]*(?P<rest>[^\n]*)$", re.MULTILINE)
QUOTED_REF = re.compile(r"^(?P<quote>[\"'])(?P<value>[^\"']*)(?P=quote)[ \t]*(?:#.*)?$")
BARE_REF = re.compile(r"^(?P<value>[^\s\"'#]+)(?:[ \t]+#.*)?$")
FSDK_REF = re.compile(
    r"^freedesktop-sdk-(?P<version>\S+?)-(?P<count>\d+)-g(?P<ref>[0-9a-f]{40})$"
)
# A label line, however YAML quotes the key and the value: both may be single
# quoted, double quoted, or bare, since all three spell the same mapping entry.
LABEL = (
    r"^[ \t]*(?P<kq>[\"']?)io\.projectbluefin\.fsdk\.%s(?P=kq)[ \t]*:[ \t]*"
    r"(?:(?P<vq>[\"'])(?P<quoted>[^\"']*)(?P=vq)|(?P<bare>[^\s\"'#]+))"
)


def label_value(match: re.Match[str]) -> str:
    """The value a LABEL match captured, whichever quoting style it used."""
    quoted = match.group("quoted")
    return quoted if quoted is not None else match.group("bare")


class Failure(Exception):
    """A condition that must stop the caller rather than be worked around."""


def read_text(path: Path, describes: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Failure(f"{path} does not exist, so {describes} cannot be read") from None
    except OSError as error:
        raise Failure(f"cannot read {path}: {error}") from None


def ref_lines(text: str) -> list[tuple[str, str | None]]:
    """Every ``ref:`` line of a BuildStream element, paired with its value.

    A ref line may carry a trailing YAML comment and may quote its value, so
    ``ref: "<sha>"  # pinned by hand`` states the same ref as ``ref: <sha>``.
    A line whose value cannot be read that way is returned with ``None`` so the
    caller reports it as unparseable rather than as a missing ref.
    """
    lines: list[tuple[str, str | None]] = []
    for match in REF_LINE.finditer(text):
        rest = match.group("rest").rstrip()
        value = None
        for pattern in (QUOTED_REF, BARE_REF):
            found = pattern.match(rest)
            if found:
                value = found.group("value") or None
                break
        lines.append((match.group(0).strip(), value))
    return lines


def junction_ref(tree: Path) -> str:
    """The commit the fsdk-containers junction is pinned to.

    ``git-describe`` refs (``freedesktop-sdk-...``) cannot appear here: the
    updater restores a plain commit after tracking so the same fsdk-containers
    commit is never rewritten as a change, and a describe-shaped ref would make
    the base neither immutable nor comparable.
    """
    text = read_text(tree / JUNCTION, f"the {JUNCTION} pin")
    lines = ref_lines(text)
    unparseable = [raw for raw, value in lines if value is None]
    if unparseable:
        raise Failure(
            f"{JUNCTION} has a ref line this check cannot read: '{unparseable[0]}'"
        )
    refs = [value for _, value in lines]
    if not refs:
        raise Failure(f"{JUNCTION} pins no source ref, so the base is not immutable")
    if len(set(refs)) > 1:
        raise Failure(f"{JUNCTION} pins {len(set(refs))} different refs; it must pin one")
    ref = refs[0]
    if not COMMIT.match(ref):
        raise Failure(
            f"{JUNCTION} pins '{ref}', which is not a full commit; a branch, tag or "
            "describe-shaped ref is a moving base and leaves no digest to roll back to"
        )
    return ref


def oci_labels(tree: Path, oci_element: str) -> tuple[str, str]:
    text = read_text(tree / oci_element, f"the {LABEL_VERSION}/{LABEL_REF} labels")
    values = {}
    for key, pattern in (("version", LABEL % "version"), ("ref", LABEL % "ref")):
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            raise Failure(f"{oci_element} does not label io.projectbluefin.fsdk.{key}")
        values[key] = label_value(match)
    if not COMMIT.match(values["ref"]):
        raise Failure(
            f"{oci_element} labels io.projectbluefin.fsdk.ref as "
            f"'{values['ref']}', which is not a full commit"
        )
    return values["version"], values["ref"]


def fsdk_pin(commit: str, remote: str) -> tuple[str, str]:
    """The FSDK version and commit that fsdk-containers builds on at ``commit``."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        try:
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "fetch",
                    "-q",
                    "--depth",
                    "1",
                    "--end-of-options",
                    remote,
                    commit,
                ],
                check=True,
                capture_output=True,
            )
            junction = subprocess.run(
                ["git", "-C", str(repo), "show", f"FETCH_HEAD:{FSDK_FREEDESKTOP_SDK}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (subprocess.CalledProcessError, OSError) as error:
            detail = getattr(error, "stderr", "") or error
            raise Failure(
                f"cannot read {FSDK_FREEDESKTOP_SDK} from fsdk-containers "
                f"{commit[:12]} at {remote}: {str(detail).strip()}"
            ) from None
    match = None
    for _, value in ref_lines(junction):
        if value is None:
            continue
        found = FSDK_REF.match(value)
        if found:
            match = found
            break
    if not match:
        raise Failure(
            f"fsdk-containers {commit[:12]} pins no "
            "freedesktop-sdk-<version>-<n>-g<commit> ref, so its FSDK release is ambiguous"
        )
    return match.group("version"), match.group("ref")


def renovate_file_patterns(config: dict) -> list[tuple[str, str]]:
    """The file patterns of a ``renovate.json``, each tagged with its key.

    The key matters, because the two keys do not share a syntax: ``fileMatch``
    entries are always regexes, while ``managerFilePatterns`` (which replaced
    it) is a glob unless the entry is wrapped in slashes. ``enabledManagers``
    names no file pattern at all and is returned only for the record.
    """
    patterns: list[tuple[str, str]] = []
    managers = config.get("customManagers")
    if isinstance(managers, list):
        for manager in managers:
            if isinstance(manager, dict):
                for key in ("managerFilePatterns", "fileMatch"):
                    value = manager.get(key)
                    if isinstance(value, list):
                        patterns.extend((key, v) for v in value if isinstance(v, str))
    if isinstance(config.get("enabledManagers"), list):
        patterns.extend(
            ("enabledManagers", manager)
            for manager in config["enabledManagers"]
            if isinstance(manager, str)
        )
    return patterns


def glob_matches_junction(pattern: str) -> bool:
    """Whether a minimatch glob can match ``elements/fsdk-containers.bst``.

    Renovate matches ``managerFilePatterns`` globs with minimatch, where ``*``
    and ``?`` stop at a path separator and only ``**`` crosses one. Python's
    :func:`fnmatch.fnmatch` lets ``*`` cross ``/``, so a pattern such as
    ``*.bst`` — which minimatch reads as "a ``.bst`` file at the repository
    root" — would otherwise be read as a manager of the junction and reported
    as a second writer that is not there.

    Known limitation: minimatch's brace expansion (``{a,b}``) is not handled
    here, so ``elements/{fsdk-containers,other}.bst`` returns False even
    though minimatch would match the junction. None of the printer forks'
    current ``renovate.json`` files use brace expansion over the junction, so
    the check stays sound; add the expansion if that ever changes.
    """
    regex = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                    regex += "(?:.*/)?"
                else:
                    regex += ".*"
                continue
            regex += "[^/]*"
        elif char == "?":
            regex += "[^/]"
        elif char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                regex += re.escape(char)
            else:
                body = pattern[index + 1 : close]
                if body.startswith(("!", "^")):
                    body = "^" + body[1:]
                regex += "[" + body + "]"
                index = close + 1
                continue
        else:
            regex += re.escape(char)
        index += 1
    return re.fullmatch(regex, JUNCTION) is not None


def pattern_matches_junction(pattern: str, regex: bool = False) -> bool:
    """Whether a Renovate file pattern can match ``elements/fsdk-containers.bst``.

    ``regex=True`` is the ``fileMatch`` syntax, where the entry is a bare regex
    (``^elements/fsdk-containers\\.bst$``) and is never a glob. Otherwise the
    entry is a ``managerFilePatterns`` one, which is a glob
    (``include/source-pins.yml``) unless it is wrapped in slashes
    (``/^elements\\/fsdk-containers\\.bst$/``), which is how the printer forks
    write theirs.
    """
    if regex:
        expression: str | None = pattern
    elif pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 1:
        expression = pattern[1:-1]
    else:
        expression = None
    if expression is None:
        return glob_matches_junction(pattern.lstrip("/"))
    try:
        return re.search(expression, JUNCTION) is not None
    except re.error:
        raise Failure(f"renovate.json file pattern {pattern!r} is not a regex") from None


def renovate_manages_junction(config: dict) -> bool:
    """True when a Renovate manager could rewrite ``elements/fsdk-containers.bst``.

    A manager is only a second writer for the junction if its file patterns can
    match the junction file. ``enabledManagers`` entries such as
    ``github-actions`` name no file pattern, so they are reported for the record
    but never counted as a manager of a ``.bst`` element.
    """
    for key, pattern in renovate_file_patterns(config):
        if key == "enabledManagers":
            continue
        if pattern_matches_junction(pattern, regex=key == "fileMatch"):
            return True
    return False


def renovate_extends(config: dict) -> list[str]:
    """The presets a ``renovate.json`` inherits from.

    Only the fork's own file is parsed, so a custom manager defined in a shared
    preset (the forks extend ``local>projectbluefin/renovate-config``) is
    invisible to this check and is reported as a limit rather than a pass.
    """
    extends = config.get("extends")
    if isinstance(extends, list):
        return [preset for preset in extends if isinstance(preset, str)]
    return []


def check_tree(
    tree: Path,
    oci_element: str,
    remote: str,
    fetch: bool = True,
    notes: list[str] | None = None,
) -> list[str]:
    """Every contract violation of one fork checkout, as plain sentences.

    ``notes`` collects what the check cannot see rather than what it rejects:
    a Renovate config that inherits presets may define a junction manager this
    check never reads.
    """
    violations: list[str] = []

    # 1. An immutable junction, with one proposal owner.
    owners: list[str] = []
    if (tree / UPDATER).is_file():
        owners.append(UPDATER)
    renovate_config = tree / RENOVATE
    if renovate_config.is_file():
        try:
            config = json.loads(renovate_config.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise Failure(f"cannot read {renovate_config}: {error}") from None
        if renovate_manages_junction(config):
            owners.append(f"a {renovate_config.name} manager")
        elif notes is not None:
            presets = renovate_extends(config)
            if presets:
                notes.append(
                    f"{renovate_config.name} extends {', '.join(presets)}; only this file is "
                    f"read, so a manager over {JUNCTION} defined in an inherited preset would "
                    "not be seen here"
                )
    if not owners:
        violations.append(
            f"{tree.name} has no {UPDATER} and no Renovate manager over {JUNCTION}: nothing "
            "proposes the shared junction, so a new FSDK release would leave this image on a "
            "stale base indefinitely"
        )
    elif len(owners) > 1:
        violations.append(
            f"{' and '.join(owners)} both propose {JUNCTION}: two proposal owners for one "
            "junction means duplicate or racing updates; keep exactly one"
        )

    try:
        commit = junction_ref(tree)
    except Failure as failure:
        violations.append(str(failure))
        return violations

    # 2. Labels that describe the FSDK release the junction actually pins.
    try:
        version, ref = oci_labels(tree, oci_element)
    except Failure as failure:
        violations.append(str(failure))
        return violations

    if not fetch:
        return violations

    try:
        pinned_version, pinned_ref = fsdk_pin(commit, remote)
    except Failure as failure:
        violations.append(str(failure))
        return violations

    if version != pinned_version:
        violations.append(
            f"{oci_element} labels io.projectbluefin.fsdk.version as '{version}' but "
            f"fsdk-containers {commit[:12]} builds on FSDK {pinned_version}"
        )
    if ref != pinned_ref:
        violations.append(
            f"{oci_element} labels io.projectbluefin.fsdk.ref as '{ref}' but "
            f"fsdk-containers {commit[:12]} pins FSDK commit {pinned_ref}"
        )
    return violations


def infer_oci_element(tree: Path) -> str | None:
    """The single OCI element carrying the FSDK labels, if the tree names one.

    Lets the checker run against a checkout under any directory name, which is
    what a fork's own CI would do; FORKS is only the convenience mapping.
    """
    oci_dir = tree / "elements/oci"
    if not oci_dir.is_dir():
        return None
    labelled = [
        element
        for element in sorted(oci_dir.glob("*.bst"))
        if re.search(LABEL % "version", read_text(element, "its FSDK labels"), re.MULTILINE)
    ]
    return str(labelled[0].relative_to(tree)) if len(labelled) == 1 else None


def check_fork(
    name: str,
    tree: Path,
    remote: str,
    fetch: bool = True,
    oci_element: str | None = None,
    notes: list[str] | None = None,
) -> list[str]:
    element = oci_element or FORKS.get(name) or infer_oci_element(tree)
    if not element:
        raise Failure(
            f"cannot tell which element of {tree} carries the {LABEL_VERSION}/{LABEL_REF} "
            f"labels; pass --oci-element (known forks: {', '.join(sorted(FORKS))})"
        )
    return check_tree(tree, element, remote, fetch=fetch, notes=notes)


def clone(name: str, ref: str, destination: Path) -> Path:
    if not REPO_NAME.match(name):
        raise Failure(
            f"--repo {name!r} is not a repository name; it must match "
            f"{REPO_NAME.pattern} (known forks: {', '.join(sorted(FORKS))})"
        )
    url = f"https://github.com/projectbluefin/{name}.git"
    try:
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "--branch", ref, url, str(destination)],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as error:
        detail = getattr(error, "stderr", "") or error
        raise Failure(f"cannot clone {url} at {ref}: {str(detail).strip()}") from None
    return destination


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "trees",
        nargs="*",
        type=Path,
        help="fork checkouts to check; defaults to --repo for each named fork",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="FORK",
        help="check a fork by name, cloning its branch from projectbluefin (repeatable)",
    )
    parser.add_argument(
        "--ref",
        default="testing",
        help="branch to clone for --repo (default: %(default)s)",
    )
    parser.add_argument(
        "--fsdk-remote",
        default=FSDK_CONTAINERS_URL,
        help="where to read the nested FSDK pin (default: %(default)s)",
    )
    parser.add_argument(
        "--oci-element",
        help=(
            "the OCI element carrying the FSDK labels; by default the fork name is "
            "looked up and, failing that, the labels are found in the checkout"
        ),
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip the nested FSDK pin lookup (checks 1 and 3 only, no network)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing when every fork is clean",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    trees = list(args.trees)
    oci_element = args.oci_element
    # The clones must outlive the loop that checks them, so this cannot be a
    # context manager: a TemporaryDirectory would delete the trees before they
    # are read.
    clones: str | None = None
    try:
        if oci_element and len(args.repo) + len(trees) != 1:
            print("--oci-element applies to a single checkout", file=sys.stderr)
            return 2
        if args.repo:
            clones = tempfile.mkdtemp(prefix="check-printing-junction-")
            for name in args.repo:
                try:
                    trees.append(clone(name, args.ref, Path(clones) / name))
                except Failure as failure:
                    print(f"check-printing-junction: {failure}", file=sys.stderr)
                    return 1
        if not trees:
            print("no fork checkout or --repo given; nothing to check", file=sys.stderr)
            return 2

        total = 0
        for tree in trees:
            name = tree.resolve().name
            notes: list[str] = []
            try:
                violations = check_fork(
                    name,
                    tree,
                    args.fsdk_remote,
                    fetch=not args.no_fetch,
                    oci_element=oci_element,
                    notes=notes,
                )
            except Failure as failure:
                print(f"check-printing-junction: {failure}", file=sys.stderr)
                return 1
            for note in notes:
                print(f"{name}: note: {note}", file=sys.stderr)
            for violation in violations:
                print(f"{name}: {violation}", file=sys.stderr)
            total += len(violations)
            if not violations and not args.quiet:
                print(f"{name}: OK")
        if total:
            print(f"check-printing-junction: {total} violation(s)", file=sys.stderr)
            return 1
        if not args.quiet:
            print("check-printing-junction: OK")
        return 0
    finally:
        if clones:
            shutil.rmtree(clones, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
