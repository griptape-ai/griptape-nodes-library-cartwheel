from __future__ import annotations

from typing import Any

from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import AsyncResult, SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_video import ParameterVideo
from griptape_nodes.retained_mode.griptape_nodes import logger
from griptape_nodes.traits.options import Options

try:
    from cartwheel.cartwheel_api import (
        DEFAULT_POLL_DELAY_SECONDS,
        create_static_download,
        download_bytes,
        extract_media_upload_item,
        extract_motion_ids,
        extract_urls_from_motion_items,
        get_api_key,
        guess_content_type,
        list_all_batch_motions,
        list_zip_entries,
        prepare_file,
        probe_video_duration_seconds,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        video_artifact_from_url,
        wait_for_batch,
    )
except ImportError:
    from cartwheel_api import (  # type: ignore[no-redef]
        DEFAULT_POLL_DELAY_SECONDS,
        create_static_download,
        download_bytes,
        extract_media_upload_item,
        extract_motion_ids,
        extract_urls_from_motion_items,
        get_api_key,
        guess_content_type,
        list_all_batch_motions,
        list_zip_entries,
        prepare_file,
        probe_video_duration_seconds,
        request_json,
        timestamped_file_name,
        upload_to_presigned_url,
        video_artifact_from_url,
        wait_for_batch,
    )


AXIS_CHOICES = ["Z", "Y", "X", "-Z", "-Y", "-X"]
EXPORT_TYPE_CHOICES = ["bvh", "fbx-blender", "fbx-maya", "fbx-unreal", "fbx-roblox", "glb", "ma", "mb"]
FACE_EXPRESSION_CHOICES = [
    "Default",
    "Neutral",
    "Angry",
    "Annoyed",
    "Bored",
    "Confusion",
    "DelightedGrin",
    "Delighted",
    "Excited",
    "Goof",
    "HappyLookLeft",
    "HappyLookRight",
    "Love",
    "Nervous",
    "Pain",
    "Pleased",
    "Puzzled",
    "Rage",
    "RollLookLeft",
    "RollLookRight",
    "Sad",
    "SadEyesClosed",
    "SadLookLeft",
    "SadLookRight",
    "Squeeze",
    "Stretch",
    "Stunned",
    "Surprise",
    "Suspicious",
    "ThinkingLookLeft",
    "ThinkingLookRight",
    "Wink",
]
FRAME_RATE_CHOICES = [2, 12, 23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 90, 100, 120, 144, 240]
HAND_POSE_CHOICES = [
    "default",
    "claw",
    "curled",
    "fist",
    "fist_clenched",
    "fist_relaxed",
    "gripping",
    "love_you",
    "open_loose",
    "open_tight",
    "peace",
    "pointing",
    "pointing_cool",
    "relaxed",
    "rock",
    "splay",
    "thumbs_up",
]
KEYFRAME_CLEANING_CHOICES = ["none", "reduce", "simplify"]
REQUESTED_MODEL_CHOICES = ["scoot", "swing"]


