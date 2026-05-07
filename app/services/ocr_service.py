import os
from dotenv import load_dotenv
from google.cloud import vision

load_dotenv()

credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if credentials_path:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path


def extract_text_from_image(image_bytes: bytes) -> str:
    client = vision.ImageAnnotatorClient()

    image = vision.Image(content=image_bytes)
    response = client.text_detection(image=image)

    if response.error.message:
        raise Exception(response.error.message)

    texts = response.text_annotations

    if not texts:
        return ""

    return texts[0].description