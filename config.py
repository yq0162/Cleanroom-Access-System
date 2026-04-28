import os
import logging
import re
from datetime import timedelta # Added for potential future config

logger = logging.getLogger(__name__)

# Handle yaml import with fallback
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.warning("PyYAML not available, using JSON fallback for config")
    import json

def validate_config_section(config, section_name, required_fields):
    """Validate that a config section exists and has required fields"""
    if section_name not in config:
        raise ValueError(f"Missing required config section: {section_name}")
    
    section = config[section_name]
    for field in required_fields:
        if field not in section:
            raise ValueError(f"Missing required field '{field}' in section '{section_name}'")

def validate_config(config):
    """Validate configuration structure and values"""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a dictionary")
    
    # Validate required sections and their required fields
    required_sections = {
        'system': ['log_level', 'log_file'],
        'rfid': ['port', 'baudrate', 'timeout', 'read_cooldown'],
        'camera': ['resolution', 'format'],
        'yolo': ['model_path', 'confidence_threshold', 'detection_stability'],
        'database': ['credential_path'],
        'gui': ['window_size', 'max_video_width', 'max_video_height'],
        'rules': ['gown_lifetime_days', 'retry_window_minutes'],
        'ppe_detection': ['required_classes']
    }
    
    for section_name, required_fields in required_sections.items():
        validate_config_section(config, section_name, required_fields)
    
    # Validate specific value ranges and formats
    system_config = config['system']
    valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if system_config['log_level'] not in valid_log_levels:
        raise ValueError(f"Invalid log_level. Must be one of: {valid_log_levels}")
    
    # Validate numeric ranges
    rfid_config = config['rfid']
    if not (1200 <= rfid_config['baudrate'] <= 115200):
        raise ValueError("RFID baudrate must be between 1200 and 115200")
    if not (0.1 <= rfid_config['timeout'] <= 30.0):
        raise ValueError("RFID timeout must be between 0.1 and 30.0 seconds")
    
    yolo_config = config['yolo']
    if not (0.01 <= yolo_config['confidence_threshold'] <= 1.0):
        raise ValueError("YOLO confidence_threshold must be between 0.01 and 1.0")
    if not (1 <= yolo_config['detection_stability'] <= 10):
        raise ValueError("YOLO detection_stability must be between 1 and 10")
    
    # Validate PPE detection config
    ppe_config = config['ppe_detection']
    required_classes = ppe_config['required_classes']
    valid_ppe_classes = ['gown', 'gloves', 'hairnet', 'mask']
    
    for class_name in valid_ppe_classes:
        if class_name not in required_classes:
            raise ValueError(f"Missing PPE class configuration: {class_name}")
        
        class_config = required_classes[class_name]
        if 'enabled' not in class_config:
            raise ValueError(f"Missing 'enabled' field for PPE class: {class_name}")
        if 'class_names' not in class_config:
            raise ValueError(f"Missing 'class_names' field for PPE class: {class_name}")
        if 'min_confidence' not in class_config:
            raise ValueError(f"Missing 'min_confidence' field for PPE class: {class_name}")
        
        if not isinstance(class_config['enabled'], bool):
            raise ValueError(f"PPE class '{class_name}' enabled must be a boolean")
        if not isinstance(class_config['class_names'], list):
            raise ValueError(f"PPE class '{class_name}' class_names must be a list")
        if not (0.01 <= class_config['min_confidence'] <= 1.0):
            raise ValueError(f"PPE class '{class_name}' min_confidence must be between 0.01 and 1.0")
        
        # Validate optional required_count field
        if 'required_count' in class_config:
            if not isinstance(class_config['required_count'], int) or class_config['required_count'] < 1:
                raise ValueError(f"PPE class '{class_name}' required_count must be a positive integer")
    
    # Validate camera resolution
    camera_config = config['camera']
    if not isinstance(camera_config['resolution'], list) or len(camera_config['resolution']) != 2:
        raise ValueError("Camera resolution must be a list of [width, height]")
    width, height = camera_config['resolution']
    if not (160 <= width <= 4096) or not (120 <= height <= 3072):
        raise ValueError("Camera resolution must be within reasonable bounds")
    
    # Validate GUI window size format
    gui_config = config['gui']
    if not re.match(r'^\d+x\d+$', gui_config['window_size']):
        raise ValueError("GUI window_size must be in format 'WIDTHxHEIGHT'")
    
    # Validate rules
    rules_config = config['rules']
    if not (1 <= rules_config['gown_lifetime_days'] <= 365):
        raise ValueError("Gown lifetime must be between 1 and 365 days")
    if not (1 <= rules_config['retry_window_minutes'] <= 1440):
        raise ValueError("Retry window must be between 1 and 1440 minutes")

