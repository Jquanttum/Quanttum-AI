"""A small client for Ollama, the program that runs language models locally.

* Only this computer may be contacted: the host must be a loopback address.
  Quanttum AI never sends your messages anywhere else.
* No third-party packages: just the standard library.
* Every failure becomes an LLMError with a message that is safe to show.
"""

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_HOST = "http://127.0.0.1:11434"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class LLMError(Exception):
    """Something went wrong talking to the local model. Safe to display."""


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size: int = 0                    # bytes on disk, roughly the RAM needed


@dataclass
class ChatResult:
    text: str
    tokens: int = 0                  # tokens generated
    seconds: float = 0.0             # time spent generating
    load_seconds: float = 0.0        # time spent loading the model
    cancelled: bool = False
    prompt_tokens: int = 0           # prompt tokens actually processed (cached
    prompt_seconds: float = 0.0      # ones are not counted)

    @property
    def tokens_per_second(self):
        return self.tokens / self.seconds if self.seconds > 0 else 0.0

    @property
    def prompt_tokens_per_second(self):
        return (self.prompt_tokens / self.prompt_seconds
                if self.prompt_seconds > 0 else 0.0)


class OllamaClient:
    def __init__(self, host=DEFAULT_HOST, connect_timeout=2.0,
                 read_timeout=180.0, probe_timeout=1.0, clock=time.monotonic):
        parsed = urlparse(host if "://" in host else f"http://{host}")

        if parsed.scheme != "http" or (parsed.hostname or "") not in _LOOPBACK:
            raise LLMError(
                "For privacy, I only talk to a model running on this "
                "computer (127.0.0.1 or localhost).")

        self.host = parsed.hostname
        self.port = parsed.port or 11434
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.probe_timeout = probe_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._up = (False, -1e9)             # (answer, when it was checked)

    # ---- plumbing ---------------------------------------------------

    def _connect(self, timeout):
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=self.connect_timeout)

        try:
            connection.connect()
            connection.sock.settimeout(timeout)
        except (ConnectionError, OSError, socket.timeout):
            connection.close()
            raise LLMError(
                "I can't reach Ollama. Start the Ollama app and try again.")

        return connection

    def _json(self, method, path, body=None, timeout=None):
        connection = self._connect(timeout or self.probe_timeout)

        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload else {}
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            raw = response.read()

            if response.status != 200:
                raise LLMError(self._explain(raw, response.status))

            return json.loads(raw)

        except LLMError:
            raise
        except socket.timeout:
            raise LLMError("Ollama didn't answer in time.")
        except (ValueError, OSError, http.client.HTTPException):
            raise LLMError("Ollama gave an unexpected reply.")
        finally:
            connection.close()

    @staticmethod
    def _explain(raw, status):
        try:
            message = json.loads(raw).get("error", "")
        except (ValueError, AttributeError):
            message = ""

        return f"Ollama says: {message}" if message else (
            f"Ollama returned an error (HTTP {status}).")

    # ---- queries ----------------------------------------------------

    def is_up(self, max_age=5.0):
        """True if Ollama answers. The answer is cached for a few seconds so
        repeated checks stay cheap."""

        with self._lock:
            answer, when = self._up

            if self._clock() - when < max_age:
                return answer

        try:
            self._json("GET", "/api/version")
            answer = True
        except LLMError:
            answer = False

        with self._lock:
            self._up = (answer, self._clock())

        return answer

    def version(self):
        return str(self._json("GET", "/api/version").get("version", "?"))

    def models(self):
        data = self._json("GET", "/api/tags", timeout=5.0)
        found = []

        for entry in data.get("models", []):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str):
                found.append(ModelInfo(name, int(entry.get("size") or 0)))

        return found

    # ---- chat -------------------------------------------------------

    def chat(self, model, messages, on_token=None, options=None,
             json_mode=False, keep_alive="5m", cancel=None):
        """Send a conversation and stream the reply.

        on_token(text) is called for each piece as it arrives. Setting the
        `cancel` event stops generation promptly.
        """

        body = {"model": model, "messages": messages, "stream": True,
                "keep_alive": keep_alive}

        if options:
            body["options"] = options
        if json_mode:
            body["format"] = "json"

        connection = self._connect(self.read_timeout)
        pieces = []
        result = ChatResult("")

        try:
            connection.request("POST", "/api/chat", json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()

            if response.status != 200:
                raise LLMError(self._explain(response.read(), response.status))

            while True:
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    break

                line = response.readline()

                if not line:
                    break

                line = line.strip()

                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except ValueError:
                    raise LLMError("Ollama sent a reply I couldn't read.")

                if "error" in chunk:
                    raise LLMError(f"Ollama says: {chunk['error']}")

                piece = (chunk.get("message") or {}).get("content", "")

                if piece:
                    pieces.append(piece)

                    if on_token is not None:
                        on_token(piece)

                if chunk.get("done"):
                    result.tokens = int(chunk.get("eval_count") or 0)
                    result.seconds = (chunk.get("eval_duration") or 0) / 1e9
                    result.load_seconds = (chunk.get("load_duration") or 0) / 1e9
                    result.prompt_tokens = int(chunk.get("prompt_eval_count") or 0)
                    result.prompt_seconds = (
                        chunk.get("prompt_eval_duration") or 0) / 1e9
                    break

        except LLMError:
            raise
        except socket.timeout:
            raise LLMError(
                "The model took too long to answer. It may still be loading; "
                "try again in a moment, or choose a smaller model.")
        except (OSError, http.client.HTTPException) as error:
            raise LLMError(f"The connection to Ollama dropped ({error}).")
        finally:
            connection.close()

        result.text = "".join(pieces)
        return result
