"""Detached daemon that owns one running Minecraft server process.

Launched by the `start` controller via:  python -m mcpanel.supervisor <id>
with start_new_session=True so it survives the launching CLI process.

Responsibilities (the long-lived half of what main.js's runningServers did):
  * spawn the java process in the server's directory
  * stream stdout/stderr to run/<id>.log.jsonl as {time,text,type} records
  * expose a unix control socket for cmd / kill / status requests
  * write run/<id>.json state, and clean everything up when java exits
"""

import json
import os
import socket
import sys
import threading
import time

from . import paths, runstate
from .config import load_config, find_server
from .util import resolve_jar, build_java_command


def _now_ms():
    return int(time.time() * 1000)


class Supervisor:
    def __init__(self, server_id):
        self.id = server_id
        self.proc = None
        self.log_file = None
        self.log_lock = threading.Lock()
        self.sock = None
        self.stop_accept = False

    # ─── logging ──────────────────────────────────────────────────────────
    def log(self, text, type_="out"):
        rec = {"time": _now_ms(), "text": text, "type": type_}
        with self.log_lock:
            if self.log_file:
                self.log_file.write(json.dumps(rec) + "\n")
                self.log_file.flush()

    def _pump(self, stream, type_):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line:
                self.log(line, type_)
        try:
            stream.close()
        except Exception:
            pass

    # ─── control socket ───────────────────────────────────────────────────
    def _serve_control(self):
        while not self.stop_accept:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            try:
                conn.settimeout(5)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                reply = self._handle(data.decode("utf-8", "replace").strip())
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle(self, line):
        try:
            req = json.loads(line) if line else {}
        except Exception:
            return {"ok": False, "error": "bad request"}
        op = req.get("op")
        if op == "cmd":
            text = req.get("text", "")
            try:
                self.proc.stdin.write((text + "\n").encode("utf-8"))
                self.proc.stdin.flush()
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if op == "kill":
            try:
                self.proc.kill()
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if op == "status":
            return {"ok": True, "running": self.proc.poll() is None,
                    "javaPid": self.proc.pid, "started": self.started}
        return {"ok": False, "error": "unknown op"}

    # ─── lifecycle ────────────────────────────────────────────────────────
    def run(self):
        import subprocess

        paths.ensure_dirs()
        cfg = load_config()
        srv = find_server(cfg, self.id)
        if not srv:
            self._boot_fail("Server not found")
            return
        jar, err = resolve_jar(srv)
        if err:
            self._boot_fail(err)
            return

        cmd = build_java_command(srv, jar)
        runstate.rotate_log(self.id)
        self.log_file = open(runstate.log_path(self.id), "w", encoding="utf-8")

        try:
            self.proc = subprocess.Popen(
                cmd, cwd=srv["dir"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except Exception as e:
            self._boot_fail(f"Failed to launch java: {e}")
            return

        self.started = _now_ms()

        # Control socket
        sp = runstate.sock_path(self.id)
        try:
            os.remove(sp)
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(sp)
        self.sock.listen(8)

        # State file (signals "running" to the rest of the CLI)
        with open(runstate.state_path(self.id), "w", encoding="utf-8") as f:
            json.dump({"supervisorPid": os.getpid(), "javaPid": self.proc.pid,
                       "port": srv.get("port"), "started": self.started}, f)
        self._clear_boot_err()

        t_out = threading.Thread(target=self._pump, args=(self.proc.stdout, "out"), daemon=True)
        t_err = threading.Thread(target=self._pump, args=(self.proc.stderr, "err"), daemon=True)
        t_ctl = threading.Thread(target=self._serve_control, daemon=True)
        t_out.start(); t_err.start(); t_ctl.start()

        code = self.proc.wait()
        t_out.join(timeout=2); t_err.join(timeout=2)
        self.log(f"[mcpanel] server process exited with code {code}", "out")

        # Teardown
        self.stop_accept = True
        try:
            self.sock.close()
        except Exception:
            pass
        with self.log_lock:
            if self.log_file:
                self.log_file.close()
        runstate.cleanup_state(self.id)

    # ─── failures before we ever reached "running" ───────────────────────
    def _boot_fail(self, message):
        try:
            with open(runstate.boot_err_path(self.id), "w", encoding="utf-8") as f:
                f.write(message)
        except Exception:
            pass

    def _clear_boot_err(self):
        try:
            os.remove(runstate.boot_err_path(self.id))
        except OSError:
            pass


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m mcpanel.supervisor <server_id>", file=sys.stderr)
        return 2
    Supervisor(argv[0]).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
