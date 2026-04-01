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
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

import requests
from griptape.artifacts import ImageUrlArtifact, VideoUrlArtifact
from griptape_nodes.retained_mode.events.os_events import ExistingFilePolicy
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes, logger


BASE_URL = "https://external-mogen.api.getcartwheel.com"
API_KEY_ENV_VAR = "CARTWHEEL_API_KEY"
DEFAULT_POLL_DELAY_SECONDS = 5

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


def get_api_key() -> str:
    api_key = GriptapeNodes.SecretsManager().get_secret(API_KEY_ENV_VAR)
    if not api_key:
        msg = f"Cartwheel API key not found. Please set the {API_KEY_ENV_VAR} secret."
        raise ValueError(msg)
    return api_key


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

    response = requests.request(
        method=method,
        url=f"{BASE_URL}{path}",
        headers={"Accept": "application/json", "Content-Type": "application/json", "x-api-key": get_api_key()},
        json=payload,
        params=params,
        timeout=timeout,
    )

    if response.status_code not in expected_statuses:
        raise RuntimeError(
            f"Cartwheel request failed for {method} {path} with status {response.status_code}: {response.text[:500]}"
        )

    if not response.content:
        return {}

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"Cartwheel returned invalid JSON for {method} {path}: {response.text[:500]}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"Cartwheel returned unexpected JSON for {method} {path}: {data!r}")

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

    response = requests.put(upload_url, data=data, headers=headers, timeout=timeout)
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"Failed to upload file to Cartwheel presigned URL. Status {response.status_code}: {response.text[:500]}"
        )


def download_bytes(url: str, *, timeout: int = 300) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


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
            error_message = first_non_empty_string(
                character,
                preferred_keys=("errorMessage", "message", "rejectedReason"),
            ) or f"Character processing failed with status {upload_status}"
            raise RuntimeError(error_message)

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise RuntimeError(
        f"Timed out waiting for Cartwheel character {character_id}. Last response: {json.dumps(last_response or {})}"
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
            error_message = first_non_empty_string(
                batch,
                preferred_keys=("errorMessage", "message", "rejectedReason"),
            ) or f"Motion batch failed with status {status}"
            raise RuntimeError(error_message)

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise RuntimeError(f"Timed out waiting for Cartwheel batch {batch_id}. Last response: {json.dumps(last_response or {})}")


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
            error_message = first_non_empty_string(
                mascot,
                preferred_keys=("errorMessage", "message", "rejectedReason"),
            ) or f"Mascot generation failed with status {status}"
            raise RuntimeError(error_message)

        if attempt < max_attempts - 1:
            time.sleep(poll_delay)

    raise RuntimeError(f"Timed out waiting for Cartwheel mascot {mascot_id}. Last response: {json.dumps(last_response or {})}")


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
        raise ValueError(f"Missing required file input for {default_file_name}")

    if isinstance(value, dict):
        if "base64" in value and isinstance(value["base64"], str):
            return prepared_file_from_base64(
                value["base64"],
                file_name=guess_file_name(value.get("name"), default_file_name, default_extension),
            )
        if "value" in value:
            return prepare_file(value["value"], default_file_name=default_file_name, default_extension=default_extension)

    if isinstance(value, bytes):
        return PreparedFile(data=value, file_name=f"{default_file_name}.{default_extension}", extension=default_extension)

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

    raise TypeError(f"Unsupported file input type: {type(value).__name__}")


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
        raise RuntimeError(f"Unexpected media upload response from Cartwheel: {payload}")

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
        raise RuntimeError(f"Could not determine media upload details from Cartwheel response: {payload}")

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
