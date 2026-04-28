import os
import logging
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from config import CONFIG

logger = logging.getLogger(__name__)

class SecurityManager:
    """Centralized security management for the cleanroom system"""
    
    def __init__(self):
        self.max_failed_attempts = 5
        self.lockout_duration = 300  # 5 minutes
        self.failed_attempts = {}
        self.session_timeout = 3600  # 1 hour
        self.active_sessions = {}
        
    def rate_limit(self, identifier, max_attempts=None, window_seconds=60):
        """Simple rate limiting implementation"""
        if max_attempts is None:
            max_attempts = self.max_failed_attempts
            
        current_time = time.time()
        
        # Clean old attempts
        if identifier in self.failed_attempts:
            self.failed_attempts[identifier] = [
                attempt_time for attempt_time in self.failed_attempts[identifier]
                if current_time - attempt_time < window_seconds
            ]
        
        # Check if rate limited
        attempt_count = len(self.failed_attempts.get(identifier, []))
        if attempt_count >= max_attempts:
            logger.warning(f"Rate limit exceeded for {identifier}")
            return False
            
        return True
        
    def record_failed_attempt(self, identifier):
        """Record a failed attempt for rate limiting"""
        current_time = time.time()
        if identifier not in self.failed_attempts:
            self.failed_attempts[identifier] = []
        self.failed_attempts[identifier].append(current_time)
        logger.warning(f"Failed attempt recorded for {identifier}")
        
    def clear_failed_attempts(self, identifier):
        """Clear failed attempts for an identifier"""
        if identifier in self.failed_attempts:
            del self.failed_attempts[identifier]
            
    def generate_secure_token(self, length=32):
        """Generate a cryptographically secure random token"""
        return secrets.token_urlsafe(length)
        
    def hash_password(self, password, salt=None):
        """Hash a password with salt (for future admin features)"""
        if salt is None:
            salt = secrets.token_hex(16)
        
        pwdhash = hashlib.pbkdf2_hmac('sha256', 
                                     password.encode('utf-8'), 
                                     salt.encode('utf-8'), 
                                     100000)  # 100k iterations
        return salt + pwdhash.hex()
        
    def verify_password(self, password, hashed):
        """Verify a password against its hash"""
        try:
            salt = hashed[:32]
            stored_hash = hashed[32:]
            pwdhash = hashlib.pbkdf2_hmac('sha256',
                                         password.encode('utf-8'),
                                         salt.encode('utf-8'),
                                         100000)
            return stored_hash == pwdhash.hex()
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False

class SecurityLogger:
    """Enhanced security logging for audit trails"""
    
    def __init__(self):
        # Create security-specific logger
        self.security_logger = logging.getLogger('security')
        
        # Create security log file handler
        security_log_file = CONFIG.get('system', {}).get('security_log_file', 'security.log')
        security_handler = logging.FileHandler(security_log_file)
        security_formatter = logging.Formatter(
            '%(asctime)s - SECURITY - %(levelname)s - %(message)s'
        )
        security_handler.setFormatter(security_formatter)
        self.security_logger.addHandler(security_handler)
        self.security_logger.setLevel(logging.INFO)
        
    def log_access_attempt(self, worker_name, serial_number, result, reason=None):
        """Log access attempts for security audit"""
        status = "GRANTED" if result else "DENIED"
        message = f"ACCESS {status} - Worker: {worker_name}, Gown: {serial_number}"
        if reason:
            message += f", Reason: {reason}"
            
        if result:
            self.security_logger.info(message)
        else:
            self.security_logger.warning(message)
            
    def log_admin_action(self, action, user, details=None):
        """Log administrative actions"""
        message = f"ADMIN ACTION - {action} by {user}"
        if details:
            message += f" - {details}"
        self.security_logger.info(message)
        
    def log_system_event(self, event_type, details):
        """Log system security events"""
        message = f"SYSTEM EVENT - {event_type}: {details}"
        self.security_logger.info(message)
        
    def log_data_access(self, user, data_type, action):
        """Log data access for compliance"""
        message = f"DATA ACCESS - User: {user}, Type: {data_type}, Action: {action}"
        self.security_logger.info(message)

def security_audit(action_type):
    """Decorator for security auditing"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                logger.info(f"Security audit: {action_type} completed in {execution_time:.3f}s")
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"Security audit: {action_type} failed after {execution_time:.3f}s - {str(e)}")
                raise
        return wrapper
    return decorator

def sanitize_log_input(input_string):
    """Sanitize input for safe logging (prevent log injection)"""
    if not isinstance(input_string, str):
        input_string = str(input_string)
    
    # Remove potential log injection characters
    dangerous_chars = ['\n', '\r', '\t', '\0']
    sanitized = input_string
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, '')
    
    # Limit length to prevent log flooding
    if len(sanitized) > 200:
        sanitized = sanitized[:197] + "..."
        
    return sanitized

def validate_system_integrity():
    """Basic system integrity checks"""
    integrity_issues = []
    
    # Check critical files exist
    critical_files = [
        'config.py',
        'firebase_database.py',
        'app.py',
        'yolo_detector.py',
        'rfid_reader.py'
    ]
    
    for file_path in critical_files:
        if not os.path.exists(file_path):
            integrity_issues.append(f"Critical file missing: {file_path}")
    
    # Check configuration integrity
    try:
        from config import CONFIG, validate_config
        validate_config(CONFIG)
    except Exception as e:
        integrity_issues.append(f"Configuration validation failed: {str(e)}")
    
    # Check log file permissions
    log_file = CONFIG.get('system', {}).get('log_file', 'cleanroom_app.log')
    try:
        if os.path.exists(log_file):
            # Check if file is writable
            if not os.access(log_file, os.W_OK):
                integrity_issues.append(f"Log file not writable: {log_file}")
    except Exception as e:
        integrity_issues.append(f"Cannot check log file permissions: {str(e)}")
    
    return integrity_issues

# Global security manager instance
security_manager = SecurityManager()
security_logger = SecurityLogger()

# Export key functions
__all__ = [
    'SecurityManager',
    'SecurityLogger', 
    'security_audit',
    'sanitize_log_input',
    'validate_system_integrity',
    'security_manager',
    'security_logger'
] 