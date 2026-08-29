import os
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from google import genai
from google.genai import errors, types
from google.oauth2 import service_account


DEFAULT_VERTEX_SERVICE_ACCOUNT_PATH = Path(
    "/home/lqy/.config/robopara/p-150gk23k-718446bc2ebd.json"
)
DEFAULT_VERTEX_PROJECT = "p-150gk23k"
DEFAULT_VERTEX_LOCATION = "global"
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
VERTEX_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def _get_api_error_status(error: Exception) -> Optional[int]:
    status = getattr(error, "code", None)
    if status is None:
        status = getattr(error, "status_code", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def is_retryable_error(error: Exception) -> bool:
    if not isinstance(error, errors.APIError):
        return True
    status = _get_api_error_status(error)
    return status in (408, 429) or (status is not None and status >= 500)


def _get_retry_after(error: Exception) -> Optional[float]:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


def get_retry_delay(attempt: int, error: Exception) -> float:
    retry_after = _get_retry_after(error)
    if retry_after is not None:
        return min(max(retry_after, 0.0), 60.0)

    base_delay = min(2 ** attempt, 60)
    jitter = random.uniform(0, min(base_delay * 0.25, 1.0))
    return min(base_delay + jitter, 60.0)


def _serialize_usage(usage_metadata: Any) -> Dict[str, Any]:
    if usage_metadata is None:
        return {}
    if isinstance(usage_metadata, dict):
        return usage_metadata
    if hasattr(usage_metadata, "model_dump"):
        return usage_metadata.model_dump(mode="json", exclude_none=True)
    if hasattr(usage_metadata, "dict"):
        return usage_metadata.dict(exclude_none=True)
    return {"value": str(usage_metadata)}


def detect_image_mime_type(image_data: bytes) -> str:
    """Detect a supported image MIME type from its file signature."""
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(image_data) >= 12
        and image_data.startswith(b"RIFF")
        and image_data[8:12] == b"WEBP"
    ):
        return "image/webp"
    raise ValueError("Unsupported image data; expected PNG, JPEG, or WebP")


class VertexGeminiClient:
    """Synchronous Vertex AI Gemini client used by project prompters."""

    def __init__(
        self,
        credentials_path: Optional[Union[str, Path]] = None,
        model: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
        credentials: Any = None,
    ) -> None:
        self.model = model or os.getenv("VERTEX_MODEL") or DEFAULT_GEMINI_MODEL
        self.project = (
            project or os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_VERTEX_PROJECT
        )
        self.location = (
            location
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or DEFAULT_VERTEX_LOCATION
        )

        configured_credentials_path = (
            credentials_path
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or DEFAULT_VERTEX_SERVICE_ACCOUNT_PATH
        )
        self.credentials_path = Path(configured_credentials_path).expanduser()

        if credentials is None:
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_path,
                    scopes=VERTEX_SCOPES,
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    "Vertex AI service-account file not found: "
                    f"{self.credentials_path}. Pass credentials_path or set "
                    "GOOGLE_APPLICATION_CREDENTIALS."
                ) from exc
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "Unable to load Vertex AI service-account credentials from "
                    f"{self.credentials_path}: {exc}"
                ) from exc

        self.client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
            credentials=credentials,
        )

    def generate(
        self,
        prompt: str,
        max_output_tokens: int,
        temperature: float = 0,
    ) -> Tuple[str, Dict[str, Any]]:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )
        return self._extract_response(response)

    def generate_multimodal(
        self,
        prompt: str,
        image_paths: Iterable[Union[str, Path]],
        max_output_tokens: int,
        temperature: float = 0,
    ) -> Tuple[str, Dict[str, Any]]:
        """Generate text from a prompt and one or more local scene images."""
        parts: List[types.Part] = [types.Part.from_text(text=prompt)]
        loaded_images = 0
        for configured_path in image_paths:
            image_path = Path(configured_path).expanduser()
            image_data = image_path.read_bytes()
            parts.append(
                types.Part.from_bytes(
                    data=image_data,
                    mime_type=detect_image_mime_type(image_data),
                )
            )
            loaded_images += 1

        if loaded_images == 0:
            raise ValueError("generate_multimodal requires at least one image")

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )
        return self._extract_response(response)

    @staticmethod
    def _extract_response(response: Any) -> Tuple[str, Dict[str, Any]]:
        response_text = response.text
        if response_text is None or not response_text.strip():
            raise RuntimeError(
                "Gemini returned no text. "
                f"prompt_feedback={getattr(response, 'prompt_feedback', None)!r}, "
                f"candidates={getattr(response, 'candidates', None)!r}"
            )
        return response_text, _serialize_usage(
            getattr(response, "usage_metadata", None)
        )
