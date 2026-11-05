from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from zipfile import BadZipFile, ZipFile

import requests
from griptape.artifacts import ImageUrlArtifact, VideoUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError, NodeErrorLink
from griptape_nodes.retained_mode.events.os_events import ExistingFilePolicy
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes, logger

BASE_URL = "https://external-mogen.api.getcartwheel.com"
API_KEY_ENV_VAR = "CARTWHEEL_API_KEY"
DEFAULT_POLL_DELAY_SECONDS = 5

KEY_REFUSED_STATUSES = {401, 403}
RATE_LIMITED_STATUS = 429
SERVER_ERROR_STATUS = 500
# Headers that carry a request ID, in the order they are checked.
REQUEST_ID_HEADERS = ("x-request-id", "x-amzn-requestid", "x-amz-request-id", "cf-ray")
MAX_RESPONSE_TEXT = 2000
MAX_RESPONSE_BYTES = 12_000

CHARACTER_STATUS_COMPLETE = "COMPLETE"
CHARACTER_IN_PROGRESS_STATUSES = {
    "PENDING",
    "AUTORIGGING_IN_PROGRESS",
    "RETARGETING_IN_PROGRESS",
}
CHARACTER_FAILURE_STATUSES = {
    "ADJUSTMENT_FAILED",
    "FAILED",
    "NEEDS_VALIDATION",
}

BATCH_STATUS_COMPLETE = "COMPLETED"
BATCH_IN_PROGRESS_STATUSES = {"VALIDATING", "IN_PROGRESS"}
BATCH_FAILURE_STATUSES = {"REJECTED", "FAILED", "CANCELLED"}

URL_PREFIXES = ("http://", "https://")

MEDIA_ID_PREFIX = "media-"
CHARACTER_ID_PREFIX = "char-"
MASCOT_ID_PREFIX = "mascot-"
MOTION_ID_PREFIXES = ("mogen-", "motion-", "job-")
BATCH_ID_PREFIX = "batch-"


@dataclass(slots=True)
class PreparedFile:
    data: bytes
    file_name: str
    extension: str


def api_key_link(label: str) -> NodeErrorLink:
    """Link that opens the API key secret in the editor's settings."""
    return NodeErrorLink(label=label, url=f"#settings-secrets?filter={quote(API_KEY_ENV_VAR)}")


def missing_api_key_error() -> NodeError:
    """Error for a run that has no Cartwheel API key."""
    return NodeError(
        f"{API_KEY_ENV_VAR} is not set. Add it in Settings → API Keys & Secrets, then run the node again.",
        links=[api_key_link("Add the API key")],
    )


def get_api_key() -> str:
    api_key = GriptapeNodes.SecretsManager().get_secret(API_KEY_ENV_VAR)
    if not api_key:
        raise missing_api_key_error()
    return api_key


def parameter_error(message: str, parameter: str) -> NodeError:
    """Error about one parameter's value, naming it in `fields`."""
    return NodeError(message, fields={"parameter": parameter})


