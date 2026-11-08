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

def extract_text_and_quality(image_bytes: bytes) -> tuple[str, dict]:
    """
    Vision OCR 텍스트와 글자 인식 품질 통계를 함께 반환한다.

    통계: word_mean(단어 평균 신뢰도), word_low(신뢰도 0.6 미만 단어 비율), words(단어 수)
    """
    client = vision.ImageAnnotatorClient()

    image_bytes = preprocess_image(image_bytes)
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise Exception(response.error.message)

    # OCR confidence 기반 품질 검증
    pages = response.full_text_annotation.pages if response.full_text_annotation else []
    word_conf: list[float] = []
    if pages:
        confidences = [
            block.confidence
            for page in pages
            for block in page.blocks
            if block.confidence > 0
        ]
        if confidences and (sum(confidences) / len(confidences)) < 0.3:
            raise Exception("IMAGE_QUALITY_LOW")

        word_conf = [
            word.confidence
            for page in pages
            for block in page.blocks
            for paragraph in block.paragraphs
            for word in paragraph.words
        ]

    stats = {
        "words": len(word_conf),
        "word_mean": round(sum(word_conf) / len(word_conf), 3) if word_conf else 0.0,
        "word_low": (
            round(sum(1 for c in word_conf if c < 0.6) / len(word_conf), 3)
            if word_conf
            else 1.0
        ),
    }

    # document_text_detection의 레이아웃 인식 결과 사용
    if response.full_text_annotation:
        return response.full_text_annotation.text, stats

    return "", stats


def extract_text_from_image(image_bytes: bytes) -> str:
    text, _ = extract_text_and_quality(image_bytes)
    return text