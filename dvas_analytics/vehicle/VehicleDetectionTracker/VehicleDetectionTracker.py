# VehicleDetectionTracker.py (updated fourcc for .avi)
import json
import math
import cv2
import base64
from collections import defaultdict
from ultralytics import YOLO
import numpy as np
from ultralytics.utils.plotting import colors
from VehicleDetectionTracker.color_classifier.classifier import Classifier as ColorClassifier
from VehicleDetectionTracker.model_classifier.classifier import Classifier as ModelClassifier
from datetime import datetime

class VehicleDetectionTracker:

    def __init__(self, model_path="visdrone.pt"):
        """
        Initialize the VehicleDetection class with improved ID mapping.

        Args:
            model_path (str): Path to the YOLO model file.
        """
        # Load the YOLO model and set up data structures for tracking.
        self.model = YOLO(model_path)
        self.track_history = defaultdict(lambda: [])
        self.detected_vehicles = set()
        self.color_classifier = None
        self.model_classifier = None
        self.vehicle_timestamps = defaultdict(list)
        
        # ID mapping system - maps raw tracker IDs to sequential IDs
        self.id_mapping = {}  # {tracker_id: sequential_id}
        self.next_sequential_id = 1  # Counter for next sequential ID
        self.active_ids = set()  # Currently active vehicle IDs in current frame


    def _initialize_classifiers(self):
        if self.color_classifier is None:
            self.color_classifier = ColorClassifier()
        if self.model_classifier is None:
            self.model_classifier = ModelClassifier()

    def _get_sequential_id(self, tracker_id):
        """
        Convert raw tracker ID to a sequential ID for better user experience.
        
        Args:
            tracker_id: Raw ID from the tracking algorithm
            
        Returns:
            int: Sequential ID (1, 2, 3, ...)
        """
        if tracker_id not in self.id_mapping:
            # Assign new sequential ID
            self.id_mapping[tracker_id] = self.next_sequential_id
            self.next_sequential_id += 1
        
        return self.id_mapping[tracker_id]

    def _map_direction_to_label(self, direction):
        direction_ranges = {
            (-math.pi / 8, math.pi / 8): "Right",
            (math.pi / 8, 3 * math.pi / 8): "Bottom Right",
            (3 * math.pi / 8, 5 * math.pi / 8): "Bottom",
            (5 * math.pi / 8, 7 * math.pi / 8): "Bottom Left",
            (7 * math.pi / 8, -7 * math.pi / 8): "Left",
            (-7 * math.pi / 8, -5 * math.pi / 8): "Top Left",
            (-5 * math.pi / 8, -3 * math.pi / 8): "Top",
            (-3 * math.pi / 8, -math.pi / 8): "Top Right"
        }
        for angle_range, label in direction_ranges.items():
            if angle_range[0] <= direction <= angle_range[1]:
                return label
        return "Unknown"


    def _encode_image_base64(self, image):
        """
        Encode an image as base64.

        Args:
            image (numpy.ndarray): The image to be encoded.

        Returns:
            str: Base64-encoded image.
        """
        _, buffer = cv2.imencode('.jpg', image)
        image_base64 = base64.b64encode(buffer).decode()
        return image_base64
    
    def _decode_image_base64(self, image_base64):
        """
        Decode a base64-encoded image.

        Args:
            image_base64 (str): Base64-encoded image data.

        Returns:
            numpy.ndarray or None: Decoded image as a numpy array or None if decoding fails.
        """
        try:
            image_data = base64.b64decode(image_base64)
            image_np = np.frombuffer(image_data, dtype=np.uint8)
            image = cv2.imdecode(image_np, flags=cv2.IMREAD_COLOR)
            return image
        except Exception as e:
            return None
        
    def _increase_brightness(self, image, factor=1.5):
        """
        Increases the brightness of an image by multiplying its pixels by a factor.

        :param image: The input image in numpy array format.
        :param factor: The brightness increase factor. A value greater than 1 will increase brightness.
        :return: The image with increased brightness.
        """
        brightened_image = cv2.convertScaleAbs(image, alpha=factor, beta=0)
        return brightened_image

    def _convert_meters_per_second_to_kmph(self, meters_per_second):
        kmph = meters_per_second * 3.6
        return kmph

    def _draw_custom_detection(self, frame, box, display_id, cls, conf, names):
        """
        Draw custom bounding boxes and labels with thin lines and small text.
        
        Args:
            frame: The frame to draw on
            box: Bounding box coordinates (x, y, w, h)
            display_id: Sequential display ID
            cls: Class ID
            conf: Confidence score
            names: Class names dictionary
        """
        x, y, w, h = box
        label = str(names[cls])
        
        # Get color for this class
        bbox_color = colors(cls, True)
        
        # Calculate box corners
        x1 = int(x - w / 2)
        y1 = int(y - h / 2)
        x2 = int(x + w / 2)
        y2 = int(y + h / 2)
        
        # Draw thinner bounding box (thickness=1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), bbox_color, 1)
        
        # Prepare label text with sequential ID
        label_text = f"{label} #{display_id}"
        
        # Use smaller font scale and thinner text
        font_scale = 0.4
        font_thickness = 1
        font = cv2.FONT_HERSHEY_SIMPLEX
        
        # Get text size
        (text_width, text_height), baseline = cv2.getTextSize(
            label_text, font, font_scale, font_thickness
        )
        
        # Draw semi-transparent background for text
        padding = 2
        bg_y1 = y1 - text_height - padding * 2
        bg_y2 = y1
        
        # Ensure background stays within frame
        if bg_y1 < 0:
            bg_y1 = y2
            bg_y2 = y2 + text_height + padding * 2
        
        # Draw background rectangle with transparency
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (x1, bg_y1),
            (x1 + text_width + padding * 2, bg_y2),
            bbox_color,
            -1
        )
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
        
        # Draw text
        text_y = bg_y1 + text_height + padding if bg_y1 < y1 else bg_y2 - padding
        cv2.putText(
            frame,
            label_text,
            (x1 + padding, text_y),
            font,
            font_scale,
            (255, 255, 255),
            font_thickness,
            cv2.LINE_AA
        )
        
        return frame

    def process_frame_base64(self, frame_base64, frame_timestamp):
        """
        Process a base64-encoded frame to detect and track vehicles.

        Args:
            frame_base64 (str): Base64-encoded input frame for processing.

        Returns:
            dict or None: Processed information including tracked vehicles' details and the annotated frame in base64,
            or an error message if decoding fails.
        """
        frame = self._decode_image_base64(frame_base64)
        if frame is not None:
            return self.process_frame(frame, frame_timestamp)
        else:
            return {
                "error": "Failed to decode the base64 image"
            }

    def process_frame(self, frame, frame_timestamp):
        """
        Process a single video frame to detect and track vehicles with sequential ID mapping.

        Args:
            frame (numpy.ndarray): Input frame for processing.

        Returns:
            dict: Processed information including tracked vehicles' details, the annotated frame in base64, and the original frame in base64.
        """
        self._initialize_classifiers()
        response = {
            "number_of_vehicles_detected": 0,
            "detected_vehicles": [],
            "annotated_frame_base64": None,
            "original_frame_base64": None
        }
        
        # Clear active IDs for this frame
        self.active_ids.clear()
        
        # Perform vehicle tracking
        results = self.model.track(self._increase_brightness(frame), persist=True, tracker="bytetrack.yaml")
        
        if results is not None and results[0] is not None and results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xywh.cpu()
            conf_list = results[0].boxes.conf.cpu()
            track_ids = results[0].boxes.id.int().cpu().tolist()
            clss = results[0].boxes.cls.cpu().tolist()
            names = results[0].names
            
            # Create annotated frame (copy of original)
            annotated_frame = frame.copy()

            for box, tracker_id, cls, conf in zip(boxes, track_ids, clss, conf_list):
                x, y, w, h = box
                label = str(names[cls])
                
                # Get sequential ID for this vehicle
                display_id = self._get_sequential_id(tracker_id)
                self.active_ids.add(tracker_id)
                
                bbox_color = colors(cls, True)
                
                # Draw custom detection with sequential ID
                annotated_frame = self._draw_custom_detection(
                    annotated_frame, box, display_id, cls, conf, names
                )
                
                # Track history for trajectory (use tracker_id internally)
                if tracker_id not in self.track_history:
                    self.track_history[tracker_id] = []
                
                track = self.track_history[tracker_id]
                track.append((float(x), float(y)))
                
                max_history_length = 30
                if len(track) > max_history_length:
                    track.pop(0)
                
                # Draw thinner tracking line
                points = np.hstack(track).astype(np.int32).reshape((-1, 1, 2))
                cv2.polylines(annotated_frame, [points], isClosed=False, color=bbox_color, thickness=1)

                if tracker_id not in self.vehicle_timestamps:
                    self.vehicle_timestamps[tracker_id] = {"timestamps": [], "positions": []}

                self.vehicle_timestamps[tracker_id]["timestamps"].append(frame_timestamp)
                self.vehicle_timestamps[tracker_id]["positions"].append((x, y))
                
                # Calculate speed
                timestamps = self.vehicle_timestamps[tracker_id]["timestamps"]
                positions = self.vehicle_timestamps[tracker_id]["positions"]
                speed_kph = None
                reliability = 0.0
                direction_label = None
                direction = None
                
                if len(timestamps) >= 2:
                    delta_t_list = []
                    distance_list = []
                    
                    for i in range(1, len(timestamps)):
                        t1, t2 = timestamps[i - 1], timestamps[i]
                        delta_t = t2.timestamp() - t1.timestamp()
                        if delta_t > 0:
                            x1, y1 = positions[i - 1]
                            x2, y2 = positions[i]
                            distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                            delta_t_list.append(delta_t)
                            distance_list.append(distance)
                    
                    speeds = [distance / delta_t for distance, delta_t in zip(distance_list, delta_t_list)]
                    if len(speeds) > 0:
                        avg_speed_mps = sum(speeds) / len(speeds)
                    else:
                        avg_speed_mps = None

                    if avg_speed_mps is not None:
                        speed_kph = self._convert_meters_per_second_to_kmph(avg_speed_mps)
                    else:
                        speed_kph = None
                    
                    initial_x, initial_y = positions[0]
                    final_x, final_y = positions[-1]
                    direction = math.atan2(final_y - initial_y, final_x - initial_x)
                    direction_label = self._map_direction_to_label(direction)

                    if len(timestamps) < 5:
                        reliability = 0.5
                    elif len(timestamps) < 10:
                        reliability = 0.7
                    else:
                        reliability = 1.0

                self.detected_vehicles.add(tracker_id)
                response["number_of_vehicles_detected"] += 1

                # Extract vehicle frame
                vehicle_frame = frame[int(y - h / 2):int(y + h / 2), int(x - w / 2):int(x + w / 2)]
                vehicle_frame_base64 = self._encode_image_base64(vehicle_frame)
                color_info = self.color_classifier.predict(vehicle_frame)
                color_info_json = json.dumps(color_info)
                model_info = self.model_classifier.predict(vehicle_frame)
                model_info_json = json.dumps(model_info)
    
                response["detected_vehicles"].append({
                    "vehicle_id": display_id,  # Use sequential ID
                    "tracker_id": tracker_id,   # Keep original for debugging
                    "vehicle_type": label,
                    "detection_confidence": conf.item(),
                    "vehicle_coordinates": {
                        "x": x.item(),
                        "y": y.item(), 
                        "width": w.item(), 
                        "height": h.item()
                    },
                    "vehicle_frame_base64": vehicle_frame_base64,
                    "vehicle_frame_timestamp": frame_timestamp, 
                    "color_info": color_info_json,
                    "model_info": model_info_json,
                    "speed_info": {
                        "kph": speed_kph, 
                        "reliability": reliability,
                        "direction_label": direction_label,
                        "direction": direction
                    }
                })
                    
            annotated_frame_base64 = self._encode_image_base64(annotated_frame)
            response["annotated_frame_base64"] = annotated_frame_base64

        original_frame_base64 = self._encode_image_base64(frame)
        response["original_frame_base64"] = original_frame_base64

        return response

    def process_video(self, video_path, result_callback, output_path=None):
        """
        Process a video by calling a callback for each frame's results, with optional video saving.

        Args:
            video_path (str): Path to the video file.
            result_callback (function): A callback function to handle the processing results for each frame.
            output_path (str, optional): Path to save the annotated output video. If None, no video is saved.
        """
        cap = cv2.VideoCapture(video_path)
        video_writer = None

        if output_path:
            # Get video properties
            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            
            # Use 'XVID' for .avi or 'mp4v' for .mp4
            if str(output_path).lower().endswith('.avi'):
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
            else:
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            
            # Initialize video writer
            video_writer = cv2.VideoWriter(str(output_path), fourcc, fps, (frame_width, frame_height))

        while cap.isOpened():
            success, frame = cap.read()
            if success:
                timestamp = datetime.now()
                response = self.process_frame(frame, timestamp)
                result_callback(response)
                
                if 'annotated_frame_base64' in response:
                    annotated_frame = self._decode_image_base64(response['annotated_frame_base64'])
                    if annotated_frame is not None:
                        cv2.imshow("Vehicle Detection Tracker", annotated_frame)
                        if video_writer:
                            video_writer.write(annotated_frame)
                
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                break

        cap.release()
        if video_writer:
            video_writer.release()
        cv2.destroyAllWindows()
        
        # Print final statistics
        print(f"\n--- Tracking Summary ---")
        print(f"Total unique vehicles tracked: {len(self.id_mapping)}")
        print(f"ID mapping: {dict(sorted(self.id_mapping.items(), key=lambda x: x[1]))}")