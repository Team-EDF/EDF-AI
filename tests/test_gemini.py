import sys
sys.path.insert(0, "C:/Users/mcbri/PycharmProjects/GreenStep")

from app.services.gemini_vision_service import extract_receipt_from_gemini_vision
import json

with open(r"C:\Users\mcbri\OneDrive\바탕 화면\영수증 규격화 테스트\영수증7.jpg", "rb") as f:
    image_bytes = f.read()

result = extract_receipt_from_gemini_vision(image_bytes)
print(json.dumps(result, ensure_ascii=False, indent=2))