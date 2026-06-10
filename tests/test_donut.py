from transformers import DonutProcessor, VisionEncoderDecoderModel
from PIL import Image
import torch, re, json

processor = DonutProcessor.from_pretrained("naver-clova-ix/donut-base-finetuned-cord-v2")
model = VisionEncoderDecoderModel.from_pretrained("naver-clova-ix/donut-base-finetuned-cord-v2")
model.eval()

image = Image.open(r"C:\Users\mcbri\OneDrive\바탕 화면\영수증 규격화 테스트\영수증9.jpg").convert("RGB")

task_prompt = "<s_cord-v2>"
decoder_input_ids = processor.tokenizer(task_prompt, add_special_tokens=False, return_tensors="pt").input_ids
pixel_values = processor(image, return_tensors="pt").pixel_values

with torch.no_grad():
    outputs = model.generate(
        pixel_values,
        decoder_input_ids=decoder_input_ids,
        max_length=model.decoder.config.max_position_embeddings,
        early_stopping=True,
        pad_token_id=processor.tokenizer.pad_token_id,
        eos_token_id=processor.tokenizer.eos_token_id,
        use_cache=True,
        num_beams=1,
    )

sequence = processor.batch_decode(outputs)[0]
sequence = sequence.replace(processor.tokenizer.eos_token, "").replace(processor.tokenizer.pad_token, "")
sequence = re.sub(r"<.*?>", "", sequence, count=1).strip()
result = processor.token2json(sequence)
print(json.dumps(result, ensure_ascii=False, indent=2))