import os
from dotenv import load_dotenv
from google.cloud import vision
from PIL import Image, ImageEnhance, ImageFilter
import io

load_dotenv()

credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if credentials_path:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

def preprocess_image(image_bytes: bytes) -> bytes:
    """OCR 정확도 향상을 위한 이미지 전처리: 그레이스케일 → 대비 강화 → 샤프닝."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L")  # 그레이스케일
    img = ImageEnhance.Contrast(img).enhance(1.3)           # 대비 2배 강화
    img = img.filter(ImageFilter.SHARPEN)                    # 샤프닝
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()

def extract_text_from_image(image_bytes: bytes) -> str:
    client = vision.ImageAnnotatorClient()

    image_bytes = preprocess_image(image_bytes)
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise Exception(response.error.message)

    # OCR confidence 기반 품질 검증
    pages = response.full_text_annotation.pages if response.full_text_annotation else []
    if pages:
        confidences = [
            block.confidence
            for page in pages
            for block in page.blocks
            if block.confidence > 0
        ]
        if confidences and (sum(confidences) / len(confidences)) < 0.3:
            raise Exception("IMAGE_QUALITY_LOW")

    # document_text_detection의 레이아웃 인식 결과 사용
    if response.full_text_annotation:
        return response.full_text_annotation.text

    return ""