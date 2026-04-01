from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from griptape.artifacts import ImageArtifact, ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterList, ParameterMode
from griptape_nodes.exe_types.node_types import AsyncResult, SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_video import ParameterVideo
from griptape_nodes.retained_mode.griptape_nodes import logger
from griptape_nodes.utils.artifact_normalization import normalize_artifact_list

try:
    from cartwheel.cartwheel_api import (
        BATCH_FAILURE_STATUSES,
        BATCH_IN_PROGRESS_STATUSES,
        DEFAULT_POLL_DELAY_SECONDS,
        MASCOT_ID_PREFIX,
        create_static_download,
        download_bytes,
        get_api_key,
        guess_content_type,
        list_all_batch_mascots,
        prepare_file,
        probe_video_duration_seconds,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        video_artifact_from_url,
        wait_for_mascot,
    )
except ImportError:
    from cartwheel_api import (  # type: ignore[no-redef]
        BATCH_FAILURE_STATUSES,
        BATCH_IN_PROGRESS_STATUSES,
        DEFAULT_POLL_DELAY_SECONDS,
        MASCOT_ID_PREFIX,
        create_static_download,
        download_bytes,
        get_api_key,
        guess_content_type,
        list_all_batch_mascots,
        prepare_file,
        probe_video_duration_seconds,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        video_artifact_from_url,
        wait_for_mascot,
    )


