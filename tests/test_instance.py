import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from clipboard_app.instance import InstanceLock


class InstanceLockTests(unittest.TestCase):
    def test_an_old_lock_file_does_not_block_a_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            path.write_text("old process metadata")
            lock = InstanceLock(path)
            self.assertTrue(lock.acquire())
            lock.release()

    def test_live_lock_rejects_second_instance_and_allows_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            first, second = InstanceLock(path), InstanceLock(path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.release()
                self.assertTrue(second.acquire())
            finally:
                first.release()
                second.release()

    def test_forced_exit_releases_lock_without_deleting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            script = (
                "import sys,time; from pathlib import Path; "
                "from clipboard_app.instance import InstanceLock; "
                "lock=InstanceLock(Path(sys.argv[1])); assert lock.acquire(); "
                "print('ready',flush=True); time.sleep(60)"
            )
            process = subprocess.Popen([sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                process.kill()
                process.wait(timeout=5)
                lock = InstanceLock(path)
                self.assertTrue(lock.acquire())
                lock.release()
                self.assertTrue(path.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
