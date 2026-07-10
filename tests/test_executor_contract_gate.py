import tempfile
import unittest
from pathlib import Path

from scripts.executor_contract_gate import audit_executor_contract


VALID_API = """
class ActionRequest:
    action: str
    player_id: str
    snapshot_id: int
    proposal_id: str
    input_hash: str
    confirm_player_name: str
"""

VALID_ACTIONS = """
def execute():
    proposal_id = input_hash = snapshot_id = slot_moves = None
    preflight = max_snapshot_age = None
    post_write = verify = None
"""

VALID_TESTS = """
def test_stale_snapshot_rejected(): pass
def test_proposal_hash_mismatch_rejected(): pass
def test_slot_legality_preflight(): pass
def test_post_write_verification_failure(): pass
def test_protected_anchor_cannot_leave_roster(): pass
"""


class ExecutorContractGateTests(unittest.TestCase):
    def write_tree(self, root: Path, *, api=VALID_API, actions=VALID_ACTIONS, tests=VALID_TESTS):
        (root / "tests").mkdir()
        (root / "sandlot_api.py").write_text(api)
        (root / "sandlot_actions.py").write_text(actions)
        (root / "tests" / "test_sandlot_actions.py").write_text(tests)

    def test_complete_contract_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tree(root)

            self.assertEqual(audit_executor_contract(root), [])

    def test_current_mock_only_shape_fails_with_actionable_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tree(
                root,
                api="""
class ActionRequest:
    action: str
    player_id: str
    confirm_player_name: str
""",
                actions="def execute_action(): pass\n",
                tests="def test_successful_add_returns_200(): pass\n",
            )

            failures = audit_executor_contract(root)
            report = "\n".join(failures)
            self.assertIn("missing fields: input_hash, proposal_id, snapshot_id", report)
            self.assertIn("fresh live preflight", report)
            self.assertIn("post-write verification", report)
            self.assertIn("stale snapshot rejection", report)
            self.assertIn("protected dynasty asset", report)


if __name__ == "__main__":
    unittest.main()
