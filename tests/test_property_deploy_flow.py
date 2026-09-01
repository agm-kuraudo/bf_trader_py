"""
Property-based and unit tests for SP-328 deploy orchestration.

Covers the pure decision logic behind ``scripts/deploy.sh`` in
``logic.deploy_checks.deploy_outcome`` — specifically Property 5 (deploy
atomicity) and the deploy abort-ordering unit test (Req 7.8).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.deploy_checks import DEPLOY_STEPS, deploy_outcome

# === Property 5: Deploy is atomic — a failed step leaves the running container unchanged ===


def _staged_results(fail_at_index: int | None, n_steps: int) -> list[bool]:
    """Build an ordered step-result list.

    Steps run in order and short-circuit on the first failure, so the list
    contains successes up to (but not past) the failing step. When
    ``fail_at_index`` is ``None`` all ``n_steps`` steps ran and succeeded.
    """
    if fail_at_index is None:
        return [True] * n_steps
    return [True] * fail_at_index + [False]


class TestProperty5DeployAtomicity:
    """Feature: season-background-data-capture.

    Property 5: Deploy is atomic — a failed step leaves the running container unchanged.
    """

    @given(fail_at_index=st.integers(min_value=0, max_value=len(DEPLOY_STEPS) - 1))
    @settings(max_examples=200)
    # Feature: season-background-data-capture, Property 5: Deploy is atomic — a failed step leaves the running container unchanged  # noqa: E501
    def test_failed_step_leaves_container_unchanged_unless_build_succeeded(self, fail_at_index):
        """
        For any staged failure at or before build_recreate, container_changed is
        False and the result identifies the first failed step. A failure strictly
        after a successful build_recreate reports container_changed True (the
        container was already replaced). Only a successful build_recreate step
        ever sets container_changed True.

        **Validates: Requirements 7.7, 7.8, 7.9**
        """
        results = _staged_results(fail_at_index, len(DEPLOY_STEPS))
        outcome = deploy_outcome(results)

        failed_step_name = DEPLOY_STEPS[fail_at_index]
        build_index = DEPLOY_STEPS.index("build_recreate")

        # The result identifies exactly the first failed step.
        assert outcome["failed_step"] == failed_step_name
        assert outcome["success"] is False

        # container_changed is True only if build_recreate ran AND succeeded,
        # i.e. the failure happened strictly after build_recreate.
        expected_changed = fail_at_index > build_index
        assert outcome["container_changed"] is expected_changed

        # A failure at or before build_recreate leaves the container unchanged.
        if fail_at_index <= build_index:
            assert outcome["container_changed"] is False

        # No step after the failed one ran.
        assert outcome["ran_steps"] == list(DEPLOY_STEPS[: fail_at_index + 1])

    @given(st.just(True))
    @settings(max_examples=100)
    # Feature: season-background-data-capture, Property 5: Deploy is atomic — a failed step leaves the running container unchanged  # noqa: E501
    def test_full_success_changes_container(self, _):
        """
        When every step runs and succeeds, the deploy succeeds and the container
        is considered changed (a fresh build+recreate replaced it).

        **Validates: Requirements 7.7, 7.8, 7.9**
        """
        outcome = deploy_outcome([True] * len(DEPLOY_STEPS))

        assert outcome["failed_step"] is None
        assert outcome["success"] is True
        assert outcome["container_changed"] is True
        assert outcome["ran_steps"] == list(DEPLOY_STEPS)


# === Unit test: deploy aborts BEFORE recreation when .env validation fails (Req 7.8) ===


class TestDeployAbortOrdering:
    """Deploy must abort before container recreation on invalid .env (Req 7.8)."""

    def test_env_validation_failure_aborts_before_build_recreate(self):
        """
        Given validate_env fails, build_recreate must NOT run and the container
        is left unchanged.

        _Requirements: 7.8_
        """
        # Steps: sync succeeded, validate_env failed -> nothing after runs.
        outcome = deploy_outcome([True, False])

        assert outcome["failed_step"] == "validate_env"
        assert "build_recreate" not in outcome["ran_steps"]
        assert outcome["container_changed"] is False
        assert outcome["success"] is False

    def test_sync_failure_aborts_before_everything(self):
        """
        Given the code-sync step fails, no later step runs and the container is
        unchanged (Req 7.7 / E6).
        """
        outcome = deploy_outcome([False])

        assert outcome["failed_step"] == "sync"
        assert outcome["ran_steps"] == ["sync"]
        assert outcome["container_changed"] is False
        assert outcome["success"] is False
