import logging
import asyncio
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from typing import Optional, Set

from src.smartmatch.config import settings

# Setup production structured logging
logger = logging.getLogger("smartmatch.utils.photo_storage")


class ImageService:
    """
    Production-grade B2/S3 storage provider for Smart Match Africa.
    Handles asset image persistence using the B2 S3-compatible API.

    CRITICAL: Because boto3 is synchronous, all network/signature calls
    are wrapped in `asyncio.to_thread()` to prevent blocking the main asyncio event loop.
    """

    def __init__(
            self,
            aws_access_key: str,
            aws_secret_key: str,
            region_name: Optional[str] = None,
            bucket_name: Optional[str] = None,
            endpoint_url: Optional[str] = None
    ):
        """
        Initializes the ImageService with dynamic configurations passed from main.py.
        Ensures proper fallback for Backblaze B2 S3 endpoint URLs and S3v4 signatures.
        """
        # Assign default values from configuration profile
        self.bucket_name: str = bucket_name or "sma-assets-image-bucket"
        self.region_name: str = region_name or "eu-central-003"
        self.allowed_mime_types: Set[str] = {'image/jpeg', 'image/png', 'image/webp'}

        # 🛠️ CRITICAL: Ensure valid Backblaze B2 endpoint URL if endpoint_url is None
        self.endpoint_url: str = endpoint_url or f"https://s3.{self.region_name}.backblazeb2.com"

        # Safely initialize the B2 S3-compatible client during startup using S3v4 Signatures
        try:
            self.client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=self.region_name,
                config=Config(
                    signature_version='s3v4',
                    s3={'addressing_style': 'path'}  # Path-style addressing required for Backblaze B2
                )
            )
            logger.info(f"PhotoStorage successfully initialized for Backblaze B2 at {self.endpoint_url}.")
        except Exception as e:
            logger.critical(f"Failed to initialize B2 PhotoStorage: {e}")
            raise

    def _extract_object_key(self, photo_ref: str) -> str:
        """
        Extracts a clean B2 object key from full HTTP URLs, S3 paths, or database keys.
        Handles dirty input formats gracefully.
        """
        if not photo_ref:
            return ""

        clean_ref = str(photo_ref).strip()

        # Handle full path containing the bucket name
        if f"{self.bucket_name}/" in clean_ref:
            return clean_ref.split(f"{self.bucket_name}/")[-1].lstrip('/')

        # Handle standard HTTP(S) raw domain URLs
        if clean_ref.startswith("http://") or clean_ref.startswith("https://"):
            parts = clean_ref.split("/")
            return "/".join(parts[3:]).lstrip('/')

        return clean_ref.lstrip('/')

    def get_presigned_url(self, photo_ref: str, expires_in: int = 3600) -> Optional[str]:
        """
        Generates a temporary pre-signed URL for a private Backblaze B2 object.
        Allows Telegram's servers to fetch images from private buckets directly.

        :param photo_ref: File key or stored path in database
        :param expires_in: Signature validity duration in seconds (default: 1 hour)
        :return: Signed HTTP URL string
        """
        if not photo_ref:
            return None

        object_key = self._extract_object_key(photo_ref)
        if not object_key:
            return photo_ref

        try:
            presigned_url = self.client.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': object_key
                },
                ExpiresIn=expires_in
            )
            return presigned_url
        except Exception as e:
            logger.error(f"Failed to generate pre-signed URL for '{photo_ref}' (key: '{object_key}'): {e}")
            return photo_ref

    async def get_presigned_url_async(self, photo_ref: str, expires_in: int = 3600) -> Optional[str]:
        """
        Async thread-safe wrapper to generate pre-signed URLs off the main asyncio event loop.
        """
        if not photo_ref:
            return None
        return await asyncio.to_thread(self.get_presigned_url, photo_ref, expires_in)

    # ==========================================
    # ASYNC I/O OPERATIONS (NON-BLOCKING)
    # ==========================================
    async def process_and_upload(self, file_bytes: bytes, mime_type: str, asset_id: str) -> str:
        """
        Validates the image and orchestrates the upload to permanent storage.
        Returns the public URL of the uploaded image.
        """
        if mime_type not in self.allowed_mime_types:
            logger.error(f"Upload rejected: Unsupported MIME type {mime_type} for asset {asset_id}")
            raise ValueError("Unsupported file type. Please upload JPEG, PNG, or WEBP.")

        # Generate a cryptographically unique path to prevent namespace collisions
        filename = f"assets/{asset_id}/{uuid.uuid4()}.jpg"

        # Dispatch the upload securely using the internal class method
        public_url = await self.upload(file_bytes, filename, content_type=mime_type)

        if not public_url:
            raise RuntimeError("Cloud storage failed to return a valid public URL.")

        logger.info(f"Image processing and upload complete for asset {asset_id}: {public_url}")
        return public_url

    async def upload(self, file_bytes: bytes, filename: str, content_type: str = 'image/jpeg') -> Optional[str]:
        """
        Uploads image bytes to the B2 bucket.
        Wraps the synchronous boto3 put_object call in a background thread.
        """
        try:
            # We use asyncio.to_thread to prevent boto3 from freezing the Telegram event loop
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket_name,
                Key=filename,
                Body=file_bytes,
                ContentType=content_type
            )

            # Construct standard S3-compatible public URL
            endpoint = settings.B2_ENDPOINT_URL if hasattr(settings, 'B2_ENDPOINT_URL') else "https://s3.eu-central-003.backblazeb2.com"
            public_url = f"{endpoint}/{self.bucket_name}/{filename}"
            logger.debug(f"Successfully pushed {filename} to B2 storage thread.")
            return public_url

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Cloud storage transport layer failed for {filename}: {str(e)}")
            return None

    async def upload_buffer(self, file_bytes: bytes, filename: str, content_type: str = 'image/jpeg') -> Optional[str]:
        """
        🛠️ Compatibility wrapper method to satisfy upload_buffer calls from handlers.
        Automatically infers content-type for .webp, .png, and .jpg files.
        """
        lower_name = filename.lower()
        if lower_name.endswith('.webp'):
            content_type = 'image/webp'
        elif lower_name.endswith('.png'):
            content_type = 'image/png'
        elif lower_name.endswith(('.jpg', '.jpeg')):
            content_type = 'image/jpeg'

        return await self.upload(file_bytes, filename, content_type=content_type)

    async def delete(self, filename: str) -> bool:
        """
        Removes an asset image from B2 storage cleanly via a background thread.
        """
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket_name,
                Key=filename
            )
            logger.info(f"Successfully purged {filename} from B2 storage.")
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Deletion payload failed for {filename}: {str(e)}")
            return False
