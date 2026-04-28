import cv2
import numpy as np
import os
import threading
import time
import logging
from pathlib import Path
import queue
from config import CONFIG

# Set up logging
logger = logging.getLogger(__name__)

# Check for required packages
PICAMERA_AVAILABLE = False
YOLO_AVAILABLE = False

# Import Picamera2 with fallback
try:
    from picamera2 import Picamera2
    PICAMERA_AVAILABLE = True
except ImportError:
    logger.warning("Picamera2 not available, will use mock camera or OpenCV")

# Import YOLO with fallback
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    logger.warning("Ultralytics YOLO not available, will use mock detection")



class YOLODetector:
    def __init__(self):
        """Initialize YOLODetector with camera and model"""
        # Get configuration values
        yolo_config = CONFIG.get('yolo', {})
        self.model_path = yolo_config.get('model_path', 'models/bestsmall.onnx')
        self.fallback_model = yolo_config.get('fallback_model', 'yolov8n.pt')
        self.confidence_threshold = yolo_config.get('confidence_threshold', 0.5)
        self.detection_stability = yolo_config.get('detection_stability', 3)
        self.enable_mock = yolo_config.get('enable_mock', False)
        
        # Get PPE detection configuration
        ppe_config = CONFIG.get('ppe_detection', {}).get('required_classes', {})
        self.ppe_classes = {}
        
        # Get locked mechanism setting
        self.enable_locked_mechanism = CONFIG.get('ppe_detection', {}).get('enable_locked_mechanism', True)
        logger.info(f"PPE Locked Mechanism: {'Enabled' if self.enable_locked_mechanism else 'Disabled'}")
        
        for class_name, config in ppe_config.items():
            if config.get('enabled', False):
                self.ppe_classes[class_name] = {
                    'class_names': config.get('class_names', [class_name]),
                    'negative_classes': config.get('negative_classes', []),
                    'incomplete_classes': config.get('incomplete_classes', []),
                    'min_confidence': config.get('min_confidence', 0.5),
                    'required_count': config.get('required_count', 1),
                    'required_consecutive_frames': config.get('required_consecutive_frames', 5),
                    'detected': False,
                    'detection_count': 0,
                    'no_detection_count': 0,
                    'current_frame_count': 0,
                    'last_detection_type': 'none',  # 'positive', 'absent', 'incomplete', 'none'
                    'is_locked': False,  # New field to track locked results
                    'locked_status': 'none'  # Track what status is locked ('positive', 'absent', 'incomplete', 'none')
                }
        
        # Get camera configuration
        camera_config = CONFIG.get('camera', {})
        self.camera_resolution = camera_config.get('resolution', [1280, 720])
        self.camera_format = camera_config.get('format', 'RGB888')
        self.camera_mock = camera_config.get('enable_mock', False)
        
        # Components
        self.model = None
        self.camera = None
        self.running = False
        self.detector_thread = None
        self.frame = None
        self.annotated_frame = None
        self.using_hailo = False  # Track whether we're using Hailo or Ultralytics
        
        # Legacy hairnet detection for backward compatibility
        self.hairnet_detected = False
        self.callback = None
        
        # Frame processing queue with better management
        self.frame_queue = queue.Queue(maxsize=3)  # Slightly larger queue
        
        # Detection stabilization - legacy for hairnet
        self.hairnet_detected_count = 0
        self.no_hairnet_detected_count = 0
        
        # Error handling
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        self.error_threshold_for_mock = 5
        self.critical_error_callback = None  # Callback for critical errors
        self.error_recovery_attempts = 0
        self.max_recovery_attempts = 3
        
        # Thread safety
        self._lock = threading.RLock()  # Use RLock for nested locking
        
        # Frame validation
        self.min_frame_width = 160
        self.min_frame_height = 120
        
        logger.info(f"YOLODetector initialized (Mock: {self.enable_mock})")
        logger.info(f"PPE Classes to detect: {list(self.ppe_classes.keys())}")
        
        # Check if model exists
        if os.path.exists(self.model_path):
            logger.info(f"YOLO model found at: {self.model_path}")
        else:
            logger.warning(f"YOLO model not found at: {self.model_path}")
            logger.info(f"Will use fallback model: {self.fallback_model}")
            self.model_path = self.fallback_model
    

    
    def _load_model(self):
        """Load YOLO model with error handling and Hailo support"""
        if self.enable_mock:
            logger.info("Using mock detection mode")
            return True
        
        # Check for Hailo configuration
        hailo_config = CONFIG.get('hailo', {})
        enable_hailo = hailo_config.get('enable', False)
        model_name = hailo_config.get('model_name', 'yolov11s')
        zoo_path = hailo_config.get('zoo_path', 'models')
        
        # Try Hailo DeGirum first if enabled
        if enable_hailo:
            try:
                import degirum as dg
                logger.info(f"Attempting to load Hailo model: {model_name} from {zoo_path}")
                
                self.model = dg.load_model(
                    model_name=model_name,
                    inference_host_address="@local",
                    zoo_url=zoo_path
                )
                
                logger.info("Hailo DeGirum model loaded successfully")
                self.using_hailo = True
                return True
                
            except ImportError as e:
                logger.warning(f"DeGirum not available: {e}, falling back to Ultralytics YOLO")
            except Exception as e:
                logger.error(f"Error loading Hailo model: {e}, falling back to Ultralytics YOLO")
        
        # Fallback to Ultralytics YOLO
        if not YOLO_AVAILABLE:
            logger.error("Ultralytics YOLO package not installed")
            self.enable_mock = True
            return True
            
        try:
            logger.info(f"Loading Ultralytics YOLO model from {self.model_path}")
            
            # Try primary model first
            if os.path.exists(self.model_path):
                self.model = YOLO(self.model_path)
            else:
                logger.warning(f"Primary model not found, trying fallback: {self.fallback_model}")
                self.model = YOLO(self.fallback_model)
                
            logger.info("Ultralytics YOLO model loaded successfully")
            self.using_hailo = False
            return True
            
        except Exception as e:
            logger.error(f"Error loading YOLO model: {e}", exc_info=True)
            # Switch to mock mode on error
            logger.warning("Switching to mock detection mode due to model loading error")
            self.enable_mock = True
            return True



    def _add_ppe_status_overlay(self, frame, ppe_results):
        """Add PPE status overlay to frame"""
        try:
            if frame is None or ppe_results is None:
                return
                
            # Add PPE detection status
            y_offset = 30
            for ppe_class, detected in ppe_results.items():
                status_text = f"{ppe_class.upper()}: {'✓' if detected else '✗'}"
                color = (0, 255, 0) if detected else (0, 0, 255)
                cv2.putText(frame, status_text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                y_offset += 30
            
            # Add overall status
            all_detected = all(ppe_results.values())
            overall_text = "PPE COMPLIANT" if all_detected else "PPE VIOLATION"
            overall_color = (0, 255, 0) if all_detected else (0, 0, 255)
            cv2.putText(frame, overall_text, (10, y_offset + 20), cv2.FONT_HERSHEY_SIMPLEX, 1, overall_color, 2)
            
        except Exception as e:
            logger.error(f"Error adding PPE status overlay: {e}", exc_info=True)



    def _init_camera(self):
        """Initialize camera with improved error handling"""
        if self.camera_mock:
            logger.info("Using mock camera")
            return True
            
        try:
            # Try Picamera2 first (for Raspberry Pi)
            if PICAMERA_AVAILABLE:
                logger.info("Initializing Picamera2")
                
                # Enhanced Picamera2 initialization with resource cleanup
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # Force cleanup before attempting to initialize
                        if attempt > 0:
                            logger.info(f"Picamera2 initialization attempt {attempt + 1} - cleaning up first")
                            self.force_camera_cleanup()
                            time.sleep(2.0)  # Longer wait after cleanup
                        
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
                            
                            # Configure camera with preview mode
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
                            
                            logger.info("Picamera2 initialized and tested successfully")
                            return True
                            
                        except Exception as config_error:
                            logger.error(f"Picamera2 configuration error (attempt {attempt + 1}): {config_error}")
                            # Clean up the failed camera instance
                            try:
                                if self.camera and hasattr(self.camera, 'stop'):
                                    self.camera.stop()
                                if self.camera and hasattr(self.camera, 'close'):
                                    self.camera.close()
                            except:
                                pass
                            self.camera = None
                            
                            # If this was the last attempt, fall through to OpenCV
                            if attempt == max_retries - 1:
                                break
                            else:
                                continue
                    
                    except Exception as init_error:
                        logger.error(f"Picamera2 initialization error (attempt {attempt + 1}): {init_error}")
                        self.camera = None
                        
                        # If this was the last attempt, fall through to OpenCV
                        if attempt == max_retries - 1:
                            break
                        else:
                            continue
                    
                # If we get here, Picamera2 failed, fall through to OpenCV
                logger.warning("Picamera2 initialization failed after all attempts, trying OpenCV camera")
                            
            # Fall back to OpenCV camera with enhanced configuration
            logger.info("Initializing OpenCV camera")
            for camera_id in range(3):  # Try multiple camera indices
                try:
                    self.camera = cv2.VideoCapture(camera_id)
                    if not self.camera.isOpened():
                        self.camera.release()
                        continue
                    
                    # Configure camera for portrait mode (same logic as Picamera)
                    width, height = self.camera_resolution
                    if width > height:
                        # Swap for portrait mode
                        self.camera_resolution = (height, width)
                        logger.info(f"Adjusted resolution for portrait mode: {self.camera_resolution}")
                        
                    # Enhanced camera configuration (matching Picamera robustness)
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_resolution[0])
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_resolution[1])
                    self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce buffer for lower latency
                    self.camera.set(cv2.CAP_PROP_FPS, 30)  # Set consistent frame rate
                    
                    # Allow camera to warm up (same as Picamera)
                    time.sleep(0.5)
                    
                    # Test camera with enhanced validation
                    ret, test_frame = self.camera.read()
                    if ret and test_frame is not None and test_frame.size > 0:
                        # Validate frame dimensions
                        h, w = test_frame.shape[:2]
                        if h >= self.min_frame_height and w >= self.min_frame_width:
                            logger.info(f"OpenCV camera {camera_id} initialized successfully with resolution {w}x{h}")
                            return True
                        else:
                            logger.warning(f"Camera {camera_id} frame too small: {w}x{h}")
                            self.camera.release()
                    else:
                        logger.warning(f"Camera {camera_id} failed test capture")
                        self.camera.release()
                        
                except Exception as e:
                    logger.warning(f"Failed to initialize camera {camera_id}: {e}")
                    if self.camera:
                        self.camera.release()
                        self.camera = None
                        
            # If we get here, no camera worked
            logger.error("Failed to initialize any camera")
            self.camera_mock = True
            return True
                
        except Exception as e:
            logger.error(f"Error initializing camera: {e}", exc_info=True)
            # Switch to mock mode on error
            self.camera_mock = True
            return True

    def start(self, callback=None, critical_error_callback=None):
        """Start camera and detection thread with improved error handling"""
        with self._lock:
            if self.running:
                logger.warning("Detection already running")
                return False
                
            logger.info("Starting YOLODetector...")
            
            # Load model if not loaded
            if self.model is None and not self.enable_mock:
                if not self._load_model():
                    logger.error("Failed to load YOLO model")
                    return False
            
            # Initialize camera
            if self.camera is None and not self.camera_mock:
                if not self._init_camera():
                    logger.error("Failed to initialize camera")
                    return False
            
            self.callback = callback
            self.critical_error_callback = critical_error_callback
            self.running = True
            
            # Reset detection state
            self.hairnet_detected = False
            self.hairnet_detected_count = 0
            self.no_hairnet_detected_count = 0
            self.consecutive_errors = 0
            self.error_recovery_attempts = 0
            
            # Clear frame queue
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
        
        # Start detection in separate thread
        self.detector_thread = threading.Thread(target=self._detection_loop, name="YOLODetectionThread")
        self.detector_thread.daemon = True
        self.detector_thread.start()
        
        logger.info("Detection thread started")
        return True
    
    def stop(self):
        """Stop detection and release camera with improved cleanup"""
        with self._lock:
            if not self.running:
                logger.debug("YOLODetector already stopped")
                return
                
            logger.info("Stopping YOLODetector...")
            self.running = False
        
        # Wait for detector thread to finish (only if not called from within the thread)
        current_thread = threading.current_thread()
        if (self.detector_thread and self.detector_thread.is_alive() and 
            current_thread != self.detector_thread):
            logger.debug("Waiting for detection thread to finish...")
            self.detector_thread.join(timeout=3.0)
            if self.detector_thread.is_alive():
                logger.warning("Detection thread did not terminate in time")
            else:
                logger.debug("Detection thread finished successfully")
            self.detector_thread = None
        elif current_thread == self.detector_thread:
            logger.debug("Stop called from detection thread, skipping join")
        
        # Stop the camera
        self._cleanup_camera()
                
        logger.info("YOLODetector stopped")
    
    def _cleanup_camera(self):
        """Safely cleanup camera resources"""
        if self.camera:
            try:
                if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                    logger.info("Cleaning up Picamera2 resources")
                    try:
                        # Check if camera is running and stop it
                        if hasattr(self.camera, 'started') and self.camera.started:
                            logger.debug("Stopping Picamera2...")
                            self.camera.stop()
                            time.sleep(0.2)  # Give time for stop to complete
                        
                        # Close the camera properly
                        if hasattr(self.camera, 'close'):
                            logger.debug("Closing Picamera2...")
                            self.camera.close()
                            time.sleep(0.2)  # Give time for close to complete
                            
                    except Exception as picam_error:
                        logger.warning(f"Error during Picamera2 cleanup: {picam_error}")
                        # Try force cleanup even if normal cleanup fails
                        try:
                            if hasattr(self.camera, '_cm'):
                                self.camera._cm = None
                        except:
                            pass
                    
                elif hasattr(self.camera, 'release'):
                    logger.info("Cleaning up OpenCV camera resources")
                    self.camera.release()
                    
                logger.debug("Camera stopped and released")
            except Exception as e:
                logger.error(f"Error stopping camera: {e}", exc_info=True)
            finally:
                self.camera = None
                # Add small delay to ensure cleanup completion
                time.sleep(0.1)

    def reset_ppe_locked_status(self):
        """Reset all locked PPE detection status for a new detection session"""
        with self._lock:
            for ppe_class, ppe_config in self.ppe_classes.items():
                ppe_config['is_locked'] = False
                ppe_config['locked_status'] = 'none'
                ppe_config['detected'] = False
                ppe_config['detection_count'] = 0
                ppe_config['no_detection_count'] = 0
                ppe_config['current_frame_count'] = 0
                ppe_config['last_detection_type'] = 'none'
            
            # Also reset legacy hairnet detection
            self.hairnet_detected = False
            self.hairnet_detected_count = 0
            self.no_hairnet_detected_count = 0
            
            logger.info("PPE locked status reset for new detection session")

    def reset_camera(self):
        """Reset camera to prevent resource busy issues"""
        try:
            logger.info("Resetting camera to prevent resource busy issues")
            
            # Stop current detection if running
            was_running = self.running
            if was_running:
                self.stop()
                
            # Force cleanup camera with additional steps
            self._cleanup_camera()
            
            # Force system camera resource cleanup
            self.force_camera_cleanup()
            
            # Add longer delay to ensure resources are fully released
            time.sleep(2.0)
            
            # Clear any lingering frame queues
            try:
                while not self.frame_queue.empty():
                    self.frame_queue.get_nowait()
            except queue.Empty:
                pass
            
            # Reset frame variables
            with self._lock:
                self.frame = None
                self.annotated_frame = None
            
            # Restart if it was running before
            if was_running:
                self.start(self.callback, self.critical_error_callback)
                
            logger.info("Camera reset completed successfully")
            
        except Exception as e:
            logger.error(f"Error during camera reset: {e}", exc_info=True)
            # If reset fails, try to force cleanup anyway
            try:
                self.force_camera_cleanup()
                time.sleep(1.0)
            except:
                pass
    
    def _validate_frame(self, frame):
        """Validate frame quality and dimensions"""
        if frame is None:
            return False, "Frame is None"
            
        if frame.size == 0:
            return False, "Frame is empty"
            
        if len(frame.shape) != 3 or frame.shape[2] not in [3, 4]:
            return False, f"Invalid frame shape: {frame.shape}"
            
        height, width = frame.shape[:2]
        if width < self.min_frame_width or height < self.min_frame_height:
            return False, f"Frame too small: {width}x{height}"
            
        return True, "Valid frame"
    
    def _capture_mock_frame(self):
        """Generate a mock frame for testing with better visual feedback"""
        # Use portrait orientation (height > width)
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)  # Portrait: 720x1280
        
        # Create gradient background
        for y in range(1280):
            intensity = int(40 + (y / 1280) * 60)  # Gradient from dark to lighter
            frame[y, :] = [intensity, intensity//2, intensity//3]
        
        # Alternate between showing and not showing PPE
        timestamp = int(time.time())
        cycle_time = timestamp % 20  # 20 second cycle
        show_ppe = cycle_time < 10  # Show PPE for 10 seconds, then hide for 10
        
        if show_ppe:
            # Draw mock PPE items - positioned for portrait
            y_pos = 200
            
            # Draw a "gown" (large rectangle)
            cv2.rectangle(frame, (200, y_pos), (520, y_pos + 400), (0, 255, 100), -1)
            cv2.putText(frame, "MOCK GOWN", (250, y_pos + 50), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            
            # Draw "gloves" (small rectangles for hands)
            cv2.rectangle(frame, (150, y_pos + 200), (200, y_pos + 250), (255, 100, 0), -1)
            cv2.rectangle(frame, (520, y_pos + 200), (570, y_pos + 250), (255, 100, 0), -1)
            cv2.putText(frame, "2 GLOVES", (300, y_pos + 230), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            
            # Draw a "hairnet" (blue ellipse)
            center = (360, y_pos - 50)
            cv2.ellipse(frame, center, (150, 80), 0, 0, 360, (255, 100, 0), -1)
            cv2.ellipse(frame, center, (130, 60), 0, 0, 360, (200, 150, 50), 3)
            cv2.putText(frame, "HAIRNET", (280, y_pos - 80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            
            # Draw a "mask" (rectangle over face area)
            cv2.rectangle(frame, (280, y_pos + 80), (440, y_pos + 140), (100, 255, 255), -1)
            cv2.putText(frame, "MASK", (320, y_pos + 115), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
            
            # Add detection status
            cv2.putText(frame, "STATUS: ALL PPE DETECTED", (150, 700), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            # No PPE detected - positioned for portrait
            cv2.putText(frame, "NO PPE", (280, 400), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
            cv2.putText(frame, "DETECTED", (250, 450), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
            
            # Add warning status
            cv2.putText(frame, "STATUS: PPE VIOLATION", (120, 700), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        
        # Add title and timestamp
        cv2.putText(frame, "YOLO PPE DETECTOR", (150, 80), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        cv2.putText(frame, "MOCK MODE", (240, 130), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        cv2.putText(frame, f"Time: {timestamp}", (50, 1250), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, f"Cycle: {cycle_time}/20", (50, 200), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        return frame
    
    def _detection_loop(self):
        """Main detection loop with improved error handling"""
        logger.info("Detection loop started")
        
        frame_count = 0
        fps_time = time.time()
        fps_values = []
        last_error_log_time = 0
        
        while self.running:
                try:
                    # Initialize detection results at the start of each loop iteration
                    ppe_detected_this_frame = {}
                    for class_name in self.ppe_classes:
                        ppe_detected_this_frame[class_name] = False
                    detected_this_frame = False
                    
                    # Capture frame from camera
                    if self.camera_mock:
                        # Generate mock frame
                        frame = self._capture_mock_frame()
                        # Mock camera detection with 11-class priority system
                        timestamp = int(time.time())
                        cycle_time = timestamp % 20  # 20 second cycle
                        show_ppe = cycle_time < 10  # Show PPE for 10 seconds, then hide for 10
                        
                        # Apply same 11-class logic as real detection
                        for class_name, ppe_config in self.ppe_classes.items():
                            if show_ppe:
                                ppe_detected_this_frame[class_name] = True
                                ppe_config['last_detection_type'] = 'positive'
                            else:
                                ppe_detected_this_frame[class_name] = False
                                ppe_config['last_detection_type'] = 'absent'
                        
                        detected_this_frame = show_ppe
                        
                    else:
                        # Capture from real camera
                        frame = None
                        
                        # Unified frame capture with consistent error handling
                        if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                            try:
                                frame = self.camera.capture_array()
                                if frame is None or frame.size == 0:
                                    logger.warning("Picamera2 captured empty frame")
                                    frame = None
                            except Exception as e:
                                logger.error(f"Picamera2 capture error: {e}")
                                frame = None
                        else:
                            try:
                                ret, frame = self.camera.read()
                                if not ret or frame is None or frame.size == 0:
                                    logger.warning("OpenCV camera failed to capture or captured empty frame")
                                    frame = None
                            except Exception as e:
                                logger.error(f"OpenCV camera read error: {e}")
                                frame = None
                        
                        # Apply unified rotation correction for both camera types
                        if frame is not None:
                            camera_config = CONFIG.get('camera', {})
                            height, width = frame.shape[:2]
                            
                            # Unified rotation logic for both camera types
                            should_rotate = False
                            camera_type = ""
                            
                            if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                                # Raspberry Pi camera - use unified setting
                                should_rotate = camera_config.get('auto_rotate', True) and width > height
                                camera_type = "Raspberry Pi"
                            else:
                                # Webcam/OpenCV camera - use same unified setting
                                should_rotate = camera_config.get('auto_rotate', True) and width > height
                                camera_type = "webcam/OpenCV"
                            
                            if should_rotate:
                                # Rotate to portrait mode (90 degrees clockwise)
                                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                                logger.debug(f"Rotated {camera_type} camera frame from {width}x{height} to portrait mode")
                        
                        # Validate frame
                        if frame is not None:
                            is_valid, validation_msg = self._validate_frame(frame)
                            if not is_valid:
                                logger.warning(f"Invalid frame: {validation_msg}")
                                frame = None
                        
                        if frame is None:
                            self.consecutive_errors += 1
                            current_time = time.time()
                            
                            # Log errors but not too frequently
                            if current_time - last_error_log_time > 5.0:
                                logger.warning(f"Frame capture failed ({self.consecutive_errors} consecutive errors)")
                                last_error_log_time = current_time
                            
                            # If too many errors, consider switching to mock
                            if self.consecutive_errors >= self.error_threshold_for_mock:
                                logger.error("Too many frame capture errors, switching to mock camera")
                                self.camera_mock = True
                                self._cleanup_camera()
                                self.consecutive_errors = 0
                            
                            time.sleep(0.1)
                            continue
                        
                        # Reset error counter on successful frame
                        if self.consecutive_errors > 0:
                            logger.info(f"Recovery successful after {self.consecutive_errors} errors")
                            self.consecutive_errors = 0
                        
                        # Process frame with YOLO
                        if self.enable_mock:
                            # In mock detection mode, simulate realistic PPE detection with 11-class system
                            timestamp = int(time.time())
                            cycle_time = timestamp % 20  # 20 second cycle
                            show_ppe = cycle_time < 10  # Show PPE for 10 seconds, then hide for 10
                            
                            ppe_detected_this_frame = {}
                            for class_name, ppe_config in self.ppe_classes.items():
                                if show_ppe:
                                    # During positive cycle, show positive detection
                                    ppe_detected_this_frame[class_name] = True
                                    ppe_config['last_detection_type'] = 'positive'
                                else:
                                    # During negative cycle, simulate absent detection (priority system)
                                    ppe_detected_this_frame[class_name] = False
                                    ppe_config['last_detection_type'] = 'absent'
                            
                            # Legacy hairnet detection for backward compatibility
                            detected_this_frame = show_ppe
                            
                        else:
                            # Run actual YOLO detection with 11-class priority system
                                
                            try:
                                # MIRROR FRAME FIRST for inference to prevent mirrored information display
                                mirrored_frame = cv2.flip(frame, 1)
                                
                                # Use different inference based on model type
                                if self.using_hailo:
                                    # DeGirum Hailo inference on mirrored frame
                                    hailo_result = self.model(mirrored_frame)
                                    detections = hailo_result.results if hasattr(hailo_result, 'results') else []
                                    annotated_frame = hailo_result.image_overlay if hasattr(hailo_result, 'image_overlay') else mirrored_frame.copy()
                                    
                                    # Convert DeGirum results to our format
                                    results = []
                                    
                                else:
                                    # Ultralytics YOLO inference on mirrored frame
                                    results = self.model.predict(mirrored_frame, verbose=False, conf=self.confidence_threshold)
                                    detections = []
                                    
                                    # Convert Ultralytics results to our format
                                    for r in results:
                                        if hasattr(r, 'boxes') and r.boxes is not None and len(r.boxes) > 0:
                                            boxes = r.boxes.cpu().numpy()
                                            names = getattr(r, 'names', {})
                                            
                                            for box in boxes:
                                                if hasattr(box, 'cls') and hasattr(box, 'conf'):
                                                    cls_id = int(box.cls[0])
                                                    detected_class_name = names.get(cls_id, '').lower()
                                                    confidence = float(box.conf[0])
                                                    
                                                    detections.append({
                                                        'label': detected_class_name,
                                                        'score': confidence
                                                    })
                                
                                # Initialize detection arrays for priority system: ABSENT > INCOMPLETE > POSITIVE
                                positive_detections = {}
                                absent_detections = {}
                                incomplete_detections = {}
                                
                                # Legacy hairnet detection
                                detected_this_frame = False
                                
                                # Reset frame counts for each PPE class
                                for ppe_class in self.ppe_classes:
                                    positive_detections[ppe_class] = 0
                                    absent_detections[ppe_class] = 0
                                    incomplete_detections[ppe_class] = 0
                                
                                # Process detections using unified format
                                for detection in detections:
                                    detected_class_name = detection.get('label', '').lower()
                                    confidence = detection.get('score', 0.0)
                                    
                                    # Check each PPE class with priority system
                                    for ppe_class, ppe_config in self.ppe_classes.items():
                                        # Check absent classes first (highest priority)
                                        for absent_class in ppe_config.get('negative_classes', []):
                                            if (absent_class.lower() in detected_class_name and 
                                                confidence >= ppe_config['min_confidence']):
                                                absent_detections[ppe_class] += 1
                                                break
                                        
                                        # Check incomplete classes (medium priority)
                                        for incomplete_class in ppe_config.get('incomplete_classes', []):
                                            if (incomplete_class.lower() in detected_class_name and 
                                                confidence >= ppe_config['min_confidence']):
                                                incomplete_detections[ppe_class] += 1
                                                break
                                        
                                        # Check positive classes (lowest priority)
                                        for positive_class in ppe_config['class_names']:
                                            if (positive_class.lower() in detected_class_name and 
                                                confidence >= ppe_config['min_confidence']):
                                                positive_detections[ppe_class] += 1
                                                break
                                        
                                        # Apply priority-based decision logic: ABSENT > INCOMPLETE > POSITIVE
                                        for ppe_class, ppe_config in self.ppe_classes.items():
                                            required_count = ppe_config['required_count']
                                            
                                            # Priority 1: Check for absent detection (immediate failure)
                                            if absent_detections[ppe_class] >= required_count:
                                                ppe_detected_this_frame[ppe_class] = False
                                                ppe_config['last_detection_type'] = 'absent'
                                                # Legacy compatibility for hairnet - absent means not detected
                                                if ppe_class == 'hairnet':
                                                    detected_this_frame = False
                                            # Priority 2: Check for incomplete detection (partial failure)
                                            elif incomplete_detections[ppe_class] >= required_count:
                                                ppe_detected_this_frame[ppe_class] = False
                                                ppe_config['last_detection_type'] = 'incomplete'
                                                # Legacy compatibility for hairnet - incomplete means not detected
                                                if ppe_class == 'hairnet':
                                                    detected_this_frame = False
                                            # Priority 3: Check for positive detection (success)
                                            elif positive_detections[ppe_class] >= required_count:
                                                ppe_detected_this_frame[ppe_class] = True
                                                ppe_config['last_detection_type'] = 'positive'
                                                # Legacy compatibility for hairnet
                                                if ppe_class == 'hairnet':
                                                    detected_this_frame = True
                                            else:
                                                # No sufficient detection of any type
                                                ppe_detected_this_frame[ppe_class] = False
                                                ppe_config['last_detection_type'] = 'none'
                                                if ppe_class == 'hairnet':
                                                    detected_this_frame = False
                                
                                # Create annotated frame - handled differently for Hailo vs Ultralytics
                                if not self.using_hailo:
                                    # For Ultralytics, create annotated frame from results
                                    if results and len(results) > 0:
                                        annotated_frame = results[0].plot()
                                    else:
                                        annotated_frame = mirrored_frame.copy()
                                # For Hailo, annotated_frame was already created above
                                
                                # Add PPE status overlay for both types
                                self._add_ppe_status_overlay(annotated_frame, ppe_detected_this_frame)
                                    
                            except Exception as e:
                                self.consecutive_errors += 1
                                logger.error(f"YOLO detection error ({self.consecutive_errors}/{self.max_consecutive_errors}): {e}", exc_info=True)
                                
                                # Initialize empty detection results on error
                                ppe_detected_this_frame = {}
                                for class_name in self.ppe_classes:
                                    ppe_detected_this_frame[class_name] = False
                                detected_this_frame = False
                                annotated_frame = cv2.flip(frame, 1).copy() if frame is not None else None
                                
                                # Check if we've had too many detection errors
                                if self.consecutive_errors >= self.error_threshold_for_mock:
                                    logger.error("Too many YOLO detection errors, switching to mock detection")
                                    self.enable_mock = True
                                    self.error_recovery_attempts += 1
                                    
                                    # If we've tried recovery too many times, call critical error callback
                                    if (self.error_recovery_attempts >= self.max_recovery_attempts and 
                                        self.critical_error_callback):
                                        try:
                                            self.critical_error_callback("YOLO detection system failed")
                                        except Exception as cb_error:
                                            logger.error(f"Error in critical error callback: {cb_error}")
                                
                                # Add delay to prevent error flooding
                                time.sleep(0.5)
                    
                    # Store frames (thread-safe) - already mirrored for proper display
                    with self._lock:
                        self.frame = frame.copy() if frame is not None else None
                        if 'annotated_frame' in locals() and annotated_frame is not None:
                            # Annotated frame is already mirrored from inference
                            self.annotated_frame = annotated_frame.copy()
                        elif frame is not None:
                            # Mirror the plain frame for natural mirror experience
                            mirrored_frame = cv2.flip(frame, 1)
                            self.annotated_frame = mirrored_frame.copy()
                    
                    # Update PPE detection stabilization counters
                    for ppe_class, ppe_config in self.ppe_classes.items():
                        if ppe_detected_this_frame.get(ppe_class, False):
                            ppe_config['detection_count'] += 1
                            ppe_config['no_detection_count'] = 0
                        else:
                            ppe_config['no_detection_count'] += 1
                            ppe_config['detection_count'] = 0
                    
                    # Legacy hairnet detection update
                    if detected_this_frame:
                        self.hairnet_detected_count += 1
                        self.no_hairnet_detected_count = 0
                    else:
                        self.no_hairnet_detected_count += 1
                        self.hairnet_detected_count = 0
                    
                    # Update stable detection status
                    self._update_detection_status()
                    
                    # Call callback if provided
                    if self.callback:
                        try:
                            self.callback(self.are_all_ppe_detected(), self.get_ppe_detection_status())
                        except Exception as cb_error:
                            logger.error(f"Error in detection callback: {cb_error}")
                    
                    frame_count += 1
                    
                    # Calculate FPS occasionally
                    if frame_count % 30 == 0:
                        current_time = time.time()
                        fps = 30 / (current_time - fps_time)
                        fps_values.append(fps)
                        fps_time = current_time
                        
                        if len(fps_values) > 10:
                            fps_values.pop(0)
                            
                        avg_fps = sum(fps_values) / len(fps_values)
                        logger.debug(f"Detection FPS: {avg_fps:.1f}")
                
                except Exception as e:
                    self.consecutive_errors += 1
                    logger.error(f"Error in detection loop ({self.consecutive_errors}/{self.max_consecutive_errors}): {e}", exc_info=True)
                    
                    # Check if we should trigger critical error callback
                    if (self.consecutive_errors >= self.max_consecutive_errors and 
                        self.critical_error_callback):
                        try:
                            self.critical_error_callback("Detection system failed")
                        except Exception as cb_error:
                            logger.error(f"Error in critical error callback: {cb_error}")
                    
                    # Add delay to prevent error flooding
                    time.sleep(0.5)
        
        logger.info("Detection loop finished")



    def _update_detection_status(self):
        """Update stable detection status with 5 consecutive frames requirement and locking mechanism"""
        # Get the required consecutive frames from config (should be 5 for new system)
        required_consecutive = 5
        
        # Update individual PPE detection status using enhanced consecutive frame logic with locking
        for ppe_class, ppe_config in self.ppe_classes.items():
            class_required_consecutive = ppe_config.get('required_consecutive_frames', required_consecutive)
            
            # Skip updates if this class is already locked AND locking is enabled
            if self.enable_locked_mechanism and ppe_config.get('is_locked', False):
                # Keep the locked status - don't update detected status
                if ppe_config.get('locked_status') == 'positive':
                    ppe_config['detected'] = True
                else:
                    ppe_config['detected'] = False
                continue
            
            # Check if we should lock the result based on consecutive frames (only if locking enabled)
            if ppe_config['detection_count'] >= class_required_consecutive:
                ppe_config['detected'] = True
                
                # Lock only if locking mechanism is enabled
                if self.enable_locked_mechanism:
                    ppe_config['is_locked'] = True
                    ppe_config['locked_status'] = 'positive'
                    logger.info(f"PPE {ppe_class} locked as PRESENT after {ppe_config['detection_count']} consecutive frames")
                
            elif ppe_config['no_detection_count'] >= class_required_consecutive:
                ppe_config['detected'] = False
                
                # Lock only if locking mechanism is enabled
                if self.enable_locked_mechanism:
                    ppe_config['is_locked'] = True
                    
                    # Lock the specific type based on last detection
                    if ppe_config.get('last_detection_type') == 'absent':
                        ppe_config['locked_status'] = 'absent'
                        logger.info(f"PPE {ppe_class} locked as ABSENT after {ppe_config['no_detection_count']} consecutive frames")
                    elif ppe_config.get('last_detection_type') == 'incomplete':
                        ppe_config['locked_status'] = 'incomplete'
                        logger.info(f"PPE {ppe_class} locked as INCOMPLETE after {ppe_config['no_detection_count']} consecutive frames")
                    else:
                        ppe_config['locked_status'] = 'absent'  # Default to absent
                        logger.info(f"PPE {ppe_class} locked as ABSENT (default) after {ppe_config['no_detection_count']} consecutive frames")
        
        # Legacy hairnet detection update (still uses old logic for backward compatibility)
        if self.hairnet_detected_count >= self.detection_stability:
            self.hairnet_detected = True
        elif self.no_hairnet_detected_count >= self.detection_stability:
            self.hairnet_detected = False

    def _add_status_annotations(self):
        """Add status information to the annotated frame"""
        if self.annotated_frame is None:
            return
            
        try:
            # Add FPS information
            fps_text = f'FPS: Processing'
            cv2.putText(self.annotated_frame, fps_text, (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
            
            # Add PPE detection status
            y_offset = 70
            for ppe_class, ppe_config in self.ppe_classes.items():
                if ppe_config['detected']:
                    if ppe_config.get('required_count', 1) > 1:
                        status_text = f"{ppe_class.upper()}: {ppe_config['current_frame_count']}/{ppe_config['required_count']} DETECTED"
                    else:
                        status_text = f"{ppe_class.upper()}: DETECTED"
                    color = (0, 255, 0)  # Green
                else:
                    if ppe_config.get('required_count', 1) > 1:
                        status_text = f"{ppe_class.upper()}: {ppe_config['current_frame_count']}/{ppe_config['required_count']} MISSING"
                    else:
                        status_text = f"{ppe_class.upper()}: MISSING"
                    color = (0, 0, 255)  # Red
                    
                cv2.putText(self.annotated_frame, status_text, (10, y_offset), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
                y_offset += 35
            
            # Add overall compliance status
            all_detected = self.are_all_ppe_detected()
            if all_detected:
                overall_text = "PPE COMPLIANT"
                overall_color = (0, 255, 0)  # Green
            else:
                missing_items = self.get_missing_ppe()
                overall_text = f"MISSING: {', '.join(missing_items)}"
                overall_color = (0, 0, 255)  # Red
                
            cv2.putText(self.annotated_frame, overall_text, (10, y_offset + 20), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, overall_color, 2, cv2.LINE_AA)
            
            # Add mode indicator
            mode_text = f"Mode: {'Mock' if (self.enable_mock or self.camera_mock) else 'Live'}"
            cv2.putText(self.annotated_frame, mode_text, (10, y_offset + 60), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1, cv2.LINE_AA)
            
        except Exception as e:
            logger.error(f"Error adding status annotations: {e}", exc_info=True)
    
    def get_current_frame(self):
        """Return the most recent annotated frame (thread-safe)"""
        # First try to get from queue (most recent)
        try:
            if not self.frame_queue.empty():
                return self.frame_queue.get_nowait()
        except queue.Empty:
            pass
            
        # Fall back to direct frame access
        with self._lock:
            return self.annotated_frame.copy() if self.annotated_frame is not None else None
    
    def is_hairnet_detected(self):
        """Check if a hairnet is currently detected (thread-safe)"""
        with self._lock:
            return self.hairnet_detected
    
    def get_status(self):
        """Get current detector status"""
        with self._lock:
            if not self.running:
                return "Stopped"
            elif self.enable_mock or self.camera_mock:
                return "Mock Mode"
            elif self.camera is not None:
                return "Running"
            else:
                return "Error"

    def get_ppe_detection_status(self):
        """Get detailed PPE detection status"""
        with self._lock:
            status = {}
            for ppe_class, ppe_config in self.ppe_classes.items():
                status[ppe_class] = {
                    'detected': ppe_config['detected'],
                    'detection_count': ppe_config['detection_count'],
                    'no_detection_count': ppe_config['no_detection_count']
                }
            return status
    
    def get_missing_ppe(self):
        """Get list of missing PPE classes"""
        with self._lock:
            missing = []
            for ppe_class, ppe_config in self.ppe_classes.items():
                if not ppe_config['detected']:
                    missing.append(ppe_class.title())
            return missing
    
    def are_all_ppe_detected(self):
        """Check if all required PPE classes are detected"""
        with self._lock:
            return all(ppe_config['detected'] for ppe_config in self.ppe_classes.values())

    def get_specific_ppe_status(self):
        """Get detailed status for the 4 specific PPE classes: Gown, Hairnet, Mask, Gloves"""
        with self._lock:
            specific_status = {}
            required_classes = ['gown', 'hairnet', 'mask', 'gloves']
            
            for ppe_class in required_classes:
                if ppe_class in self.ppe_classes:
                    ppe_config = self.ppe_classes[ppe_class]
                    specific_status[ppe_class] = {
                        'class_names': ppe_config['class_names'],
                        'detected': ppe_config['detected'],
                        'detection_count': ppe_config['detection_count'],
                        'no_detection_count': ppe_config['no_detection_count'],
                        'current_frame_count': ppe_config.get('current_frame_count', 0),
                        'min_confidence': ppe_config['min_confidence'],
                        'required_count': ppe_config.get('required_count', 1)
                    }
                else:
                    specific_status[ppe_class] = {
                        'enabled': False,
                        'error': 'Class not configured'
                    }
            
            return specific_status
    
    def log_specific_ppe_status(self):
        """Log the current status of all 4 specific PPE classes"""
        status = self.get_specific_ppe_status()
        logger.info("=== Specific PPE Detection Status ===")
        for ppe_class, info in status.items():
            if 'error' in info:
                logger.warning(f"{ppe_class.upper()}: {info['error']}")
            else:
                status_str = "DETECTED" if info['detected'] else "MISSING"
                if info['required_count'] > 1:
                    count_str = f" ({info['current_frame_count']}/{info['required_count']})"
                else:
                    count_str = ""
                logger.info(f"{ppe_class.upper()}: {status_str}{count_str} - Target class: {info['class_names']}")
        logger.info("=====================================")

    def get_ppe_issues(self):
        """Get list of PPE issues with specific feedback for absent vs incomplete detection"""
        with self._lock:
            issues = []
            for ppe_class, ppe_config in self.ppe_classes.items():
                if not ppe_config['detected']:
                    # Provide specific feedback based on the last detection type
                    last_type = ppe_config.get('last_detection_type', 'none')
                    
                    if last_type == 'absent':
                        issue_description = f"{ppe_class.title()} Absent"
                    elif last_type == 'incomplete':
                        issue_description = f"{ppe_class.title()} Incomplete needs fixing"
                    else:
                        # Fallback for 'none' or untracked states
                        issue_description = f"{ppe_class.title()} Absent"
                    
                    issues.append(issue_description)
            return issues

    def get_organized_detection_status(self):
        """Get organized detection status for access denied display with locking support"""
        with self._lock:
            status_lines = []
            
            # Define the order of PPE classes for consistent display
            class_order = ['gown', 'hairnet', 'gloves', 'mask']
            
            for ppe_class in class_order:
                if ppe_class in self.ppe_classes:
                    ppe_config = self.ppe_classes[ppe_class]
                    
                    # Get detection status details
                    detected = ppe_config['detected']
                    is_locked = ppe_config.get('is_locked', False)
                    locked_status = ppe_config.get('locked_status', 'none')
                    last_type = ppe_config.get('last_detection_type', 'none')
                    required_count = ppe_config.get('required_count', 1)
                    detection_count = ppe_config.get('detection_count', 0)
                    no_detection_count = ppe_config.get('no_detection_count', 0)
                    required_consecutive = ppe_config.get('required_consecutive_frames', 5)
                    
                    # Format the class name for display
                    class_display = ppe_class.title()
                    
                    # Determine status text based on locking and detection state
                    if is_locked and self.enable_locked_mechanism:
                        # Show locked status - this prevents the "??" issue
                        if locked_status == 'positive':
                            if required_count > 1:
                                status = f"{required_count} Present (Locked)"
                            else:
                                status = "Present (Locked)"
                        elif locked_status == 'absent':
                            if required_count > 1:
                                status = f"{required_count} Absent (Locked)"
                            else:
                                status = "Absent (Locked)"
                        elif locked_status == 'incomplete':
                            status = "Incomplete (Locked)"
                        else:
                            status = "Unknown (Locked)"
                    elif detected:
                        # Show current detection status when not locked or locking disabled
                        if required_count > 1:
                            status = f"{required_count} Present"
                        else:
                            status = "Present"
                    else:
                        # Show progress towards decision or current status
                        if detection_count > 0 and self.enable_locked_mechanism:
                            # Show positive detection progress (only when locking enabled)
                            progress = f"({detection_count}/{required_consecutive})"
                            status = f"Detecting {progress}"
                        elif no_detection_count > 0 and self.enable_locked_mechanism:
                            # Show negative detection progress (only when locking enabled)
                            progress = f"({no_detection_count}/{required_consecutive})"
                            if last_type == 'absent':
                                status = f"Absent {progress}"
                            elif last_type == 'incomplete':
                                status = f"Incomplete {progress}"
                            else:
                                status = f"Missing {progress}"
                        else:
                            # When locking is disabled, show simple status based on last detection
                            if last_type == 'absent':
                                if required_count > 1:
                                    status = f"{required_count} Absent"
                                else:
                                    status = "Absent"
                            elif last_type == 'incomplete':
                                status = "Incomplete"
                            elif last_type == 'positive':
                                if required_count > 1:
                                    status = f"{required_count} Present"
                                else:
                                    status = "Present"
                            else:
                                status = "Checking..."
                    
                    # Create formatted line
                    status_line = f"{class_display}: {status}"
                    status_lines.append(status_line)
                else:
                    # Class not configured
                    class_display = ppe_class.title()
                    status_lines.append(f"{class_display}: Not Configured")
            
            return status_lines

    @staticmethod
    def force_camera_cleanup():
        """Force cleanup of any lingering camera resources (use with caution)"""
        try:
            import gc
            
            # Enhanced Picamera2 cleanup
            if PICAMERA_AVAILABLE:
                try:
                    # Import Picamera2 for cleanup operations
                    from picamera2 import Picamera2
                    
                    # Try to clean up any existing Picamera2 instances
                    logger.info("Attempting to cleanup existing Picamera2 instances...")
                    
                    # Force close any existing camera instances
                    try:
                        # Use the Picamera2 global cleanup method if available
                        if hasattr(Picamera2, 'global_cleanup'):
                            Picamera2.global_cleanup()
                        
                        # Additional cleanup: try to release camera via system call
                        import subprocess
                        try:
                            # Kill any processes that might be using the camera
                            subprocess.run(['sudo', 'pkill', '-f', 'libcamera'], 
                                         capture_output=True, timeout=2)
                            subprocess.run(['sudo', 'modprobe', '-r', 'bcm2835_isp'], 
                                         capture_output=True, timeout=2)
                            subprocess.run(['sudo', 'modprobe', '-r', 'bcm2835_codec'], 
                                         capture_output=True, timeout=2)
                            subprocess.run(['sudo', 'modprobe', 'bcm2835_codec'], 
                                         capture_output=True, timeout=2)
                            subprocess.run(['sudo', 'modprobe', 'bcm2835_isp'], 
                                         capture_output=True, timeout=2)
                            logger.info("Camera drivers reset successfully")
                        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
                            logger.debug(f"Camera driver reset failed (normal on some systems): {e}")
                        
                    except Exception as e:
                        logger.debug(f"Picamera2 global cleanup not available or failed: {e}")
                    
                except ImportError:
                    logger.debug("Picamera2 not available for cleanup")
            
            # Force garbage collection to clean up any unreferenced camera objects
            logger.info("Running garbage collection for camera cleanup...")
            for _ in range(3):  # Multiple GC cycles
                gc.collect()
                time.sleep(0.2)
            
            # Additional system-level cleanup
            try:
                import os
                # Clear any camera device locks in /dev
                if os.path.exists('/dev'):
                    logger.debug("Attempting to clear camera device locks...")
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Device lock cleanup failed: {e}")
            
            logger.info("Camera resource cleanup completed")
            time.sleep(1.0)  # Give system time to fully release resources
            
        except Exception as e:
            logger.warning(f"Force camera cleanup failed: {e}")
            # Even if cleanup fails, wait a bit for system recovery
            time.sleep(2.0)

# For testing as standalone script
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s')
    
    def on_detection(hairnet_detected):
        print(f"Hairnet detected: {hairnet_detected}")
    
    # Create detector
    detector = YOLODetector()
    
    try:
        # Start detection
        if detector.start(on_detection):
            print(f"Detector started. Status: {detector.get_status()}")
            
            # Run for 60 seconds
            print("Running for 60 seconds...")
            for i in range(60):
                time.sleep(1)
                status = detector.get_status()
                detected = detector.is_hairnet_detected()
                print(f"Second {i+1}/60 - Status: {status}, Hairnet: {detected}")
                
        else:
            print("Failed to start detector")
            
    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        # Stop detector
        detector.stop()
        print("Test completed")