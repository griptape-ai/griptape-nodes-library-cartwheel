"""Errors reach the user as NodeError, through the real requests path against a local HTTP server."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from griptape_nodes.exe_types.core_types import NodeError

from cartwheel import cartwheel_api
from cartwheel.create_character import CartwheelCreateCharacter
from cartwheel.create_mascot import CartwheelCreateMascot
from cartwheel.generate_motion import CartwheelGenerateMotion

REPO_ROOT = Path(__file__).resolve().parents[3]
NODE_FILES = ["create_character", "create_mascot", "generate_motion"]
SETTINGS_LINK = "#settings-secrets?filter=CARTWHEEL_API_KEY"


class _Handler(BaseHTTPRequestHandler):
    reply: dict[str, Any] = {}

    def _answer(self) -> None:
        reply = type(self).reply
        body = reply["body"]
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(reply["status"])
        for name, value in reply.get("headers", {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _answer

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Handler]]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cartwheel_api, "BASE_URL", f"http://127.0.0.1:{httpd.server_port}")
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    yield _Handler
    httpd.shutdown()
    httpd.server_close()


def test_refused_key_links_to_settings_and_keeps_the_response(server: type[_Handler]) -> None:
    server.reply = {
        "status": 401,
        "body": {"message": "Invalid API key"},
        "headers": {"x-request-id": "req-1"},
    }
    with pytest.raises(NodeError) as raised:
        cartwheel_api.request_json("GET", "/characters/char-1")
    error = raised.value
    assert "Cartwheel refused the API key" in str(error)
    assert "Invalid API key" in str(error)
    assert [link.url for link in error.links] == [SETTINGS_LINK]
    assert error.fields == {"status_code": 401, "endpoint": "GET /characters/char-1", "request_id": "req-1"}
    assert error.response == {"message": "Invalid API key"}


def test_rejected_request_puts_the_reason_in_the_message(server: type[_Handler]) -> None:
    server.reply = {"status": 400, "body": {"errorMessage": "prompt is too long"}}
    with pytest.raises(NodeError) as raised:
        cartwheel_api.request_json("POST", "/motion/fromText", payload={}, expected_statuses={202})
    assert str(raised.value) == "Cartwheel rejected the request. It said: prompt is too long"
    assert raised.value.fields["status_code"] == 400
    assert raised.value.links == []


def test_rate_limit_and_server_error_messages(server: type[_Handler]) -> None:
    server.reply = {"status": 429, "body": "slow down"}
    with pytest.raises(NodeError, match="rate-limiting"):
        cartwheel_api.request_json("GET", "/characters/char-1")
    server.reply = {"status": 503, "body": "<html>bad gateway</html>"}
    with pytest.raises(NodeError, match="problem handling the request") as raised:
        cartwheel_api.request_json("GET", "/characters/char-1")
    assert raised.value.response == {"text": "<html>bad gateway</html>"}


def test_unreachable_server_is_a_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cartwheel_api, "BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    with pytest.raises(NodeError, match="Could not reach Cartwheel") as raised:
        cartwheel_api.request_json("GET", "/characters/char-1", timeout=2)
    assert raised.value.fields == {"endpoint": "GET /characters/char-1"}


def test_failed_upload_names_the_storage_not_cartwheel(server: type[_Handler]) -> None:
    server.reply = {"status": 403, "body": "<Error><Code>AccessDenied</Code></Error>"}
    with pytest.raises(NodeError) as raised:
        cartwheel_api.upload_to_presigned_url(f"{cartwheel_api.BASE_URL}/upload?sig=secret", b"data")
    assert "Cartwheel's upload storage rejected the request" in str(raised.value)
    assert raised.value.links == []
    assert "secret" not in json.dumps(raised.value.fields)


def test_failed_download_hides_the_signature(server: type[_Handler]) -> None:
    server.reply = {"status": 404, "body": "gone"}
    with pytest.raises(NodeError) as raised:
        cartwheel_api.download_bytes(f"{cartwheel_api.BASE_URL}/file.zip?sig=secret")
    assert str(raised.value.fields["endpoint"]).endswith("/file.zip")
    assert "secret" not in json.dumps(raised.value.fields)


def test_character_failure_status_carries_the_reason_and_id(server: type[_Handler]) -> None:
    server.reply = {"status": 200, "body": {"uploadStatus": "FAILED", "errorMessage": "Rig has no root bone"}}
    with pytest.raises(NodeError) as raised:
        cartwheel_api.wait_for_character("char-1", poll_delay=0, max_attempts=3)
    assert str(raised.value) == "Rig has no root bone"
    assert raised.value.fields == {"status": "FAILED", "character_id": "char-1"}
    assert raised.value.response == {"uploadStatus": "FAILED", "errorMessage": "Rig has no root bone"}


def test_polling_timeout_names_the_setting_and_the_id(server: type[_Handler]) -> None:
    server.reply = {"status": 200, "body": {"status": "IN_PROGRESS"}}
    with pytest.raises(NodeError, match="Max Poll Attempts") as raised:
        cartwheel_api.wait_for_batch("batch-1", poll_delay=0, max_attempts=2)
    assert raised.value.fields == {"batch_id": "batch-1"}
    assert raised.value.response == {"status": "IN_PROGRESS"}


def test_missing_file_input_names_the_parameter() -> None:
    with pytest.raises(NodeError) as raised:
        cartwheel_api.prepare_file(None, default_file_name="reference_video", default_extension="mp4")
    assert raised.value.fields == {"parameter": "reference_video"}


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTWHEEL_API_KEY", raising=False)
    monkeypatch.setattr(
        cartwheel_api.GriptapeNodes.SecretsManager(), "get_secret", lambda *_a, **_k: None, raising=False
    )


@pytest.mark.parametrize("node_class", [CartwheelCreateCharacter, CartwheelCreateMascot, CartwheelGenerateMotion])
def test_missing_key_is_the_only_error_and_links_to_settings(no_key: None, node_class: type) -> None:
    node = node_class(name="node")
    errors = node.validate_before_node_run()
    assert errors is not None
    assert len(errors) == 1
    assert isinstance(errors[0], NodeError)
    assert "CARTWHEEL_API_KEY is not set" in str(errors[0])
    assert [link.url for link in errors[0].links] == [SETTINGS_LINK]


@pytest.mark.parametrize("node_class", [CartwheelCreateCharacter, CartwheelCreateMascot, CartwheelGenerateMotion])
def test_input_problems_are_node_errors_naming_the_parameter(monkeypatch: pytest.MonkeyPatch, node_class: type) -> None:
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    for module in (sys.modules[node_class.__module__],):
        monkeypatch.setattr(module, "get_api_key", lambda: "test-key")
    node = node_class(name="node")
    errors = node.validate_before_node_run()
    assert errors
    assert all(isinstance(error, NodeError) for error in errors)
    assert all("parameter" in error.fields for error in errors)  # type: ignore[attr-defined]


@pytest.mark.parametrize("file_name", NODE_FILES)
def test_node_files_load_the_way_the_engine_loads_them(monkeypatch: pytest.MonkeyPatch, file_name: str) -> None:
    """The engine loads each node file on its own, with the manifest's folder on sys.path."""
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        f"_engine_style_{file_name}", REPO_ROOT / "cartwheel" / f"{file_name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
