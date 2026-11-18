import socket
import threading
import unittest

from core.llm import LLMError, OllamaClient
from tests.fake_ollama import FakeOllama


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeOllama().start()
        self.client = OllamaClient(self.server.url)

    def tearDown(self):
        self.server.stop()


class PrivacyTests(unittest.TestCase):
    def test_only_this_computer_is_allowed(self):
        for host in ("http://example.com:11434", "http://192.168.1.5:11434",
                     "http://10.0.0.2", "https://127.0.0.1:11434",
                     "http://0.0.0.0:11434", "http://127.0.0.1.evil.com"):
            with self.subTest(host=host):
                with self.assertRaises(LLMError):
                    OllamaClient(host)

    def test_loopback_forms_are_accepted(self):
        for host in ("http://127.0.0.1:11434", "localhost:11434",
                     "http://localhost", "http://[::1]:11434"):
            with self.subTest(host=host):
                OllamaClient(host)


class QueryTests(ClientCase):
    def test_version_and_models(self):
        self.assertEqual(self.client.version(), "0.5.0")
        models = self.client.models()
        self.assertEqual([(m.name, m.size) for m in models],
                         [("llama3.2:3b", 2_000_000_000)])

    def test_is_up_true_false_and_cached(self):
        self.assertTrue(self.client.is_up())
        before = len(self.server.requests)
        for _ in range(5):
            self.assertTrue(self.client.is_up())
        self.assertEqual(len(self.server.requests), before)   # cached

        down = OllamaClient(f"http://127.0.0.1:{closed_port()}")
        self.assertFalse(down.is_up())

    def test_cache_expires(self):
        now = [0.0]
        client = OllamaClient(self.server.url, clock=lambda: now[0])
        client.is_up()
        count = len(self.server.requests)
        now[0] = 10.0
        client.is_up()
        self.assertEqual(len(self.server.requests), count + 1)


class ChatTests(ClientCase):
    def test_streams_tokens_and_reports_speed(self):
        self.server.reply = "one two three four"
        seen = []
        result = self.client.chat("llama3.2:3b",
                                  [{"role": "user", "content": "hi"}],
                                  on_token=seen.append)
        self.assertEqual("".join(seen), "one two three four")
        self.assertEqual(len(seen), 4)
        self.assertEqual(result.text, "one two three four")
        self.assertEqual(result.tokens, 4)
        self.assertAlmostEqual(result.tokens_per_second, 10.0, places=3)
        self.assertAlmostEqual(result.load_seconds, 0.5, places=3)

    def test_request_carries_model_messages_options_and_json_mode(self):
        messages = [{"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"}]
        self.client.chat("qwen2.5:3b", messages,
                         options={"num_ctx": 2048, "temperature": 0},
                         json_mode=True, keep_alive="1m")
        body = self.server.chat_requests()[0]
        self.assertEqual(body["model"], "qwen2.5:3b")
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["options"], {"num_ctx": 2048, "temperature": 0})
        self.assertEqual(body["format"], "json")
        self.assertEqual(body["keep_alive"], "1m")
        self.assertTrue(body["stream"])

    def test_cancel_stops_generation_early(self):
        self.server.reply = " ".join(f"w{i}" for i in range(60))
        self.server.token_delay = 0.02
        cancel = threading.Event()
        seen = []

        def on_token(piece):
            seen.append(piece)
            if len(seen) == 3:
                cancel.set()

        result = self.client.chat("m", [{"role": "user", "content": "x"}],
                                  on_token=on_token, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertLess(len(seen), 10)

    def test_unknown_model_is_explained(self):
        self.server.fail = "404"
        with self.assertRaises(LLMError) as caught:
            self.client.chat("nope:1b", [{"role": "user", "content": "x"}])
        self.assertIn("model 'nope:1b' not found", str(caught.exception))

    def test_error_in_the_middle_of_a_reply(self):
        self.server.reply = "a b c d e f"
        self.server.fail = "midstream_error"
        with self.assertRaises(LLMError) as caught:
            self.client.chat("m", [{"role": "user", "content": "x"}])
        self.assertIn("boom", str(caught.exception))

    def test_garbage_from_the_server(self):
        self.server.reply = "a b c d"
        self.server.fail = "garbage"
        with self.assertRaises(LLMError):
            self.client.chat("m", [{"role": "user", "content": "x"}])

    def test_ollama_not_running(self):
        down = OllamaClient(f"http://127.0.0.1:{closed_port()}")
        with self.assertRaises(LLMError) as caught:
            down.chat("m", [{"role": "user", "content": "x"}])
        self.assertIn("Start the Ollama app", str(caught.exception))
        with self.assertRaises(LLMError):
            down.models()

    def test_slow_server_times_out_with_a_helpful_message(self):
        self.server.fail = ("hang", 1.0)
        client = OllamaClient(self.server.url, read_timeout=0.3)
        with self.assertRaises(LLMError) as caught:
            client.chat("m", [{"role": "user", "content": "x"}])
        self.assertIn("took too long", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