class CartwheelGenerateMotion(SuccessFailureNode):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self.add_parameter(
            Parameter(
                name="character_id",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Cartwheel character ID to animate.",
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
                tooltip="Text prompt used when generating motion without a reference video.",
                ui_options={"multiline": True},
            )
        )
        self.add_parameter(
            ParameterVideo(
                name="reference_video",
                tooltip="Optional reference video. When set, the node uses Cartwheel's motion-from-video endpoint.",
                display_name="Reference Video",
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
                tooltip="Optional batch name for the Cartwheel UI.",
            )
        )
        self.add_parameter(
            Parameter(
                name="callback_url",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional webhook URL for completion callbacks.",
            )
        )
        self.add_parameter(
            Parameter(
                name="reference_video_resolution",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional reference video resolution, for example 1920x1080.",
            )
        )
        self.add_parameter(
            Parameter(
                name="export_type",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="fbx-blender",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Motion export format.",
                traits={Options(choices=EXPORT_TYPE_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="requested_model",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="swing",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Model used by Cartwheel for text-based motion generation.",
                traits={Options(choices=REQUESTED_MODEL_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="requested_duration",
                type="float",
                input_types=["float", "int"],
                output_type="float",
                default_value=10,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Requested motion duration in seconds. Used for text-based generation when supported.",
            )
        )
        self.add_parameter(
            Parameter(
                name="face_expression",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="Default",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Face expression to apply.",
                traits={Options(choices=FACE_EXPRESSION_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="forward",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="Z",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Forward axis orientation.",
                traits={Options(choices=AXIS_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="up",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="Y",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Up axis orientation.",
                traits={Options(choices=AXIS_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="frame_rate",
                type="float",
                input_types=["float", "int"],
                output_type="float",
                default_value=24,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Output frame rate.",
                traits={Options(choices=FRAME_RATE_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="frame_step_size",
                type="float",
                input_types=["float", "int"],
                output_type="float",
                default_value=1,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Frame step size for generated motion.",
            )
        )
        self.add_parameter(
            Parameter(
                name="hand_pose",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="relaxed",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Hand pose to apply. Cartwheel notes this is not applicable for motion-from-video.",
                traits={Options(choices=HAND_POSE_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="ik_feet",
                type="bool",
                input_types=["bool"],
                output_type="bool",
                default_value=True,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Use inverse kinematics for the feet. Applicable to Maya exports.",
            )
        )
        self.add_parameter(
            Parameter(
                name="ik_hands",
                type="bool",
                input_types=["bool"],
                output_type="bool",
                default_value=False,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Use inverse kinematics for the hands. Applicable to Maya exports.",
            )
        )
        self.add_parameter(
            Parameter(
                name="include_mesh",
                type="bool",
                input_types=["bool"],
                output_type="bool",
                default_value=True,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Include the character mesh in the generated export when supported.",
            )
        )
        self.add_parameter(
            Parameter(
                name="keyframe_cleaning",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="none",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Keyframe cleaning method.",
                traits={Options(choices=KEYFRAME_CLEANING_CHOICES)},
            )
        )
        self.add_parameter(
            Parameter(
                name="move_in_place",
                type="bool",
                input_types=["bool"],
                output_type="bool",
                default_value=False,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Move the character in place.",
            )
        )
        self.add_parameter(
            Parameter(
                name="loop",
                type="bool",
                input_types=["bool"],
                output_type="bool",
                default_value=False,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Generate a seamless looping motion.",
            )
        )
        self.add_parameter(
            Parameter(
                name="skin_hex",
                type="str",
                input_types=["str"],
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional skin color override in hex format.",
            )
        )
        self.add_parameter(
            Parameter(
                name="rotation_offsets",
                type="dict",
                input_types=["dict"],
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Optional rotation offsets object matching Cartwheel's exportSettings.rotationOffsets schema.",
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
                tooltip="Maximum number of batch status checks before timing out.",
                hide=True,
            )
        )
        self.add_parameter(
            Parameter(
                name="motion_page_limit",
                type="int",
                input_types=["int"],
                output_type="int",
                default_value=100,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                tooltip="Maximum number of motion items to request per page from the batch motions endpoint.",
                hide=True,
            )
        )

        self.add_parameter(
            Parameter(
                name="batch_id",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Cartwheel motion batch ID.",
            )
        )
        self.add_parameter(
            Parameter(
                name="zip_download_url",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Remote Cartwheel presigned zip download URL.",
            )
        )
        self.add_parameter(
            Parameter(
                name="downloaded_zip_url",
                type="str",
                output_type="str",
                default_value="",
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Static URL for the downloaded batch zip.",
            )
        )
        self.add_parameter(
            Parameter(
                name="downloaded_zip_entries",
                type="list",
                output_type="list[str]",
                default_value=[],
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="File entries inside the downloaded batch zip.",
            )
        )
        self.add_parameter(
            Parameter(
                name="motion_ids",
                type="list",
                output_type="list[str]",
                default_value=[],
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Motion IDs returned for the batch.",
            )
        )
        self.add_parameter(
            Parameter(
                name="preview_video_url",
                type="VideoUrlArtifact",
                output_type="VideoUrlArtifact",
                default_value=None,
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="First preview video URL found in the batch motions response.",
            )
        )
        self.add_parameter(
            Parameter(
                name="preview_video_urls",
                type="list",
                output_type="list[str]",
                default_value=[],
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Preview video URLs found in the batch motions response.",
            )
        )
        self.add_parameter(
            Parameter(
                name="bvh_urls",
                type="list",
                output_type="list[str]",
                default_value=[],
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="BVH URLs found in the batch motions response.",
            )
        )
        self.add_parameter(
            Parameter(
                name="motion_batch",
                type="dict",
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Raw batch response from Cartwheel.",
            )
        )
        self.add_parameter(
            Parameter(
                name="motions_response",
                type="dict",
                output_type="dict",
                default_value={},
                allowed_modes={ParameterMode.OUTPUT},
                tooltip="Raw motions-by-batch response from Cartwheel.",
            )
        )
        self._create_status_parameters(
            result_details_tooltip="Motion generation result details.",
            result_details_placeholder="Motion generation status will be shown here.",
        )

    def validate_node(self) -> list[Exception] | None:
        errors: list[Exception] = []

        try:
            get_api_key()
        except Exception as e:
            errors.append(e)

        character_id = str(self.get_parameter_value("character_id") or "").strip()
        prompt = str(self.get_parameter_value("prompt") or "").strip()
        reference_video = self.get_parameter_value("reference_video")

        if not character_id:
            errors.append(ValueError("character_id is required."))

        if reference_video is None and not prompt:
            errors.append(ValueError("prompt is required when reference_video is not provided."))

        motion_page_limit = self.get_parameter_value("motion_page_limit")
        if isinstance(motion_page_limit, int) and not 1 <= motion_page_limit <= 100:
            errors.append(ValueError("motion_page_limit must be between 1 and 100."))

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

        batch = result.get("batch", {}) if isinstance(result, dict) else {}
        batch_id = str(batch.get("batchID") or self.get_parameter_value("batch_id") or "").strip()
        batch_status = str(batch.get("status") or "").strip()
        status_suffix = f" ({batch_status})" if batch_status else ""
        self._set_status_results(
            was_successful=True,
            result_details=f"Success: Generated Cartwheel motion batch {batch_id}{status_suffix}.",
        )
        return result

    def _process(self) -> dict[str, Any]:
        self._publish_outputs(
            batch_id="",
            zip_download_url="",
            downloaded_zip_url="",
            downloaded_zip_entries=[],
            motion_ids=[],
            preview_video_url=None,
            preview_video_urls=[],
            bvh_urls=[],
            motion_batch={},
            motions_response={},
        )

        reference_video = self.get_parameter_value("reference_video")
        batch_payload = self._build_base_payload()

        if reference_video is None:
            prompt = str(self.get_parameter_value("prompt") or "").strip()
            batch_payload["prompts"] = [prompt]
            batch_payload["requestedModel"] = self.get_parameter_value("requested_model")

            requested_duration = self.get_parameter_value("requested_duration")
            if requested_duration:
                batch_payload["requestedDuration"] = requested_duration

            route = "/motion/fromText"
        else:
            media_id = self._upload_reference_video(reference_video)
            batch_payload["mediaIDs"] = [media_id]
            route = "/motion/fromVideo"

        logger.info("Submitting Cartwheel motion batch via %s", route)
        batch_response = request_json("POST", route, payload=batch_payload, expected_statuses={202})

        batch_id = str(batch_response.get("batchID") or "")
        if not batch_id:
            raise RuntimeError(f"Cartwheel did not return a batchID: {batch_response}")

        final_batch = wait_for_batch(
            batch_id,
            poll_delay=DEFAULT_POLL_DELAY_SECONDS,
            max_attempts=int(self.get_parameter_value("max_poll_attempts") or 120),
        )

        motions_response = list_all_batch_motions(
            batch_id,
            limit=int(self.get_parameter_value("motion_page_limit") or 100),
        )
        motion_items = motions_response.get("items", [])
        if not isinstance(motion_items, list):
            motion_items = []

        zip_download_url = str(final_batch.get("downloadURL") or "")
        downloaded_zip_url = ""
        zip_entries: list[str] = []

        if zip_download_url:
            zip_bytes = download_bytes(zip_download_url)
            downloaded_zip_url = create_static_download(
                zip_bytes, timestamped_file_name("cartwheel_motion_batch", "zip")
            )
            zip_entries = list_zip_entries(zip_bytes)

        motion_ids = extract_motion_ids([item for item in motion_items if isinstance(item, dict)])
        preview_video_urls = extract_urls_from_motion_items(
            [item for item in motion_items if isinstance(item, dict)],
            tokens=("preview", "url"),
        )
        if not preview_video_urls:
            preview_video_urls = extract_urls_from_motion_items(
                [item for item in motion_items if isinstance(item, dict)],
                tokens=("video", "url"),
            )
        bvh_urls = extract_urls_from_motion_items(
            [item for item in motion_items if isinstance(item, dict)],
            tokens=("bvh", "url"),
        )

        self._publish_outputs(
            batch_id=batch_id,
            zip_download_url=zip_download_url,
            downloaded_zip_url=downloaded_zip_url,
            downloaded_zip_entries=zip_entries,
            motion_ids=motion_ids,
            preview_video_url=video_artifact_from_url(preview_video_urls[0] if preview_video_urls else None),
            preview_video_urls=preview_video_urls,
            bvh_urls=bvh_urls,
            motion_batch=final_batch,
            motions_response=motions_response,
        )

        return {
            "batch": final_batch,
            "motions": motions_response,
            "downloaded_zip_url": downloaded_zip_url,
        }

    def _build_base_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "exportSettings": {
                "characterID": self.get_parameter_value("character_id"),
                "exportType": self.get_parameter_value("export_type"),
                "faceExpression": self.get_parameter_value("face_expression"),
                "forward": self.get_parameter_value("forward"),
                "frameRate": self.get_parameter_value("frame_rate"),
                "frameStepSize": self.get_parameter_value("frame_step_size"),
                "handPose": self.get_parameter_value("hand_pose"),
                "ikFeet": self.get_parameter_value("ik_feet"),
                "ikHands": self.get_parameter_value("ik_hands"),
                "includeMesh": self.get_parameter_value("include_mesh"),
                "keyframeCleaning": self.get_parameter_value("keyframe_cleaning"),
                "moveInPlace": self.get_parameter_value("move_in_place"),
                "up": self.get_parameter_value("up"),
            }
        }

        batch_name = str(self.get_parameter_value("batch_name") or "").strip()
        if batch_name:
            payload["batchName"] = batch_name

        callback_url = str(self.get_parameter_value("callback_url") or "").strip()
        if callback_url:
            payload["callbackURL"] = callback_url

        skin_hex = str(self.get_parameter_value("skin_hex") or "").strip()
        if skin_hex:
            payload["exportSettings"]["skinHex"] = skin_hex

        rotation_offsets = self.get_parameter_value("rotation_offsets")
        if isinstance(rotation_offsets, dict) and rotation_offsets:
            payload["exportSettings"]["rotationOffsets"] = rotation_offsets

        loop = self.get_parameter_value("loop")
        if loop:
            payload["loop"] = loop

        return payload

    def _upload_reference_video(self, reference_video: Any) -> str:
        video_file = prepare_file(
            reference_video,
            default_file_name="reference_video",
            default_extension="mp4",
        )

        media_request: dict[str, Any] = {
            "extension": video_file.extension,
            "name": "reference-video",
        }

        resolution = str(self.get_parameter_value("reference_video_resolution") or "").strip()
        if resolution:
            media_request["resolution"] = resolution

        duration = probe_video_duration_seconds(video_file)
        if duration:
            media_request["duration"] = duration

        media_response = request_json("POST", "/media/upload", payload={"media": [media_request]})
        media_upload = extract_media_upload_item(media_response)

        upload_to_presigned_url(
            str(media_upload["upload_url"]),
            video_file.data,
            content_type=guess_content_type(video_file.extension, fallback="video/mp4"),
        )

        return str(media_upload["media_id"])

    def _publish_outputs(self, **outputs: Any) -> None:
        preview_value = outputs.pop("preview_video_url", None)
        self.publish_update_to_parameter("preview_video_url", preview_value)
        self.parameter_output_values["preview_video_url"] = preview_value

        for name, value in outputs.items():
            self.publish_update_to_parameter(name, value)
            self.parameter_output_values[name] = value
