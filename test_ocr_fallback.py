import requests
import base64
import cv2
import numpy as np
from paddleocr import PaddleOCR
import re

PLATE_SANITY_RE = re.compile(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{5,12}$")
def clean_plate_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())

resp = requests.get("http://localhost:8104/plates/live?limit=5")
plates = resp.json().get("plates", [])
found = False

ocr = PaddleOCR(use_angle_cls=False, lang='en', show_log=False)

for p in plates:
    b64 = p.get("vehicle_image_b64")
    if not b64: continue
    
    img_data = base64.b64decode(b64)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    print(f"Testing on vehicle crop of size {img.shape}")
    
    ocr_res = ocr.ocr(img, cls=False)
    if ocr_res and ocr_res[0]:
        for line in ocr_res[0]:
            box, (text, conf) = line
            clean = clean_plate_text(text)
            print(f"  Found text: '{text}' (clean: '{clean}', conf: {conf})")
            if conf >= 0.4 and PLATE_SANITY_RE.match(clean):
                print(f"  *** VALID PLATE FOUND: {clean} *** at box: {box}")
                found = True
    else:
        print("  No text found")

if not found:
    print("No valid plates found in any crops using direct OCR fallback")
