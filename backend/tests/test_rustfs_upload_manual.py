#!/usr/bin/env python3
"""
Manual test script for uploading an alarm image to RustFS (S3 compatible storage).
This script mimics the behavior of DetectionService._upload_image.

Usage:
    export PYTHONPATH=$PYTHONPATH:.
    python3 tests/test_rustfs_upload_manual.py
"""

import os
import sys
import time
import datetime
import io
import logging

# Add backend directory to path so we can import app modules
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from PIL import Image, ImageDraw

from app.config import get_config

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def create_dummy_image() -> bytes:
    """Creates a simple JPEG image in memory for testing."""
    img = Image.new('RGB', (640, 480), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    d.text((10, 10), f"Test RustFS Upload {datetime.datetime.now()}", fill=(255, 255, 0))
    
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()

def test_upload():
    try:
        config = get_config()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return

    rc = config.rustfs
    logger.info(f"Loaded RustFS config: Endpoint={rc.endpoint}, Bucket={rc.bucket}, Secure={rc.secure}")

    # Determine endpoint URL with scheme (logic from DetectionService)
    protocol = "https" if rc.secure else "http"
    endpoint = rc.endpoint
    if not endpoint.startswith("http"):
        endpoint = f"{protocol}://{endpoint}"

    logger.info(f"Connecting to S3 at {endpoint}...")

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=rc.access_key,
            aws_secret_access_key=rc.secret_key,
            config=Config(signature_version="s3v4"),
        )
    except Exception as e:
        logger.error(f"Failed to initialize Boto3 client: {e}")
        return

    # Check bucket existence
    try:
        s3.head_bucket(Bucket=rc.bucket)
        logger.info(f"Bucket '{rc.bucket}' exists and is accessible.")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "404":
            logger.warning(f"Bucket '{rc.bucket}' not found. Attempting to create it...")
            try:
                s3.create_bucket(Bucket=rc.bucket)
                logger.info(f"Bucket '{rc.bucket}' created successfully.")
            except ClientError as create_err:
                logger.error(f"Failed to create bucket: {create_err}")
                return
        elif error_code == "403":
             logger.error(f"Access denied to bucket '{rc.bucket}'. Check credentials.")
             return
        else:
            logger.error(f"Error checking bucket: {e}")
            return

    # Prepare upload
    image_bytes = create_dummy_image()
    timestamp_ms = int(time.time() * 1000)
    task_id = "manual-test-task"
    bind_id = "test-stream-001"
    
    object_name = f"{bind_id}/{timestamp_ms}_{task_id}.jpg"
    
    logger.info(f"Uploading object to {rc.bucket}/{object_name} ({len(image_bytes)} bytes)...")

    try:
        s3.put_object(
            Bucket=rc.bucket,
            Key=object_name,
            Body=image_bytes,
            ContentType="image/jpeg",
        )
        logger.info("Upload successful.")
    except ClientError as e:
        logger.error(f"Upload failed: {e}")
        return

    # Generate presigned URL
    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": rc.bucket, "Key": object_name},
            ExpiresIn=int(datetime.timedelta(days=7).total_seconds()),
        )
        logger.info("-" * 40)
        logger.info("Presigned URL generated successfully:")
        logger.info(url)
        logger.info("-" * 40)
        logger.info("You can open this URL in a browser to verify the image.")

    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")

if __name__ == "__main__":
    test_upload()
