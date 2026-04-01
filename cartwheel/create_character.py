from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import AsyncResult, SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_three_d import Parameter3D
from griptape_nodes.retained_mode.griptape_nodes import logger

try:
    from cartwheel.cartwheel_api import (
        DEFAULT_POLL_DELAY_SECONDS,
        PreparedFile,
        create_static_download,
        get_api_key,
        guess_content_type,
        image_artifact_from_url,
        prepare_file,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        wait_for_character,
    )
except ImportError:
    from cartwheel_api import (  # type: ignore[no-redef]
        DEFAULT_POLL_DELAY_SECONDS,
        PreparedFile,
        create_static_download,
        get_api_key,
        guess_content_type,
        image_artifact_from_url,
        prepare_file,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        wait_for_character,
    )


class CartwheelCreateCharacter(SuccessFailureNode):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self.add_parameter(
            Parameter3D(
                name="character_model",
                tooltip="3D model file to upload to Cartwheel.",
                display_name="Character Model",
                ui_options={"expander": True},
            )
        )
        self.add_parameter(
            ParameterImage(
                name="thumbnail_image",
                tooltip="Thumbnail image for the character.",
                display_name="Thumbnail Image",
            )
        )
        self.add_parameter(
            Parameter(
                name="character_name",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional character name.",
            )
        )
        self.add_parameter(
            Parameter(
                name="character_description",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional character description.",
                ui_options={"multiline": True},
            )
        )
        self.add_parameter(
            Parameter(
                name="max_poll_attempts",
                type="int",
                input_types=["int"],
                output_type="int",
                default_value=90,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Maximum number of status checks before timing out.",
                hide=True,
            )
        )

        self.add_parameter(
            Parameter(
                name="character_id",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Cartwheel character identifier.",
            )
        )
        self.add_parameter(
            Parameter(
                name="estimated_seconds_wait_time",
                type="float",
                output_type="float",
                default_value=0,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Estimated wait time reported by Cartwheel.",
            )
        )
        self.add_parameter(
            Parameter(
                name="thumbnail_url",
                type="ImageUrlArtifact",
                output_type="ImageUrlArtifact",
                default_value=None,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Character thumbnail URL.",
            )
        )
        self.add_parameter(
            Parameter(
                name="uploaded_model_url",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Static URL for the uploaded character model file.",
            )
        )
        self.add_parameter(
            Parameter(
                name="character",
                type="dict",
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Raw Cartwheel character response.",
            )
        )
        self._create_status_parameters(
            result_details_tooltip="Character creation result details.",
            result_details_placeholder="Character creation status will be shown here.",
        )

    def validate_node(self) -> list[Exception] | None:
        errors: list[Exception] = []

        try:
            get_api_key()
        except Exception as e:
            errors.append(e)

        if self.get_parameter_value("character_model") is None:
            errors.append(ValueError("character_model is required."))

        if self.get_parameter_value("thumbnail_image") is None:
            errors.append(ValueError("thumbnail_image is required."))

        max_poll_attempts = self.get_parameter_value("max_poll_attempts")
        if isinstance(max_poll_attempts, int) and max_poll_attempts < 1:
            errors.append(ValueError("max_poll_attempts must be at least 1."))

        return errors or None

    def process(self) -> AsyncResult[dict[str, Any] | None]:
        self._clear_execution_status()
        yield lambda: self._process_with_status()

    def _process_with_status(self) -> dict[str, Any] | None:
        try:
            character = self._process()
        except Exception as error:
            self._set_status_results(was_successful=False, result_details=f"Failure: {error}")
            self._handle_failure_exception(error)
            return None

        character_id = str(character.get("characterID") or self.get_parameter_value("character_id") or "").strip()
        upload_status = str(character.get("uploadStatus") or "").strip()
        generated_status = str(character.get("generatedStatus") or "").strip()
        status_parts = [status for status in (upload_status, generated_status) if status]
        status_suffix = f" ({', '.join(status_parts)})" if status_parts else ""
        self._set_status_results(
            was_successful=True,
            result_details=f"Success: Created Cartwheel character {character_id}{status_suffix}.",
        )
        return character

    def _process(self) -> dict[str, Any]:
        self._publish_outputs(
            character_id="",
            estimated_seconds_wait_time=0,
            thumbnail_url=None,
            uploaded_model_url="",
            character={},
        )

        model_file = prepare_file(
            self.get_parameter_value("character_model"),
            default_file_name="character_model",
            default_extension="glb",
        )
        thumbnail_file = prepare_file(
            self.get_parameter_value("thumbnail_image"),
            default_file_name="thumbnail",
            default_extension="png",
        )

        character_name = str(self.get_parameter_value("character_name") or "").strip()
        character_description = str(self.get_parameter_value("character_description") or "").strip()

        if not character_name:
            character_name = Path(model_file.file_name).stem

        payload = {
            "fileExtension": model_file.extension,
            "thumbnailExtension": thumbnail_file.extension,
            "characterName": character_name,
            "characterDescription": character_description,
        }

        logger.info("Creating Cartwheel character upload for '%s'", character_name)
        upload_response = request_json("POST", "/characters/upload", payload=payload)

        character_id = str(upload_response.get("characterID") or "")
        if not character_id:
            raise RuntimeError(f"Cartwheel did not return a characterID: {upload_response}")

        model_upload_url = str(upload_response.get("characterFileUploadURL") or "")
        config_upload_url = str(upload_response.get("configUploadURL") or "")
        thumbnail_upload_url = str(upload_response.get("thumbnailUploadURL") or "")

        if not model_upload_url or not config_upload_url:
            raise RuntimeError(f"Cartwheel upload response is missing required upload URLs: {upload_response}")

        upload_to_presigned_url(
            model_upload_url,
            model_file.data,
            content_type=guess_content_type(model_file.extension),
        )

        config_payload = self._build_character_config(
            model_file=model_file,
            thumbnail_file=thumbnail_file,
            character_name=character_name,
            character_description=character_description,
        )
        upload_to_presigned_url(
            config_upload_url,
            json.dumps(config_payload).encode("utf-8"),
            content_type="application/json",
        )

        if thumbnail_upload_url:
            upload_to_presigned_url(
                thumbnail_upload_url,
                thumbnail_file.data,
                content_type=guess_content_type(thumbnail_file.extension, fallback="image/png"),
            )

        uploaded_model_url = create_static_download(
            model_file.data,
            timestamped_file_name("cartwheel_character_model", model_file.extension),
        )

        character = wait_for_character(
            character_id,
            poll_delay=DEFAULT_POLL_DELAY_SECONDS,
            max_attempts=int(self.get_parameter_value("max_poll_attempts") or 90),
        )

        self._publish_outputs(
            character_id=character_id,
            estimated_seconds_wait_time=character.get("estimatedSecondsWaitTime") or 0,
            thumbnail_url=image_artifact_from_url(character.get("thumbnailURL")),
            uploaded_model_url=uploaded_model_url,
            character=character,
        )

        return character

    def _build_character_config(
        self,
        *,
        model_file: PreparedFile,
        thumbnail_file: PreparedFile,
        character_name: str,
        character_description: str,
    ) -> dict[str, Any]:
        # The docs require uploading a config JSON but do not document its schema.
        # This payload is a best-effort metadata envelope built from the documented request fields.
        return {
            "characterName": character_name,
            "characterDescription": character_description,
            "fileExtension": model_file.extension,
            "thumbnailExtension": thumbnail_file.extension,
            "source": "griptape-nodes-library-cartwheel",
        }

    def _publish_outputs(self, **outputs: Any) -> None:
        thumbnail_value = outputs.pop("thumbnail_url", None)
        self.publish_update_to_parameter("thumbnail_url", thumbnail_value)
        self.parameter_output_values["thumbnail_url"] = thumbnail_value

        for name, value in outputs.items():
            self.publish_update_to_parameter(name, value)
            self.parameter_output_values[name] = value