def _response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _reason_from_body(body: Any) -> str | None:
    """The provider's own explanation, from the usual error keys of a JSON body."""
    if isinstance(body, str) and body.strip():
        return body.strip()
    if not isinstance(body, dict):
        return None
    for key in ("errorMessage", "message", "error", "detail", "rejectedReason"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        nested = _reason_from_body(value) if isinstance(value, dict) else None
        if nested:
            return nested
    return None


def error_from_response(response: requests.Response, *, endpoint: str, subject: str = "Cartwheel") -> NodeError:
    """Build a NodeError from a failed HTTP response.

    The message carries the provider's reason when the body has one. The status code, request ID and
    endpoint go in `fields`, and the body in `response`. A refused API key gets a Settings link.

    Args:
        response: The response that was not accepted.
        endpoint: What was called, shown in `fields`, such as "POST /characters/upload".
        subject: Who answered. "Cartwheel" for the API, a different name for the upload storage.
    """
    status = response.status_code
    body = _response_body(response)
    reason = _reason_from_body(body)
    if reason is None and body is None:
        reason = response.text.strip()[:MAX_RESPONSE_TEXT] or None
    links: list[NodeErrorLink] = []

    if subject == "Cartwheel" and status in KEY_REFUSED_STATUSES:
        message = f"Cartwheel refused the API key. Update {API_KEY_ENV_VAR} in Settings → API Keys & Secrets."
        links.append(api_key_link("Update the API key"))
    elif status == RATE_LIMITED_STATUS:
        message = f"{subject} is rate-limiting requests. Wait a minute and run the node again."
    elif status >= SERVER_ERROR_STATUS:
        message = f"{subject} had a problem handling the request. Run the node again in a few minutes."
    else:
        message = f"{subject} rejected the request."
    if reason and status != RATE_LIMITED_STATUS:
        message = f"{message} It said: {reason}"

    fields: dict[str, str | int | float | bool] = {"status_code": status, "endpoint": endpoint}
    for header in REQUEST_ID_HEADERS:
        request_id = response.headers.get(header)
        if request_id:
            fields["request_id"] = request_id
            break

    if isinstance(body, dict):
        attached: dict[str, Any] | None = body
    elif body is None and response.text.strip():
        attached = {"text": response.text.strip()[:MAX_RESPONSE_TEXT]}
    else:
        attached = None
    return NodeError(message, fields=fields, response=limit_response(attached), links=links)


def limit_response(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop a response too large for the editor, which would hide the rest of the error."""
    if body is None:
        return None
    try:
        if len(json.dumps(body)) > MAX_RESPONSE_BYTES:
            return {"note": "The response was too large to show."}
    except (TypeError, ValueError):
        return None
    return body


def connection_error(error: requests.RequestException, *, endpoint: str) -> NodeError:
    """Build a NodeError for a request that never got an answer."""
    if isinstance(error, requests.Timeout):
        message = "Cartwheel did not answer in time. Run the node again."
    else:
        message = "Could not reach Cartwheel. Check your internet connection and run the node again."
    return NodeError(message, fields={"endpoint": endpoint})


def unexpected_response_error(
    message: str, body: Any, *, fields: dict[str, str | int | float | bool] | None = None
) -> NodeError:
    """Build a NodeError for a successful HTTP response that is missing what the node needs."""
    return NodeError(message, fields=fields, response=limit_response(body if isinstance(body, dict) else None))


def request_json(  # noqa: PLR0913
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    expected_statuses: set[int] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    if expected_statuses is None:
        expected_statuses = {200}

    endpoint = f"{method} {path}"
    try:
        response = requests.request(
            method=method,
            url=f"{BASE_URL}{path}",
            headers={"Accept": "application/json", "Content-Type": "application/json", "x-api-key": get_api_key()},
            json=payload,
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise connection_error(e, endpoint=endpoint) from e

    if response.status_code not in expected_statuses:
        raise error_from_response(response, endpoint=endpoint)

    if not response.content:
        return {}

    try:
        data = response.json()
    except ValueError as e:
        raise NodeError(
            "Cartwheel sent a reply that is not JSON. Run the node again.",
            fields={"status_code": response.status_code, "endpoint": endpoint},
            response={"text": response.text[:MAX_RESPONSE_TEXT]},
        ) from e

    if not isinstance(data, dict):
        raise NodeError(
            "Cartwheel sent a reply in an unexpected format. Run the node again.",
            fields={"status_code": response.status_code, "endpoint": endpoint},
            response={"body": data} if isinstance(data, list) else None,
        )

    return data


def upload_to_presigned_url(
    upload_url: str,
    data: bytes,
    *,
    content_type: str | None = None,
    timeout: int = 300,
) -> None:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type

    endpoint = "PUT (Cartwheel upload URL)"
    try:
        response = requests.put(upload_url, data=data, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise connection_error(e, endpoint=endpoint) from e
    if response.status_code not in {200, 201}:
        raise error_from_response(response, endpoint=endpoint, subject="Cartwheel's upload storage")


def download_bytes(url: str, *, timeout: int = 300) -> bytes:
    # The query string of a presigned URL carries a signature, so only the path is reported.
    parsed = urlparse(url)
    endpoint = f"GET {parsed.scheme}://{parsed.netloc}{parsed.path}"
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise connection_error(e, endpoint=endpoint) from e
    if not response.ok:
        raise error_from_response(response, endpoint=endpoint, subject="The download server")
    return response.content


def timeout_error(  # noqa: PLR0913
    kind: str, item_id: str, *, id_field: str, max_attempts: int, last: dict[str, Any] | None
) -> NodeError:
    """Build a NodeError for a job that is still running when the node stops polling."""
    return NodeError(
        f"The Cartwheel {kind} was still processing after {max_attempts} status checks. "
        "Raise 'Max Poll Attempts' and run the node again, or look it up later by its ID.",
        fields={id_field: item_id},
        response=limit_response(last),
    )


def wait_for_character(
    character_id: str,
    *,
    poll_delay: int,
    max_attempts: int,
) -> dict[str, Any]:
    last_response: dict[str, Any] | None = None

    for attempt in range(max_attempts):
        character = request_json("GET", f"/characters/{character_id}")
        last_response = character
        upload_status = str(character.get("uploadStatus") or "").upper()

        logger.info(
            "Cartwheel character %s status: %s (attempt %s/%s)",
            character_id,
            upload_status,
            attempt + 1,
            max_attempts,
        )

        if upload_status == CHARACTER_STATUS_COMPLETE:
            return character

        if upload_status in CHARACTER_FAILURE_STATUSES:
            error_message = (
                first_non_empty_string(
                    character,
                    preferred_keys=("errorMessage", "message", "rejectedReason"),
                )
                or f"Character processing failed with status {upload_status}"
            )
            raise NodeError(
                error_message,
                fields={"status": upload_status, "character_id": character_id},
                response=limit_response(character),
            )

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise timeout_error(
        "character", character_id, id_field="character_id", max_attempts=max_attempts, last=last_response
    )


def wait_for_batch(
    batch_id: str,
    *,
    poll_delay: int,
    max_attempts: int,
) -> dict[str, Any]:
    last_response: dict[str, Any] | None = None

    for attempt in range(max_attempts):
        batch = request_json("GET", f"/batch/{batch_id}")
        last_response = batch
        status = str(batch.get("status") or "").upper()

        logger.info(
            "Cartwheel batch %s status: %s (attempt %s/%s)",
            batch_id,
            status,
            attempt + 1,
            max_attempts,
        )

        if status == BATCH_STATUS_COMPLETE:
            return batch

        if status in BATCH_FAILURE_STATUSES:
            error_message = (
                first_non_empty_string(
                    batch,
                    preferred_keys=("errorMessage", "message", "rejectedReason"),
                )
                or f"Motion batch failed with status {status}"
            )
            raise NodeError(
                error_message,
                fields={"status": status, "batch_id": batch_id},
                response=limit_response(batch),
            )

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise timeout_error("motion batch", batch_id, id_field="batch_id", max_attempts=max_attempts, last=last_response)


def list_all_batch_motions(batch_id: str, *, limit: int = 100) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        params: dict[str, Any] = {"limit": limit}
        if next_token:
            params["nextToken"] = next_token

        page = request_json("GET", f"/motions/{batch_id}", params=params)
        page_items = page.get("items")
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))

        token_value = page.get("nextToken")
        next_token = token_value if isinstance(token_value, str) and token_value else None
        if not next_token:
            break

    return {"items": items}


def wait_for_mascot(
    mascot_id: str,
    *,
    poll_delay: int,
    max_attempts: int,
) -> dict[str, Any]:
    last_response: dict[str, Any] | None = None

    for attempt in range(max_attempts):
        mascot = request_json("GET", f"/mascot/{mascot_id}")
        last_response = mascot
        status = str(mascot.get("status") or "").upper()

        logger.info(
            "Cartwheel mascot %s status: %s (attempt %s/%s)",
            mascot_id,
            status,
            attempt + 1,
            max_attempts,
        )

        if status == BATCH_STATUS_COMPLETE:
            return mascot

        if status in BATCH_FAILURE_STATUSES:
            error_message = (
                first_non_empty_string(
                    mascot,
                    preferred_keys=("errorMessage", "message", "rejectedReason"),
                )
                or f"Mascot generation failed with status {status}"
            )
            raise NodeError(
                error_message,
                fields={"status": status, "mascot_id": mascot_id},
                response=limit_response(mascot),
            )

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise timeout_error("mascot", mascot_id, id_field="mascot_id", max_attempts=max_attempts, last=last_response)


def list_all_batch_mascots(batch_id: str, *, limit: int = 100) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        params: dict[str, Any] = {"limit": limit}
        if next_token:
            params["nextToken"] = next_token

        page = request_json("GET", f"/mascots/{batch_id}", params=params)
        page_items = page.get("items")
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))

        token_value = page.get("nextToken")
        next_token = token_value if isinstance(token_value, str) and token_value else None
        if not next_token:
            break

    return {"items": items}


def prepare_file(value: Any, *, default_file_name: str, default_extension: str) -> PreparedFile:
    if value is None:
        raise parameter_error(f"Connect a file to the {default_file_name.replace('_', ' ')} input.", default_file_name)

    if isinstance(value, dict):
        if "base64" in value and isinstance(value["base64"], str):
            return prepared_file_from_base64(
                value["base64"],
                file_name=guess_file_name(value.get("name"), default_file_name, default_extension),
            )
        if "value" in value:
            return prepare_file(
                value["value"], default_file_name=default_file_name, default_extension=default_extension
            )

    if isinstance(value, bytes):
        return PreparedFile(
            data=value, file_name=f"{default_file_name}.{default_extension}", extension=default_extension
        )

    artifact_value = getattr(value, "value", None)
    if isinstance(artifact_value, str):
        return prepare_file(
            artifact_value,
            default_file_name=default_file_name,
            default_extension=default_extension,
        )

    artifact_base64 = getattr(value, "base64", None)
    if isinstance(artifact_base64, str):
        return prepared_file_from_base64(
            artifact_base64,
            file_name=guess_file_name(getattr(value, "name", None), default_file_name, default_extension),
        )

    if isinstance(value, str):
        return prepared_file_from_string(
            value,
            default_file_name=default_file_name,
            default_extension=default_extension,
        )

    raise parameter_error(
        f"The {default_file_name.replace('_', ' ')} input is a {type(value).__name__}, which Cartwheel nodes can't read. "
        "Connect a file, a file path or a URL.",
        default_file_name,
    )


def prepared_file_from_string(value: str, *, default_file_name: str, default_extension: str) -> PreparedFile:
    file_name = file_name_from_value(value) or f"{default_file_name}.{default_extension}"
    extension = extension_from_name(file_name, default_extension)

    if value.startswith(URL_PREFIXES):
        data = download_bytes(value, timeout=300)
        return PreparedFile(data=data, file_name=file_name, extension=extension)

    path = Path(value).expanduser()
    data = path.read_bytes()
    return PreparedFile(data=data, file_name=path.name, extension=extension_from_name(path.name, default_extension))


def prepared_file_from_base64(value: str, *, file_name: str) -> PreparedFile:
    payload = value.split("base64,", maxsplit=1)[1] if "base64," in value else value
    data = base64.b64decode(payload)
    extension = extension_from_name(file_name, "bin")
    return PreparedFile(data=data, file_name=file_name, extension=extension)


def extension_from_name(file_name: str, default_extension: str) -> str:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix or default_extension


def file_name_from_value(value: str) -> str | None:
    if value.startswith(URL_PREFIXES):
        parsed = urlparse(value)
        name = Path(parsed.path).name
        if name:
            return name
        return None

    path = Path(value).expanduser()
    return path.name or None


def guess_file_name(name: Any, default_file_name: str, default_extension: str) -> str:
    if isinstance(name, str) and name:
        return name
    return f"{default_file_name}.{default_extension}"


def guess_content_type(extension: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(f"file.{extension}")
    return guessed or fallback


def probe_video_duration_seconds(file: PreparedFile) -> float | None:
    suffix = f".{file.extension}" if file.extension else ""

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as temp_file:
            temp_file.write(file.data)
            temp_file.flush()

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    temp_file.name,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
    except FileNotFoundError:
        logger.warning("ffprobe is not available; skipping automatic video duration detection.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out while determining video duration for %s.", file.file_name)
        return None
    except OSError as error:
        logger.warning("Failed to prepare temporary video file for duration probing: %s", error)
        return None

    if result.returncode != 0:
        logger.warning(
            "ffprobe failed while determining video duration for %s: %s",
            file.file_name,
            result.stderr.strip() or result.stdout.strip(),
        )
        return None

    try:
        payload = json.loads(result.stdout)
    except ValueError:
        logger.warning("ffprobe returned invalid JSON while determining video duration for %s.", file.file_name)
        return None

    format_data = payload.get("format")
    if not isinstance(format_data, dict):
        return None

    duration_value = format_data.get("duration")
    if duration_value is None:
        return None
    try:
        duration = float(duration_value)
    except (TypeError, ValueError):
        return None

    return duration if duration > 0 else None


def create_static_download(data: bytes, file_name: str) -> str:
    return GriptapeNodes.StaticFilesManager().save_static_file(data, file_name, ExistingFilePolicy.CREATE_NEW)


def list_zip_entries(data: bytes) -> list[str]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            return archive.namelist()
    except BadZipFile:
        return []


def first_non_empty_string(
    data: dict[str, Any],
    *,
    preferred_keys: tuple[str, ...],
) -> str | None:
    lowered = {str(key).lower(): value for key, value in data.items()}

    for key in preferred_keys:
        value = lowered.get(key.lower())
        if isinstance(value, str) and value.strip():
            return value

    for value in data.values():
        if isinstance(value, str) and value.strip():
            return value

    return None


def recursive_key_value_pairs(data: Any, *, prefix: str = "") -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            pairs.append((path, value))
            pairs.extend(recursive_key_value_pairs(value, prefix=path))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            path = f"{prefix}[{index}]"
            pairs.append((path, value))
            pairs.extend(recursive_key_value_pairs(value, prefix=path))

    return pairs


def find_string_field(
    data: dict[str, Any],
    *,
    exact_keys: tuple[str, ...] = (),
    required_key_tokens: tuple[str, ...] = (),
    forbidden_key_tokens: tuple[str, ...] = (),
    value_prefixes: tuple[str, ...] = (),
) -> str | None:
    lowered = {str(key).lower(): value for key, value in data.items()}

    for key in exact_keys:
        value = lowered.get(key.lower())
        if isinstance(value, str) and value:
            return value

    for key, value in data.items():
        if not isinstance(value, str) or not value:
            continue

        key_lower = str(key).lower()
        if required_key_tokens and not all(token in key_lower for token in required_key_tokens):
            continue
        if forbidden_key_tokens and any(token in key_lower for token in forbidden_key_tokens):
            continue
        if value_prefixes and not value.startswith(value_prefixes):
            continue

        return value

    return None


def extract_media_upload_item(payload: dict[str, Any]) -> dict[str, str | None]:
    uploads = payload.get("mediaUploads")
    item = uploads[0] if isinstance(uploads, list) and uploads else payload
    if not isinstance(item, dict):
        raise unexpected_response_error("Cartwheel's media upload reply was not in the expected format.", payload)

    media_id = find_string_field(
        item,
        exact_keys=("mediaID", "mediaId", "id"),
        value_prefixes=(MEDIA_ID_PREFIX,),
    )
    upload_url = find_string_field(
        item,
        exact_keys=("mediaUploadURL", "mediaUploadUrl", "uploadURL", "uploadUrl", "url", "presignedURL"),
        required_key_tokens=("upload", "url"),
        forbidden_key_tokens=("thumbnail",),
        value_prefixes=URL_PREFIXES,
    )

    if not media_id or not upload_url:
        raise unexpected_response_error("Cartwheel's media upload reply has no media ID or upload URL.", payload)

    return {"media_id": media_id, "upload_url": upload_url}


def extract_motion_ids(items: list[dict[str, Any]]) -> list[str]:
    motion_ids: list[str] = []

    for item in items:
        motion_id = find_string_field(
            item,
            exact_keys=("motionID", "motionId", "jobID", "jobId", "id"),
            value_prefixes=MOTION_ID_PREFIXES,
        )
        if motion_id:
            motion_ids.append(motion_id)

    return motion_ids


def extract_mascot_ids(items: list[dict[str, Any]]) -> list[str]:
    mascot_ids: list[str] = []

    for item in items:
        mascot_id = find_string_field(
            item,
            exact_keys=("mascotID", "mascotId", "id"),
            value_prefixes=(MASCOT_ID_PREFIX,),
        )
        if mascot_id:
            mascot_ids.append(mascot_id)

    return mascot_ids


def extract_urls_from_motion_items(items: list[dict[str, Any]], *, tokens: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    for item in items:
        for path, value in recursive_key_value_pairs(item):
            if not isinstance(value, str) or not value.startswith(URL_PREFIXES):
                continue

            path_lower = path.lower()
            if all(token in path_lower for token in tokens) and value not in seen:
                seen.add(value)
                urls.append(value)

    return urls


def image_artifact_from_url(url: str | None) -> ImageUrlArtifact | None:
    if not url:
        return None
    return ImageUrlArtifact(url)


def video_artifact_from_url(url: str | None) -> VideoUrlArtifact | None:
    if not url:
        return None
    return VideoUrlArtifact(url)


def timestamped_file_name(prefix: str, extension: str) -> str:
    timestamp = int(time.time() * 1000)
    return f"{prefix}_{timestamp}.{extension}"
