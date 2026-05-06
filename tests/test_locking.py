from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperfetch_app.locking import file_lock


class LockingTests(unittest.TestCase):
    def test_lock_serializes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "x.lock"
            order: list[str] = []

            def worker(name: str, hold: float) -> None:
                with file_lock(lock_path, timeout_sec=5.0, poll_interval=0.01):
                    order.append(f"start-{name}")
                    time.sleep(hold)
                    order.append(f"end-{name}")

            t1 = threading.Thread(target=worker, args=("a", 0.1))
            t2 = threading.Thread(target=worker, args=("b", 0.0))
            t1.start()
            time.sleep(0.02)
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(order[0], "start-a")
            self.assertEqual(order[1], "end-a")
            self.assertEqual(order[2], "start-b")


if __name__ == "__main__":
    unittest.main()
