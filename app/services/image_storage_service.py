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


def _save_to_s3(image_bytes: bytes, filename: str) -> str:
    """S3에 이미지 업로드, 성공 시 URL 반환. 실패 시 예외를 그대로 발생시킨다."""
    import boto3

    if not S3_BUCKET_NAME:
        raise RuntimeError("S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.")

    key = _build_object_key(filename)
    client = boto3.client("s3", region_name=AWS_REGION)
    client.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=image_bytes)
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{key}"


def _save_to_local_disk(image_bytes: bytes, filename: str) -> str:
    ext = Path(filename).suffix or ".jpg"
    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = LOCAL_UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    target.write_bytes(image_bytes)
    return str(target)


# ── 방법 A: 로컬 개발 환경에서도 항상 실제 S3에 업로드 ──────────────────────
# 필요할 때 아래 함수의 주석을 풀고, save_receipt_image() 안에서
# _save_to_local_disk() 호출 대신 이 함수를 쓰도록 바꾸면 된다.
#
# def save_receipt_image(image_bytes: bytes, filename: str) -> str | None:
#     return _save_to_s3(image_bytes, filename)


# ── 방법 B: 배포 환경은 S3, 로컬 개발 환경은 디스크 (현재 활성화된 방식) ────
def save_receipt_image(image_bytes: bytes, filename: str) -> str:
    if APP_ENV in ("prod", "production"):
        return _save_to_s3(image_bytes, filename)
    return _save_to_local_disk(image_bytes, filename)

def describe_storage_error(error: Exception) -> str:
    """실패 원인을 응답에 넣을 짧은 문자열로 변환 (ARN 등 상세는 로그에만 남김)."""
    response = getattr(error, "response", None)  # botocore ClientError
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return f"{type(error).__name__}: {code}"
    return f"{type(error).__name__}: {str(error)[:150]}"