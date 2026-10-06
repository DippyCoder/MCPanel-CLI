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

_USE_UNIX_SOCKET = runstate._USE_UNIX_SOCKET

# If java dies this soon after launch, treat it as a failed start and report
# its last output lines as the start error rather than "started".
_EARLY_EXIT_SECONDS = 10
_BOOT_TAIL = 12


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
        self.started = None
        self.tail = []
        self.owns_lock = False

    # ─── logging ──────────────────────────────────────────────────────────
    def log(self, text, type_="out"):
        rec = {"time": _now_ms(), "text": text, "type": type_}
        with self.log_lock:
            if self.log_file:
                try:
                    self.log_file.write(json.dumps(rec) + "\n")
                    self.log_file.flush()
                except (OSError, ValueError):
                    # Disk full / file closed. Never let this kill the pump
                    # thread: if nothing drains java's stdout pipe, the pipe
                    # fills and the whole server freezes mid-tick.
                    pass
        self.tail.append(text)
        if len(self.tail) > _BOOT_TAIL:
            self.tail.pop(0)

    def _pump(self, stream, type_):
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").replace("\r", "").rstrip("\n")
                if line:
                    self.log(line, type_)
        except Exception:
            pass
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
        if self.proc is None:
            return {"ok": False, "error": "server is still starting"}
        if op == "cmd":
            text = str(req.get("text", "")).replace("\r", " ").replace("\n", " ")
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

    # ─── single-instance lock ─────────────────────────────────────────────
    def _acquire_lock(self):
        path = runstate.lock_path(self.id)
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if runstate.supervisor_alive(self.id):
                    return False
                try:
                    os.remove(path)  # stale: its owner died without cleaning up
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            self.owns_lock = True
            return True
        return False

    def _release_lock(self):
        if not self.owns_lock:
            return
        try:
            if runstate.supervisor_pid(self.id) == os.getpid():
                os.remove(runstate.lock_path(self.id))
        except OSError:
            pass
        self.owns_lock = False

    # ─── lifecycle ────────────────────────────────────────────────────────
    def run(self):
        paths.ensure_dirs()
        if not self._acquire_lock():
            self._boot_fail("Already running (another supervisor owns this server)")
            return
        try:
            self._run_locked()
        except Exception as e:
            self._boot_fail(f"Supervisor crashed: {e}")
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            runstate.cleanup_state(self.id)
        finally:
            with self.log_lock:
                if self.log_file:
                    try:
                        self.log_file.close()
                    except Exception:
                        pass
                    self.log_file = None
            self._release_lock()

    def _open_control_socket(self):
        if _USE_UNIX_SOCKET:
            sp = runstate.sock_path(self.id)
            try:
                os.remove(sp)
            except OSError:
                pass
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(sp)
            except OSError as e:
                sock.close()
                raise OSError(f"could not create control socket {sp} ({e}). Unix socket paths "
                              "are limited to ~107 characters — use a shorter MCPANEL_HOME") from e
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            tmp = runstate.port_path(self.id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as pf:
                pf.write(str(port))
            os.replace(tmp, runstate.port_path(self.id))
        sock.listen(8)
        return sock

    def _run_locked(self):
        import subprocess

        cfg = load_config()
        srv = find_server(cfg, self.id)
        if not srv:
            self._boot_fail("Server not found")
            return
        jar, err = resolve_jar(srv)
        if err:
            self._boot_fail(err)
            return

        # Socket first: if it can't be created there is no way to stop the
        # server later, so fail *before* java exists rather than orphaning it.
        try:
            self.sock = self._open_control_socket()
        except OSError as e:
            self._boot_fail(str(e))
            return

        cmd = build_java_command(srv, jar)
        runstate.rotate_log(self.id)
        self.log_file = open(runstate.log_path(self.id), "w", encoding="utf-8")

        popen_kwargs = {}
        if sys.platform == "win32":
            # This supervisor itself is launched with DETACHED_PROCESS (see
            # servers.py's _spawn_supervisor), so it has no console of its own. Spawning
            # java.exe (a console-subsystem binary) without CREATE_NO_WINDOW makes
            # Windows allocate it a brand-new console window, even with stdio piped.
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self.proc = subprocess.Popen(
                cmd, cwd=srv["dir"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        except Exception as e:
            self._close_socket()
            runstate.cleanup_state(self.id)
            self._boot_fail(f"Failed to launch java: {e}")
            return

        self.started = _now_ms()

        # State file (signals "running" to the rest of the CLI)
        runstate.write_state(self.id, {
            "supervisorPid": os.getpid(), "javaPid": self.proc.pid,
            "port": srv.get("port"), "started": self.started,
        })
        self._clear_boot_err()

        t_out = threading.Thread(target=self._pump, args=(self.proc.stdout, "out"), daemon=True)
        t_err = threading.Thread(target=self._pump, args=(self.proc.stderr, "err"), daemon=True)
        t_ctl = threading.Thread(target=self._serve_control, daemon=True)
        t_out.start(); t_err.start(); t_ctl.start()

        code = self.proc.wait()
        t_out.join(timeout=2); t_err.join(timeout=2)
        self.log(f"[mcpanel] server process exited with code {code}", "out")

        # Died straight away (bad -Xmx, wrong Java for the jar, port in use,
        # corrupt jar…): surface that as a start failure with java's own
        # output, instead of the caller reporting a successful start.
        if code != 0 and (_now_ms() - self.started) < _EARLY_EXIT_SECONDS * 1000:
            detail = "\n".join(self.tail[:-1][-_BOOT_TAIL:]).strip()
            self._boot_fail(f"Server exited immediately (code {code})"
                            + (f":\n{detail}" if detail else ""))

        # Teardown
        self._close_socket()
        runstate.cleanup_state(self.id)

    def _close_socket(self):
        self.stop_accept = True
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

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
