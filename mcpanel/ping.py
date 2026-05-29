"""Minecraft server-list ping (Server List Ping protocol) — port of
pingServer() in main.js. Returns the same shape the IPC handler returned."""

import json
import socket
import struct


def _var_int(value):
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value != 0:
            b |= 0x80
        out.append(b)
        if value == 0:
            break
    return bytes(out)


def ping_server(host, port, timeout=3.0):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception:
        return {"online": False}
    sock.settimeout(timeout)
    try:
        host_buf = host.encode("utf-8")
        port_buf = struct.pack(">H", port)
        # Handshake (0x00): protocol 762 (1.19.4), nextState=1 (status)
        hs_body = (_var_int(0x00) + _var_int(762) + _var_int(len(host_buf))
                   + host_buf + port_buf + _var_int(1))
        sr_body = _var_int(0x00)  # status request
        sock.sendall(_var_int(len(hs_body)) + hs_body + _var_int(len(sr_body)) + sr_body)

        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            s = data.decode("utf-8", "replace")
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end > start:
                try:
                    parsed = json.loads(s[start:end + 1])
                except Exception:
                    continue
                players = parsed.get("players") or {}
                desc = parsed.get("description")
                motd = desc if isinstance(desc, str) else (desc or {}).get("text", "")
                return {
                    "online": True,
                    "players": players.get("online", 0),
                    "maxPlayers": players.get("max", 0),
                    "playerList": [p.get("name") for p in (players.get("sample") or [])],
                    "version": (parsed.get("version") or {}).get("name", "Unknown"),
                    "motd": motd,
                }
        return {"online": False}
    except socket.timeout:
        return {"online": False}
    except Exception:
        return {"online": False}
    finally:
        try:
            sock.close()
        except Exception:
            pass
