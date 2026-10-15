"""A minimal MCP client over stdio, standard library only, for the service
archetype: it starts a server, initializes the session, lists the tools and
calls them. The stdio transport carries one JSON-RPC 2.0 message per line in
each direction.

The client answers the requests a server may send it (`ping` with an empty
result, `roots/list` with no roots, anything else with method-not-found) and
ignores notifications. A server that writes something other than JSON on
stdout has that line skipped; its stderr goes to a log file, so a chatty
server cannot fill a pipe and stall.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "assay-service-world", "version": "1"}
METHOD_NOT_FOUND = -32601


class McpError(RuntimeError):
    """A JSON-RPC error answer, or a server that went away or timed out."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class McpClient:
    """One server process and one session with it."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        stderr_log: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.timeout = timeout
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._log: IO[str] | None = stderr_log.open("a") if stderr_log is not None else None
        try:
            self.process = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log if self._log is not None else subprocess.DEVNULL,
                text=True, bufsize=1, env=dict(env) if env is not None else None, cwd=cwd,
            )
        except OSError as error:
            raise McpError(f"the MCP server could not start: {error}") from error
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.server_info: dict[str, Any] = {}
        self.initialize()

    # -- the session ----------------------------------------------------------

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.server_info = dict(result.get("serverInfo") or {})
        self.notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": dict(arguments)})

    def close(self) -> None:
        process = self.process
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        if self._log is not None:
            self._log.close()
            self._log = None

    # -- JSON-RPC -------------------------------------------------------------

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            ident = self._next_id
            self._next_id += 1
            answers: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[ident] = answers
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": dict(params or {})})
        try:
            message = answers.get(timeout=self.timeout)
        except queue.Empty as error:
            raise McpError(f"the MCP server did not answer {method} within {self.timeout:g} seconds") from error
        finally:
            with self._lock:
                self._pending.pop(ident, None)
        if "error" in message:
            failure = message["error"] or {}
            raise McpError(f"{method}: {failure.get('message', 'error')}", failure.get("code"))
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = dict(params)
        self._send(message)

    def _send(self, message: Mapping[str, Any]) -> None:
        line = json.dumps(message, separators=(",", ":"))
        with self._write_lock:
            if self.process.stdin is None or self.process.poll() is not None:
                raise McpError("the MCP server has exited")
            try:
                self.process.stdin.write(line + "\n")
                self.process.stdin.flush()
            except OSError as error:
                raise McpError(f"the MCP server stopped reading: {error}") from error

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                self._answer(message)
            elif "id" in message:
                with self._lock:
                    waiting = self._pending.get(message["id"])
                if waiting is not None:
                    waiting.put(message)
        # The server is gone: wake every waiting request with an error.
        with self._lock:
            pending = list(self._pending.values())
        for waiting in pending:
            waiting.put({"error": {"message": "the MCP server exited", "code": None}})

    def _answer(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "ping":
            reply["result"] = {}
        elif method == "roots/list":
            reply["result"] = {"roots": []}
        else:
            reply["error"] = {"code": METHOD_NOT_FOUND, "message": f"the client does not offer {method}"}
        try:
            self._send(reply)
        except McpError:
            pass


def child_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The daemon's environment with the recipe's additions."""
    return {**os.environ, **(extra or {})}
