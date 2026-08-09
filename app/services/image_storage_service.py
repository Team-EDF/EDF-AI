"""
영수증 원본 이미지 저장 서비스.

- 배포(k8s) 환경: S3에 저장 (APP_ENV=prod)
- 로컬 개발 환경: 기본은 디스크에 저장 (방법 B)
  단, 로컬에서도 실제 S3 업로드를 테스트하고 싶으면
  아래 "방법 A" 블록의 주석을 해제하고 방법 B 분기를 주석 처리
"""
import os
import uuid
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ENV = os.getenv("APP_ENV", "dev")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_UPLOAD_PREFIX = os.getenv("S3_UPLOAD_PREFIX", "receipts")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
LOCAL_UPLOAD_DIR = Path(os.getenv("LOCAL_UPLOAD_DIR", "uploads/receipts"))


def _build_object_key(filename: str) -> str:
    ext = Path(filename).suffix or ".jpg"
    return f"{S3_UPLOAD_PREFIX}/{uuid.uuid4()}{ext}"


def _save_to_s3(image_bytes: bytes, filename: str) -> str | None:
    """S3에 이미지 업로드, 성공 시 접근 가능한 URL 반환. 실패 시 None."""
    import boto3
    from botocore.exceptions import ClientError

    key = _build_object_key(filename)
    try:
        client = boto3.client("s3", region_name=AWS_REGION)
        client.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=image_bytes)
        return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{key}"
    except ClientError:
        logger.exception("S3 이미지 업로드 실패: key=%s", key)
        return None


def _save_to_local_disk(image_bytes: bytes, filename: str) -> str | None:
    """로컬 디스크에 이미지 저장, 성공 시 상대 경로 반환. 실패 시 None."""
    ext = Path(filename).suffix or ".jpg"
    saved_name = f"{uuid.uuid4()}{ext}"
    try:
        LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = LOCAL_UPLOAD_DIR / saved_name
        target.write_bytes(image_bytes)
        return str(target)
    except OSError:
        logger.exception("로컬 이미지 저장 실패: filename=%s", filename)
        return None


# ── 방법 A: 로컬 개발 환경에서도 항상 실제 S3에 업로드 ──────────────────────
# 필요할 때 아래 함수의 주석을 풀고, save_receipt_image() 안에서
# _save_to_local_disk() 호출 대신 이 함수를 쓰도록 바꾸면 된다.
#
# def save_receipt_image(image_bytes: bytes, filename: str) -> str | None:
#     return _save_to_s3(image_bytes, filename)


# ── 방법 B: 배포 환경은 S3, 로컬 개발 환경은 디스크 (현재 활성화된 방식) ────
def save_receipt_image(image_bytes: bytes, filename: str) -> str | None:
    if APP_ENV in ("prod", "production"):
        return _save_to_s3(image_bytes, filename)
    return _save_to_local_disk(image_bytes, filename)