def sanitize_worker_name(name):
    """Sanitize and validate worker name input"""
    if not isinstance(name, str):
        raise ValueError("Worker name must be a string")
    
    # Remove leading/trailing whitespace
    name = name.strip()
    
    # Check length
    if not (1 <= len(name) <= 50):
        raise ValueError("Worker name must be between 1 and 50 characters")
    
    # Check format - only letters, spaces, hyphens, periods, and apostrophes
    if not re.match(r"^[A-Za-z\s\-\.']+$", name):
        raise ValueError("Worker name contains invalid characters")
    
    return name

def sanitize_serial_number(serial):
    """Sanitize and validate serial number input"""
    if not isinstance(serial, str):
        raise ValueError("Serial number must be a string")
    
    # Remove leading/trailing whitespace
    serial = serial.strip().upper()
    
    # Check length
    if not (3 <= len(serial) <= 20):
        raise ValueError("Serial number must be between 3 and 20 characters")
    
    # Check format - only alphanumeric characters
    if not re.match(r'^[A-Z0-9]+$', serial):
        raise ValueError("Serial number must contain only letters and numbers")
    
    return serial

def sanitize_file_path(path):
    """Sanitize file path to prevent directory traversal"""
    if not isinstance(path, str):
        raise ValueError("File path must be a string")
    
    # Normalize path and remove any directory traversal attempts
    path = os.path.normpath(path)
    
    # Check for directory traversal attempts
    if '..' in path or path.startswith('/'):
        raise ValueError("Invalid file path - directory traversal not allowed")
    
    return path

def load_config(config_file='cleanroom_config.yaml'):
    """Load configuration from file or create with defaults if not exists"""
    
    # If YAML not available, use JSON config instead
    if not YAML_AVAILABLE:
        config_file = config_file.replace('.yaml', '.json').replace('.yml', '.json')
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as file:
                if YAML_AVAILABLE:
                    config = yaml.safe_load(file)
                else:
                    config = json.load(file)
                
                # Validate the loaded configuration
                validate_config(config)
                
                logger.info(f"Configuration loaded and validated from {config_file}")
                return config
        else:
            # Create default config
            config = create_default_config()
            
            # Validate default config
            validate_config(config)

            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(config_file) if os.path.dirname(config_file) else '.', exist_ok=True)

            # Save default config
            with open(config_file, 'w') as file:
                if YAML_AVAILABLE:
                    yaml.dump(config, file, default_flow_style=False, indent=2)
                else:
                    json.dump(config, file, indent=2)

            logger.info(f"Default configuration created at {config_file}")
            return config

    except Exception as e:
        logger.error(f"Error loading configuration: {e}", exc_info=True)
        # Fallback to basic defaults with mandatory sections
        logger.warning("Using emergency fallback configuration")
        config = create_emergency_fallback_config()
        
        # Even the fallback should be validated
        try:
            validate_config(config)
        except Exception as fallback_error:
            logger.critical(f"Emergency fallback config is invalid: {fallback_error}")
            raise RuntimeError("Cannot create valid configuration") from fallback_error
        
        return config

