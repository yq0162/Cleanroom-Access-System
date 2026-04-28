# --- START OF FILE hand_washing_detector.py ---

import cv2
import numpy as np
import os
import threading
import time
import logging
import pickle
from pathlib import Path
import queue
from datetime import datetime, timezone
from config import CONFIG

# Set up logging
logger = logging.getLogger(__name__)

# Import picamera2 with fallback
PICAMERA_AVAILABLE = False
try:
    from picamera2 import Picamera2
    PICAMERA_AVAILABLE = True
    logger.info("Picamera2 library available")
except ImportError:
    logger.warning("Picamera2 not available, will use mock camera or OpenCV")

# Import face_recognition with fallback
FACE_RECOGNITION_AVAILABLE = False
FACE_RECOGNITION_LOCK = threading.Lock()  # Add thread safety lock
try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
    logger.info("face_recognition library available")
except ImportError:
    logger.warning("face_recognition not available, will use mock face detection")

class HandWashingDetector:
    def __init__(self):
        """Initialize HandWashingDetector with camera and face recognition"""
        # Get configuration values
        hw_config = CONFIG.get('hand_washing', {})
        self.enable_mock = hw_config.get('enable_mock', False)
        self.min_washing_time = hw_config.get('min_washing_time', 30.0)  # seconds
        self.face_detection_interval = hw_config.get('face_detection_interval', 2.0)  # seconds - optimized for better FPS
        
        # Get camera configuration
        camera_config = CONFIG.get('camera', {})
        self.camera_resolution = camera_config.get('resolution', [1280, 720])
        self.camera_format = camera_config.get('format', 'RGB888')
        self.camera_mock = camera_config.get('enable_mock', False)
        
        # Face recognition setup
        self.face_encodings_file = "face_encodings.pkl"
        self.known_face_encodings = []
        self.known_face_names = []
        self.load_face_encodings()
        
        # NOTE: Removed MediaPipe hands setup since we only need face detection
        
        # Components
        self.camera = None
        self.running = False
        self.detector_thread = None
        self.frame = None
        self.annotated_frame = None
        self.callback = None
        self.critical_error_callback = None
        
        # Detection state - simplified to face-only
        self.current_worker = None
        self.washing_start_time = None
        self.washing_duration = 0.0
        self.face_detected = False
        self.washing_active = False
        
        # Frame processing
        self.frame_queue = queue.Queue(maxsize=3)
        self.last_face_detection_time = 0
        
        # Error handling
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Washing quality thresholds
        self.quality_thresholds = {
            'good': 30.0,     # 30+ seconds = good
            'moderate': 15.0,  # 15-30 seconds = moderate
            'bad': 0.0        # < 15 seconds = bad
        }
        
        # Operating mode
        self.camera_only_mode = False  # Track if running in camera-only mode
        
        logger.info(f"HandWashingDetector initialized (Mock: {self.enable_mock}, Face-only mode)")
    
    def load_face_encodings(self):
        """Load known face encodings from file"""
        try:
            if os.path.exists(self.face_encodings_file):
                with open(self.face_encodings_file, 'rb') as f:
                    data = pickle.load(f)
                    self.known_face_encodings = data.get('encodings', [])
                    self.known_face_names = data.get('names', [])
                logger.info(f"Loaded {len(self.known_face_encodings)} face encodings")
            else:
                logger.info("No face encodings file found, starting with empty database")
        except Exception as e:
            logger.error(f"Error loading face encodings: {e}", exc_info=True)
            self.known_face_encodings = []
            self.known_face_names = []
    
    def save_face_encodings(self):
        """Save face encodings to file"""
        try:
            data = {
                'encodings': self.known_face_encodings,
                'names': self.known_face_names
            }
            with open(self.face_encodings_file, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"Saved {len(self.known_face_encodings)} face encodings")
            return True
        except Exception as e:
            logger.error(f"Error saving face encodings: {e}", exc_info=True)
            return False
    
    def add_worker_face(self, worker_name, face_image):
        """Add a new worker's face to the recognition database"""
        if not FACE_RECOGNITION_AVAILABLE:
            logger.error("Face recognition library not available - cannot add face")
            return False
        
        try:
            logger.info(f"Attempting to register face for worker: {worker_name}")
            
            # Make a deep copy of the image to prevent memory corruption
            face_image_copy = face_image.copy()
            
            # Convert BGR to RGB if needed
            if len(face_image_copy.shape) == 3 and face_image_copy.shape[2] == 3:
                rgb_image = cv2.cvtColor(face_image_copy, cv2.COLOR_BGR2RGB)
            else:
                rgb_image = face_image_copy.copy()
            
            logger.debug("Image converted to RGB, detecting faces...")
            
            # Use thread lock to ensure face_recognition calls are thread-safe
            with FACE_RECOGNITION_LOCK:
                # Find face locations in the image
                face_locations = face_recognition.face_locations(rgb_image, model="hog")
                if not face_locations:
                    logger.debug("No face detected in the provided image")
                    return False
                
                logger.debug(f"Found {len(face_locations)} face(s) in image")
                
                # Generate face encodings
                face_encodings = face_recognition.face_encodings(rgb_image, face_locations)
                if not face_encodings:
                    logger.warning("Could not generate face encoding from detected face")
                    return False
                
                # Use the first detected face (usually the largest/most prominent)
                face_encoding = face_encodings[0]
                logger.debug("Face encoding generated successfully")
            
            return face_encoding  # Return the encoding instead of saving immediately
            
        except Exception as e:
            logger.error(f"Error processing face image: {e}", exc_info=True)
            return False

    def add_worker_face_multiple(self, worker_name, face_encodings_list):
        """Add multiple face encodings for a worker (for better accuracy)"""
        if not face_encodings_list:
            logger.warning("No face encodings provided")
            return False
        
        try:
            logger.info(f"Adding {len(face_encodings_list)} face encodings for worker: {worker_name}")
            
            # Check if worker already exists
            if worker_name in self.known_face_names:
                # Remove existing encodings for this worker
                indices_to_remove = []
                for i, name in enumerate(self.known_face_names):
                    if name == worker_name:
                        indices_to_remove.append(i)
                
                # Remove in reverse order to maintain indices
                for i in reversed(indices_to_remove):
                    del self.known_face_encodings[i]
                    del self.known_face_names[i]
                
                logger.info(f"Removed existing encodings for worker: {worker_name}")
            
            # Add all new encodings
            for encoding in face_encodings_list:
                self.known_face_encodings.append(encoding)
                self.known_face_names.append(worker_name)
            
            logger.info(f"Added {len(face_encodings_list)} face encodings for worker: {worker_name}")
            
            # Save to file
            save_success = self.save_face_encodings()
            if save_success:
                logger.info(f"Face registration completed successfully for {worker_name}")
            else:
                logger.warning(f"Face registered in memory but failed to save to file for {worker_name}")
            
            return save_success
            
        except Exception as e:
            logger.error(f"Error adding multiple worker faces: {e}", exc_info=True)
            return False
    
    def remove_worker_face(self, worker_name):
        """Remove a worker's face from the recognition database"""
        try:
            # Remove ALL encodings for this worker (not just the first one)
            indices_to_remove = []
            for i, name in enumerate(self.known_face_names):
                if name == worker_name:
                    indices_to_remove.append(i)
            
            if indices_to_remove:
                # Remove in reverse order to maintain indices
                for i in reversed(indices_to_remove):
                    del self.known_face_encodings[i]
                    del self.known_face_names[i]
                
                logger.info(f"Removed {len(indices_to_remove)} face encodings for worker: {worker_name}")
                return self.save_face_encodings()
            else:
                logger.warning(f"Worker {worker_name} not found in face database")
                return False
        except Exception as e:
            logger.error(f"Error removing worker face: {e}", exc_info=True)
            return False
    
    def clean_face_database(self):
        """Clean up face database by removing invalid entries"""
        try:
            # Remove test entries and duplicates
            clean_encodings = []
            clean_names = []
            
            # Keep track of workers we've seen
            worker_count = {}
            
            for i, name in enumerate(self.known_face_names):
                # Skip test workers
                if 'Test' in name or 'test' in name:
                    logger.info(f"Removing test worker entry: {name}")
                    continue
                
                # Limit encodings per worker to prevent database bloat
                if name not in worker_count:
                    worker_count[name] = 0
                
                if worker_count[name] < 5:  # Maximum 5 encodings per worker
                    clean_encodings.append(self.known_face_encodings[i])
                    clean_names.append(name)
                    worker_count[name] += 1
                else:
                    logger.info(f"Limiting encodings for {name} to 5 (skipping extra)")
            
            # Update the lists
            self.known_face_encodings = clean_encodings
            self.known_face_names = clean_names
            
            logger.info(f"Cleaned database: {len(clean_encodings)} encodings for {len(worker_count)} workers")
            return self.save_face_encodings()
            
        except Exception as e:
            logger.error(f"Error cleaning face database: {e}", exc_info=True)
            return False
    
    def get_worker_statistics(self):
        """Get statistics about registered workers"""
        try:
            worker_count = {}
            for name in self.known_face_names:
                worker_count[name] = worker_count.get(name, 0) + 1
            
            return worker_count
        except Exception as e:
            logger.error(f"Error getting worker statistics: {e}", exc_info=True)
            return {}
    
    def _init_camera(self):
        """Initialize camera with picamera2 support and error handling"""
        if self.camera_mock:
            logger.info("Using mock camera for hand washing detection")
            return True
        
        try:
            # Try Picamera2 first (for Raspberry Pi)
            if PICAMERA_AVAILABLE:
                logger.info("Initializing Picamera2 for hand washing detection")
                self.camera = Picamera2()
                
                # Configure camera with error checking
                try:
                    # Get available configurations
                    configs = self.camera.sensor_modes
                    if not configs:
                        logger.warning("No camera sensor modes available")
                        raise RuntimeError("No camera configurations available")
                    
                    # Configure camera with preview mode for portrait orientation
                    # Use portrait resolution (height > width)
                    width, height = self.camera_resolution
                    if width > height:
                        # Swap for portrait mode
                        self.camera_resolution = (height, width)
                        logger.info(f"Adjusted resolution for portrait mode: {self.camera_resolution}")
                    
                    self.camera.preview_configuration.main.size = tuple(self.camera_resolution)
                    self.camera.preview_configuration.main.format = self.camera_format
                    self.camera.preview_configuration.align()
                    self.camera.configure("preview")
                    
                    # Start the camera with timeout
                    self.camera.start()
                    
                    # Test camera by capturing a frame
                    time.sleep(0.5)  # Allow camera to warm up
                    test_frame = self.camera.capture_array()
                    if test_frame is None or test_frame.size == 0:
                        raise RuntimeError("Camera produced empty test frame")
                    
                    logger.info("Picamera2 initialized and tested successfully for hand washing detection")
                    return True
                    
                except Exception as e:
                    logger.error(f"Picamera2 configuration error: {e}")
                    if self.camera:
                        try:
                            self.camera.stop()
                        except:
                            pass
                        self.camera = None
                    # Fall through to OpenCV camera
            
            # Fall back to OpenCV camera
            logger.info("Initializing OpenCV camera for hand washing detection")
            # Try different camera indices
            for camera_id in range(3):
                try:
                    self.camera = cv2.VideoCapture(camera_id)
                    if not self.camera.isOpened():
                        self.camera.release()
                        continue
                    
                    # Configure camera for portrait mode
                    width, height = self.camera_resolution
                    if width > height:
                        # Swap for portrait mode
                        self.camera_resolution = (height, width)
                        logger.info(f"Adjusted resolution for portrait mode: {self.camera_resolution}")
                    
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_resolution[0])
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_resolution[1])
                    self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    # Optimize: Additional settings for better FPS
                    self.camera.set(cv2.CAP_PROP_FPS, 30)  # Set target FPS
                    self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))  # Use MJPG codec for better performance
                    
                    # Disable auto-exposure for consistent performance (optional)
                    try:
                        self.camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # Manual exposure
                    except:
                        pass  # Not all cameras support this
                    
                    # Test camera
                    ret, test_frame = self.camera.read()
                    if ret and test_frame is not None and test_frame.size > 0:
                        logger.info(f"OpenCV camera {camera_id} initialized for hand washing detection")
                        return True
                    else:
                        self.camera.release()
                
                except Exception as e:
                    logger.warning(f"Failed to initialize camera {camera_id}: {e}")
                    if self.camera:
                        self.camera.release()
                        self.camera = None
            
            # If no camera worked, fall back to mock
            logger.error("Failed to initialize any camera, switching to mock mode")
            self.camera_mock = True
            return True
            
        except Exception as e:
            logger.error(f"Error initializing camera: {e}", exc_info=True)
            self.camera_mock = True
            return True
    
    def start(self, callback=None, critical_error_callback=None, camera_only=False):
        """Start hand washing detection
        
        Args:
            callback: Callback for hand washing events (ignored in camera_only mode)
            critical_error_callback: Callback for critical errors
            camera_only: If True, only start camera without hand washing detection logic
        """
        with self._lock:
            if self.running:
                logger.warning("Hand washing detection already running")
                return False
            
            if camera_only:
                logger.info("Starting camera in camera-only mode (no hand washing detection)")
            else:
                logger.info("Starting hand washing detection...")
            
            # Initialize camera
            if self.camera is None and not self.camera_mock:
                if not self._init_camera():
                    logger.error("Failed to initialize camera")
                    return False
            
            self.callback = callback if not camera_only else None  # Don't use callback in camera-only mode
            self.critical_error_callback = critical_error_callback
            self.running = True
            self.camera_only_mode = camera_only  # Store the mode
            
            # Reset detection state
            self.current_worker = None
            self.washing_start_time = None
            self.washing_duration = 0.0
            self.face_detected = False
            self.washing_active = False
            self.consecutive_errors = 0
            
            # Clear frame queue
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
        
        # Start detection thread
        self.detector_thread = threading.Thread(target=self._detection_loop, name="HandWashingThread")
        self.detector_thread.daemon = True
        self.detector_thread.start()
        
        mode_text = "camera-only mode" if camera_only else "full hand washing detection"
        logger.info(f"Hand washing detection thread started in {mode_text}")
        return True
    
    def stop(self):
        """Stop hand washing detection"""
        with self._lock:
            if not self.running:
                logger.debug("Hand washing detection already stopped")
                return
            
            logger.info("Stopping hand washing detection and camera...")
            self.running = False
        
        # Wait for thread to finish
        if self.detector_thread and self.detector_thread.is_alive():
            logger.debug("Waiting for hand washing detection thread to finish...")
            self.detector_thread.join(timeout=3.0)
            if self.detector_thread.is_alive():
                logger.warning("Hand washing detection thread did not terminate in time")
            else:
                logger.debug("Hand washing detection thread finished successfully")
            self.detector_thread = None
        
        # Cleanup camera
        self._cleanup_camera()
        
        # Reset operating mode
        self.camera_only_mode = False
        
        logger.info("Hand washing detection and camera stopped")
    
    def _cleanup_camera(self):
        """Safely cleanup camera resources"""
        if self.camera:
            try:
                logger.info("Releasing camera resources...")
                if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                    if hasattr(self.camera, 'started') and self.camera.started:
                        self.camera.stop()
                elif hasattr(self.camera, 'release'):
                    self.camera.release()
                logger.info("Camera resources released successfully")
            except Exception as e:
                logger.error(f"Error releasing hand washing camera: {e}", exc_info=True)
            finally:
                self.camera = None
        else:
            logger.debug("No camera to cleanup (was in mock mode or already released)")
    
    def _detect_face(self, frame):
        """Detect and recognize faces in the frame"""
        # Only use mock detection if explicitly enabled in config or if face_recognition is not available
        if self.enable_mock or not FACE_RECOGNITION_AVAILABLE:
            if self.enable_mock:
                logger.debug("Using mock face detection (enabled in config)")
            else:
                logger.debug("Using mock face detection (face_recognition not available)")
            
            # Mock face detection - simulate worker presence at sink
            timestamp = int(time.time())
            cycle_time = timestamp % 15
            if cycle_time < 10:  # Face present for 10 seconds, absent for 5
                return "Mock Worker"
            else:
                return None
        
        try:
            # Make a deep copy of the frame to prevent memory corruption
            frame_copy = frame.copy()
            
            # Optimize: Resize frame for faster face detection (smaller = faster)
            height, width = frame_copy.shape[:2]
            if width > 480:  # Reduced from 640 for even better performance
                scale_factor = 480 / width
                new_width = 480
                new_height = int(height * scale_factor)
                detection_frame = cv2.resize(frame_copy, (new_width, new_height))
            else:
                detection_frame = frame_copy
            
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(detection_frame, cv2.COLOR_BGR2RGB)
            
            # Use thread lock to ensure face_recognition calls are thread-safe
            with FACE_RECOGNITION_LOCK:
                # Find faces in the frame (use faster but less accurate model for real-time)
                face_locations = face_recognition.face_locations(rgb_frame, model="hog", number_of_times_to_upsample=0)
                
                if not face_locations:
                    return None
                
                # If we have no known faces registered, just return that a face was detected
                if not self.known_face_encodings:
                    logger.debug("Face detected but no registered workers for comparison")
                    return "Unregistered Worker"
                
                # Get face encodings for detected faces (only for the first/largest face for speed)
                face_encodings = face_recognition.face_encodings(rgb_frame, [face_locations[0]])  # Only process first face
                
                if not face_encodings:
                    return None
                
                # Compare with known faces using distance for better accuracy
                face_encoding = face_encodings[0]
                
                # Calculate face distances for better matching
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                
                # Find the best match with stricter tolerance
                tolerance = 0.6  # More strict tolerance for better accuracy
                best_match_index = None
                best_distance = float('inf')
                
                # Find all matches within tolerance and pick the best one
                for i, distance in enumerate(face_distances):
                    if distance < tolerance and distance < best_distance:
                        best_distance = distance
                        best_match_index = i
                
                if best_match_index is not None:
                    # Calculate confidence score for each unique worker
                    worker_scores = {}
                    for i, distance in enumerate(face_distances):
                        if distance < tolerance:
                            worker_name = self.known_face_names[i]
                            if worker_name not in worker_scores:
                                worker_scores[worker_name] = []
                            worker_scores[worker_name].append(1.0 - distance)  # Convert distance to confidence
                    
                    # Find worker with highest average confidence
                    best_worker = None
                    best_avg_confidence = 0
                    
                    for worker_name, confidences in worker_scores.items():
                        avg_confidence = sum(confidences) / len(confidences)
                        if avg_confidence > best_avg_confidence:
                            best_avg_confidence = avg_confidence
                            best_worker = worker_name
                    
                    if best_worker and best_avg_confidence > 0.4:  # Minimum confidence threshold
                        logger.debug(f"Recognized worker: {best_worker} (confidence: {best_avg_confidence:.3f}, distance: {best_distance:.3f})")
                        return best_worker
                    else:
                        logger.debug(f"Face match too weak: best confidence {best_avg_confidence:.3f}")
                        return "Unknown Worker"
                
                # Face detected but not recognized
                logger.debug("Face detected but not recognized - no matches within tolerance")
                return "Unknown Worker"
            
        except Exception as e:
            logger.error(f"Error in face detection: {e}", exc_info=True)
            return None
    
    def _capture_mock_frame(self):
        """Generate mock frame for hand washing - simplified for face-only detection"""
        # Use portrait orientation (height > width)
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)  # Portrait: 720x1280
        
        # Create gradient background
        for y in range(1280):
            intensity = int(60 + (y / 1280) * 40)
            frame[y, :] = [intensity//3, intensity//2, intensity]
        
        # Add visual elements
        timestamp = int(time.time())
        
        if self.camera_only_mode:
            # Camera-only mode display
            cv2.putText(frame, "CAMERA ONLY MODE", (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            cv2.putText(frame, "FACE REGISTRATION", (50, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            cv2.putText(frame, "Ready for registration", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            
            # Draw face detection area (centered for portrait)
            cv2.circle(frame, (360, 640), 120, (0, 255, 0), 3)
            cv2.putText(frame, "POSITION", (280, 500), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(frame, "FACE HERE", (280, 540), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            
            # Simulate face presence occasionally for demo
            cycle_time = timestamp % 10
            if cycle_time < 6:  # Face present 60% of the time
                cv2.circle(frame, (360, 640), 80, (255, 255, 0), 3)
                cv2.putText(frame, "FACE DETECTED", (220, 800), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
        else:
            # Full hand washing monitoring mode display
            # Draw sink area (adjusted for portrait)
            cv2.rectangle(frame, (150, 1000), (570, 1200), (200, 200, 200), -1)
            cv2.putText(frame, "SINK AREA", (280, 1100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
            
            # Simulate worker presence at sink (face detection)
            cycle_time = timestamp % 15  # 15 second cycle
            face_present = cycle_time < 10  # Face present for 10 seconds, absent for 5
            
            if face_present:
                # Draw face detection (positioned for portrait)
                cv2.circle(frame, (360, 400), 80, (0, 255, 0), 3)  # Green circle for face
                cv2.putText(frame, "MOCK WORKER", (220, 340), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, "FACE DETECTED", (230, 600), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, "WASHING", (280, 650), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            else:
                cv2.putText(frame, "NO WORKER", (250, 600), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                cv2.putText(frame, "DETECTED", (260, 650), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            
            # Add title and info for hand washing mode
            cv2.putText(frame, "HAND WASHING", (150, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            cv2.putText(frame, "MONITOR", (250, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            cv2.putText(frame, f"Cycle: {cycle_time}/15", (50, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        # Common elements
        cv2.putText(frame, f"Time: {timestamp}", (50, 1250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        return frame
    
    def _detection_loop(self):
        """Main detection loop"""
        logger.info("Hand washing detection loop started")
        
        while self.running:
            try:
                # Capture frame
                if self.camera_mock:
                    frame = self._capture_mock_frame()
                else:
                    # Handle different camera types
                    if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                        try:
                            frame = self.camera.capture_array()
                            if frame is None or frame.size == 0:
                                raise RuntimeError("Picamera2 produced empty frame")
                        except Exception as e:
                            logger.error(f"Picamera2 capture error: {e}")
                            self.consecutive_errors += 1
                            if self.consecutive_errors >= 5:
                                logger.error("Too many camera errors, switching to mock mode")
                                self.camera_mock = True
                                self._cleanup_camera()
                            time.sleep(0.1)
                            continue
                    else:
                        # OpenCV camera
                        # Optimize: Clear camera buffer to get latest frame (reduces lag)
                        for _ in range(3):  # Read more frames to get the very latest
                            ret, frame = self.camera.read()
                            if not ret or frame is None:
                                break
                        
                        if not ret or frame is None:
                            self.consecutive_errors += 1
                            if self.consecutive_errors >= 5:
                                logger.error("Too many camera errors, switching to mock mode")
                                self.camera_mock = True
                                self._cleanup_camera()
                            time.sleep(0.1)
                            continue
                
                # Apply rotation correction based on camera type and configuration
                camera_config = CONFIG.get('camera', {})
                height, width = frame.shape[:2]
                
                # Check if rotation should be applied
                should_rotate = False
                if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                    # Raspberry Pi camera
                    should_rotate = camera_config.get('auto_rotate_raspberry_pi', True) and width > height
                    camera_type = "Raspberry Pi"
                else:
                    # Webcam/OpenCV camera
                    should_rotate = camera_config.get('auto_rotate_webcam', False) and width > height
                    camera_type = "webcam"
                
                if should_rotate:
                    # Rotate to portrait mode (90 degrees clockwise)
                    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                    logger.debug(f"Rotated {camera_type} camera frame from {width}x{height} to portrait mode")
                
                # Reset error counter on successful frame
                self.consecutive_errors = 0
                
                # Store frame
                with self._lock:
                    self.frame = frame.copy()
                
                current_time = time.time()
                
                # Face detection (less frequent)
                if current_time - self.last_face_detection_time >= self.face_detection_interval:
                    detected_worker = self._detect_face(frame)
                    self.face_detected = detected_worker is not None
                    
                    # Handle worker detection events (only if not in camera-only mode)
                    if not self.camera_only_mode:
                        if detected_worker and detected_worker not in ["Unknown Worker", "Unregistered Worker"]:
                            if self.current_worker != detected_worker:
                                self.current_worker = detected_worker
                                logger.info(f"Worker detected: {detected_worker}")
                                if self.callback:
                                    try:
                                        self.callback('worker_detected', detected_worker)
                                    except Exception as e:
                                        logger.error(f"Error in worker detection callback: {e}")
                        elif detected_worker in ["Unknown Worker", "Unregistered Worker"]:
                            # Face detected but not recognized
                            if self.current_worker != detected_worker:
                                self.current_worker = detected_worker
                                logger.info(f"Face detected but {detected_worker.lower()}")
                                if self.callback:
                                    try:
                                        self.callback('worker_detected', detected_worker)
                                    except Exception as e:
                                        logger.error(f"Error in worker detection callback: {e}")
                        elif not detected_worker and self.current_worker:
                            # No face detected, clear current worker
                            logger.info("No face detected, clearing current worker")
                            self.current_worker = None
                    
                    self.last_face_detection_time = current_time
                
                # Update washing state (only if not in camera-only mode)
                if not self.camera_only_mode:
                    self._update_washing_state(self.face_detected, current_time)
                
                # Create annotated frame - mirror the camera feed first, then add annotations
                mirrored_frame = cv2.flip(frame, 1)  # Mirror for natural camera experience
                annotated_frame = mirrored_frame.copy()
                self._add_annotations(annotated_frame)
                
                with self._lock:
                    self.annotated_frame = annotated_frame
                
                # Put frame in queue for display (optimize queue management)
                try:
                    # Clear old frames to prevent lag
                    while not self.frame_queue.empty():
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            break
                    self.frame_queue.put_nowait(annotated_frame.copy())
                except Exception as e:
                    logger.debug(f"Frame queue error: {e}")
                
            except Exception as e:
                self.consecutive_errors += 1
                logger.error(f"Error in hand washing detection loop: {e}", exc_info=True)
                
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.error("Too many consecutive errors, stopping detection")
                    break
                
                time.sleep(0.5)
            
            time.sleep(0.02)  # ~50 FPS potential - faster than before
        
        logger.info("Hand washing detection loop ended")
    
    def _update_washing_state(self, face_detected, current_time):
        """Update hand washing state based on face detection"""
        if face_detected and not self.washing_active:
            # Start washing
            self.washing_active = True
            self.washing_start_time = current_time
            logger.info("Hand washing started")
            if self.callback:
                try:
                    self.callback('washing_started', None)
                except Exception as e:
                    logger.error(f"Error in washing start callback: {e}")
        
        elif not face_detected and self.washing_active:
            # Stop washing
            self.washing_active = False
            if self.washing_start_time:
                self.washing_duration = current_time - self.washing_start_time
                quality = self._get_washing_quality(self.washing_duration)
                logger.info(f"Hand washing stopped. Duration: {self.washing_duration:.1f}s, Quality: {quality}")
                
                if self.callback:
                    try:
                        self.callback('washing_completed', {
                            'worker': self.current_worker,
                            'duration': self.washing_duration,
                            'quality': quality,
                            'timestamp': datetime.now(timezone.utc)
                        })
                    except Exception as e:
                        logger.error(f"Error in washing completion callback: {e}")
        
        # Update current duration if washing is active
        if self.washing_active and self.washing_start_time:
            self.washing_duration = current_time - self.washing_start_time
        
        self.face_detected = face_detected
    
    def _get_washing_quality(self, duration):
        """Determine washing quality based on duration"""
        if duration >= self.quality_thresholds['good']:
            return 'good'
        elif duration >= self.quality_thresholds['moderate']:
            return 'moderate'
        else:
            return 'bad'
    
    def _add_annotations(self, frame):
        """Add visual annotations to the frame"""
        try:
            if self.camera_only_mode:
                # Simple annotations for camera-only mode
                cv2.putText(frame, "CAMERA ONLY MODE", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
                cv2.putText(frame, "Face Registration Ready", (10, 70), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                
                # Add mode indicator
                mode_text = f"Mode: {'Mock' if (self.enable_mock or self.camera_mock) else 'Live'} Camera Only"
                cv2.putText(frame, mode_text, (10, frame.shape[0] - 20), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
                return
            
            # Full annotations for hand washing monitoring mode
            # Add current worker info
            if self.current_worker:
                cv2.putText(frame, f"Worker: {self.current_worker}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            else:
                cv2.putText(frame, "No worker detected", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            
            # Add washing status and duration
            if self.washing_active:
                cv2.putText(frame, f"WASHING: {self.washing_duration:.1f}s", (10, 70), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
                
                # Add quality indicator
                quality = self._get_washing_quality(self.washing_duration)
                quality_color = {
                    'good': (0, 255, 0),
                    'moderate': (0, 255, 255), 
                    'bad': (0, 0, 255)
                }.get(quality, (255, 255, 255))
                
                cv2.putText(frame, f"Quality: {quality.upper()}", (10, 110), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, quality_color, 2)
            else:
                cv2.putText(frame, "NOT WASHING", (10, 70), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (128, 128, 128), 2)
            
            # Add mode indicator
            mode_text = f"Mode: {'Mock' if (self.enable_mock or self.camera_mock) else 'Live'}"
            cv2.putText(frame, mode_text, (10, frame.shape[0] - 20), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
                       
        except Exception as e:
            logger.warning(f"Error adding annotations: {e}")
    
    def get_current_frame(self):
        """Get the current annotated frame"""
        try:
            if not self.frame_queue.empty():
                return self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        
        with self._lock:
            return self.annotated_frame.copy() if self.annotated_frame is not None else None
    
    def get_status(self):
        """Get current detector status"""
        with self._lock:
            if not self.running:
                return {
                    'status': 'Stopped',
                    'worker': None,
                    'washing': False,
                    'duration': 0.0,
                    'quality': None
                }
            
            return {
                'status': 'Mock Mode' if (self.enable_mock or self.camera_mock) else 'Running',
                'worker': self.current_worker,
                'washing': self.washing_active,
                'duration': self.washing_duration,
                'quality': self._get_washing_quality(self.washing_duration) if self.washing_active else None
            }
    
    def get_known_workers(self):
        """Get list of workers with registered faces"""
        return self.known_face_names.copy()

# For testing as standalone script
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s')
    
    def on_event(event_type, data):
        print(f"Event: {event_type}, Data: {data}")
    
    # Create detector
    detector = HandWashingDetector()
    
    try:
        if detector.start(on_event):
            print(f"Hand washing detector started. Status: {detector.get_status()}")
            
            # Run for 60 seconds
            print("Running for 60 seconds...")
            for i in range(60):
                time.sleep(1)
                status = detector.get_status()
                print(f"Second {i+1}/60 - Status: {status}")
        else:
            print("Failed to start detector")
    
    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        detector.stop()
        print("Test completed")

# --- END OF FILE hand_washing_detector.py --- 