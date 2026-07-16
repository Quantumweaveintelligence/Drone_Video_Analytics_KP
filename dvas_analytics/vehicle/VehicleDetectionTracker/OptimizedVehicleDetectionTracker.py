# OptimizedVehicleDetectionTracker.py (fixed FP16 + further tweaks)
import json
import math
import cv2
import torch  # For CUDA check
from collections import defaultdict
from ultralytics import YOLO
import numpy as np
from datetime import datetime
from tqdm import tqdm  # For progress bar

from VehicleDetectionTracker.color_classifier.classifier import Classifier as ColorClassifier
from VehicleDetectionTracker.model_classifier.classifier import Classifier as ModelClassifier

class OptimizedVehicleDetectionTracker:

    def __init__(self, model_path="yolov8n.pt", classify_every_n=10, conf_threshold=0.5, imgsz=320):
        """
        Initialize the OptimizedVehicleDetectionTracker class (ultra-optimized).

        Args:
            model_path (str): Path to a lightweight YOLO model (e.g., 'yolov8n.pt' for nano speed).
            classify_every_n (int): Classify color/model every N frames per vehicle.
            conf_threshold (float): Minimum confidence to consider a detection.
            imgsz (int): Input image size for YOLO (lower = faster, e.g., 320).
        """
        # Load a lightweight YOLO model for speed (auto-downloads if needed)
        self.model = YOLO(model_path)
        # DO NOT call .half() here - causes fuse dtype mismatch; use half=True in track()
        self.track_history = defaultdict(lambda: [])  # History of vehicle tracking
        self.detected_vehicles = set()  # Set of ever-detected vehicle IDs
        self.color_classifier = None
        self.model_classifier = None
        self.vehicle_timestamps = defaultdict(lambda: {"timestamps": [], "positions": []})
        self.last_classified_frame = defaultdict(int)
        self.frame_count = 0
        self.classify_every_n = classify_every_n
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self.use_half = torch.cuda.is_available()  # For FP16

    def _initialize_classifiers(self):
        if self.color_classifier is None:
            self.color_classifier = ColorClassifier()
        if self.model_classifier is None:
            self.model_classifier = ModelClassifier()

    def _map_direction_to_label(self, direction):
        # Simplified direction mapping (fewer checks for minor speed gain)
        direction = (direction + math.pi) % (2 * math.pi) - math.pi
        if -math.pi/8 <= direction <= math.pi/8:
            return "Right"
        elif math.pi/8 < direction <= 3*math.pi/8:
            return "Bottom Right"
        elif 3*math.pi/8 < direction <= 5*math.pi/8:
            return "Bottom"
        elif 5*math.pi/8 < direction <= 7*math.pi/8:
            return "Bottom Left"
        elif 7*math.pi/8 < direction or direction <= -7*math.pi/8:
            return "Left"
        elif -7*math.pi/8 < direction <= -5*math.pi/8:
            return "Top Left"
        elif -5*math.pi/8 < direction <= -3*math.pi/8:
            return "Top"
        elif -3*math.pi/8 < direction < -math.pi/8:
            return "Top Right"
        return "Unknown"

    def _convert_meters_per_second_to_kmph(self, meters_per_second):
        return meters_per_second * 3.6 if meters_per_second else None

    def process_frame(self, frame, frame_time):
        """
        Process a single video frame (ultra-optimized: no brightness, low-res, conf filter).
        """
        self.frame_count += 1
        self._initialize_classifiers()
        response = {
            "number_of_vehicles_detected": 0,
            "detected_vehicles": [],
        }
        # Run YOLO with low res, conf threshold, and half=True for FP16 if CUDA
        results = self.model.track(
            frame,  # Use raw frame
            imgsz=self.imgsz,  # Lower res = faster
            persist=True, 
            tracker="bytetrack.yaml", 
            verbose=False,
            conf=self.conf_threshold,  # Filter low-conf early
            half=self.use_half  # FP16 for speed on GPU
        )
        if results is not None and results[0] is not None and results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xywh.cpu()
            conf_list = results[0].boxes.conf.cpu()
            track_ids = results[0].boxes.id.int().cpu().tolist()
            clss = results[0].boxes.cls.cpu().tolist()
            names = results[0].names

            for box, track_id, cls, conf in zip(boxes, track_ids, clss, conf_list):
                if conf < self.conf_threshold:  # Double-check filter
                    continue
                x, y, w, h = box
                label = str(names[cls])

                # Update track history (capped)
                if track_id not in self.track_history:
                    self.track_history[track_id] = []
                track = self.track_history[track_id]
                track.append((float(x), float(y)))
                if len(track) > 30:
                    track.pop(0)

                # Update timestamps/positions
                if track_id not in self.vehicle_timestamps:
                    self.vehicle_timestamps[track_id] = {"timestamps": [], "positions": []}
                self.vehicle_timestamps[track_id]["timestamps"].append(frame_time)
                self.vehicle_timestamps[track_id]["positions"].append((float(x), float(y)))

                # Speed calc (use last 3 for even faster, was 5)
                timestamps = self.vehicle_timestamps[track_id]["timestamps"]
                positions = self.vehicle_timestamps[track_id]["positions"]
                speed_kph = None
                reliability = 0.0
                direction_label = None
                direction = None
                if len(timestamps) >= 2:
                    recent_n = min(3, len(timestamps) - 1)  # Fewer intervals
                    speeds = []
                    for i in range(len(timestamps) - recent_n, len(timestamps)):
                        t1, t2 = timestamps[i - 1], timestamps[i]
                        delta_t = t2 - t1
                        if delta_t > 0:
                            x1, y1 = positions[i - 1]
                            x2, y2 = positions[i]
                            distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                            speeds.append(distance / delta_t)
                    if speeds:
                        avg_speed_mps = sum(speeds) / len(speeds)
                        speed_kph = self._convert_meters_per_second_to_kmph(avg_speed_mps)
                    
                    initial_x, initial_y = positions[0]
                    final_x, final_y = positions[-1]
                    direction = math.atan2(final_y - initial_y, final_x - initial_x)
                    direction_label = self._map_direction_to_label(direction)

                    num_samples = len(timestamps)
                    if num_samples < 5:
                        reliability = 0.5
                    elif num_samples < 10:
                        reliability = 0.7
                    else:
                        reliability = 1.0

                if track_id not in self.detected_vehicles:
                    self.detected_vehicles.add(track_id)

                # Crop (with bounds)
                h_img, w_img = frame.shape[:2]
                y1, y2 = max(0, int(y - h / 2)), min(h_img, int(y + h / 2))
                x1, x2 = max(0, int(x - w / 2)), min(w_img, int(x + w / 2))
                vehicle_frame = frame[y1:y2, x1:x2]

                # Classification (less frequent)
                should_classify = (
                    track_id not in self.last_classified_frame or
                    self.frame_count - self.last_classified_frame[track_id] >= self.classify_every_n
                )
                if should_classify:
                    color_info = self.color_classifier.predict(vehicle_frame)
                    color_info_json = json.dumps(color_info)
                    model_info = self.model_classifier.predict(vehicle_frame)
                    model_info_json = json.dumps(model_info)
                    self.last_classified_frame[track_id] = self.frame_count
                else:
                    color_info_json = json.dumps([])
                    model_info_json = json.dumps([])

                # Add to response
                response["detected_vehicles"].append({
                    "vehicle_id": int(track_id),
                    "vehicle_type": label,
                    "detection_confidence": float(conf),
                    "vehicle_coordinates": {
                        "x": float(x),
                        "y": float(y), 
                        "width": float(w), 
                        "height": float(h)
                    },
                    "color_info": color_info_json,
                    "model_info": model_info_json,
                    "speed_info": {
                        "kph": speed_kph, 
                        "reliability": reliability,
                        "direction_label": direction_label,
                        "direction": direction
                    }
                })
                response["number_of_vehicles_detected"] += 1

        return response

    def process_video(self, video_path, result_callback, show_progress=True):
        """
        Process a video (unchanged except for total_frames fetch).
        """
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_rate = int(cap.get(cv2.CAP_PROP_FPS))
        
        if show_progress:
            progress_bar = tqdm(total=total_frames, desc="Processing frames", unit="frame")
            print(f"Processing {total_frames} frames at {frame_rate} FPS (ultra-optimized: low-res model + FP16)")
        else:
            print(f"Processing {total_frames} frames at {frame_rate} FPS (ultra-optimized: low-res model + FP16)")

        frame_idx = 0

        while cap.isOpened():
            success, frame = cap.read()
            if success:
                frame_time = frame_idx / frame_rate
                response = self.process_frame(frame, frame_time)
                result_callback(response, frame_time)
                frame_idx += 1
                if show_progress:
                    progress_bar.update(1)
                    progress_bar.set_postfix({"Vehicles/frame": response.get("number_of_vehicles_detected", 0)})
            else:
                break

        if show_progress:
            progress_bar.close()

        cap.release()
        print(f"Processed {self.frame_count} frames.")