def create_default_config():
    """Create the default configuration"""
    return {
        'system': {
            'debug_mode': False,
            'log_level': 'INFO',
            'log_file': 'cleanroom_app.log',
            'security_log_file': 'security.log'
        },
        'rfid': {
            'port': 'COM3',
            'baudrate': 9600,
            'timeout': 1.0,
            'read_cooldown': 2.0,
            'enable_mock': False,
            'enable_mock_fallback': False
        },
        'camera': {
            'resolution': [750, 950],  # Portrait mode: width x height (matching YAML)
            'format': 'RGB888',
            'enable_mock': False,
            'auto_rotate': False  # Unified auto-rotate setting (matching YAML)
        },
        'yolo': {
            'model_path': 'models/bestsmall.onnx',
            'fallback_model': 'yolov8n.pt',
            'confidence_threshold': 0.25,
            'detection_stability': 3,
            'detection_timeout_seconds': 5.0,
            'enable_mock': False
        },
        'hailo': {
            'enable': True,
            'model_name': 'yolov11s',
            'zoo_path': 'models'
        },
        'hand_washing': {
            'enable_mock': False,
            'min_washing_time': 30.0,
            'face_detection_interval': 0.5,
            'quality_thresholds': {
                'good': 30.0,
                'moderate': 15.0,
                'bad': 0.0
            }
        },
        'database': {
            'credential_path': 'serviceAccountKey.json',
            'enable_offline': False
        },
        'gui': {
            'window_size': '720x1280',  # Portrait mode
            'theme': 'default',
            'max_video_width': 720,   # Portrait orientation (matching YAML)
            'max_video_height': 1280,  # Portrait orientation (matching YAML)
            'approved_display_seconds': 5.0,  # Matching YAML
            'denied_display_seconds': 5.0,    # Matching YAML
            'fullscreen': True,               # Matching YAML
            'show_video_in_gui': False        # Matching YAML
        },
        'rules': {
            'gown_lifetime_days': 7,
            'retry_window_minutes': 5
        },
        'ppe_detection': {
            'required_classes': {
                'gloves': {
                    'enabled': True, 
                    'class_names': ['Gloves'], 
                    'negative_classes': ['Gloves_Absent'],
                    'incomplete_classes': [],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 2
                },
                'gown': {
                    'enabled': True, 
                    'class_names': ['Gown'], 
                    'negative_classes': ['Gown_Absent'],
                    'incomplete_classes': ['Gown_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                },
                'hairnet': {
                    'enabled': True, 
                    'class_names': ['Hairnet'], 
                    'negative_classes': ['Hairnet_Absent'],
                    'incomplete_classes': ['Hairnet_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                },
                'mask': {
                    'enabled': True, 
                    'class_names': ['Mask'], 
                    'negative_classes': ['Mask_Absent'],
                    'incomplete_classes': ['Mask_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                }
            }
        }
    }

def create_emergency_fallback_config():
    """Create emergency fallback configuration with safe defaults"""
    return {
        'system': {'debug_mode': False, 'log_level': 'INFO', 'log_file': 'cleanroom_app.log', 'security_log_file': 'security.log'},
        'rfid': {'port': 'COM3', 'baudrate': 9600, 'enable_mock': True, 'read_cooldown': 2.0, 'timeout': 1.0, 'enable_mock_fallback': True},
        'camera': {'resolution': [750, 950], 'format': 'RGB888', 'enable_mock': True, 'auto_rotate': False},
        'yolo': {'model_path': 'models/bestsmall.onnx', 'fallback_model': 'yolov8n.pt', 'confidence_threshold': 0.25, 'detection_stability': 3, 'detection_timeout_seconds': 5.0, 'enable_mock': True},
        'hailo': {'enable': False, 'model_name': 'yolov11s', 'zoo_path': 'models'},
        'hand_washing': {'enable_mock': True, 'min_washing_time': 30.0, 'face_detection_interval': 0.5, 'quality_thresholds': {'good': 30.0, 'moderate': 15.0, 'bad': 0.0}},
        'database': {'credential_path': 'firebase-credentials.json', 'enable_offline': True},
        'gui': {'window_size': '720x1280', 'theme': 'default', 'max_video_width': 720, 'max_video_height': 1280, 'approved_display_seconds': 5.0, 'denied_display_seconds': 5.0, 'fullscreen': True, 'show_video_in_gui': False},
        'rules': {'gown_lifetime_days': 7, 'retry_window_minutes': 5},
        'ppe_detection': {
            'required_classes': {
                'gloves': {
                    'enabled': True, 
                    'class_names': ['Gloves'], 
                    'negative_classes': ['Gloves_Absent'],
                    'incomplete_classes': [],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 2
                },
                'gown': {
                    'enabled': True, 
                    'class_names': ['Gown'], 
                    'negative_classes': ['Gown_Absent'],
                    'incomplete_classes': ['Gown_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                },
                'hairnet': {
                    'enabled': True, 
                    'class_names': ['Hairnet'], 
                    'negative_classes': ['Hairnet_Absent'],
                    'incomplete_classes': ['Hairnet_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                },
                'mask': {
                    'enabled': True, 
                    'class_names': ['Mask'], 
                    'negative_classes': ['Mask_Absent'],
                    'incomplete_classes': ['Mask_Incomplete'],
                    'min_confidence': 0.5,
                    'required_consecutive_frames': 5,
                    'required_count': 1
                }
            }
        }
    }

# Global configuration - now properly validated
try:
    CONFIG = load_config()
except Exception as e:
    logger.critical(f"Failed to load configuration: {e}")
    raise RuntimeError(f"Application cannot start without valid configuration: {e}") from e