import cv2
import os
import time

def test_decode():
    video_path = "DJI_0110.MP4"
    
    print("Testing CPU decode...")
    cap = cv2.VideoCapture(video_path)
    start = time.time()
    frames = 0
    while frames < 100:
        ret, frame = cap.read()
        if not ret: break
        frames += 1
    end = time.time()
    print(f"CPU fps: {frames / (end - start)}")
    cap.release()

    print("\nTesting NVDEC decode...")
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "video_codec;h264_cuvid"
    cap2 = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
    start = time.time()
    frames = 0
    while frames < 100:
        ret, frame = cap2.read()
        if not ret: break
        frames += 1
    end = time.time()
    print(f"NVDEC fps: {frames / (end - start)}")
    cap2.release()
    os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

if __name__ == "__main__":
    test_decode()
