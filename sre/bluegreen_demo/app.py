#!/usr/bin/env python3
"""Tiny, dependency-free HTTP app used ONLY by sre/blue_green_demo.sh.

This is deliberately NOT a real product service - it's a stand-in so the
blue/green demo is fully self-contained and doesn't depend on the
sibling `services/` microservices (agent-service, etc.) being built yet
by the other agents working on this project. Swap this for a real
service's two image tags/ports once they exist - the nginx config and
the flip mechanism in blue_green_demo.sh don't care what's actually
listening on the other end.

Usage:
    VERSION=v1 PORT=9101 python3 app.py
    VERSION=v2 PORT=9102 python3 app.py

Uses only the Python standard library on purpose - no pip install, no
venv, no requirements.txt needed to run this demo.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = os.environ.get("VERSION", "v1")
PORT = int(os.environ.get("PORT", "9101"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "version": VERSION,
                "port": PORT,
                "path": self.path,
                "ts": time.time(),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Keep the demo's terminal output quiet - blue_green_demo.sh does
        # its own logging of what's happening at each step.
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[{VERSION}] listening on 127.0.0.1:{PORT}")
    server.serve_forever()
