import cv2
import numpy as np
import time
import simplejpeg

def test_encode():
    # Create a dummy 4K BGR frame
    frame = np.random.randint(0, 255, (2160, 3840, 3), dtype=np.uint8)
    
    print("Testing cv2.imencode...")
    start = time.time()
    for _ in range(50):
        cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    end = time.time()
    print(f"cv2 fps: {50 / (end - start)}")

    print("\nTesting simplejpeg...")
    start = time.time()
    for _ in range(50):
        # simplejpeg natively accepts BGR format, so no conversion needed!
        simplejpeg.encode_jpeg(frame, quality=85, colorspace='BGR', fastdct=True)
    end = time.time()
    print(f"simplejpeg fps: {50 / (end - start)}")

if __name__ == "__main__":
    test_encode()
