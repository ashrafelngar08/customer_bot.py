"""
Runs both bots together in one process group. Used on hosts (like a single
Railway service) where you only get one deployable process but want the
customer bot and admin bot to share the same local shop.db file/filesystem.

If either bot exits, this restarts it, and if you stop this script (Ctrl+C
or the platform stopping the service) it shuts both down cleanly.
"""
import subprocess
import sys
import time
import signal

PROCS = {}


def start(name, script):
    print(f"[run_both] starting {name} ({script})...", flush=True)
    PROCS[name] = subprocess.Popen([sys.executable, script])


def stop_all(*_):
    print("[run_both] stopping all bots...", flush=True)
    for p in PROCS.values():
        p.terminate()
    for p in PROCS.values():
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)

    start("customer_bot", "customer_bot.py")
    start("admin_bot", "admin_bot.py")

    # Watch both processes; if one crashes, restart just that one after a
    # short delay so a bug in one bot doesn't take the other down for good.
    while True:
        time.sleep(3)
        for name, script in [("customer_bot", "customer_bot.py"), ("admin_bot", "admin_bot.py")]:
            proc = PROCS.get(name)
            if proc is not None and proc.poll() is not None:
                print(f"[run_both] {name} exited with code {proc.returncode}, restarting in 3s...", flush=True)
                time.sleep(3)
                start(name, script)


if __name__ == "__main__":
    main()
