"""Run: python3 test_daemon.py  (about 6 s): the sampler stops once its app home, track.py included, is deleted."""
import os, shutil, subprocess, sys, tempfile, time

home = tempfile.mkdtemp()
mod = os.path.join(home, "mods", "timetrack")
os.makedirs(mod)
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "track.py"), mod)
open(os.path.join(mod, "config.json"), "w").write('{"sample_seconds": 5}')  # the shortest sample interval
env = {**os.environ, "TIMETRACK_DATA": os.path.join(home, "timetrack-data")}
p = subprocess.Popen([sys.executable, os.path.join(mod, "track.py"), "--run"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)  # inside the first 5-s sleep, so the next look finds track.py gone
assert p.poll() is None, "the sampler runs"
shutil.rmtree(home)
try:
    p.wait(timeout=20)
except subprocess.TimeoutExpired:
    p.kill()
    raise AssertionError("the sampler kept running without its app home")
print("ok daemon")
