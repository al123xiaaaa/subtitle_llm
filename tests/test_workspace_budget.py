import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from subtitle_llm.workspace.budget import BudgetExceeded, BudgetLedger


class BudgetTest(unittest.TestCase):
    def test_reservations_persist_and_extra_spend_never_enlarges_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.sqlite3"
            ledger = BudgetLedger(path)
            ledger.configure("task:auto", baseline=10000, ratio=0.3)
            self.assertTrue(ledger.reserve("task:auto", "repair-1", 2000))
            restarted = BudgetLedger(path)
            with self.assertRaises(BudgetExceeded):
                restarted.reserve("task:auto", "repair-2", 1500)
            restarted.settle("repair-1", 1700)
            restarted.settle("repair-1", 1700)
            self.assertFalse(restarted.reserve("task:auto", "repair-1", 2000))
            restarted.configure("task:auto", baseline=10000, ratio=0.3)
            state = restarted.state("task:auto")
            self.assertEqual(state["limit"], 3000)
            self.assertEqual(state["spent"], 1700)
            self.assertEqual(state["available"], 1300)
            restarted.reserve("task:auto", "repair-3", 1200)
            restarted.settle("repair-3", None)
            self.assertEqual(restarted.state("task:auto")["available"], 100)
            self.assertEqual(restarted.state("task:auto")["unknown_calls"], 1)


if __name__ == "__main__":
    unittest.main()
