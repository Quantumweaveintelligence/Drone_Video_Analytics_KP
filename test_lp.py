import requests
import base64
import cv2
import numpy as np
from ultralytics import YOLO

# Get a vehicle crop from the live endpoint
resp = requests.get("http://localhost:8104/plates/live?limit=5")
plates = resp.json().get("plates", [])
found = False
for p in plates:
    b64 = p.get("vehicle_image_b64")
    if not b64: continue
    
    img_data = base64.b64decode(b64)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    print(f"Testing on vehicle crop of size {img.shape}")
    model = YOLO("/analytics/anpr/models/license_plate_detector.pt")
    res = model.predict(img, imgsz=640, conf=0.01, verbose=False)
    
    if res and res[0].boxes and len(res[0].boxes) > 0:
        confs = res[0].boxes.conf.tolist()
        print("Found plates with confs:", confs)
        found = True
    else:
        print("No plates found even at conf 0.01")
    break

if not found:
    print("No plates found in any crops")
