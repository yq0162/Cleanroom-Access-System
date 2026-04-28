import serial
import threading
import time
import logging
import random
import string
import re # Import regex for more advanced cleaning
from config import CONFIG

logger = logging.getLogger(__name__)

class RFIDReader:
    def __init__(self, port=None, baudrate=None, timeout=None, read_cooldown=None):
        """Initialize RFID Reader with specified or configuration values"""
        # Get values from config if not provided
        rfid_config = CONFIG.get('rfid', {})
        self.port = port or rfid_config.get('port', '/dev/ttyACM0')
        self.baudrate = baudrate or rfid_config.get('baudrate', 9600)
        self.timeout = timeout or rfid_config.get('timeout', 1.0)
        self.read_cooldown = read_cooldown or rfid_config.get('read_cooldown', 2.0)
        self.mock_mode = rfid_config.get('enable_mock', False)

        self.ser = None
        self.running = False
        self.callback = None
        self.reader_thread = None

        # Debug mode for verbose logging
        self.debug = CONFIG.get('system', {}).get('debug_mode', False)

        # Last read tag (to avoid duplicates)
        self.last_tag = None
        self.last_read_time = 0

        # Configure minimum tag length and cooldown
        self.min_tag_length = 3  # Ignore tags shorter than this

        # Connection retry parameters
        self.max_retry_attempts = 3
        self.retry_delay = 1.0

        # Thread safety
        self._lock = threading.Lock()
        
        # Force termination flag for stuck threads
        self._force_stop = threading.Event()

        logger.info(f"RFID Reader initialized (Port: {self.port}, Mock: {self.mock_mode})")

    def start(self, callback=None):
        """
        Start reading RFID tags
        callback: Function to call when tag is detected (receives serial number as argument)
        """
        with self._lock:
            if self.running:
                logger.warning("RFID reader already running")
                return False # Indicate already running

            self.callback = callback
            self.running = True
            self.last_tag = None # Reset last tag on start
            self.last_read_time = 0

        # Start reader in separate thread
        self.reader_thread = threading.Thread(target=self._read_loop, name="RFIDReadThread")
        self.reader_thread.daemon = True
        self.reader_thread.start()

        logger.info(f"RFID reader started (Mock mode: {self.mock_mode})")
        return True # Indicate successful start

    def stop(self):
        """Stop reading RFID tags with improved cleanup"""
        with self._lock:
            if not self.running:
                logger.debug("RFID reader already stopped.")
                return

            logger.info("Stopping RFID reader...")
            self.running = False # Signal thread to stop
            self._force_stop.set() # Signal force stop

        # Close serial port first (this might help thread exit if blocked on read)
        self._close_serial_connection()

        # Wait for thread to finish with progressive timeouts
        if self.reader_thread and self.reader_thread.is_alive():
            logger.debug("Joining RFID reader thread...")
            
            # First attempt - normal timeout
            self.reader_thread.join(timeout=3.0)
            if self.reader_thread.is_alive():
                logger.warning("RFID reader thread did not terminate normally, trying force stop...")
                
                # Second attempt - force stop and longer timeout
                self._force_stop.set()
                self.reader_thread.join(timeout=5.0)
                
                if self.reader_thread.is_alive():
                    logger.error("RFID reader thread stuck - unable to terminate gracefully")
                    # In a production environment, you might want to consider more drastic measures
                    # like terminating the entire process if critical threads won't stop
                else:
                    logger.debug("RFID reader thread terminated after force stop.")
            else:
                 logger.debug("RFID reader thread joined normally.")
        
        self.reader_thread = None
        self._force_stop.clear() # Reset for next start
        logger.info("RFID reader stopped")

    def _close_serial_connection(self):
        """Safely close the serial connection"""
        if self.ser:
            try:
                if hasattr(self.ser, 'is_open') and self.ser.is_open:
                    self.ser.close()
                    logger.debug("RFID serial port closed.")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}", exc_info=True)
            finally:
                self.ser = None

    def _generate_mock_tag(self):
        """Generate random RFID tag ID for mock mode"""
        # Create random alphanumeric string of length 10
        tag_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        return tag_id

    def _establish_serial_connection(self):
        """Establish serial connection with retry logic"""
        for attempt in range(self.max_retry_attempts):
            try:
                logger.debug(f"Attempting to open serial port: {self.port} (attempt {attempt + 1}/{self.max_retry_attempts})")
                
                # Test if port exists (basic check)
                try:
                    test_ser = serial.Serial(self.port, timeout=0.1)
                    test_ser.close()
                except (serial.SerialException, OSError) as e:
                    logger.warning(f"Port {self.port} not accessible: {e}")
                    if attempt < self.max_retry_attempts - 1:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False

                # Establish actual connection
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE
                )
                
                # Short delay to allow port to initialize
                time.sleep(0.1)
                
                # Verify connection is properly established
                if not self.ser.is_open:
                    logger.warning(f"Serial port {self.port} failed to open properly")
                    self.ser.close()
                    self.ser = None
                    if attempt < self.max_retry_attempts - 1:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False
                
                # Verify file descriptor is valid
                if hasattr(self.ser, 'fd') and self.ser.fd is None:
                    logger.warning(f"Serial port {self.port} has invalid file descriptor")
                    self.ser.close()
                    self.ser = None
                    if attempt < self.max_retry_attempts - 1:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False
                
                # Test connection by clearing any pending data
                try:
                    if self.ser.in_waiting > 0:
                        self.ser.read_all()
                except Exception as e:
                    logger.warning(f"Error testing serial connection: {e}")
                    self.ser.close()
                    self.ser = None
                    if attempt < self.max_retry_attempts - 1:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False
                
                logger.info(f"RFID reader connected successfully on port {self.port}")
                return True
                
            except (serial.SerialException, OSError) as e:
                logger.warning(f"Attempt {attempt + 1} failed to connect to {self.port}: {e}")
                if attempt < self.max_retry_attempts - 1:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(f"Failed to establish serial connection after {self.max_retry_attempts} attempts")
                    return False
        
        return False

    def _read_loop(self):
        """Main reading loop that runs in separate thread"""
        logger.info(f"RFID reader thread started (Mock: {self.mock_mode}).")
        try:
            # In mock mode, we don't need to connect to actual hardware
            if self.mock_mode:
                logger.info("RFID reader running in mock mode - will generate random tags")
                self._mock_read_loop()
                return # Exit thread after mock loop finishes

            # --- Real hardware mode ---
            if not self._establish_serial_connection():
                logger.error("Failed to establish serial connection, stopping reader")
                with self._lock:
                    self.running = False
                return

            if self.debug:
                logger.info("RFID reader in debug mode - will print all received data")

            consecutive_errors = 0
            max_consecutive_errors = 5

            while self.running and not self._force_stop.is_set():
                try:
                    # Check if still connected
                    if not self.ser or not self.ser.is_open:
                         logger.warning("Serial port closed unexpectedly. Attempting reconnection...")
                         if not self._establish_serial_connection():
                             logger.error("Reconnection failed, stopping reader")
                             break
                         consecutive_errors = 0
                         continue

                    # Read the data from serial port
                    try:
                         # Additional check to ensure serial object and its file descriptor are valid
                         if not self.ser or not self.ser.is_open or (hasattr(self.ser, 'fd') and self.ser.fd is None):
                             logger.warning("Serial port or file descriptor is invalid. Attempting reconnection...")
                             if not self._establish_serial_connection():
                                 logger.error("Reconnection failed, stopping reader")
                                 break
                             consecutive_errors = 0
                             continue
                         
                         # Test connection stability before reading
                         try:
                             # Quick test to ensure the connection is still valid
                             if hasattr(self.ser, 'in_waiting'):
                                 _ = self.ser.in_waiting  # This will fail if fd is None
                         except (AttributeError, OSError, TypeError) as test_err:
                             logger.warning(f"Connection test failed: {test_err}. Reconnecting...")
                             self._close_serial_connection()
                             if not self._establish_serial_connection():
                                 logger.error("Reconnection failed after connection test failure")
                                 break
                             consecutive_errors = 0
                             continue
                             
                         # Perform the actual read with maximum protection
                         try:
                             line_bytes = self.ser.readline()
                         except (TypeError, AttributeError) as fd_err:
                             # Handle the specific case where fd becomes None during readline
                             if "NoneType" in str(fd_err):
                                 logger.warning("File descriptor became None during readline - connection lost")
                                 self._close_serial_connection()
                                 
                                 # Check if we're shutting down - if so, don't try to reconnect
                                 if not self.running or self._force_stop.is_set():
                                     logger.info("Reader is shutting down, not attempting reconnection")
                                     break
                                 
                                 consecutive_errors += 1
                                 if consecutive_errors >= max_consecutive_errors:
                                     logger.error("Too many consecutive connection losses, stopping reader")
                                     break
                                 # Try to reconnect
                                 if not self._establish_serial_connection():
                                     logger.error("Failed to reconnect after fd loss")
                                     consecutive_errors += 1
                                 else:
                                     logger.info("Reconnected successfully after fd loss")
                                     consecutive_errors = 0
                                 continue
                             else:
                                 # Re-raise if it's a different type of error
                                 raise
                         
                         consecutive_errors = 0  # Reset error counter on successful read
                         
                         if self.debug and line_bytes:
                             logger.debug(f"Raw bytes received: {line_bytes!r}")
                             
                    except (serial.SerialException, TypeError, AttributeError, OSError) as read_err:
                         consecutive_errors += 1
                         
                         # Check if this is the specific file descriptor error
                         if "NoneType" in str(read_err) and "integer" in str(read_err):
                             logger.error(f"File descriptor became None during read operation: {read_err}")
                         else:
                             logger.error(f"Serial read error on {self.port}: {read_err}")
                         
                         # Close and reset serial connection on error
                         try:
                             if self.ser:
                                 self.ser.close()
                         except:
                             pass  # Ignore errors when closing
                         self.ser = None
                         
                         if consecutive_errors >= max_consecutive_errors:
                             logger.error(f"Too many consecutive read errors ({consecutive_errors}), stopping reader")
                             break
                         
                         # Wait and attempt reconnection
                         logger.info(f"Waiting {self.retry_delay}s before reconnection attempt...")
                         time.sleep(self.retry_delay)
                         if not self._establish_serial_connection():
                             logger.error("Reconnection failed after read error")
                             consecutive_errors += 1
                         else:
                             logger.info("Successfully reconnected after read error")
                             consecutive_errors = 0  # Reset on successful reconnection
                         continue

                    if not line_bytes:
                         # Timeout occurred, just loop again
                         time.sleep(0.01) # Prevent busy-waiting on timeout
                         continue

                    # Decode and perform aggressive cleaning
                    try:
                        raw_string = line_bytes.decode('utf-8', errors='ignore')
                        if self.debug:
                            logger.debug(f"Decoded string (before strip): '{raw_string}' (len={len(raw_string)})")

                        # Remove common leading/trailing control chars and whitespace
                        cleaned_string = raw_string.strip('\r\n\t \x00\x02\x03\x04\x05\x06\x07\x08\x0B\x0C\x0E\x0F')
                        
                        # Additional cleaning for common RFID reader artifacts
                        cleaned_string = re.sub(r'^[\s\x00-\x1F\x7F]+|[\s\x00-\x1F\x7F]+$', '', cleaned_string)

                        if self.debug:
                            logger.debug(f"Cleaned string (after strip): '{cleaned_string}' (len={len(cleaned_string)})")
                            
                    except Exception as decode_err:
                         logger.error(f"Error decoding/cleaning bytes: {line_bytes!r} - {decode_err}")
                         continue # Skip this line

                    # Process valid data
                    self._process_tag(cleaned_string) # Pass the cleaned data

                except Exception as loop_err:
                    # Catch unexpected errors within the loop
                    consecutive_errors += 1
                    logger.error(f"Unexpected error in RFID read loop: {loop_err}", exc_info=True)
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"Too many consecutive errors, stopping reader")
                        break
                    
                    # Add a delay to prevent rapid error loops
                    time.sleep(0.5)

        finally:
            # Ensure serial port is closed if it was opened
            self._close_serial_connection()
            logger.info("RFID reader thread finished.")

    def _mock_read_loop(self):
        """Mock read loop for testing without hardware"""
        while self.running and not self._force_stop.is_set():
            # In mock mode, generate a tag every few seconds
            sleep_time = random.uniform(3.0, 6.0)
            # Allow interruption during sleep
            for _ in range(int(sleep_time * 10)): # Check every 100ms
                 if not self.running: break
                 time.sleep(0.1)
            if not self.running: break

            # Generate a mock tag ID
            tag_id = self._generate_mock_tag()

            if self.debug:
                logger.info(f"Mock RFID tag generated: {tag_id}")

            # Process the mock tag (no stripping needed here)
            self._process_tag(tag_id)

    def _process_tag(self, tag_id):
        """Process a tag ID (real or mock), ensuring it's stripped and valid"""
        # Ensure tag_id is a string and stripped (already done in _read_loop for real data)
        processed_tag_id = str(tag_id).strip()

        # Skip empty data or data too short AFTER stripping
        if not processed_tag_id or len(processed_tag_id) < self.min_tag_length:
            if self.debug and tag_id: # Log original if it wasn't empty before strip
                 logger.debug(f"Skipping empty or too short tag after processing: original='{tag_id}', processed='{processed_tag_id}'")
            return

        # Additional validation - ensure tag contains only expected characters
        if not re.match(r'^[A-Za-z0-9]+$', processed_tag_id):
            logger.warning(f"Tag contains invalid characters, skipping: '{processed_tag_id}'")
            return

        # Skip duplicate reads (same tag within cooldown period)
        current_time = time.time()
        if processed_tag_id == self.last_tag and current_time - self.last_read_time < self.read_cooldown:
            if self.debug:
                logger.debug(f"Skipping duplicate tag within cooldown: {processed_tag_id}")
            return

        # Keep track of last read tag and time
        self.last_tag = processed_tag_id
        self.last_read_time = current_time

        # Call the callback with the valid, stripped tag
        if self.callback:
            logger.info(f"Calling callback with RFID: '{processed_tag_id}'")
            try:
                self.callback(processed_tag_id)
            except Exception as e:
                logger.error(f"Error in RFID callback function: {e}", exc_info=True)
        else:
            logger.warning("No callback set for RFID reader")

    def is_connected(self):
        """Check if RFID reader is connected and running"""
        with self._lock:
            if self.mock_mode:
                return self.running
            return self.running and self.ser and self.ser.is_open

    def get_status(self):
        """Get current status of RFID reader"""
        with self._lock:
            if not self.running:
                return "Stopped"
            elif self.mock_mode:
                return "Mock Mode"
            elif self.ser and self.ser.is_open:
                return "Connected"
            else:
                return "Disconnected"

# For testing as standalone script
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.DEBUG, # Use DEBUG for testing this module
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    def on_tag_read(tag_id):
        print(f"--- Tag detected in main script: '{tag_id}' ---")

    # Create reader instance - it will use config file settings by default
    reader = RFIDReader() # Uses config

    try:
        # Start detection
        if reader.start(on_tag_read):
            print(f"Reader started (Mock: {reader.mock_mode}, Status: {reader.get_status()}). Waiting for tags...")
            # Keep running until Ctrl+C
            while True:
                time.sleep(1)
                if not reader.is_connected():
                    print(f"Reader disconnected. Status: {reader.get_status()}")
                    break
        else:
             print("Failed to start RFID reader.")

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        # Stop reader
        if 'reader' in locals() and reader:
             reader.stop()
        print("Test completed")