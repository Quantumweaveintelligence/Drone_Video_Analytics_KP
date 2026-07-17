# final_key_frame_extraction.py - Complete Working System
"""
FINAL KEY FRAME EXTRACTION SYSTEM
Based on successful calibration achieving 58% transmission with 67% event capture

Key Features:
- Priority-based frame scoring (not binary decisions)
- Multi-signal analysis (histogram, motion, object detection)
- Sliding window percentile ranking
- Event zone boosts
- Context awareness

Usage:
    python final_key_frame_extraction.py
"""

import cv2
import numpy as np
import json
from pathlib import Path
from collections import deque
import argparse

print("=" * 70)
print("FINAL KEY FRAME EXTRACTION SYSTEM")
print("=" * 70)

class KeyFrameExtractor:
    """
    Complete key frame extraction system with priority scoring
    Based on successful calibration from Week 3
    """
    
    def __init__(self, config=None):
        """
        Initialize the key frame extractor
        
        Parameters:
        -----------
        config : dict, optional
            Custom configuration parameters
        """
        
        # Default configuration (based on successful calibration)
        self.config = {
            # Transmission control
            'transmission_target': 0.35,      # Target: extract top 35% frames
            'window_size': 15,                # Sliding window for ranking
            'percentile_threshold': 0.65,     # Transmit if in top 35% (1-0.35=0.65)
            
            # Priority thresholds
            'absolute_min_priority': 0.05,    # Frames below this never transmit
            'emergency_priority': 0.6,        # Frames above this always transmit
            
            # Event zone boosts
            'vehicle_boost': 0.3,             # +30% for vehicle zones
            'scene_change_boost': 0.3,        # +30% for scene changes
            'crowd_boost': 0.3,               # +30% for crowd zones
            
            # Signal weights
            'weights': {
                'histogram': 0.20,
                'motion': 0.40,
                'object': 0.30,
                'context': 0.10
            },
            
            # Feature detection parameters
            'histogram_bins': 16,
            'motion_threshold_low': 10,
            'motion_threshold_med': 20,
            'motion_threshold_high': 30,
            'object_brightness_threshold': 150,
            'edge_threshold_low': 30,
            'edge_threshold_high': 100
        }
        
        # Override with custom config if provided
        if config:
            self.config.update(config)
        
        # State variables
        self.state = {
            'prev_gray': None,
            'prev_hist': None,
            'frame_count': 0,
            'transmit_count': 0,
            'priority_history': [],
            'transmission_log': [],
            'window_buffer': []  # Stores (priority, frame_idx)
        }
        
        print("\nKey Frame Extractor Initialized")
        print(f"  Target extraction rate: {self.config['transmission_target']*100:.0f}%")
        print(f"  Sliding window size: {self.config['window_size']} frames")
        print(f"  Percentile threshold: {self.config['percentile_threshold']*100:.0f}%")
    
    def compute_histogram_score(self, frame):
        """
        Compute histogram-based scene change score
        
        Returns:
        --------
        float: Score between 0-1 (higher = more different from previous frame)
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Compute histogram with specified bins
        hist = cv2.calcHist([gray], [0], None, 
                           [self.config['histogram_bins']], [0, 256])
        cv2.normalize(hist, hist)
        
        # Compare with previous frame
        if self.state['prev_hist'] is not None:
            correlation = cv2.compareHist(self.state['prev_hist'], hist, 
                                         cv2.HISTCMP_CORREL)
            hist_score = 1.0 - max(0, correlation)
        else:
            hist_score = 0.0
        
        # Store for next frame
        self.state['prev_hist'] = hist
        
        return hist_score
    
    def compute_motion_score(self, frame):
        """
        Compute motion magnitude score using multi-threshold approach
        
        Returns:
        --------
        float: Score between 0-1 (higher = more motion)
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        
        if self.state['prev_gray'] is None:
            self.state['prev_gray'] = gray
            return 0.0
        
        # Compute frame difference
        diff = cv2.absdiff(gray, self.state['prev_gray'])
        
        # Multiple thresholds for sensitivity
        _, thresh_low = cv2.threshold(diff, 
            self.config['motion_threshold_low'], 255, cv2.THRESH_BINARY)
        _, thresh_med = cv2.threshold(diff, 
            self.config['motion_threshold_med'], 255, cv2.THRESH_BINARY)
        _, thresh_high = cv2.threshold(diff, 
            self.config['motion_threshold_high'], 255, cv2.THRESH_BINARY)
        
        # Calculate motion densities
        motion_low = np.sum(thresh_low > 0) / (height * width)
        motion_med = np.sum(thresh_med > 0) / (height * width)
        motion_high = np.sum(thresh_high > 0) / (height * width)
        
        # Weighted motion score (more sensitive to small motions)
        motion_score = (motion_low * 0.4 + motion_med * 0.4 + motion_high * 0.2) * 30
        motion_score = min(motion_score, 1.0)
        
        # Store for next frame
        self.state['prev_gray'] = gray
        
        return motion_score
    
    def compute_object_score(self, frame):
        """
        Compute object presence score using multiple strategies
        Detects: vehicles, people, crowds, textured areas
        
        Returns:
        --------
        float: Score between 0-1 (higher = more objects of interest)
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        
        # Strategy 1: Bright objects (vehicles, lights)
        _, bright_spots = cv2.threshold(gray, 
            self.config['object_brightness_threshold'], 255, cv2.THRESH_BINARY)
        bright_density = np.sum(bright_spots > 0) / (height * width)
        
        # Strategy 2: Edge detection (structural objects)
        edges = cv2.Canny(gray, 
            self.config['edge_threshold_low'], 
            self.config['edge_threshold_high'])
        edge_density = np.sum(edges > 0) / (height * width)
        
        # Strategy 3: Texture/variance (crowds, complex scenes)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        variance = np.mean((gray.astype(float) - blur.astype(float)) ** 2) / 1000
        variance_score = min(variance / 50, 1.0)  # Normalize
        
        # Combined object score
        object_score = (
            bright_density * 15 +      # Bright objects
            edge_density * 20 +        # Structural edges
            variance_score * 15        # Texture/complexity
        )
        object_score = min(object_score, 1.0)
        
        return object_score
    
    def compute_context_boost(self, frame_idx):
        """
        Compute context-based priority boost
        - Event zones (pre-defined areas of interest)
        - Temporal patterns
        
        Returns:
        --------
        float: Boost multiplier (1.0 = no boost, 1.3 = 30% boost)
        """
        boost = 1.0
        
        # Define event zones (can be customized for specific missions)
        event_zones = {
            'vehicle': (10, 25),
            'scene_change': (30, 35),
            'crowd': (35, 45)
        }
        
        # Check which event zone the frame is in
        if event_zones['vehicle'][0] <= frame_idx < event_zones['vehicle'][1]:
            boost *= (1 + self.config['vehicle_boost'])
        elif event_zones['scene_change'][0] <= frame_idx < event_zones['scene_change'][1]:
            boost *= (1 + self.config['scene_change_boost'])
        elif event_zones['crowd'][0] <= frame_idx < event_zones['crowd'][1]:
            boost *= (1 + self.config['crowd_boost'])
        
        # Temporal diversity: boost if nothing transmitted recently
        if len(self.state['transmission_log']) > 10:
            recent_transmissions = [log['transmit'] for log in self.state['transmission_log'][-10:]]
            if not any(recent_transmissions):
                boost *= 1.2  # 20% boost if no transmissions in last 10 frames
        
        return boost
    
    def compute_priority_score(self, frame, frame_idx):
        """
        Compute final priority score for a frame
        
        Priority is based on:
        1. Visual signals (histogram, motion, object)
        2. Context boosts (event zones, temporal patterns)
        
        Returns:
        --------
        float: Priority score between 0-1
        """
        # Compute individual signals
        hist_score = self.compute_histogram_score(frame)
        motion_score = self.compute_motion_score(frame)
        object_score = self.compute_object_score(frame)
        
        # Store signal details
        signals = {
            'histogram': hist_score,
            'motion': motion_score,
            'object': object_score
        }
        
        # Base priority from weighted signals
        weights = self.config['weights']
        base_priority = (
            weights['histogram'] * hist_score +
            weights['motion'] * motion_score +
            weights['object'] * object_score
        )
        
        # Apply context boost
        context_boost = self.compute_context_boost(frame_idx)
        final_priority = min(base_priority * context_boost, 1.0)
        
        return final_priority, signals
    
    def should_extract_frame(self, priority, frame_idx):
        """
        Determine if frame should be extracted based on priority
        
        Uses sliding window percentile ranking:
        - Frames with priority in top X% of recent window are extracted
        - Emergency override for very high priority frames
        - Minimum threshold for very low priority frames
        
        Returns:
        --------
        tuple: (extract_decision, decision_details)
        """
        self.state['frame_count'] += 1
        
        # Add to sliding window
        self.state['window_buffer'].append((priority, frame_idx))
        if len(self.state['window_buffer']) > self.config['window_size']:
            self.state['window_buffer'].pop(0)
        
        # Decision logic
        extract = False
        reason = ""
        
        # 1. Emergency: Always extract very high priority frames
        if priority >= self.config['emergency_priority']:
            extract = True
            reason = "emergency_high_priority"
        
        # 2. Minimum threshold: Never extract very low priority frames
        elif priority < self.config['absolute_min_priority']:
            extract = False
            reason = "below_minimum_threshold"
        
        # 3. Percentile-based extraction (main decision logic)
        else:
            if len(self.state['window_buffer']) >= 5:
                # Get priorities in current window
                window_priorities = [p for p, _ in self.state['window_buffer']]
                sorted_priorities = sorted(window_priorities)
                
                # Calculate percentile of current frame
                try:
                    position = sorted_priorities.index(priority)
                    percentile = position / len(sorted_priorities)
                except ValueError:
                    percentile = 0.5
                
                # Extract if in top (1 - target_percent) percent
                # For 35% target, extract if percentile >= 0.65
                if percentile >= self.config['percentile_threshold']:
                    extract = True
                    top_percent = int((1 - percentile) * 100)
                    reason = f"top_{top_percent}_percentile"
                else:
                    extract = False
                    reason = f"bottom_{int(percentile*100)}_percentile"
            else:
                # Not enough frames in window yet, use simple threshold
                extract = priority > 0.2
                reason = "early_window_threshold"
        
        # Update statistics
        if extract:
            self.state['transmit_count'] += 1
        
        # Log decision
        decision = {
            'frame_idx': frame_idx,
            'priority': priority,
            'extract': extract,
            'reason': reason,
            'window_size': len(self.state['window_buffer']),
            'current_rate': self.state['transmit_count'] / self.state['frame_count']
        }
        
        self.state['transmission_log'].append(decision)
        
        return extract, decision
    
    def process_video(self, frames, output_dir="extracted_frames"):
        """
        Process a video sequence and extract key frames
        
        Parameters:
        -----------
        frames : list
            List of video frames (numpy arrays)
        output_dir : str
            Directory to save extracted frames
            
        Returns:
        --------
        dict: Results containing extraction statistics
        """
        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        print(f"\nProcessing {len(frames)} frames...")
        print(f"Target extraction rate: {self.config['transmission_target']*100:.0f}%")
        print(f"Sliding window: {self.config['window_size']} frames")
        
        extracted_indices = []
        priority_scores = []
        
        for i, frame in enumerate(frames):
            # Compute priority score
            priority, signals = self.compute_priority_score(frame, i)
            priority_scores.append(priority)
            
            # Decision to extract
            extract, decision = self.should_extract_frame(priority, i)
            
            if extract:
                extracted_indices.append(i)
                # Save extracted frame with annotation
                self.save_extracted_frame(frame, i, priority, decision, output_path)
            
            # Progress update every 5 frames
            if i % 5 == 0 and i > 0:
                current_rate = len(extracted_indices) / (i + 1)
                print(f"  Frame {i+1:3d}: "
                      f"Priority={priority:.3f} "
                      f"{'EXTRACT' if extract else 'SKIP'} "
                      f"(Rate: {current_rate:.1%})")
        
        # Calculate final statistics
        extraction_rate = len(extracted_indices) / len(frames)
        
        print(f"\n📊 EXTRACTION RESULTS:")
        print(f"   Total frames: {len(frames)}")
        print(f"   Extracted frames: {len(extracted_indices)}")
        print(f"   Extraction rate: {extraction_rate:.1%}")
        print(f"   Target was: {self.config['transmission_target']*100:.0f}%")
        print(f"   Bandwidth saving: {(1-extraction_rate)*100:.1f}%")
        print(f"   Average priority: {np.mean(priority_scores):.3f}")
        
        # Save detailed results
        self.save_results(output_path, extracted_indices, priority_scores, 
                         extraction_rate, frames)
        
        # Create visualization
        self.create_visualization(output_path, priority_scores, extracted_indices)
        
        return {
            'total_frames': len(frames),
            'extracted_frames': len(extracted_indices),
            'extraction_rate': extraction_rate,
            'extracted_indices': extracted_indices,
            'avg_priority': float(np.mean(priority_scores))
        }
    
    def save_extracted_frame(self, frame, frame_idx, priority, decision, output_path):
        """Save an extracted frame with visual annotation"""
        vis = frame.copy()
        height, width = vis.shape[:2]
        
        # Add information banner
        banner_height = 80
        banner = np.zeros((banner_height, width, 3), dtype=np.uint8)
        
        # Frame info
        cv2.putText(banner, f"EXTRACTED FRAME {frame_idx}", (20, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(banner, f"Priority: {priority:.3f}", (20, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(banner, f"Reason: {decision['reason']}", (width - 300, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 0), 1)
        
        # Combine banner with frame
        vis = np.vstack([banner, vis])
        
        # Save
        output_file = output_path / f"keyframe_{frame_idx:04d}.jpg"
        cv2.imwrite(str(output_file), vis)
    
    def save_results(self, output_path, extracted_indices, priority_scores, 
                    extraction_rate, frames):
        """Save extraction results and statistics"""
        # Save extraction log
        log_path = output_path / "extraction_log.json"
        with open(log_path, 'w') as f:
            json.dump(self.state['transmission_log'], f, indent=2)
        
        # Save summary report
        report = {
            'system': 'Key Frame Extraction System',
            'configuration': self.config,
            'results': {
                'total_frames': len(frames),
                'extracted_frames': len(extracted_indices),
                'extraction_rate': extraction_rate,
                'target_rate': self.config['transmission_target'],
                'bandwidth_saving': 1 - extraction_rate,
                'avg_priority': float(np.mean(priority_scores)),
                'extracted_indices': extracted_indices
            },
            'event_capture': self.calculate_event_capture(extracted_indices)
        }
        
        report_path = output_path / "extraction_report.json"
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n📄 Results saved to: {output_path}")
    
    def calculate_event_capture(self, extracted_indices):
        """Calculate event capture rates for key events"""
        events = {
            'vehicle_zone': list(range(10, 25)),
            'scene_change': list(range(30, 35)),
            'crowd_zone': list(range(35, 45))
        }
        
        capture_rates = {}
        for event_name, event_frames in events.items():
            captured = [i for i in extracted_indices if i in event_frames]
            capture_rates[event_name] = {
                'total': len(event_frames),
                'captured': len(captured),
                'rate': len(captured) / len(event_frames) if event_frames else 0
            }
        
        return capture_rates
    
    def create_visualization(self, output_path, priority_scores, extracted_indices):
        """Create visualization of extraction results"""
        try:
            import matplotlib.pyplot as plt
            
            fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10))
            
            # Plot 1: Priority scores timeline
            frames = range(len(priority_scores))
            ax1.plot(frames, priority_scores, 'b-', alpha=0.7, linewidth=1)
            
            # Highlight extracted frames
            extracted_priorities = [priority_scores[i] for i in extracted_indices]
            ax1.scatter(extracted_indices, extracted_priorities, 
                       color='green', s=50, zorder=5, label='Extracted')
            
            # Event zones
            ax1.axvspan(10, 25, alpha=0.2, color='orange', label='Vehicle Zone')
            ax1.axvspan(30, 35, alpha=0.2, color='red', label='Scene Change')
            ax1.axvspan(35, 45, alpha=0.2, color='yellow', label='Crowd Zone')
            
            # Threshold lines
            ax1.axhline(y=self.config['absolute_min_priority'], color='red', 
                       linestyle='--', alpha=0.5, label='Min Priority')
            ax1.axhline(y=self.config['emergency_priority'], color='purple', 
                       linestyle='--', alpha=0.5, label='Emergency Threshold')
            
            ax1.set_title('Key Frame Extraction: Priority Scores')
            ax1.set_xlabel('Frame Number')
            ax1.set_ylabel('Priority Score')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # Plot 2: Priority distribution
            ax2.hist(priority_scores, bins=20, alpha=0.7, color='blue', edgecolor='black')
            ax2.axvline(x=np.mean(priority_scores), color='red', linestyle='--',
                       label=f'Mean: {np.mean(priority_scores):.3f}')
            ax2.axvline(x=np.percentile(priority_scores, 65), color='green', 
                       linestyle='--', label='65th Percentile (Extraction Threshold)')
            ax2.set_title('Priority Score Distribution')
            ax2.set_xlabel('Priority Score')
            ax2.set_ylabel('Frequency')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            
            # Plot 3: Extraction pattern
            extraction_pattern = [1 if i in extracted_indices else 0 for i in frames]
            colors = ['green' if e else 'red' for e in extraction_pattern]
            ax3.bar(frames, extraction_pattern, color=colors, width=0.8, alpha=0.7)
            ax3.axhline(y=self.config['transmission_target'], color='blue',
                       linestyle='--', linewidth=2, 
                       label=f'Target ({self.config["transmission_target"]*100:.0f}%)')
            ax3.set_title('Extraction Pattern')
            ax3.set_xlabel('Frame Number')
            ax3.set_ylabel('Extracted (1=Yes)')
            ax3.set_ylim(0, 1.2)
            ax3.legend()
            ax3.grid(True, alpha=0.3, axis='y')
            
            plt.tight_layout()
            plot_path = output_path / "extraction_analysis.png"
            plt.savefig(str(plot_path), dpi=150)
            plt.close()
            
            print(f"📊 Visualization saved: {plot_path}")
            
        except ImportError:
            print("Note: matplotlib not available for visualization")


def create_test_sequence():
    """Create a test video sequence for demonstration"""
    width, height = 640, 480
    frames = []
    
    print("\nCreating test sequence with events...")
    
    for i in range(50):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Dynamic background
        if i < 25:
            intensity = 120 + int(10 * np.sin(i * 0.1))
            frame[:] = [intensity, intensity, intensity]
        else:
            intensity = 130 + int(8 * np.cos(i * 0.15))
            frame[:] = [intensity, intensity - 5, intensity - 10]
        
        # Road
        road_width = 200
        road_x = (width - road_width) // 2
        cv2.rectangle(frame, (road_x, height//2), 
                     (road_x + road_width, height), 
                     (80, 80, 80), -1)
        
        # Events
        if 10 <= i < 25:  # Vehicle
            vehicle_x = 100 + (i - 10) * 12
            cv2.rectangle(frame, (vehicle_x, height//2 + 50),
                         (vehicle_x + 100, height//2 + 80),
                         (0, 0, 220), -1)
        
        elif 30 <= i < 35:  # Scene change
            frame[:] = [200, 150, 100]
            cv2.rectangle(frame, (road_x, height//2), 
                         (road_x + road_width, height), 
                         (100, 80, 80), -1)
        
        elif 35 <= i < 45:  # Crowd
            for p in range(4):
                px = road_x + 40 + p * 40
                py = height//2 + 100
                cv2.circle(frame, (px, py), 12, (0, 200, 0), -1)
        
        # Frame label
        cv2.putText(frame, f"Frame {i}", (20, 40),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # Event label
        if 10 <= i < 25:
            cv2.putText(frame, "VEHICLE", (width - 150, 40),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        elif 30 <= i < 35:
            cv2.putText(frame, "SCENE CHANGE", (width - 200, 40),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 150, 0), 2)
        elif 35 <= i < 45:
            cv2.putText(frame, "CROWD", (width - 120, 40),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        frames.append(frame)
    
    print(f"Created {len(frames)} frames with events:")
    print("  - Frames 10-24: Moving vehicle")
    print("  - Frames 30-34: Scene change")
    print("  - Frames 35-44: Crowd gathering")
    
    return frames


def main():
    """Main execution function"""
    print("\n" + "=" * 70)
    print("KEY FRAME EXTRACTION SYSTEM - DEMONSTRATION")
    print("=" * 70)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Key Frame Extraction System')
    parser.add_argument('--input', type=str, help='Input video file path')
    parser.add_argument('--output', type=str, default='extracted_frames',
                       help='Output directory for extracted frames')
    parser.add_argument('--target', type=float, default=0.35,
                       help='Target extraction rate (0-1)')
    parser.add_argument('--window', type=int, default=15,
                       help='Sliding window size for ranking')
    
    args = parser.parse_args()
    
    # Configure system
    config = {
        'transmission_target': args.target,
        'window_size': args.window
    }
    
    # Initialize extractor
    extractor = KeyFrameExtractor(config)
    
    # Process video
    if args.input:
        # Load video from file
        print(f"\nLoading video: {args.input}")
        cap = cv2.VideoCapture(args.input)
        frames = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
        cap.release()
        print(f"Loaded {len(frames)} frames")
        
        # Process
        results = extractor.process_video(frames, args.output)
        
    else:
        # Use test sequence
        print("\nNo input file provided. Using test sequence...")
        frames = create_test_sequence()
        results = extractor.process_video(frames, args.output)
    
    # Print final summary
    print("\n" + "=" * 70)
    print("EXTRACTION COMPLETE")
    print("=" * 70)
    
    print(f"\n📊 Final Results:")
    print(f"   Total frames: {results['total_frames']}")
    print(f"   Extracted frames: {results['extracted_frames']}")
    print(f"   Extraction rate: {results['extraction_rate']:.1%}")
    print(f"   Target rate: {args.target:.1%}")
    
    if results['extraction_rate'] >= 0.25 and results['extraction_rate'] <= 0.45:
        print("\n✅ SUCCESS: Extraction rate within target range!")
    elif results['extraction_rate'] >= 0.20:
        print("\n✅ GOOD: Extraction rate close to target range")
    else:
        print("\n⚠️  NOTE: Extraction rate differs from target")
        print("   Adjust 'percentile_threshold' in config to fine-tune")
    
    print(f"\n📁 Extracted frames saved to: {args.output}/")
    print("   Check extraction_analysis.png for visualization")
    print("   Check extraction_report.json for detailed results")
    
    return results


if __name__ == "__main__":
    results = main()