"""A stand-in for Ollama, for tests. Speaks the same HTTP protocol the real
one does (including chunked streaming) but replies with canned text."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                      # clients hang up mid-stream on purpose


class FakeOllama:
    def __init__(self, models=None, reply="Hello from the fake model.",
                 token_delay=0.0, version="0.5.0"):
        self.models = models if models is not None else [
            {"name": "llama3.2:3b", "size": 2_000_000_000}]
        self.reply = reply                  # text, or callable(body) -> text
        self.token_delay = token_delay
        self.version = version
        self.fail = None                    # see handler for the modes
        self.requests = []                  # (method, path, body)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send_json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _chunk(self, obj):
                data = (json.dumps(obj) if not isinstance(obj, str)
                        else obj).encode() + b"\n"
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))

                if self.path == "/api/version":
                    self._send_json(200, {"version": outer.version})
                elif self.path == "/api/tags":
                    self._send_json(200, {"models": outer.models})
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(("POST", self.path, body))

                if self.path != "/api/chat":
                    return self._send_json(404, {"error": "not found"})

                fail = outer.fail

                if fail == "404":
                    return self._send_json(
                        404, {"error": f"model '{body.get('model')}' not found"})

                if isinstance(fail, tuple) and fail[0] == "hang":
                    time.sleep(fail[1])

                text = outer.reply(body) if callable(outer.reply) else outer.reply
                words = text.split(" ")
                pieces = [w + (" " if i < len(words) - 1 else "")
                          for i, w in enumerate(words)]

                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                try:
                    for number, piece in enumerate(pieces):
                        if fail == "garbage" and number == 1:
                            self._chunk("this is not json")
                            break
                        if fail == "midstream_error" and number == 2:
                            self._chunk({"error": "boom"})
                            break
                        self._chunk({"message": {"role": "assistant",
                                                 "content": piece},
                                     "done": False})
                        if outer.token_delay:
                            time.sleep(outer.token_delay)
                    else:
                        self._chunk({"message": {"role": "assistant",
                                                 "content": ""},
                                     "done": True,
                                     "eval_count": len(pieces),
                                     "eval_duration": int(len(pieces) * 1e8),
                                     "load_duration": int(5e8)})
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02),
            daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def chat_requests(self):
        return [body for method, path, body in self.requests
                if method == "POST" and path == "/api/chat"]

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
