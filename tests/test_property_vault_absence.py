"""Property 6: No design or configuration artifact provisions Vault (SP-328, Task 9.3).

Validates Requirement 8.4: the design SHALL NOT define, reference, or provision
any Vault hostname or Vault network wiring, such that no artifact contains a
Vault service, host, or network entry.

Interpretation (agreed with the operator): Req 8.4 forbids Vault *wiring*
(a service/host/network/env entry the app would actually connect through), NOT
the ability to describe that Vault has been removed. Narrative mentions in spec
prose (e.g. "the my_keyvault container was removed") are explicitly allowed;
what must be absent is any construct that would provision or reach Vault.

The property: for every in-scope artifact AND every Vault-wiring pattern, the
artifact must not contain that wiring pattern. Hypothesis draws (artifact,
pattern) pairs so the space is covered with >=100 examples.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# In-scope artifacts (config/wiring + the named spec docs). Paths are relative
# to the repo root. Missing files are skipped (some, e.g. Rundeck job
# definitions, live on the Pi/UI rather than in the repo).
IN_SCOPE_ARTIFACTS = [
    "docker-compose.yml",
    ".env.example",
    "build/betfair_app.dockerfile",
    "scripts/deploy.sh",
    "scripts/verify_db.py",
    "scripts/verify_deploy.py",
    "scripts/check_freshness.py",
    "scripts/run_monitor_service.sh",
    "scripts/run_target_service.sh",
    "scripts/start_up_postgres.sh",
    ".kiro/specs/season-background-data-capture/design.md",
]


# Vault WIRING patterns (would actually PROVISION or REACH Vault). Deliberately
# narrow: each unambiguously indicates wiring, not narrative. Patterns like a
# bare "Vault(" or a mention of "start_up_vault.sh" are intentionally EXCLUDED
# because they occur in docstrings/markdown describing the removal (allowed by
# the agreed Req 8.4 interpretation). Applied case-insensitively, and only to
# ACTIVE (non-comment) lines - see _active_lines.
VAULT_WIRING_PATTERNS = [
    r"^\s*my_vault\s*:",  # compose service/network key
    r"^\s*my_keyvault\s*:",  # compose service/network key
    r"image\s*:\s*[\"']?[\w./-]*vault[\w./-]*",  # vault docker image
    r"VAULT_ADDR\s*=",  # vault endpoint env
    r"VAULT_TOKEN\s*=",  # vault token env
    r"VAULT_HOST\s*=",  # vault host env
    r"\w*_HOST\s*=\s*[\"']?my_?keyvault",  # any *_HOST pointing at vault
    r"\w*_HOST\s*=\s*[\"']?my_vault",  # any *_HOST pointing at vault
    r"^\s*import\s+vault\b",  # importing the retired client
    r"^\s*from\s+vault\b",  # importing the retired client
    r"=\s*VaultReader\s*\(",  # instantiating the retired client
    r"=\s*Vault\s*\(",  # instantiating the retired client
]


def _existing_artifacts() -> list:
    return [a for a in IN_SCOPE_ARTIFACTS if os.path.isfile(os.path.join(REPO_ROOT, a))]


def _read(artifact: str) -> str:
    # errors="replace" so a stray non-UTF-8 byte does not crash the scan; the
    # wiring patterns are ASCII so replacement chars never create false matches.
    with open(os.path.join(REPO_ROOT, artifact), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _active_content(artifact: str) -> str:
    """Return the artifact with comment lines removed.

    Vault WIRING lives on active lines; narrative describing the removal lives in
    "#" comments (shell/python/yaml/dockerfile) and markdown prose. Stripping "#"
    comment lines removes the allowed narrative for code/config artifacts. For
    markdown (design.md) there is no active-code concept, so wiring there would
    only ever be a fenced compose block, which the patterns still catch on its
    own lines.
    """
    lines = [ln for ln in _read(artifact).splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


class TestProperty6VaultWiringAbsent:
    # Feature: season-background-data-capture, Property 6: No design or configuration artifact references Vault

    @given(data=st.data())
    @settings(max_examples=200)
    def test_no_artifact_contains_vault_wiring(self, data):
        """For any (artifact, wiring-pattern) pair, the wiring pattern is absent."""
        artifacts = _existing_artifacts()
        assert artifacts, "No in-scope artifacts found - check paths."
        artifact = data.draw(st.sampled_from(artifacts))
        pattern = data.draw(st.sampled_from(VAULT_WIRING_PATTERNS))

        content = _active_content(artifact)
        match = re.search(pattern, content, flags=re.IGNORECASE | re.MULTILINE)
        assert match is None, (
            f"Vault wiring pattern {pattern!r} found in {artifact}: "
            f"{match.group(0) if match else None!r} - Req 8.4 forbids Vault wiring."
        )

    def test_every_in_scope_artifact_scanned_for_all_patterns(self):
        """Exhaustive companion to the property: check the full matrix once."""
        artifacts = _existing_artifacts()
        assert artifacts
        offenders = []
        for artifact in artifacts:
            content = _active_content(artifact)
            for pattern in VAULT_WIRING_PATTERNS:
                if re.search(pattern, content, flags=re.IGNORECASE | re.MULTILINE):
                    offenders.append((artifact, pattern))
        assert offenders == [], f"Vault wiring present: {offenders}"

    def test_docker_compose_has_no_vault_wiring_in_active_lines(self):
        """The compose file has no vault token on ACTIVE (non-comment) lines.

        The compose file legitimately CONTAINS the word Vault in comments
        ("There is deliberately NO Vault service..."), which is allowed. We
        strip comment lines and assert no vault token survives in the YAML.
        """
        path = os.path.join(REPO_ROOT, "docker-compose.yml")
        if not os.path.isfile(path):
            pytest.skip("docker-compose.yml not present")
        active_lines = [line for line in _read(path).splitlines() if not line.lstrip().startswith("#")]
        active = "\n".join(active_lines).lower()
        assert "vault" not in active, "docker-compose.yml has a Vault reference on an active (non-comment) line."