class CartwheelCreateMascot(SuccessFailureNode):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self.add_parameter(
            ParameterVideo(
                name="input_video",
                tooltip="Source video to process into a mascot output.",
                display_name="Input Video",
            )
        )
        self.add_parameter(
            ParameterList(
                name="character_reference_images",
                input_types=[
                    "ImageArtifact",
                    "ImageUrlArtifact",
                    "str",
                    "list[ImageArtifact]",
                    "list[ImageUrlArtifact]",
                    "list[str]",
                ],
                default_value=[],
                tooltip="Reference images for the target character replacement. The first image is the first target.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                ui_options={"expander": True, "display_name": "Character Reference Images"},
            )
        )
        self.add_parameter(
            ParameterImage(
                name="environment_image",
                default_value=None,
                tooltip="Optional environment or scene image.",
                display_name="Environment Image",
            )
        )
        self.add_parameter(
            Parameter(
                name="prompt",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Text prompt describing the character replacement.",
                ui_options={"multiline": True},
            )
        )
        self.add_parameter(
            Parameter(
                name="mascot_name",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional name for the mascot job.",
            )
        )
        self.add_parameter(
            Parameter(
                name="batch_name",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional batch name.",
            )
        )
        self.add_parameter(
            Parameter(
                name="input_video_resolution",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional source video resolution, for example 1920x1080.",
            )
        )
        self.add_parameter(
            Parameter(
                name="reference_image_resolution",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional reference image resolution, applied to each uploaded reference image.",
            )
        )
        self.add_parameter(
            Parameter(
                name="environment_image_resolution",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional environment image resolution.",
            )
        )
        self.add_parameter(
            Parameter(
                name="max_poll_attempts",
                type="int",
                input_types=["int"],
                output_type="int",
                default_value=120,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Maximum number of status checks before timing out.",
                hide=True,
            )
        )
        self.add_parameter(
            Parameter(
                name="mascot_page_limit",
                type="int",
                input_types=["int"],
                output_type="int",
                default_value=100,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Maximum number of mascot jobs to request per batch page.",
                hide=True,
            )
        )

        self.add_parameter(
            Parameter(
                name="mascot_id",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Cartwheel mascot job ID.",
            )
        )
        self.add_parameter(
            Parameter(
                name="batch_id",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Batch ID when returned by the mascot API.",
            )
        )
        self.add_parameter(
            Parameter(
                name="estimated_seconds_wait_time",
                type="int",
                output_type="int",
                default_value=0,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Estimated wait time reported by Cartwheel.",
            )
        )
        self.add_parameter(
            Parameter(
                name="output_video_url",
                type="VideoUrlArtifact",
                output_type="VideoUrlArtifact",
                default_value=None,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Remote output video URL from Cartwheel.",
            )
        )
        self.add_parameter(
            Parameter(
                name="downloaded_output_video_url",
                type="VideoUrlArtifact",
                output_type="VideoUrlArtifact",
                default_value=None,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Downloaded mascot output video saved to the engine's static storage.",
            )
        )
        self.add_parameter(
            Parameter(
                name="uploaded_media_ids",
                type="list",
                output_type="list[str]",
                default_value=[],
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Media IDs uploaded on behalf of this mascot job.",
            )
        )
        self.add_parameter(
            Parameter(
                name="mascot",
                type="dict",
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Raw mascot object for the completed job.",
            )
        )
        self.add_parameter(
            Parameter(
                name="mascot_jobs_response",
                type="dict",
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Raw mascot jobs-by-batch response when a batch ID is available.",
            )
        )
        self._create_status_parameters(
            result_details_tooltip="Mascot generation result details.",
            result_details_placeholder="Mascot generation status will be shown here.",
        )

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == "character_reference_images" and isinstance(value, list):
            updated_list = normalize_artifact_list(value, ImageUrlArtifact, accepted_types=(ImageArtifact,))
            if updated_list != value:
                self.set_parameter_value("character_reference_images", updated_list)
                value = updated_list

        return super().after_value_set(parameter, value)

    def validate_node(self) -> list[Exception] | None:
        errors: list[Exception] = []

        try:
            get_api_key()
        except Exception as e:
            errors.append(e)

        if self.get_parameter_value("input_video") is None:
            errors.append(ValueError("input_video is required."))

        reference_images = self.get_parameter_value("character_reference_images")
        if not isinstance(reference_images, list) or not reference_images:
            errors.append(ValueError("character_reference_images must contain at least one image."))

        prompt = str(self.get_parameter_value("prompt") or "").strip()
        if not prompt:
            errors.append(ValueError("prompt is required."))

        mascot_page_limit = self.get_parameter_value("mascot_page_limit")
        if isinstance(mascot_page_limit, int) and not 1 <= mascot_page_limit <= 100:
            errors.append(ValueError("mascot_page_limit must be between 1 and 100."))

        return errors or None

    def process(self) -> AsyncResult[dict[str, Any] | None]:
        self._clear_execution_status()
        yield lambda: self._process_with_status()

    def _process_with_status(self) -> dict[str, Any] | None:
        try:
            result = self._process()
        except Exception as error:
            self._set_status_results(was_successful=False, result_details=f"Failure: {error}")
            self._handle_failure_exception(error)
            return None

        mascot = result.get("mascot", {}) if isinstance(result, dict) else {}
        mascot_id = str(mascot.get("mascotID") or self.get_parameter_value("mascot_id") or "").strip()
        mascot_status = str(mascot.get("status") or "").strip()
        status_suffix = f" ({mascot_status})" if mascot_status else ""
        self._set_status_results(
            was_successful=True,
            result_details=f"Success: Created Cartwheel mascot {mascot_id}{status_suffix}.",
        )
        return result

    def _process(self) -> dict[str, Any]:
        self._publish_outputs(
            mascot_id="",
            batch_id="",
            estimated_seconds_wait_time=0,
            output_video_url=None,
            downloaded_output_video_url=None,
            uploaded_media_ids=[],
            mascot={},
            mascot_jobs_response={},
        )

        uploaded_media_ids: list[str] = []

        input_video_media_id = self._upload_media(
            self.get_parameter_value("input_video"),
            media_name="input-video",
            default_file_name="mascot_input_video",
            default_extension="mp4",
            resolution=str(self.get_parameter_value("input_video_resolution") or "").strip(),
            include_video_duration=True,
        )
        uploaded_media_ids.append(input_video_media_id)

        reference_media_ids: list[str] = []
        for index, reference_image in enumerate(self.get_parameter_value("character_reference_images") or [], start=1):
            media_id = self._upload_media(
                reference_image,
                media_name=f"character-reference-{index}",
                default_file_name=f"character_reference_{index}",
                default_extension="png",
                resolution=str(self.get_parameter_value("reference_image_resolution") or "").strip(),
            )
            reference_media_ids.append(media_id)
            uploaded_media_ids.append(media_id)

        environment_media_id: str | None = None
        environment_image = self.get_parameter_value("environment_image")
        if environment_image is not None:
            environment_media_id = self._upload_media(
                environment_image,
                media_name="environment-image",
                default_file_name="environment_image",
                default_extension="png",
                resolution=str(self.get_parameter_value("environment_image_resolution") or "").strip(),
            )
            uploaded_media_ids.append(environment_media_id)

        job_payload: dict[str, Any] = {
            "inputVideoMediaID": input_video_media_id,
            "characterReferenceMediaIDs": reference_media_ids,
            "prompt": str(self.get_parameter_value("prompt") or "").strip(),
        }

        mascot_name = str(self.get_parameter_value("mascot_name") or "").strip()
        if mascot_name:
            job_payload["mascotName"] = mascot_name

        if environment_media_id:
            job_payload["environmentImageMediaID"] = environment_media_id

        payload: dict[str, Any] = {"jobs": [job_payload]}

        batch_name = str(self.get_parameter_value("batch_name") or "").strip()
        if batch_name:
            payload["batchName"] = batch_name

        logger.info("Submitting Cartwheel mascot job")
        create_response = request_json("POST", "/mascot", payload=payload, expected_statuses={202})

        mascot_id = self._extract_mascot_id(create_response)
        batch_id = self._extract_batch_id(create_response)

        mascot: dict[str, Any]
        mascot_jobs_response: dict[str, Any] = {}

        if mascot_id:
            mascot = wait_for_mascot(
                mascot_id,
                poll_delay=DEFAULT_POLL_DELAY_SECONDS,
                max_attempts=int(self.get_parameter_value("max_poll_attempts") or 120),
            )
            if batch_id:
                mascot_jobs_response = list_all_batch_mascots(
                    batch_id,
                    limit=int(self.get_parameter_value("mascot_page_limit") or 100),
                )
        elif batch_id:
            mascot, mascot_jobs_response = self._wait_for_batch_mascot(batch_id)
            mascot_id = str(mascot.get("mascotID") or "")
        else:
            raise RuntimeError(f"Cartwheel mascot create response did not include a mascot or batch ID: {create_response}")

        output_video_url = str(mascot.get("outputVideoURL") or "")
        downloaded_output_video_url = None

        if output_video_url:
            output_video_bytes = download_bytes(output_video_url)
            saved_url = create_static_download(
                output_video_bytes,
                self._build_download_file_name(output_video_url),
            )
            downloaded_output_video_url = video_artifact_from_url(saved_url)

        self._publish_outputs(
            mascot_id=mascot_id,
            batch_id=batch_id,
            estimated_seconds_wait_time=int(mascot.get("estimatedSecondsWaitTime") or 0),
            output_video_url=video_artifact_from_url(output_video_url or None),
            downloaded_output_video_url=downloaded_output_video_url,
            uploaded_media_ids=uploaded_media_ids,
            mascot=mascot,
            mascot_jobs_response=mascot_jobs_response,
        )

        return {
            "mascot": mascot,
            "mascot_jobs_response": mascot_jobs_response,
        }

    def _upload_media(
        self,
        value: Any,
        *,
        media_name: str,
        default_file_name: str,
        default_extension: str,
        resolution: str = "",
        duration: Any = None,
        include_video_duration: bool = False,
    ) -> str:
        prepared = prepare_file(
            value,
            default_file_name=default_file_name,
            default_extension=default_extension,
        )

        media_request: dict[str, Any] = {
            "extension": prepared.extension,
            "name": media_name,
        }
        if resolution:
            media_request["resolution"] = resolution
        if include_video_duration:
            duration = probe_video_duration_seconds(prepared)
        if duration:
            media_request["duration"] = duration

        media_response = request_json("POST", "/media/upload", payload={"media": [media_request]})
        upload_item = self._extract_media_upload(media_response)

        upload_to_presigned_url(
            upload_item["upload_url"],
            prepared.data,
            content_type=guess_content_type(prepared.extension),
        )

        return upload_item["media_id"]

    def _wait_for_batch_mascot(self, batch_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        last_response: dict[str, Any] | None = None
        last_items: list[dict[str, Any]] = []
        max_attempts = int(self.get_parameter_value("max_poll_attempts") or 120)
        page_limit = int(self.get_parameter_value("mascot_page_limit") or 100)

        for attempt in range(max_attempts):
            mascot_jobs_response = list_all_batch_mascots(batch_id, limit=page_limit)
            last_response = mascot_jobs_response
            items = mascot_jobs_response.get("items", [])
            last_items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

            statuses = [str(item.get("status") or "").upper() for item in last_items]
            logger.info(
                "Cartwheel mascot batch %s statuses: %s (attempt %s/%s)",
                batch_id,
                statuses,
                attempt + 1,
                max_attempts,
            )

            if last_items and all(status == "COMPLETED" for status in statuses):
                return last_items[0], mascot_jobs_response

            failed_item = next((item for item in last_items if str(item.get("status") or "").upper() in BATCH_FAILURE_STATUSES), None)
            if failed_item is not None:
                message = str(failed_item.get("errorMessage") or f"Mascot generation failed with status {failed_item.get('status')}")
                raise RuntimeError(message)

            if statuses and not any(status in BATCH_IN_PROGRESS_STATUSES for status in statuses):
                # If the API returns a non-empty terminal set that isn't all completed, treat it as failure.
                raise RuntimeError(f"Unexpected mascot batch terminal state: {statuses}")

            if attempt < max_attempts - 1:
                import time

                time.sleep(DEFAULT_POLL_DELAY_SECONDS)

        raise RuntimeError(f"Timed out waiting for Cartwheel mascot batch {batch_id}. Last response: {last_response or {'items': last_items}}")

    def _extract_media_upload(self, response: dict[str, Any]) -> dict[str, str]:
        media_uploads = response.get("mediaUploads")
        item = media_uploads[0] if isinstance(media_uploads, list) and media_uploads else response
        if not isinstance(item, dict):
            raise RuntimeError(f"Unexpected Cartwheel media upload response: {response}")

        media_id = ""
        for key in ("mediaID", "mediaId", "id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                media_id = value
                break

        upload_url = ""
        for key in ("mediaUploadURL", "mediaUploadUrl", "uploadURL", "uploadUrl", "url", "presignedURL"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                upload_url = value
                break

        if not media_id or not upload_url:
            raise RuntimeError(f"Could not determine media upload details from response: {response}")

        return {"media_id": media_id, "upload_url": upload_url}

    def _extract_mascot_id(self, response: dict[str, Any]) -> str:
        for key in ("mascotID", "mascotId", "id"):
            value = response.get(key)
            if isinstance(value, str) and value.startswith(MASCOT_ID_PREFIX):
                return value
        return ""

    def _extract_batch_id(self, response: dict[str, Any]) -> str:
        for key in ("batchID", "batchId"):
            value = response.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _build_download_file_name(self, output_video_url: str) -> str:
        parsed = urlparse(output_video_url)
        suffix = Path(parsed.path).suffix.lower().lstrip(".") or "mp4"
        return timestamped_file_name("cartwheel_mascot_output", suffix)

    def _publish_outputs(self, **outputs: Any) -> None:
        output_video_artifact = outputs.pop("output_video_url", None)
        downloaded_output_video_artifact = outputs.pop("downloaded_output_video_url", None)

        self.publish_update_to_parameter("output_video_url", output_video_artifact)
        self.parameter_output_values["output_video_url"] = output_video_artifact

        self.publish_update_to_parameter("downloaded_output_video_url", downloaded_output_video_artifact)
        self.parameter_output_values["downloaded_output_video_url"] = downloaded_output_video_artifact

        for name, value in outputs.items():
            self.publish_update_to_parameter(name, value)
            self.parameter_output_values[name] = value
