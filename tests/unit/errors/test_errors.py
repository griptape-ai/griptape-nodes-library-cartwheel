"""Cartwheel failures reach the user as NodeError, through real `requests` calls to a local HTTP server."""

from __future__ import annotations

import importlib
import importlib.util
import json
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
NODES = [CartwheelCreateCharacter, CartwheelCreateMascot, CartwheelGenerateMotion]
SETTINGS_LINK = "#settings-secrets?filter=CARTWHEEL_API_KEY"


class _Handler(BaseHTTPRequestHandler):
    reply: dict[str, Any] = {}

    def _answer(self) -> None:
        data = json.dumps(type(self).reply["body"]).encode()
        self.send_response(type(self).reply["status"])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Handler]]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(cartwheel_api, "BASE_URL", f"http://127.0.0.1:{httpd.server_port}")
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    yield _Handler
    httpd.shutdown()
    httpd.server_close()


def test_refused_key_links_to_settings(server: type[_Handler]) -> None:
    server.reply = {"status": 401, "body": {"message": "Invalid API key"}}
    with pytest.raises(NodeError) as raised:
        cartwheel_api.request_json("GET", "/characters/char-1")
    assert [link.url for link in raised.value.links] == [SETTINGS_LINK]
    assert raised.value.fields["status_code"] == 401
    assert raised.value.response == {"message": "Invalid API key"}


def test_rejected_request_carries_the_reason(server: type[_Handler]) -> None:
    server.reply = {"status": 400, "body": {"message": "prompt is too long"}}
    with pytest.raises(NodeError, match="prompt is too long"):
        cartwheel_api.request_json("POST", "/motion/fromText", payload={}, expected_statuses={202})


def test_unreachable_server_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cartwheel_api, "BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    with pytest.raises(NodeError, match="Could not reach Cartwheel"):
        cartwheel_api.request_json("GET", "/characters/char-1", timeout=2)


def test_failed_job_carries_status_id_and_response(server: type[_Handler]) -> None:
    body = {"uploadStatus": "FAILED", "errorMessage": "Rig has no root bone"}
    server.reply = {"status": 200, "body": body}
    with pytest.raises(NodeError, match="Rig has no root bone") as raised:
        cartwheel_api.wait_for_character("char-1", poll_delay=0, max_attempts=3)
    assert raised.value.fields == {"status": "FAILED", "character_id": "char-1"}
    assert raised.value.response == body


def test_polling_timeout_names_the_setting_and_the_id(server: type[_Handler]) -> None:
    server.reply = {"status": 200, "body": {"status": "IN_PROGRESS"}}
    with pytest.raises(NodeError, match="Max Poll Attempts") as raised:
        cartwheel_api.wait_for_batch("batch-1", poll_delay=0, max_attempts=2)
    assert raised.value.fields == {"batch_id": "batch-1"}


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTWHEEL_API_KEY", raising=False)
    monkeypatch.setattr(
        cartwheel_api.GriptapeNodes.SecretsManager(), "get_secret", lambda *_a, **_k: None, raising=False
    )


@pytest.mark.parametrize("node_class", NODES)
def test_missing_key_is_the_only_error_and_links_to_settings(no_key: None, node_class: type) -> None:
    errors = node_class(name="node").validate_before_node_run()
    assert errors is not None
    assert len(errors) == 1
    assert isinstance(errors[0], NodeError)
    assert [link.url for link in errors[0].links] == [SETTINGS_LINK]


@pytest.mark.parametrize("node_class", NODES)
def test_empty_inputs_are_reported_as_node_errors(monkeypatch: pytest.MonkeyPatch, node_class: type) -> None:
    monkeypatch.setattr(cartwheel_api, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(importlib.import_module(node_class.__module__), "get_api_key", lambda: "test-key")
    errors = node_class(name="node").validate_before_node_run()
    assert errors
    assert all(isinstance(error, NodeError) for error in errors)


@pytest.mark.parametrize("file_name", ["create_character", "create_mascot", "generate_motion"])
def test_node_files_load_the_way_the_engine_loads_them(monkeypatch: pytest.MonkeyPatch, file_name: str) -> None:
    """The engine loads each node file on its own, with the manifest's folder on sys.path."""
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    path = REPO_ROOT / "cartwheel" / f"{file_name}.py"
    spec = importlib.util.spec_from_file_location(f"_engine_style_{file_name}", path)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
