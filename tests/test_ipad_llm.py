"""Private iPad model client contract and local HTTP boundary tests."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from geniusnew.contracts import ContractError
from tools import ipad_llm


HOST = "100.100.101.102"
KEY = "TEST-ONLY-OPAQUE-KEY-1234567890123456"


def status(*, online=True, peer_online=True, peer_os="iOS", peer_ip=HOST):
    return {
        "BackendState": "Running" if online else "Stopped",
        "Self": {"Online": online},
        "Peer": {"peer": {"Online": peer_online, "OS": peer_os,
                          "TailscaleIPs": [peer_ip]}},
    }


class ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(("GET", self.path, self.headers, None))
        self.reply()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append(("POST", self.path, self.headers, body))
        self.reply()

    def reply(self):
        self.send_response(self.server.response_status)
        if self.server.redirect:
            self.send_header("Location", "http://127.0.0.1:9/leak")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.server.response_chunk_delay:
            try:
                for byte in self.server.response_body:
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
                    time.sleep(self.server.response_chunk_delay)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.wfile.write(self.server.response_body)

    def log_message(self, *_args):
        pass


class IpadModelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_file = Path(self.temp.name) / "api-key"
        self.key_file.write_text(KEY)
        self.key_file.chmod(0o600)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        self.server.requests = []
        self.server.response_status = 200
        self.server.response_body = b'{"data":[{"id":"local-gemma"}]}'
        self.server.response_chunk_delay = 0
        self.server.redirect = False
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.endpoint_patch = mock.patch.object(
            ipad_llm, "_endpoint",
            side_effect=lambda _host, _port: f"http://127.0.0.1:{self.server.server_port}",
        )
        self.status_patch = mock.patch.object(ipad_llm, "_tailnet_status",
                                              return_value=status())
        self.route_patch = mock.patch.object(ipad_llm, "_require_tailnet_route")
        self.interface_patch = mock.patch.object(ipad_llm, "_INTERFACE", "lo")

    def connected(self):
        self.endpoint_patch.start()
        self.status_patch.start()
        self.route_patch.start()
        self.interface_patch.start()
        self.addCleanup(self.interface_patch.stop)
        self.addCleanup(self.route_patch.stop)
        self.addCleanup(self.status_patch.stop)
        self.addCleanup(self.endpoint_patch.stop)

    def test_endpoint_accepts_only_exact_tailscale_ipv4_and_valid_port(self):
        self.assertEqual(ipad_llm._endpoint(HOST, 8080), f"http://{HOST}:8080")
        for host, port in (("127.0.0.1", 8080), ("192.168.1.2", 8080),
                           ("example.com", 8080), ("100.100.101.102/extra", 8080),
                           (HOST, 0), (HOST, True)):
            with self.subTest(host=host, port=port):
                with self.assertRaises(ContractError):
                    ipad_llm._endpoint(host, port)

    def test_peer_must_be_online_ios_device_with_exact_address(self):
        ipad_llm._require_ipad_peer(HOST, status())
        for state in (status(online=False), status(peer_online=False),
                      status(peer_os="linux"), status(peer_ip="100.100.101.103"),
                      {"BackendState": "Running", "Self": {"Online": True}}):
            with self.subTest(state=state):
                with self.assertRaises(ContractError):
                    ipad_llm._require_ipad_peer(HOST, state)

    def test_tailnet_status_refuses_unavailable_or_invalid_data(self):
        with mock.patch.object(ipad_llm.subprocess, "run",
                               return_value=mock.Mock(returncode=1,
                                                      stdout=json.dumps(status()))):
            with self.assertRaises(ContractError):
                ipad_llm._tailnet_status()
        with mock.patch.object(ipad_llm.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="[]")):
            with self.assertRaises(ContractError):
                ipad_llm._tailnet_status()

    def test_route_must_use_tailscale_interface(self):
        for route, accepted in (([{"dev": "tailscale0"}], True),
                                ([{"dev": "eth0"}], False), ([], False)):
            with self.subTest(route=route):
                with mock.patch.object(ipad_llm.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout=json.dumps(route),
                )):
                    if accepted:
                        ipad_llm._require_tailnet_route(HOST)
                    else:
                        with self.assertRaises(ContractError):
                            ipad_llm._require_tailnet_route(HOST)
        with mock.patch.object(ipad_llm.subprocess, "run", return_value=mock.Mock(
            returncode=1, stdout='[{"dev":"tailscale0"}]',
        )):
            with self.assertRaises(ContractError):
                ipad_llm._require_tailnet_route(HOST)

    def test_api_key_file_is_private_and_no_symlink(self):
        self.assertEqual(ipad_llm._api_key(self.key_file), KEY)
        with self.assertRaises(ContractError):
            ipad_llm._api_key(str(self.key_file))
        self.key_file.chmod(0o644)
        with self.assertRaises(ContractError):
            ipad_llm._api_key(self.key_file)
        self.key_file.chmod(0o600)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.key_file)
        with self.assertRaises(ContractError):
            ipad_llm._api_key(link)
        self.key_file.write_text("short")
        with self.assertRaises(ContractError):
            ipad_llm._api_key(self.key_file)
        self.key_file.write_bytes(b"K" * 256 + b"\nX")
        with self.assertRaises(ContractError):
            ipad_llm._api_key(self.key_file)
        self.key_file.write_text(KEY)
        with mock.patch.object(ipad_llm.os, "fstat", return_value=mock.Mock(
            st_mode=stat.S_IFIFO | 0o600, st_uid=os.getuid(),
        )), mock.patch.object(ipad_llm.os, "read", return_value=KEY.encode()):
            with self.assertRaises(ContractError):
                ipad_llm._api_key(self.key_file)
        fifo = Path(self.temp.name) / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ContractError):
            ipad_llm._api_key(fifo)

    def test_models_and_chat_send_only_bounded_text_to_the_verified_peer(self):
        self.connected()
        self.assertEqual(ipad_llm.models(HOST, 8080, self.key_file), ("local-gemma",))
        self.server.response_body = json.dumps({
            "model": "local-gemma",
            "choices": [{"message": {"role": "assistant", "content": "Hello"},
                         "finish_reason": "stop"}],
        }).encode()
        self.assertEqual(ipad_llm.chat(HOST, 8080, self.key_file,
                                       "local-gemma", "Hello?"), "Hello")
        self.assertEqual(len(self.server.requests), 2)
        get, post = self.server.requests
        self.assertEqual(get[0:2], ("GET", "/v1/models"))
        self.assertEqual(post[0:2], ("POST", "/v1/chat/completions"))
        self.assertEqual(get[2]["Authorization"], "Bearer " + KEY)
        self.assertEqual(post[2]["Authorization"], "Bearer " + KEY)
        self.assertEqual(json.loads(post[3]), {
            "model": "local-gemma", "messages": [{"role": "user", "content": "Hello?"}],
            "stream": False, "max_tokens": 256,
        })
        self.assertNotIn(b"tools", post[3])

    def test_no_request_is_sent_to_an_unverified_peer(self):
        self.connected()
        with mock.patch.object(ipad_llm, "_tailnet_status", return_value=status(peer_online=False)):
            with self.assertRaises(ContractError):
                ipad_llm.chat(HOST, 8080, self.key_file, "local-gemma", "Hello?")
        self.assertEqual(self.server.requests, [])

    def test_no_request_is_sent_for_bad_input(self):
        self.connected()
        for kwargs in ({"prompt": ""}, {"prompt": None},
                       {"prompt": "x" * 8193}, {"prompt": "\ud800"},
                       {"model": "bad model"}, {"max_tokens": 0},
                       {"timeout": 0}, {"timeout": 301}):
            with self.subTest(kwargs=kwargs):
                values = {"model": "local-gemma", "prompt": "Hello?", **kwargs}
                with self.assertRaises(ContractError):
                    ipad_llm.chat(HOST, 8080, self.key_file, **values)
        self.assertEqual(self.server.requests, [])

    def test_tool_calls_and_wrong_roles_are_refused_as_untrusted_output(self):
        self.connected()
        for message, reason in (
            ({"role": "assistant", "content": "Done", "tool_calls": [{"id": "x"}]}, "stop"),
            ({"role": "assistant", "content": "Done"}, "tool_calls"),
            ({"role": "assistant", "content": "partial"}, "length"),
            ({"role": "assistant", "content": "filtered"}, "content_filter"),
            ({"role": "tool", "content": "Done"}, "stop"),
            ({"role": "assistant", "content": ""}, "stop"),
            ({"role": "assistant", "content": None}, "stop"),
            ({"role": "assistant", "content": "\x1b[2J"}, "stop"),
        ):
            self.server.response_body = json.dumps({
                "model": "local-gemma",
                "choices": [{"message": message, "finish_reason": reason}],
            }).encode()
            with self.subTest(message=message, reason=reason):
                with self.assertRaises(ContractError):
                    ipad_llm.chat(HOST, 8080, self.key_file, "local-gemma", "Hello?")

    def test_invalid_model_list_and_completion_are_refused(self):
        self.connected()
        for body in (b"{}", b'{"data":[]}', b'{"data":[7]}',
                     b'{"data":[{"id":"x","score":NaN}]}',
                     b'{"data":[],"data":[{"id":"x"}]}',
                     b'{"data":[{"id":"bad model"}]}',
                     b'{"data":[{"id":"x"},{"id":"x"}]}'):
            self.server.response_body = body
            with self.subTest(body=body):
                with self.assertRaises(ContractError):
                    ipad_llm.models(HOST, 8080, self.key_file)
        for body in (b"{}", b'{"choices":[]}', b'{"choices":[{}]}',
                     b'{"choices":[{"message":null}]}'):
            self.server.response_body = body
            with self.subTest(body=body):
                with self.assertRaises(ContractError):
                    ipad_llm.chat(HOST, 8080, self.key_file, "local-gemma", "Hello?")
        self.server.response_body = json.dumps({
            "model": "another-model",
            "choices": [{"message": {"role": "assistant", "content": "Wrong"},
                         "finish_reason": "stop"}],
        }).encode()
        with self.assertRaises(ContractError):
            ipad_llm.chat(HOST, 8080, self.key_file, "local-gemma", "Hello?")

    def test_redirect_error_and_oversized_response_are_refused(self):
        self.connected()
        self.server.response_status = 302
        self.server.redirect = True
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file)
        self.server.response_status = 503
        self.server.redirect = False
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file)
        self.server.response_status = 201
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file)
        self.server.response_status = 200
        base_response = json.dumps({"data": [{"id": "local-gemma", "padding": ""}]})
        self.server.response_body = base_response.replace(
            '"padding": ""',
            '"padding": "' + "x" * (64 * 1024 + 1 - len(base_response)) + '"',
        ).encode()
        self.assertEqual(len(self.server.response_body), 64 * 1024 + 1)
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file)
        self.server.response_body = b"not JSON"
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file)

    def test_direct_boundary_rejects_oversized_wire_and_invalid_model_type(self):
        self.connected()
        with self.assertRaises(ContractError):
            ipad_llm._request_json(
                f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions",
                KEY, payload={"large": "x" * (64 * 1024)}, timeout=1,
            )
        self.assertEqual(self.server.requests, [])
        with self.assertRaises(ContractError):
            ipad_llm._model(None)
        with self.assertRaises(ContractError):
            ipad_llm._model("\ud800")
        for value in ("flip\u202eid", "with\u00a0space"):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    ipad_llm._model(value)

    def test_cli_requires_model_before_reading_prompt(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = ipad_llm.main(["chat", "--host", HOST, "--port", "8080",
                                  "--key-file", str(self.key_file)])
        self.assertEqual(code, 2)
        self.assertIn("chat requires an exact model ID", output.getvalue())

    def test_proxy_environment_is_ignored(self):
        self.connected()
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                           "http_proxy": "http://127.0.0.1:9",
                                           "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual(ipad_llm.models(HOST, 8080, self.key_file),
                             ("local-gemma",))

    def test_socket_must_bind_to_the_tailscale_interface(self):
        self.connected()
        with mock.patch.object(ipad_llm, "_INTERFACE", "no-such-interface"):
            with self.assertRaises(ContractError):
                ipad_llm.models(HOST, 8080, self.key_file)
        self.assertEqual(self.server.requests, [])

    def test_response_has_one_overall_deadline(self):
        self.connected()
        self.server.response_chunk_delay = 0.08
        start = time.monotonic()
        with self.assertRaises(ContractError):
            ipad_llm.models(HOST, 8080, self.key_file, timeout=0.1)
        self.assertLess(time.monotonic() - start, 1)


if __name__ == "__main__":
    unittest.main()
