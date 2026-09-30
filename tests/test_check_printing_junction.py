"""Tests for scripts/check-printing-junction.py

The script is the cross-repository half of common#1246: it answers, for any
printer-fork checkout, whether the shared printing base junction is immutable,
whether the OCI labels describe the FSDK release that junction actually pins,
and whether exactly one thing proposes the junction. The nested FSDK pin is
read from a scratch git repository, so the suite needs no network.

The rules are fail-closed: "could not read the state" is never "the state is
correct", so the unreadable cases are tested as carefully as the clean one.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts/check-printing-junction.py"

FSDK_VERSION = "26.09.2"
FSDK_COMMIT = "1111111111111111111111111111111111111111"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_printing_junction", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
Failure = _mod.Failure
COMMIT_RE = _mod.COMMIT
check_fork = _mod.check_fork
check_tree = _mod.check_tree
fsdk_pin = _mod.fsdk_pin
infer_oci_element = _mod.infer_oci_element
junction_ref = _mod.junction_ref
main = _mod.main
oci_labels = _mod.oci_labels
renovate_file_patterns = _mod.renovate_file_patterns
renovate_extends = _mod.renovate_extends
renovate_manages_junction = _mod.renovate_manages_junction


# ---------------------------------------------------------------------------
# Fixtures: a scratch fsdk-containers remote and a fork checkout that uses it
# ---------------------------------------------------------------------------

@pytest.fixture
def fsdk_remote(tmp_path):
    """A git repository standing in for projectbluefin/fsdk-containers."""
    remote = tmp_path / "fsdk-containers"
    (remote / "elements").mkdir(parents=True)
    (remote / "elements/freedesktop-sdk.bst").write_text(
        "kind: junction\n"
        "sources:\n"
        "  - kind: git_repo\n"
        "    ref-format: git-describe\n"
        f"    ref: freedesktop-sdk-{FSDK_VERSION}-0-g{FSDK_COMMIT}\n"
    )
    subprocess.run(["git", "-C", str(remote), "init", "-q"], check=True)
    commit(remote, "pin FSDK")
    return remote


def commit(remote, message):
    """Commit everything in the scratch remote and return the new commit."""
    subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True)
    subprocess.run(
        [
            "git", "-C", str(remote),
            "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "--allow-empty", "-m", message,
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def write_tree(root, *, remote=None, junction_ref=None, version=FSDK_VERSION, ref=FSDK_COMMIT,
               updater=True, oci=True, oci_element="elements/oci/ps-printer-app.bst", renovate=None):
    """A minimal fork checkout: junction, OCI element, updater, renovate.json.

    ``remote`` pins the junction at a commit that remote really has, so the
    nested FSDK pin can be read without the network.
    """
    if junction_ref is None:
        junction_ref = commit(remote, "bump") if remote else "2" * 40
    (root / "elements/oci").mkdir(parents=True)
    (root / "elements/fsdk-containers.bst").write_text(
        "kind: junction\n"
        "sources:\n"
        "  - kind: git_repo\n"
        "    url: github:projectbluefin/fsdk-containers.git\n"
        "    track: main\n"
        f"    ref: {junction_ref}\n"
    )
    if oci:
        labels = (
            f"              'io.projectbluefin.fsdk.version': '{version}'\n"
            f"              'io.projectbluefin.fsdk.ref': '{ref}'\n"
        )
        (root / oci_element).write_text("kind: oci-image\n        Labels:\n" + labels)
    if updater:
        workflows = root / ".github/workflows"
        workflows.mkdir(parents=True)
        (workflows / "update-base.yml").write_text("name: Update fsdk-containers base\n")
    if renovate is not None:
        (root / "renovate.json").write_text(json.dumps(renovate))
    return root


@pytest.fixture
def fork(tmp_path, fsdk_remote):
    tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote)
    return tree, str(fsdk_remote)


# ---------------------------------------------------------------------------
# junction_ref
# ---------------------------------------------------------------------------

class TestJunctionRef:
    def test_returns_the_pinned_commit(self, fork):
        tree, _ = fork
        assert COMMIT_RE.match(junction_ref(tree))

    def test_rejects_a_branch(self, fork):
        tree, _ = fork
        (tree / "elements/fsdk-containers.bst").write_text(
            "kind: junction\nsources:\n  - kind: git_repo\n    ref: main\n"
        )
        with pytest.raises(Failure, match="not a full commit"):
            junction_ref(tree)

    def test_rejects_a_describe_shaped_ref(self, fork):
        tree, _ = fork
        (tree / "elements/fsdk-containers.bst").write_text(
            f"kind: junction\nsources:\n  - kind: git_repo\n    ref: fsdk-containers-3-0-g{'3' * 40}\n"
        )
        with pytest.raises(Failure, match="not a full commit"):
            junction_ref(tree)

    def test_rejects_an_ambiguous_pin(self, fork):
        tree, _ = fork
        (tree / "elements/fsdk-containers.bst").write_text(
            f"kind: junction\nsources:\n  - kind: git_repo\n    ref: {junction_ref(tree)}\n"
            f"    subdir: a\n  - kind: git_repo\n    ref: {'4' * 40}\n"
        )
        with pytest.raises(Failure, match="different refs"):
            junction_ref(tree)

    def test_rejects_a_missing_junction(self, tmp_path):
        (tmp_path / "elements").mkdir()
        with pytest.raises(Failure, match="does not exist"):
            junction_ref(tmp_path)

    def test_rejects_a_junction_with_no_ref(self, fork):
        tree, _ = fork
        (tree / "elements/fsdk-containers.bst").write_text(
            "kind: junction\nsources:\n  - kind: git_repo\n    track: main\n"
        )
        with pytest.raises(Failure, match="pins no source ref"):
            junction_ref(tree)

    def test_reads_a_ref_with_a_trailing_comment(self, fork):
        tree, _ = fork
        commit = junction_ref(tree)
        (tree / "elements/fsdk-containers.bst").write_text(
            f"kind: junction\nsources:\n  - kind: git_repo\n    ref: {commit}  # pinned\n"
        )
        assert junction_ref(tree) == commit

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_reads_a_quoted_ref(self, fork, quote):
        tree, _ = fork
        commit = junction_ref(tree)
        (tree / "elements/fsdk-containers.bst").write_text(
            "kind: junction\nsources:\n  - kind: git_repo\n"
            f"    ref: {quote}{commit}{quote}  # pinned\n"
        )
        assert junction_ref(tree) == commit

    def test_reports_an_unparseable_ref_line(self, fork):
        tree, _ = fork
        (tree / "elements/fsdk-containers.bst").write_text(
            "kind: junction\nsources:\n  - kind: git_repo\n    ref: 'unterminated\n"
        )
        with pytest.raises(Failure, match="cannot read"):
            junction_ref(tree)


# ---------------------------------------------------------------------------
# oci_labels
# ---------------------------------------------------------------------------

class TestOciLabels:
    def test_reads_both_labels(self, fork):
        tree, _ = fork
        assert oci_labels(tree, "elements/oci/ps-printer-app.bst") == (FSDK_VERSION, FSDK_COMMIT)

    def test_rejects_a_missing_label(self, fork):
        tree, _ = fork
        (tree / "elements/oci/ps-printer-app.bst").write_text(
            f"kind: oci-image\n        Labels:\n"
            f"              'io.projectbluefin.fsdk.version': '{FSDK_VERSION}'\n"
        )
        with pytest.raises(Failure, match="does not label io.projectbluefin.fsdk.ref"):
            oci_labels(tree, "elements/oci/ps-printer-app.bst")

    def test_rejects_a_short_ref_label(self, fork):
        tree, _ = fork
        (tree / "elements/oci/ps-printer-app.bst").write_text(
            f"kind: oci-image\n        Labels:\n"
            f"              'io.projectbluefin.fsdk.version': '{FSDK_VERSION}'\n"
            f"              'io.projectbluefin.fsdk.ref': 'deadbeef'\n"
        )
        with pytest.raises(Failure, match="not a full commit"):
            oci_labels(tree, "elements/oci/ps-printer-app.bst")

    def test_reads_double_quoted_labels(self, fork):
        tree, _ = fork
        (tree / "elements/oci/ps-printer-app.bst").write_text(
            f"kind: oci-image\n        Labels:\n"
            f'              "io.projectbluefin.fsdk.version": "{FSDK_VERSION}"\n'
            f'              "io.projectbluefin.fsdk.ref": "{FSDK_COMMIT}"\n'
        )
        assert oci_labels(tree, "elements/oci/ps-printer-app.bst") == (FSDK_VERSION, FSDK_COMMIT)

    def test_reads_bare_labels(self, fork):
        tree, _ = fork
        (tree / "elements/oci/ps-printer-app.bst").write_text(
            f"kind: oci-image\n        Labels:\n"
            f"              io.projectbluefin.fsdk.version: {FSDK_VERSION}\n"
            f"              io.projectbluefin.fsdk.ref: {FSDK_COMMIT}\n"
        )
        assert oci_labels(tree, "elements/oci/ps-printer-app.bst") == (FSDK_VERSION, FSDK_COMMIT)

    def test_rejects_a_missing_element(self, fork):
        tree, _ = fork
        with pytest.raises(Failure, match="does not exist"):
            oci_labels(tree, "elements/oci/absent.bst")


# ---------------------------------------------------------------------------
# fsdk_pin
# ---------------------------------------------------------------------------

class TestFsdkPin:
    def test_reads_the_nested_pin(self, fsdk_remote):
        assert fsdk_pin(commit(fsdk_remote, "bump"), str(fsdk_remote)) == (FSDK_VERSION, FSDK_COMMIT)

    def test_rejects_a_ref_that_is_not_release_tagged(self, fsdk_remote):
        (fsdk_remote / "elements/freedesktop-sdk.bst").write_text(
            "kind: junction\nsources:\n  - kind: git_repo\n    ref: main\n"
        )
        moved = commit(fsdk_remote, "unpin the release")
        with pytest.raises(Failure, match="ambiguous"):
            fsdk_pin(moved, str(fsdk_remote))

    def test_rejects_a_commit_that_cannot_be_fetched(self, fsdk_remote):
        with pytest.raises(Failure, match="cannot read"):
            fsdk_pin("9" * 40, str(fsdk_remote))

    def test_rejects_a_remote_without_the_file(self, tmp_path):
        empty = tmp_path / "empty"
        (empty / "elements").mkdir(parents=True)
        subprocess.run(["git", "-C", str(empty), "init", "-q"], check=True)
        (empty / "elements/other.bst").write_text("kind: junction\n")
        with pytest.raises(Failure, match="cannot read"):
            fsdk_pin(commit(empty, "x"), str(empty))


# ---------------------------------------------------------------------------
# renovate
# ---------------------------------------------------------------------------

class TestRenovate:
    def test_a_regex_manager_over_the_junction_is_a_second_owner(self):
        config = {
            "customManagers": [
                {
                    "customType": "regex",
                    "managerFilePatterns": [r"/^elements\/fsdk-containers\.bst$/"],
                }
            ]
        }
        assert renovate_manages_junction(config) is True

    def test_github_actions_is_not_a_manager_of_a_bst_element(self):
        assert renovate_manages_junction({"enabledManagers": ["github-actions"]}) is False

    def test_a_manager_for_another_file_is_not_a_second_owner(self):
        config = {
            "customManagers": [
                {
                    "customType": "regex",
                    "managerFilePatterns": [r"/^elements\/printer-app\/hplip\.bst$/"],
                }
            ]
        }
        assert renovate_manages_junction(config) is False

    def test_file_match_entries_are_regexes_not_globs(self):
        # Renovate's legacy fileMatch is always a regex, so an unwrapped one
        # over the junction is still a second owner.
        config = {
            "customManagers": [
                {"customType": "regex", "fileMatch": [r"^elements/fsdk-containers\.bst$"]}
            ]
        }
        assert renovate_manages_junction(config) is True
        assert renovate_file_patterns(config) == [
            ("fileMatch", r"^elements/fsdk-containers\.bst$")
        ]

    def test_a_bare_suffix_file_match_matches_the_junction(self):
        config = {"customManagers": [{"customType": "regex", "fileMatch": [r"\.bst$"]}]}
        assert renovate_manages_junction(config) is True

    def test_a_file_match_for_another_file_is_not_a_second_owner(self):
        config = {"customManagers": [{"customType": "regex", "fileMatch": [r"^include/.*\.yml$"]}]}
        assert renovate_manages_junction(config) is False

    def test_a_glob_manager_file_pattern_for_another_file_is_not_a_second_owner(self):
        config = {"customManagers": [{"customType": "regex", "managerFilePatterns": ["include/*.yml"]}]}
        assert renovate_manages_junction(config) is False

    def test_a_glob_manager_file_pattern_over_the_junction_is_a_second_owner(self):
        config = {
            "customManagers": [
                {"customType": "regex", "managerFilePatterns": ["elements/*.bst"]}
            ]
        }
        assert renovate_manages_junction(config) is True

    def test_a_root_level_glob_does_not_reach_into_a_directory(self):
        # minimatch stops ``*`` at a separator, so ``*.bst`` is a root-level
        # pattern and never the junction, which lives under ``elements/``.
        config = {"customManagers": [{"customType": "regex", "managerFilePatterns": ["*.bst"]}]}
        assert renovate_manages_junction(config) is False

    def test_a_double_star_glob_crosses_directories(self):
        config = {"customManagers": [{"customType": "regex", "managerFilePatterns": ["**/*.bst"]}]}
        assert renovate_manages_junction(config) is True

    def test_a_question_mark_glob_does_not_match_a_separator(self):
        config = {
            "customManagers": [
                {"customType": "regex", "managerFilePatterns": ["elements?fsdk-containers.bst"]}
            ]
        }
        assert renovate_manages_junction(config) is False

        config = {"customManagers": [{"customType": "regex", "managerFilePatterns": ["/[unclosed/"]}]}
        with pytest.raises(Failure, match="is not a regex"):
            renovate_manages_junction(config)

    def test_an_unparseable_file_match_fails_closed(self):
        config = {"customManagers": [{"customType": "regex", "fileMatch": ["[unclosed"]}]}
        with pytest.raises(Failure, match="is not a regex"):
            renovate_manages_junction(config)

    def test_malformed_config_types_are_ignored_not_crashed(self):
        assert renovate_file_patterns({"customManagers": "nope", "enabledManagers": None}) == []

    def test_extends_lists_the_inherited_presets(self):
        assert renovate_extends({"extends": ["local>projectbluefin/renovate-config", 7]}) == [
            "local>projectbluefin/renovate-config"
        ]

    def test_extends_of_the_wrong_type_is_no_preset(self):
        assert renovate_extends({"extends": "local>x"}) == []
        assert renovate_extends({}) == []


# ---------------------------------------------------------------------------
# check_tree / check_fork
# ---------------------------------------------------------------------------

class TestCheckTree:
    def test_a_clean_fork_has_no_violations(self, fork):
        tree, remote = fork
        assert check_tree(tree, "elements/oci/ps-printer-app.bst", remote) == []

    def test_reports_a_stale_version_label(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote, version="26.08.1")
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("fsdk.version as '26.08.1'" in v for v in violations)

    def test_reports_a_stale_ref_label(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote, ref="3" * 40)
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("fsdk.ref as '3333" in v for v in violations)

    def test_reports_a_moving_base(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", junction_ref="main")
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("not a full commit" in v for v in violations)

    def test_reports_a_missing_proposal_owner(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", updater=False)
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("nothing proposes the shared junction" in v for v in violations)

    def test_reports_a_second_proposal_owner(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "ps-printer-app",
            remote=fsdk_remote,
            renovate={
                "customManagers": [
                    {
                        "customType": "regex",
                        "managerFilePatterns": [r"/^elements\/fsdk-containers\.bst$/"],
                    }
                ]
            },
        )
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("two proposal owners" in v for v in violations)

    def test_renovate_alone_is_one_owner_not_a_contradiction(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "ps-printer-app",
            remote=fsdk_remote,
            updater=False,
            renovate={
                "customManagers": [
                    {
                        "customType": "regex",
                        "managerFilePatterns": [r"/^elements\/fsdk-containers\.bst$/"],
                    }
                ]
            },
        )
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        # One owner: neither "nothing proposes" nor "also proposes it".
        assert violations == []

    def test_an_inherited_preset_is_reported_as_a_limit(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "ps-printer-app",
            remote=fsdk_remote,
            renovate={"extends": ["local>projectbluefin/renovate-config"]},
        )
        notes: list[str] = []
        violations = check_tree(
            tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote), notes=notes
        )
        assert violations == []
        assert any("renovate-config" in note for note in notes)

    def test_no_preset_note_when_renovate_already_owns_the_junction(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "ps-printer-app",
            remote=fsdk_remote,
            renovate={
                "extends": ["local>projectbluefin/renovate-config"],
                "customManagers": [
                    {"customType": "regex", "fileMatch": [r"^elements/fsdk-containers\.bst$"]}
                ],
            },
        )
        notes: list[str] = []
        check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote), notes=notes)
        assert notes == []

    def test_rejects_unreadable_renovate_json(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote)
        (tree / "renovate.json").write_text("{not json")
        with pytest.raises(Failure, match="cannot read"):
            check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))

    def test_no_fetch_skips_the_nested_pin(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote, version="26.08.1")
        # A remote that cannot be fetched must not matter when no fetch is asked for.
        assert check_tree(tree, "elements/oci/ps-printer-app.bst", "/nonexistent", fetch=False) == []

    def test_an_unreadable_pin_is_reported_not_assumed_agreeing(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote)
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", "/nonexistent")
        assert any("cannot read" in v for v in violations)

    def test_a_fork_without_labels_still_reports_the_missing_element(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "ps-printer-app", oci=False)
        violations = check_tree(tree, "elements/oci/ps-printer-app.bst", str(fsdk_remote))
        assert any("does not exist" in v for v in violations)


class TestCheckFork:
    def test_known_fork_name_selects_its_oci_element(self, fork):
        tree, remote = fork
        assert check_fork("ps-printer-app", tree, remote) == []

    def test_an_unnamed_checkout_is_inferred_from_its_labels(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "work", remote=fsdk_remote, oci_element="elements/oci/hplip-printer-app.bst"
        )
        assert check_fork("work", tree, str(fsdk_remote)) == []

    def test_inference_needs_exactly_one_labelled_element(self, tmp_path, fsdk_remote):
        tree = write_tree(tmp_path / "work")
        (tree / "elements/oci/second.bst").write_text(
            f"kind: oci-image\n        Labels:\n"
            f"              'io.projectbluefin.fsdk.version': '{FSDK_VERSION}'\n"
            f"              'io.projectbluefin.fsdk.ref': '{FSDK_COMMIT}'\n"
        )
        assert infer_oci_element(tree) is None
        with pytest.raises(Failure, match="--oci-element"):
            check_fork("work", tree, str(fsdk_remote))

    def test_inference_of_an_empty_tree_is_none(self, tmp_path):
        (tmp_path / "elements/oci").mkdir(parents=True)
        assert infer_oci_element(tmp_path) is None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_clean_tree_exits_zero(self, fork, capsys):
        tree, remote = fork
        assert main([str(tree), "--fsdk-remote", remote]) == 0
        assert "OK" in capsys.readouterr().out

    def test_quiet_prints_nothing_when_clean(self, fork, capsys):
        tree, remote = fork
        assert main([str(tree), "--fsdk-remote", remote, "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_drift_exits_one_and_names_the_fork(self, tmp_path, fsdk_remote, capsys):
        tree = write_tree(tmp_path / "ps-printer-app", remote=fsdk_remote, version="26.08.1")
        assert main([str(tree), "--fsdk-remote", str(fsdk_remote)]) == 1
        assert "ps-printer-app" in capsys.readouterr().err

    def test_no_input_exits_two(self, capsys):
        assert main([]) == 2
        assert "nothing to check" in capsys.readouterr().err

    def test_a_repo_name_that_is_a_path_is_refused_before_cloning(self, capsys):
        assert main(["--repo", "../elsewhere", "--no-fetch"]) == 1
        assert "is not a repository name" in capsys.readouterr().err

    def test_an_inherited_preset_is_noted_on_stderr(self, tmp_path, fsdk_remote, capsys):
        tree = write_tree(
            tmp_path / "ps-printer-app",
            remote=fsdk_remote,
            renovate={"extends": ["local>projectbluefin/renovate-config"]},
        )
        assert main([str(tree), "--fsdk-remote", str(fsdk_remote)]) == 0
        assert "note: renovate.json extends" in capsys.readouterr().err

    def test_an_unreadable_config_exits_one(self, fork, capsys):
        tree, remote = fork
        (tree / "renovate.json").write_text("{not json")
        assert main([str(tree), "--fsdk-remote", remote]) == 1
        assert "cannot read" in capsys.readouterr().err

    def test_oci_element_applies_to_one_checkout(self, fork, capsys):
        tree, remote = fork
        assert main([str(tree), str(tree), "--oci-element", "elements/oci/ps-printer-app.bst"]) == 2

    def test_oci_element_overrides_the_fork_mapping(self, tmp_path, fsdk_remote):
        tree = write_tree(
            tmp_path / "ps-printer-app", remote=fsdk_remote, oci_element="elements/oci/renamed-printer-app.bst"
        )
        assert (
            main(
                [
                    str(tree),
                    "--fsdk-remote",
                    str(fsdk_remote),
                    "--oci-element",
                    "elements/oci/renamed-printer-app.bst",
                ]
            )
            == 0
        )
