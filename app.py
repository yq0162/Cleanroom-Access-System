# --- START OF FILE app.py ---

import tkinter as tk
from tkinter import messagebox, ttk, simpledialog
import cv2
import time
import threading
import logging
import os
from datetime import datetime
from config import CONFIG, load_config

# Configure logging (ensure this happens before other imports using logging)
log_level = CONFIG.get('system', {}).get('log_level', 'INFO')
log_file = CONFIG.get('system', {}).get('log_file', 'cleanroom_app.log')
numeric_level = getattr(logging, log_level.upper(), logging.INFO)

# Ensure log directory exists
os.makedirs(os.path.dirname(log_file) or '.', exist_ok=True)

logging.basicConfig(level=numeric_level,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[
                       logging.FileHandler(log_file, mode='a'), # Append mode
                       logging.StreamHandler()
                   ])
logger = logging.getLogger(__name__)

# Import our modules
from firebase_database import FirebaseDatabase
from rfid_reader import RFIDReader
from yolo_detector import YOLODetector
from gui import CleanroomGUI
from worker_management import WorkerManagementGUI
from security import security_logger, sanitize_log_input
from hand_washing_detector import HandWashingDetector

class CleanroomAccessApp:
    # Constants
    PPE_DETECTION_TIMEOUT = 10.0 # seconds for YOLO detection
    SECOND_CHANCE_WINDOW = 300 # seconds (5 minutes) for second attempt
    RESET_DELAY = 3.0 # seconds after approval/denial message

    def __init__(self, load_gui=True):
        logger.info("Initializing Cleanroom Access App")

        # Initialize components
        try:
            self.db = FirebaseDatabase()
            logger.info("Database initialized")
        except Exception as e:
            logger.critical(f"CRITICAL: Error initializing database: {e}", exc_info=True)
            if load_gui:
                messagebox.showerror("Fatal Error", f"Failed to initialize database: {str(e)}\nApplication cannot start.")
            raise SystemExit(f"Database initialization failed: {e}") from e

        try:
            self.rfid_reader = RFIDReader()
            logger.info("RFID Reader initialized")
        except Exception as e:
            logger.error(f"Error initializing RFID reader: {e}", exc_info=True)
            if load_gui:
                messagebox.showwarning("RFID Warning", f"Failed to initialize RFID reader: {str(e)}\n\nFalling back to manual/mock input mode.")
                # Ensure mock mode is enabled if hardware fails
                CONFIG['rfid']['enable_mock'] = True
                self.rfid_reader = RFIDReader() # Re-init with potentially updated config
            else:
                logger.warning("Continuing without RFID hardware in non-GUI mode.")
                CONFIG['rfid']['enable_mock'] = True
                self.rfid_reader = RFIDReader() # Ensure mock mode


        try:
            self.yolo_detector = YOLODetector()
            logger.info("YOLO Detector initialized")
        except Exception as e:
            logger.error(f"Error initializing YOLO detector: {e}", exc_info=True)
            if load_gui:
                messagebox.showwarning("YOLO Warning", f"Failed to initialize YOLO detector/camera: {str(e)}\n\nFalling back to mock detection mode.")
                CONFIG['yolo']['enable_mock'] = True
                CONFIG['camera']['enable_mock'] = True
                self.yolo_detector = YOLODetector() # Re-init
            else:
                logger.warning("Continuing without YOLO/camera in non-GUI mode.")
                CONFIG['yolo']['enable_mock'] = True
                CONFIG['camera']['enable_mock'] = True
                self.yolo_detector = YOLODetector() # Ensure mock mode

        try:
            self.hand_washing_detector = HandWashingDetector()
            logger.info("Hand Washing Detector initialized")
        except Exception as e:
            logger.error(f"Error initializing Hand Washing detector: {e}", exc_info=True)
            if load_gui:
                messagebox.showwarning("Hand Washing Warning", f"Failed to initialize hand washing detector: {str(e)}\n\nFalling back to mock mode.")
            # Continue without failing as hand washing is optional feature
            self.hand_washing_detector = None

        # Create the GUI if requested
        self.root = None
        self.gui = None
        if load_gui:
            try:
                self.root = tk.Tk()
                self.gui = CleanroomGUI(self.root) # GUI now handles theming
                self.gui.set_callbacks(
                    start_cb=self.start_system,
                    record_cb=self.show_records,
                    exit_cb=self.exit_system,
                    manage_cb=self.manage_workers,
                    handwash_cb=self.handle_handwash
                )
                # Set database reference for worker management and record display
                self.gui.set_database(self.db)
                
                # Set hand washing specific callbacks
                self.gui.set_handwash_callbacks(
                    start_cb=self.start_handwash_monitoring,
                    stop_cb=self.stop_handwash_monitoring,
                    register_face_cb=self.register_worker_face
                )
                
                # Set face registration callbacks
                self.gui.set_face_reg_callbacks(
                    start_cb=self.start_face_registration,
                    stop_cb=self.stop_face_registration,
                    video_cb=self.get_face_reg_video_frame,
                    register_face_cb=self.register_worker_face_independent
                )
                logger.info("GUI initialized successfully.")
            except Exception as e:
                 logger.critical(f"CRITICAL: Failed to initialize GUI: {e}", exc_info=True)
                 # Attempt to show error if possible, then exit
                 try: messagebox.showerror("Fatal Error", f"Failed to create GUI: {e}\nApplication cannot start.")
                 except: pass # Ignore if GUI failed completely
                 raise SystemExit(f"GUI initialization failed: {e}") from e

        # Application state
        self.running = False
        self.current_serial = None
        self.current_worker = None
        self.detection_active = False
        self.exiting = False  # Flag to prevent double exit
        
        # Hand washing state
        self.handwash_monitoring = False
        self.handwash_video_running = False
        self.handwash_current_worker = None
        self.handwash_start_time = None

        # Face registration state
        self.face_reg_video_running = False

        # Thread safety locks
        self._state_lock = threading.RLock()  # For protecting state changes
        self._timer_lock = threading.Lock()   # For protecting timer operations

        # Timers
        self.transition_timer = None # Timer for resetting after approve/deny message
        self.ppe_timer = None        # Timer for PPE detection timeout

        # Second chance state: dict mapping serial_number -> {'timestamp': float, 'attempt_count': int}
        self.failed_attempt_info = {}

        # Populate sample data if database is empty (and DB is available)
        if self.db:
            self._populate_sample_data_if_needed()

        logger.info("Cleanroom Access App initialization complete")

    def _populate_sample_data_if_needed(self):
        """Add some sample data to the database if empty"""
        try:
            # Check if we have any worker gowns
            if not self.db.get_worker_gowns():
                logger.info("Database appears empty, adding sample data...")
                # Add sample worker-gown associations
                self.db.add_worker_gown("0000000001", "John Smith")
                self.db.add_worker_gown("0000000002", "Jane Doe")
                self.db.add_worker_gown("0000000003", "Mark Johnson")

                # Add sample washing records
                self.db.add_washing_record("0000000001")
                time.sleep(0.1) # Ensure timestamp difference
                self.db.add_washing_record("0000000002")
                time.sleep(0.1)

                # Add sample compliance records - simulates first use
                self.db.add_compliance_record("John Smith", True, "0000000001")
                self.db.set_gown_first_use("0000000001") # Explicitly set first use
                time.sleep(0.1)
                self.db.add_compliance_record("Jane Doe", True, "0000000002")
                self.db.set_gown_first_use("0000000002") # Explicitly set first use
                time.sleep(0.1)

                logger.info("Sample data added to database")
        except Exception as e:
            logger.error(f"Error checking or adding sample data: {e}", exc_info=True)


    def start_system(self):
        """
        Start button pressed OR called after reset:
        1. Reset state
        2. Show processing screen
        3. Start RFID reader
        4. Wait for tag
        """
        logger.info("START SYSTEM: Waiting for RFID tag")

        # Reset any previous state thoroughly
        self.stop_system() # Ensure everything is stopped first
        self.running = True
        self.detection_active = False # Not yet in detection phase
        self.current_serial = None
        self.current_worker = None
        # Keep failed_attempt_info, it persists between scans

        # Cancel just in case a transition timer was pending from a rapid event
        if self.transition_timer:
             self.transition_timer.cancel()
             self.transition_timer = None

        if self.gui:
            self.gui.show_processing_screen()
            # Clear display
            self.gui.set_status("Waiting for RFID scan...", "info")
            self.gui.set_worker_info("", "")
        else:
             logger.info("GUI not loaded, proceeding without UI updates.")

        # If in mock mode, show a dialog instead of waiting for hardware
        if self.rfid_reader.mock_mode:
            # Need GUI to show dialog
            if self.gui and self.root:
                 self.handle_mock_rfid()
            else:
                 logger.info("Mock RFID enabled, but no GUI. Cannot prompt for input.")
                 # In a non-GUI test, you might simulate a tag here or exit
            return

        # Start the RFID reader
        logger.debug("Attempting to start RFID reader...")
        if not self.rfid_reader.start(self.on_rfid_tag_detected):
            logger.error("Failed to start RFID reader hardware.")
            if self.gui:
                self.gui.set_status("RFID Reader Error!", "error")
                # Offer fallback to mock mode if hardware failed
                if not self.rfid_reader.mock_mode:
                    if messagebox.askyesno("RFID Error", "Failed to start RFID reader.\nUse manual input mode?", parent=self.root):
                        logger.info("Falling back to mock mode due to hardware start failure.")
                        CONFIG['rfid']['enable_mock'] = True
                        self.rfid_reader.mock_mode = True # Update instance
                        self.handle_mock_rfid()
                    else:
                        # If user refuses mock, go back to main? Or stay on error screen? Go back.
                        self.gui.show_main_screen()
                        self.stop_system() # Ensure clean stop
            else:
                 # Non-GUI: Log and potentially enable mock automatically
                 logger.warning("RFID hardware failed, enabling mock mode automatically.")
                 CONFIG['rfid']['enable_mock'] = True
                 self.rfid_reader.mock_mode = True
        else:
             logger.info("RFID reader started successfully.")


    def handle_mock_rfid(self):
        """Handle mock RFID mode with dialog"""
        if not self.gui or not self.root:
             logger.warning("Cannot handle mock RFID without GUI.")
             return

        self.gui.set_status("Enter RFID tag manually", "info")
        # Use after to avoid blocking GUI thread
        self.root.after(100, self.show_mock_rfid_dialog)

    def show_mock_rfid_dialog(self):
        """Show dialog to enter RFID tag manually"""
        if not self.gui or not self.root: return

        serial = simpledialog.askstring("RFID Input", "Enter RFID Serial Number:", parent=self.root)
        if serial and serial.strip():
            # Process the manually entered tag
            self.root.after(0, self.on_rfid_tag_detected, serial.strip())
        else:
            # User cancelled, go back to main menu if appropriate
            logger.info("Manual RFID input cancelled by user.")
            # Only go back if system wasn't already in a reset loop
            # Check 'running' flag which start_system sets True
            if self.running:
                 self.gui.show_main_screen()
                 self.stop_system() # Ensure clean stop


    def stop_system(self):
        """Stop all detection systems and timers"""
        logger.debug("STOP SYSTEM called.")
        self.running = False # Mark as not actively processing a user flow
        self.detection_active = False # Ensure detection phase flag is off

        # Cancel timers safely
        with self._timer_lock:
            if self.transition_timer:
                logger.debug("Cancelling transition timer.")
                self.transition_timer.cancel()
                self.transition_timer = None
            if self.ppe_timer:
                logger.debug("Cancelling PPE timer.")
                self.ppe_timer.cancel()
                self.ppe_timer = None

        # Stop hardware/threads
        if hasattr(self.rfid_reader, 'stop') and self.rfid_reader.running:
             logger.debug("Stopping RFID reader...")
             self.rfid_reader.stop()
        if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running:
             logger.debug("Stopping YOLO detector...")
             self.yolo_detector.stop()
        if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running:
             logger.debug("Stopping GUI video...")
             self.gui.stop_video()
        
        # Stop hand washing monitoring if active
        if self.handwash_monitoring:
            logger.debug("Stopping hand washing monitoring...")
            self.stop_handwash_monitoring()
            
        # Stop face registration video if active
        if hasattr(self, 'face_reg_video_running'):
            self.face_reg_video_running = False
            
        logger.debug("Hardware/Threads stop commands issued.")


    def on_rfid_tag_detected(self, serial_number):
        """
        RFID tag detected: Check status, second chance, washing, expiry, then maybe start PPE.
        """
        # Thread-safe state check and update
        with self._state_lock:
            # Prevent processing if already in detection or system stopped
            if not self.running:
                logger.warning(f"RFID ({serial_number}) detected but system is not running. Ignoring.")
                return
            if self.detection_active:
                logger.warning(f"RFID ({serial_number}) detected but already processing another tag ({self.current_serial}). Ignoring.")
                return

            logger.info(f"RFID TAG DETECTED: {serial_number}")
            # Validate and sanitize the serial number
            try:
                from config import sanitize_serial_number
                serial_number = sanitize_serial_number(serial_number)
            except ValueError as e:
                logger.warning(f"Invalid serial number format: {e}")
                return

            self.current_serial = serial_number
            self.detection_active = True # Mark start of processing this tag

        # Ensure previous transition timer is cancelled if a new tag arrives quickly
        with self._timer_lock:
            if self.transition_timer:
                logger.debug("Cancelling pending transition timer due to new tag.")
                self.transition_timer.cancel()
                self.transition_timer = None

        if self.gui: self.gui.set_status("Checking gown status...", "info")

        is_second_attempt = False
        proceed_to_ppe = False

        # --- Check for Second Chance ---
        if serial_number in self.failed_attempt_info:
            info = self.failed_attempt_info[serial_number]
            time_since_fail = time.time() - info['timestamp']
            if time_since_fail < self.SECOND_CHANCE_WINDOW and info['attempt_count'] == 1:
                logger.info(f"Second attempt detected for {serial_number} within time window.")
                is_second_attempt = True
                # Bypass wash/expiry checks for second attempt
                # Fetch worker name directly
                self.current_worker = self.db.get_worker_name_for_gown(serial_number)
                if self.current_worker:
                    logger.info(f"Proceeding directly to PPE check for second attempt (Worker: {self.current_worker}).")
                    if self.gui: self.gui.set_worker_info(self.current_worker, serial_number)
                    proceed_to_ppe = True
                else:
                     logger.error(f"Cannot proceed with second attempt: Worker for gown {serial_number} not found.")
                     # Use root.after for GUI update from non-main thread if needed
                     if self.root:
                         self.root.after(0, lambda: self.show_access_denied("Gown/Worker data error. Cannot proceed."))
                     else:
                         self.show_access_denied("Gown/Worker data error. Cannot proceed.")
                     # Clean up failed attempt info as it's invalid now
                     if serial_number in self.failed_attempt_info: del self.failed_attempt_info[serial_number]
                     return # Stop processing this tag
            else:
                 # Failed attempt info exists but is expired or already used
                 logger.info(f"Removing expired/used failed attempt info for {serial_number}.")
                 if serial_number in self.failed_attempt_info: del self.failed_attempt_info[serial_number]
        # --- End Second Chance Check ---

        # --- Regular Check (if not a valid second attempt) ---
        if not is_second_attempt:
            try:
                # Check database: (is_washed, worker_name, is_expired, first_use_timestamp)
                is_washed, worker_name, is_expired, first_use_ts = self.db.check_gown_washed(serial_number)
                self.current_worker = worker_name

                if self.gui: self.gui.set_worker_info(worker_name or "Unknown", serial_number)

                if not worker_name:
                    logger.warning(f"Access Denied: Unknown gown ({serial_number}).")
                    security_logger.log_access_attempt("Unknown", sanitize_log_input(serial_number), False, "Unknown gown")
                    if self.root: self.root.after(0, lambda: self.show_access_denied("Unknown Gown! Access Denied."))
                    else: self.show_access_denied("Unknown Gown! Access Denied.")
                    return # Stop processing

                if is_expired:
                     logger.warning(f"Access Denied: Gown {serial_number} expired (Worker: {worker_name}, First Use: {first_use_ts}).")
                     security_logger.log_access_attempt(sanitize_log_input(worker_name), sanitize_log_input(serial_number), False, "Gown expired")
                     if self.root: self.root.after(0, lambda: self.show_access_denied("Gown Expired! Access Denied."))
                     else: self.show_access_denied("Gown Expired! Access Denied.")
                     return # Stop processing

                if not is_washed:
                    logger.warning(f"Access Denied: Gown {serial_number} not washed (Worker: {worker_name}).")
                    security_logger.log_access_attempt(sanitize_log_input(worker_name), sanitize_log_input(serial_number), False, "Gown not washed")
                    if self.root: self.root.after(0, lambda: self.show_access_denied("Gown Not Washed! Access Denied."))
                    else: self.show_access_denied("Gown Not Washed! Access Denied.")
                    return # Stop processing

                # If all checks pass, proceed to PPE detection
                logger.info(f"Gown verified for {worker_name} ({serial_number}). Proceeding to PPE check.")
                proceed_to_ppe = True

            except Exception as e:
                logger.error(f"Error during gown verification for {serial_number}: {e}", exc_info=True)
                if self.root: self.root.after(0, lambda: self.show_access_denied("System Error during gown check."))
                else: self.show_access_denied("System Error during gown check.")
                return # Stop processing
        # --- End Regular Check ---


        # --- Start PPE Detection if checks passed ---
        if proceed_to_ppe:
             # If this was a second attempt, update the count now
             if is_second_attempt and serial_number in self.failed_attempt_info:
                 self.failed_attempt_info[serial_number]['attempt_count'] = 2
                 logger.debug(f"Marked attempt count as 2 for {serial_number}")


             # Use root.after to ensure detector/GUI start happens in main thread
             if self.root:
                 self.root.after(0, self.start_ppe_detection)
             else:
                  self.start_ppe_detection() # Start directly in non-GUI
        else:
             # Should not happen if logic is correct, but as a fallback reset state
             logger.error("Reached end of on_rfid_tag_detected without proceeding or denying. Resetting.")
             if self.root: self.root.after(0, self.reset_for_next_tag)
             else: self.reset_for_next_tag()


    def show_access_denied(self, message, missing_ppe=None):
        """Show access denied with simple and reliable display"""
        logger.info(f"ACCESS DENIED: {message}")
        # Stop detector/video immediately on denial
        if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running: self.yolo_detector.stop()
        if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running: self.gui.stop_video()

        # Show denial message with organized PPE status
        if self.gui:
            try:
                # Get organized detection status
                organized_status = None
                if hasattr(self.yolo_detector, 'get_organized_detection_status'):
                    try:
                        organized_status = self.yolo_detector.get_organized_detection_status()
                    except Exception as e:
                        logger.error(f"Error getting organized status for GUI: {e}")
                
                # Disabled fullscreen decision display - only use popup
                # Update the status and show the processing screen
                self.gui.set_status(f"ACCESS DENIED: {message}", "error")
                self.gui.show_processing_screen()
            except:
                # Final fallback to basic status if anything fails
                try:
                    self.gui.set_status(f"ACCESS DENIED: {message}", "error")
                except:
                    pass
        
        # Also show a simple messagebox for immediate visibility (non-blocking)
        if self.root:
            try:
                # Use after to prevent blocking
                self.root.after(0, lambda: self._show_denial_popup(message, missing_ppe))
            except:
                pass

        # Cancel any running PPE timer
        if self.ppe_timer:
            logger.debug("Cancelling PPE timer due to access denial.")
            self.ppe_timer.cancel()
            self.ppe_timer = None

        # Clear failed attempt info if this denial isn't the first PPE failure warning
        if self.current_serial in self.failed_attempt_info:
            if "try again within 5 minutes" not in message.lower():
                 logger.debug(f"Clearing failed attempt info for {self.current_serial} on denial: {message}")
                 del self.failed_attempt_info[self.current_serial]

        # Schedule reset after display (3 seconds)
        with self._timer_lock:
            if self.transition_timer: 
                self.transition_timer.cancel()
            logger.debug(f"Scheduling reset timer (3.0s after simple denial)")
            self.transition_timer = threading.Timer(3.0, self.trigger_reset_from_timer)
            self.transition_timer.daemon = True
            self.transition_timer.start()

    def show_access_approved(self):
        """Show access approved with simple and reliable display"""
        logger.info(f"ACCESS APPROVED for {self.current_worker} ({self.current_serial})")
        # Stop detector/video immediately on approval
        if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running: self.yolo_detector.stop()
        if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running: self.gui.stop_video()

        # Show simple approval message
        if self.gui:
            try:
                success_message = f"Welcome, {self.current_worker}!"
                if hasattr(self.gui, 'show_simple_decision'):
                    self.gui.show_simple_decision("APPROVED", success_message, color="green")
                else:
                    # Fallback: update the status and show the processing screen with green message
                    self.gui.set_status(f"ACCESS APPROVED: Welcome, {self.current_worker}!", "info")
                    self.gui.show_processing_screen()  # Show a simple screen
            except:
                # Final fallback to basic status if anything fails
                try:
                    self.gui.set_status(f"ACCESS APPROVED: Welcome, {self.current_worker}!", "info")
                except:
                    pass
        
        # Also show a simple messagebox for immediate visibility (non-blocking)
        if self.root:
            try:
                # Use after to prevent blocking
                self.root.after(0, lambda: self._show_approval_popup())
            except:
                pass

        # Cancel any running PPE timer (should be cancelled already)
        if self.ppe_timer:
            self.ppe_timer.cancel()
            self.ppe_timer = None

        # Clear failed attempt info on success
        if self.current_serial in self.failed_attempt_info:
             logger.debug(f"Clearing failed attempt info for {self.current_serial} on success.")
             del self.failed_attempt_info[self.current_serial]

        # Schedule reset after display (3 seconds)
        with self._timer_lock:
            if self.transition_timer: 
                self.transition_timer.cancel()
            logger.debug(f"Scheduling reset timer (3.0s after simple approval)")
            self.transition_timer = threading.Timer(3.0, self.trigger_reset_from_timer)
            self.transition_timer.daemon = True
            self.transition_timer.start()

    def _show_denial_popup(self, message, missing_ppe=None):
        """Show enhanced denial popup with organized PPE status"""
        try:
            # Get organized detection status from YOLO detector
            organized_status = []
            if hasattr(self.yolo_detector, 'get_organized_detection_status'):
                try:
                    organized_status = self.yolo_detector.get_organized_detection_status()
                except Exception as e:
                    logger.error(f"Error getting organized status: {e}")
            
            # Build popup message
            popup_message = f"ACCESS DENIED\n\n{message}"
            
            # Add organized status if available
            if organized_status:
                popup_message += f"\n\nDetection Status:\n" + "\n".join(organized_status)
            elif missing_ppe and len(missing_ppe) > 0:
                # Fallback to old format if organized status not available
                popup_message += f"\nMissing PPE: {', '.join(missing_ppe)}"
            
            # Create a larger window to accommodate detailed status
            popup = tk.Toplevel(self.root)
            popup.title("Access Denied - PPE Status")
            popup.configure(bg='#8B0000')  # Dark red background
            popup.geometry("500x350")
            popup.resizable(False, False)
            
            # Center the popup
            popup.update_idletasks()
            x = (popup.winfo_screenwidth() // 2) - (500 // 2)
            y = (popup.winfo_screenheight() // 2) - (350 // 2)
            popup.geometry(f"500x350+{x}+{y}")
            
            # Make it stay on top
            popup.attributes("-topmost", True)
            popup.grab_set()
            
            # Create main frame
            main_frame = tk.Frame(popup, bg='#8B0000')
            main_frame.pack(expand=True, fill='both', padx=20, pady=20)
            
            # Title
            title_label = tk.Label(main_frame, text="ACCESS DENIED", 
                                 bg='#8B0000', fg='white', 
                                 font=('Arial', 18, 'bold'))
            title_label.pack(pady=(0, 10))
            
            # Message
            message_label = tk.Label(main_frame, text=message, 
                                   bg='#8B0000', fg='white', 
                                   font=('Arial', 12, 'bold'),
                                   wraplength=450, justify='center')
            message_label.pack(pady=(0, 15))
            
            # Organized status display
            if organized_status:
                status_frame = tk.Frame(main_frame, bg='#8B0000')
                status_frame.pack(pady=(0, 10))
                
                status_title = tk.Label(status_frame, text="PPE Detection Status:", 
                                      bg='#8B0000', fg='white', 
                                      font=('Arial', 14, 'bold'))
                status_title.pack(pady=(0, 5))
                
                # Create a frame for organized status lines
                for status_line in organized_status:
                    # Parse the status line to highlight issues
                    if "Absent" in status_line or "Incomplete" in status_line:
                        text_color = '#FFB6C1'  # Light pink for issues
                        font_weight = 'bold'
                    else:
                        text_color = '#90EE90'  # Light green for present
                        font_weight = 'normal'
                    
                    status_label = tk.Label(status_frame, text=status_line,
                                          bg='#8B0000', fg=text_color,
                                          font=('Arial', 11, font_weight))
                    status_label.pack(anchor='w', padx=20)
            elif missing_ppe:
                # Fallback display
                missing_label = tk.Label(main_frame, text=f"Missing PPE: {', '.join(missing_ppe)}", 
                                       bg='#8B0000', fg='#FFB6C1', 
                                       font=('Arial', 11, 'bold'),
                                       wraplength=450, justify='center')
                missing_label.pack(pady=(0, 10))
            
            # Auto-close timer display
            timer_label = tk.Label(main_frame, text="This window will close automatically in 4 seconds", 
                                 bg='#8B0000', fg='white', 
                                 font=('Arial', 9))
            timer_label.pack(side='bottom')
            
            # Auto-close after 4 seconds (longer to read detailed status)
            popup.after(4000, popup.destroy)
            
        except Exception as e:
            logger.error(f"Error showing enhanced denial popup: {e}")

    def _show_approval_popup(self):
        """Show simple approval popup"""
        try:
            popup_message = f"ACCESS APPROVED\n\nWelcome, {self.current_worker}!"
            
            # Create a simple top-level window
            popup = tk.Toplevel(self.root)
            popup.title("Access Approved")
            popup.configure(bg='green')
            popup.geometry("400x200")
            popup.resizable(False, False)
            
            # Center the popup
            popup.update_idletasks()
            x = (popup.winfo_screenwidth() // 2) - (400 // 2)
            y = (popup.winfo_screenheight() // 2) - (200 // 2)
            popup.geometry(f"400x200+{x}+{y}")
            
            # Make it stay on top
            popup.attributes("-topmost", True)
            popup.grab_set()
            
            # Add the message
            label = tk.Label(popup, text=popup_message, bg='green', fg='white', 
                           font=('Arial', 14, 'bold'), wraplength=350, justify='center')
            label.pack(expand=True, fill='both')
            
            # Auto-close after 2.5 seconds
            popup.after(2500, popup.destroy)
            
        except Exception as e:
            logger.error(f"Error showing approval popup: {e}")

    def trigger_reset_from_timer(self):
        """Helper function to call reset_for_next_tag from the timer thread via root.after"""
        logger.debug("Transition timer expired, triggering reset and restart.")
        
        # Clear timer reference safely
        with self._timer_lock:
            self.transition_timer = None
        
        if self.root:
             # Schedule reset_for_next_tag AND start_system
             self.root.after(0, self.reset_for_next_tag)
             # Add a slight delay before starting the next scan cycle
             self.root.after(100, self.start_system) # <--- ADDED: Restart scan automatically
        else:
             self.reset_for_next_tag() # Call directly if no GUI
             self.start_system() # Restart directly if no GUI


    def start_ppe_detection(self):
        """
        Start YOLO detection, video feed, and the PPE timeout timer. Should be called from main thread.
        """
        if not self.detection_active or not self.current_serial:
             logger.error("Cannot start PPE detection - invalid state. Resetting.")
             self.reset_for_next_tag() # Reset if called improperly
             return

        logger.info(f"Starting PPE detection for {self.current_worker} ({self.current_serial})")
        
        # Ensure YOLO detector is stopped before restarting
        try:
            if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running:
                logger.debug("Stopping YOLO detector before restart")
                self.yolo_detector.stop()
                time.sleep(0.2)  # Give time for proper cleanup
        except Exception as e:
            logger.warning(f"Error stopping YOLO detector before restart: {e}")
        
        # Reset PPE locked status for new detection session
        try:
            if hasattr(self.yolo_detector, 'reset_ppe_locked_status'):
                self.yolo_detector.reset_ppe_locked_status()
                logger.debug("PPE locked status reset for new detection session")
        except Exception as e:
            logger.warning(f"Error resetting PPE locked status: {e}")
        
        # Reset camera to prevent resource busy issues
        try:
            if hasattr(self.yolo_detector, 'reset_camera'):
                logger.debug("Resetting camera to prevent resource busy issues")
                self.yolo_detector.reset_camera()
        except Exception as e:
            logger.warning(f"Error resetting camera: {e}")
        
        # Get list of required PPE classes for display
        try:
            required_ppe = list(self.yolo_detector.ppe_classes.keys()) if self.yolo_detector else ["PPE"]
            ppe_list = ", ".join([ppe.title() for ppe in required_ppe])
            status_message = f"Checking PPE ({ppe_list})..."
        except Exception as e:
            logger.warning(f"Error getting PPE class list: {e}")
            status_message = "Checking PPE..."
            
        # SHOW PROCESSING SCREEN IMMEDIATELY FOR OPTIMAL PERFORMANCE
        if self.gui:
            # Switch to processing screen immediately without any animations
            self.gui.show_processing_screen()
            self.gui.set_status(status_message, "info")

        # Start the YOLO detector with callback for detection events and critical error handling
        logger.debug(f"Attempting to start YOLO detector for worker {self.current_worker}")
        detector_started = False
        
        # Try to start the detector with retry logic
        for attempt in range(3):
            try:
                detector_started = self.yolo_detector.start(self.on_ppe_detection, self.on_critical_detector_error)
                if detector_started:
                    logger.info(f"YOLO detector started successfully on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(f"YOLO detector start attempt {attempt + 1} returned False")
                    if attempt < 2:  # Not the last attempt
                        time.sleep(0.5)  # Wait before retry
            except Exception as e:
                logger.error(f"YOLO detector start attempt {attempt + 1} failed: {e}")
                if attempt < 2:  # Not the last attempt
                    time.sleep(0.5)  # Wait before retry
        
        if not detector_started:
            logger.error("Failed to start YOLO detector after 3 attempts")
            self.show_access_denied("Camera/Detector Error!") # This schedules reset
            return

        # Start video display in GUI with reduced frame rate for stability
        if self.gui:
            try:
                # Reduce video update frequency during PPE detection for stability
                self.gui._video_update_interval = 50  # ~20 FPS instead of 60
                self.gui.start_video(self.yolo_detector.get_current_frame)
                logger.info("Video display started with reduced frame rate for stability")
            except Exception as e:
                logger.error(f"Error starting GUI video display: {e}", exc_info=True)
                self.yolo_detector.stop() # Stop detector if GUI fails
                self.show_access_denied(f"GUI Video Error!") # This schedules reset
                return
        else:
             logger.info("GUI not loaded, skipping video display start.")

        # Start the PPE timeout timer with safety
        with self._timer_lock:
            if self.ppe_timer: 
                self.ppe_timer.cancel()
            logger.debug(f"Scheduling PPE timeout timer ({self.PPE_DETECTION_TIMEOUT}s)")
            self.ppe_timer = threading.Timer(self.PPE_DETECTION_TIMEOUT, self.on_ppe_timeout)
            self.ppe_timer.daemon = True
            self.ppe_timer.start()
            
        logger.info(f"PPE detection fully started for {self.current_worker}")
        
        # Log the specific PPE status for debugging
        try:
            self.yolo_detector.log_specific_ppe_status()
        except Exception as e:
            logger.warning(f"Error logging PPE status: {e}")


    def on_ppe_detection(self, all_ppe_detected, ppe_status=None):
        """
        Handle stable PPE detection state change from YOLODetector callback.
        IMPORTANT: This runs in the YOLODetector thread. Avoid blocking/joining here.
                 Use root.after for GUI updates or triggering state changes.
        
        Args:
            all_ppe_detected (bool): True if all required PPE classes are detected
            ppe_status (dict): Detailed status of each PPE class detection
        """
        logger.debug(f"on_ppe_detection called: all_detected={all_ppe_detected}, worker={self.current_worker}, serial={self.current_serial}, detection_active={self.detection_active}")
        
        # Only process if detection is active for the current tag
        if not self.detection_active or not self.current_serial:
            logger.debug(f"on_ppe_detection called ({all_ppe_detected}) but detection not active. Ignoring.")
            return

        if all_ppe_detected:
            logger.info(f"All PPE Detected for {self.current_worker} ({self.current_serial})")

            # Cancel the timeout timer immediately - detection was successful
            with self._timer_lock:
                if self.ppe_timer:
                    logger.debug("Cancelling PPE timer due to successful detection.")
                    self.ppe_timer.cancel()
                    self.ppe_timer = None
                else:
                     logger.warning("Successful PPE detection but PPE timer was already None.")

            # --- Actions to perform on success (schedule via root.after) ---
            # Define a helper function to run these in the main thread
            def process_ppe_success():
                 # Double-check state in case things changed rapidly
                 if not self.detection_active or self.current_serial is None:
                     logger.warning("process_ppe_success called but state changed. Aborting.")
                     return

                 logger.debug(f"Processing successful PPE detection for {self.current_worker} in main thread.")

                 # Set first use timestamp if needed
                 logger.debug(f"Attempting to set first use timestamp for {self.current_serial}")
                 self.db.set_gown_first_use(self.current_serial)

                 # Record compliance in database
                 logger.debug(f"Recording COMPLIANT record for {self.current_worker}, gown {self.current_serial}")
                 self.db.add_compliance_record(self.current_worker, True, self.current_serial)

                 # Log successful access for security audit
                 security_logger.log_access_attempt(sanitize_log_input(self.current_worker), sanitize_log_input(self.current_serial), True, "PPE compliance verified - All required PPE detected")

                 # Show access approved (this will stop detector/video and schedule reset)
                 logger.info(f"Showing access approved for {self.current_worker}")
                 self.show_access_approved()

            # Schedule the processing function
            if self.root:
                 logger.debug("Scheduling process_ppe_success via root.after")
                 self.root.after(0, process_ppe_success)
            else:
                 # Execute directly if no GUI/root (e.g., testing)
                 logger.debug("Executing process_ppe_success directly (no GUI)")
                 process_ppe_success()
            # --- End scheduled actions ---

        else:
            # Not all PPE detected - just log for debugging, let timer handle timeout
            try:
                ppe_issues = self.yolo_detector.get_ppe_issues() if self.yolo_detector else ["Unknown PPE issue"]
                missing_ppe = self.yolo_detector.get_missing_ppe() if self.yolo_detector else ["Unknown PPE"]
                issues_str = ", ".join(ppe_issues)
                logger.debug(f"PPE detection incomplete for {self.current_worker}. Issues: {issues_str}")
            except Exception as e:
                logger.warning(f"Error getting PPE issues: {e}")

    def on_ppe_timeout(self):
        """Called by the PPE timer thread if timeout expires."""
        logger.debug("PPE Timeout Timer triggered.")
        # Use root.after to run the timeout logic in the main thread safely
        if self.root:
            self.root.after(0, self._handle_ppe_timeout)
        else:
            self._handle_ppe_timeout() # Run directly if no GUI


    def _handle_ppe_timeout(self):
        """ Contains the actual logic for handling PPE timeout. Runs in main thread."""
        # Check if detection is still supposed to be active for this tag
        # Check if timer is already cleared (e.g. by success that happened just before timeout)
        if not self.detection_active or not self.current_serial or self.ppe_timer is None:
            logger.debug("_handle_ppe_timeout: Detection no longer active or timer cancelled. Ignoring.")
            # Ensure timer ref is cleared if it wasn't already
            if self.ppe_timer:
                 self.ppe_timer.cancel() # Cancel just in case
                 self.ppe_timer = None
            return

        # Get detailed PPE issues for better feedback
        try:
            ppe_issues = self.yolo_detector.get_ppe_issues() if self.yolo_detector else ["Unknown PPE issue"]
            missing_ppe = self.yolo_detector.get_missing_ppe() if self.yolo_detector else ["Unknown PPE"]
            issues_str = ", ".join(ppe_issues)
        except Exception as e:
            logger.warning(f"Error getting PPE issues during timeout: {e}")
            ppe_issues = ["Unknown PPE issue"]
            missing_ppe = ["Unknown PPE"]
            issues_str = "Unknown PPE issue"

        logger.warning(f"PPE detection TIMEOUT for {self.current_worker} ({self.current_serial}). Issues: {issues_str}")
        self.ppe_timer = None # Clear timer reference as it has fired

        # Stop YOLO detection and video
        if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running: self.yolo_detector.stop()
        if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running: self.gui.stop_video()

        # Record non-compliance with detailed issue information
        compliance_note = f"PPE detection timeout - Issues: {issues_str}"
        logger.debug(f"Recording NON-COMPLIANT record (Timeout) for {self.current_worker}, gown {self.current_serial}")
        self.db.add_compliance_record(self.current_worker or "Unknown Worker", False, self.current_serial)

        # Log failed access for security audit with detailed information
        security_logger.log_access_attempt(
            sanitize_log_input(self.current_worker or "Unknown"), 
            sanitize_log_input(self.current_serial), 
            False, 
            f"PPE detection timeout - Issues: {issues_str}"
        )

        # --- Handle Second Chance ---
        is_first_attempt = self.current_serial not in self.failed_attempt_info
        attempt_count = self.failed_attempt_info.get(self.current_serial, {}).get('attempt_count', 0)

        if is_first_attempt or attempt_count == 1:
             # This was the first failure for this session
             logger.info(f"First PPE failure for {self.current_serial}. Issues: {issues_str}. Storing info for second chance.")
             self.failed_attempt_info[self.current_serial] = {
                 'timestamp': time.time(),
                 'attempt_count': 1, # Mark as first attempt failed
                 'missing_ppe': ppe_issues  # Store detailed issues for reference
             }
             # Show warning message with specific PPE issues
             if len(ppe_issues) == 1:
                 warning_message = f"{ppe_issues[0].title()}! Try again within 5 minutes."
             else:
                 primary_issue = ppe_issues[0].title()
                 warning_message = f"{primary_issue} and more! Try again within 5 minutes."
             
             # Show fullscreen warning with detailed PPE issues
             self.show_access_denied(warning_message, ppe_issues)

        else:
             # This was the second failure
             logger.warning(f"Second PPE failure for {self.current_serial}. Still have issues: {issues_str}. Final Access Denied.")
             # Clean up failed attempt info
             if self.current_serial in self.failed_attempt_info:
                  del self.failed_attempt_info[self.current_serial]
             # Show final denial message with specific PPE issues
             if len(ppe_issues) == 1:
                 denial_message = f"Still have {ppe_issues[0]}! Access Denied."
             else:
                 primary_issue = ppe_issues[0].title()
                 denial_message = f"Still have {primary_issue} and more! Access Denied."
             
             # Show fullscreen final denial with detailed PPE issues
             self.show_access_denied(denial_message, ppe_issues)

    def on_critical_detector_error(self, error_message):
        """Handle critical errors from the YOLO detector"""
        logger.critical(f"Critical detector error: {error_message}")
        
        # Stop detection immediately
        if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running: 
            self.yolo_detector.stop()
        if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running: 
            self.gui.stop_video()

        # Show error and reset
        self.show_access_denied(f"Critical System Error: {error_message}")
        
        # Log this as a system event for maintenance
        if self.db:
            try:
                self.db.add_compliance_record("SYSTEM", False, "CRITICAL_ERROR")
            except Exception as e:
                logger.error(f"Failed to log critical error: {e}")

    def reset_for_next_tag(self):
        """Reset state for next RFID tag detection (no animations for optimal performance)"""
        # Thread-safe state reset
        with self._state_lock:
            self.current_serial = None
            self.current_worker = None
            self.detection_active = False

        # Cleanup timers safely
        with self._timer_lock:
            if self.ppe_timer:
                self.ppe_timer.cancel()
                self.ppe_timer = None
            # Note: transition_timer is managed by itself and doesn't need cancellation here

        # Stop components and reset camera to prevent resource busy issues
        try:
            if hasattr(self.yolo_detector, 'stop') and self.yolo_detector.running:
                logger.debug("Stopping YOLO detector from reset.")
                self.yolo_detector.stop()
                # Add a brief delay to ensure camera is fully released
                time.sleep(0.1)
                
            # Reset camera to prevent resource busy issues for next worker scan
            if hasattr(self.yolo_detector, 'reset_camera'):
                logger.debug("Resetting camera to prevent resource busy issues.")
                self.yolo_detector.reset_camera()
        except Exception as e:
            logger.error(f"Error stopping/resetting YOLO detector: {e}", exc_info=True)

        try:
            if self.gui and hasattr(self.gui, 'stop_video') and self.gui.video_running:
                logger.debug("Stopping GUI video from reset.")
                self.gui.stop_video()
        except Exception as e:
            logger.error(f"Error stopping GUI video: {e}", exc_info=True)

        # Reset GUI to main screen immediately with premium styling
        if self.gui:
            try:
                # Reset video frame rate to normal
                self.gui._video_update_interval = 16  # Reset to default
                # Show main screen immediately without animations
                self.gui.show_main_screen()
                self.gui.set_status("Ready", "info")
                self.gui.set_worker_info("", "")
                logger.debug("GUI reset to main screen immediately")
            except Exception as e:
                logger.error(f"Error resetting GUI: {e}", exc_info=True)

        logger.info("System reset for next tag completed")
        
        # Force cleanup any lingering camera resources to prevent conflicts
        try:
            from yolo_detector import YOLODetector
            YOLODetector.force_camera_cleanup()
            logger.debug("Forced cleanup of camera resources completed")
        except Exception as e:
            logger.warning(f"Error in force camera cleanup: {e}")


    def show_records(self):
        """Show records screen with database data"""
        if not self.gui:
             logger.warning("Cannot show records without GUI.")
             return
        logger.info("Showing records screen")
        self.stop_system() # Stop active processing before switching screens
        # Switch to records screen
        self.gui.show_record_screen()
        # Fetch and display worker-gown associations (default table)
        # The GUI's show_record_screen now triggers the update via callback
        # self.update_records_display(1) # No longer needed here


    def update_records_display(self, table_id):
        """Update records table with data from database"""
        if not self.gui: return
        logger.debug(f"Updating records display for table ID: {table_id}")
        try:
            records = []
            if table_id == 1:  # Worker-Gown Associations
                records = self.db.get_worker_gowns_with_details() # Use detailed fetch
            elif table_id == 2:  # Washing Records
                records = self.db.get_washing_records()
            elif table_id == 3:  # Compliance Records
                records = self.db.get_compliance_records()
            elif table_id == 4:  # Hand Washing Records
                records = self.db.get_hand_washing_records()

            # Ensure GUI update runs in main thread
            if self.root:
                 self.root.after(0, self.gui.update_records_table, table_id, records)
            else:
                 self.gui.update_records_table(table_id, records)

        except Exception as e:
            logger.error(f"Error updating records display (Table ID {table_id}): {e}", exc_info=True)
            # Check if GUI is still valid before showing messagebox
            if self.gui and self.root and self.root.winfo_exists():
                 messagebox.showerror("Error", f"Failed to update records: {str(e)}", parent=self.root)


    def manage_workers(self):
        """Open worker management window"""
        if not self.gui or not self.db:
             logger.warning("Cannot manage workers without GUI and DB.")
             return
        logger.info("Opening worker management window")
        self.stop_system() # Stop active processing before opening modal window
        try:
            # Pass the main window (root) and db instance
            WorkerManagementGUI(self.root, self.db)
        except Exception as e:
            logger.error(f"Error opening worker management window: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to open worker management: {str(e)}", parent=self.root)


    def exit_system(self):
        """Clean exit of the application"""
        if self.exiting:
            logger.debug("Exit already in progress, skipping.")
            return
        
        self.exiting = True
        logger.info("EXIT SYSTEM command received.")
        
        # Stop all monitoring systems and cameras
        try:
            if self.handwash_monitoring:
                logger.info("Stopping hand washing monitoring before exit")
                self.stop_handwash_monitoring()
                
            if self.face_reg_video_running:
                logger.info("Stopping face registration before exit")
                self.stop_face_registration()
                
            # Ensure hand washing detector is stopped
            if self.hand_washing_detector and self.hand_washing_detector.running:
                logger.info("Stopping hand washing detector camera before exit")
                self.hand_washing_detector.stop()
                
        except Exception as e:
            logger.error(f"Error stopping monitoring systems during exit: {e}", exc_info=True)
        
        self.stop_system() # Ensure all threads/hardware are stopped
        
        if self.db:
            try:
                 self.db.close()
                 logger.info("Database connection closed.")
            except Exception as e:
                 logger.error(f"Error closing database: {e}", exc_info=True)

        if self.root:
            logger.info("Destroying GUI root window.")
            try:
                # Check if window still exists before destroying
                if self.root.winfo_exists():
                    self.root.destroy() # Use destroy instead of quit for cleaner exit
                else:
                    logger.debug("GUI root window already destroyed.")
            except Exception as e:
                 logger.error(f"Error destroying root window: {e}", exc_info=True)
        logger.info("Cleanroom Access System Exited.")


    def run(self):
        """Start the main application loop (if GUI is loaded)"""
        if not self.root:
             logger.info("Running in non-GUI mode. Application initialized but not entering main loop.")
             # Start listening immediately in non-GUI mode?
             # self.start_system()
             # while True: time.sleep(1) # Keep main thread alive
             return

        try:
            logger.info("Starting Tkinter main loop...")
            # Start in the main menu state
            self.gui.show_main_screen()
            self.root.mainloop()
            logger.info("Tkinter main loop finished.")
        except KeyboardInterrupt:
             logger.info("KeyboardInterrupt received. Exiting.")
        except Exception as e:
            logger.critical(f"FATAL ERROR in main loop: {e}", exc_info=True)
            try: # Attempt to show final error
                 messagebox.showerror("Fatal Error", f"Application crashed: {str(e)}")
            except: pass
        finally:
            # Ensure cleanup happens even if mainloop exits unexpectedly
            self.exit_system()

    def handle_handwash(self):
        """Handle hand washing button click - show hand washing screen"""
        try:
            logger.info("Hand washing monitor requested")
            if self.gui:
                self.stop_system()  # Stop any active processing
                self.gui.show_handwash_screen()
                # Reset hand washing status display
                self.gui.update_handwash_status()
            else:
                logger.warning("Cannot show hand washing screen without GUI")
        except Exception as e:
            logger.error(f"Error handling hand washing request: {e}", exc_info=True)

    def start_handwash_monitoring(self):
        """Start hand washing monitoring"""
        try:
            if not self.hand_washing_detector:
                logger.error("Hand washing detector not available")
                if self.gui:
                    messagebox.showerror("Error", "Hand washing detector not available", parent=self.root)
                return False
                
            if self.handwash_monitoring:
                logger.warning("Hand washing monitoring already active")
                return True
                
            logger.info("Starting hand washing monitoring")
            
            # Start the hand washing detector
            if self.hand_washing_detector.start(
                callback=self.on_handwash_event,
                critical_error_callback=self.on_handwash_critical_error
            ):
                self.handwash_monitoring = True
                self.handwash_current_worker = None
                self.handwash_start_time = None
                
                # Update GUI buttons
                if self.gui:
                    self.gui.handwash_start_btn.config(state='disabled')
                    self.gui.handwash_stop_btn.config(state='normal')
                    
                    # Start video display for hand washing
                    self.handwash_video_running = True
                    self._start_handwash_video_loop()
                    
                logger.info("Hand washing monitoring started successfully")
                return True
            else:
                logger.error("Failed to start hand washing detector")
                if self.gui:
                    messagebox.showerror("Error", "Failed to start hand washing monitoring", parent=self.root)
                    
        except Exception as e:
            logger.error(f"Error starting hand washing monitoring: {e}", exc_info=True)
            if self.gui:
                messagebox.showerror("Error", f"Failed to start monitoring: {str(e)}", parent=self.root)
            return False

    def stop_handwash_monitoring(self):
        """Stop hand washing monitoring"""
        try:
            logger.info("Stopping hand washing monitoring")
            
            self.handwash_monitoring = False
            self.handwash_video_running = False
            
            # Stop the detector (and camera)
            if self.hand_washing_detector:
                logger.info("Stopping hand washing detector and camera")
                self.hand_washing_detector.stop()
                
            # Update GUI buttons
            if self.gui:
                try:
                    self.gui.handwash_start_btn.config(state='normal')
                    self.gui.handwash_stop_btn.config(state='disabled')
                    # Reset status display
                    self.gui.update_handwash_status()
                except Exception as gui_e:
                    logger.error(f"Error updating GUI after stopping handwash: {gui_e}")
                    
            logger.info("Hand washing monitoring stopped")
            
        except Exception as e:
            logger.error(f"Error stopping hand washing monitoring: {e}", exc_info=True)

    def register_worker_face(self):
        """Transition to face registration screen"""
        try:
            if not self.hand_washing_detector:
                logger.error("Hand washing detector not available")
                if self.gui:
                    messagebox.showerror("Error", "Hand washing detector not available", parent=self.root)
                return
            
            # Start the camera specifically for face registration if not already running
            # We don't need full hand washing monitoring, just the camera
            if not self.hand_washing_detector.running:
                logger.info("Starting camera for face registration")
                if not self.hand_washing_detector.start(camera_only=True):
                    logger.error("Failed to start camera for face registration")
                    if self.gui:
                        messagebox.showerror("Error", "Failed to start camera for face registration", parent=self.root)
                    return
                # Give it a moment to start
                time.sleep(1.0)
            
            # Transition to face registration screen
            if self.gui:
                self.gui.show_face_registration_screen()
                # Start the video update loop for the face registration screen
                self._start_face_reg_video_loop()
                # Update initial status
                self.gui.update_face_reg_status("Face registration ready. Enter worker name and click Start Registration.")
                    
        except Exception as e:
            logger.error(f"Error transitioning to face registration: {e}", exc_info=True)
            if self.gui:
                messagebox.showerror("Error", f"Failed to start face registration: {str(e)}", parent=self.root)

    def start_face_registration(self, worker_name):
        """Start the multi-angle face registration process"""
        try:
            if not self.hand_washing_detector:
                if self.gui:
                    self.gui.update_face_reg_status("Hand washing detector not available")
                return False
            
            # Ensure camera is active (either through hand washing monitoring or direct camera access)
            if not self.hand_washing_detector.running:
                if self.gui:
                    self.gui.update_face_reg_status("Camera not available. Please ensure face registration camera is started.")
                return False
            
            # Initialize registration state
            self.registration_state = {
                'active': True,
                'phase': 0,
                'captured_encodings': [],
                'captures_per_phase': 3,
                'current_capture': 0,
                'start_time': time.time(),
                'worker_name': worker_name
            }
            
            # Update GUI
            if self.gui:
                self.gui.face_reg_start_btn.config(state='disabled')
                self.gui.face_reg_stop_btn.config(state='normal')
                self.gui.face_reg_progress['maximum'] = 5 * self.registration_state['captures_per_phase']
                self.gui.face_reg_progress['value'] = 0
                self.gui.update_face_reg_status(f"Starting registration for {worker_name}...")
            
            # Start first phase
            self._next_registration_phase()
            return True
                    
        except Exception as e:
            logger.error(f"Error starting face registration: {e}", exc_info=True)
            if self.gui:
                self.gui.update_face_reg_status(f"Error starting registration: {str(e)}")
            return False

    def stop_face_registration(self):
        """Stop the face registration process"""
        try:
            if hasattr(self, 'registration_state'):
                self.registration_state['active'] = False
            
            # Stop face registration video loop
            self.face_reg_video_running = False
            
            # If hand washing monitoring is not active, OR if we're in camera-only mode, stop the camera
            # (it was started only for face registration)
            should_stop_camera = (
                not self.handwash_monitoring or 
                (self.hand_washing_detector and getattr(self.hand_washing_detector, 'camera_only_mode', False))
            )
            
            if should_stop_camera and self.hand_washing_detector and self.hand_washing_detector.running:
                logger.info("Stopping camera that was started for face registration")
                self.hand_washing_detector.stop()
            
            # Reset GUI
            if self.gui:
                self.gui.face_reg_start_btn.config(state='normal')
                self.gui.face_reg_stop_btn.config(state='disabled')
                self.gui.face_reg_progress['value'] = 0
                self.gui.update_face_reg_status("Registration stopped by user")
                
        except Exception as e:
            logger.error(f"Error stopping face registration: {e}", exc_info=True)

    def get_face_reg_video_frame(self):
        """Get current video frame for face registration display"""
        try:
            if self.hand_washing_detector:
                return self.hand_washing_detector.get_current_frame()
            return None
        except Exception as e:
            logger.error(f"Error getting face registration video frame: {e}")
            return None

    def _start_face_reg_video_loop(self):
        """Start video loop for face registration display"""
        if not self.gui:
            return
        
        # Set a flag to control this video loop
        self.face_reg_video_running = True
            
        def video_loop():
            frame_skip_counter = 0
            while self.face_reg_video_running and hasattr(self, 'gui') and hasattr(self.gui, 'face_reg_video_label'):
                try:
                    frame = self.get_face_reg_video_frame()
                    if frame is not None and self.gui:
                        # Optimize: Skip fewer frames for higher FPS
                        frame_skip_counter += 1
                        if frame_skip_counter >= 2:  # Skip every 2nd frame instead of 3rd
                            frame_skip_counter = 0
                            # Add registration overlay if active
                            display_frame = self._add_registration_overlay(frame)
                            # Use after_idle to prevent GUI blocking
                            try:
                                self.root.after_idle(self.gui.show_face_reg_video, display_frame)
                            except:
                                pass
                    time.sleep(0.03)  # ~33 FPS target - faster than before
                except Exception as e:
                    logger.error(f"Error in face registration video loop: {e}")
                    time.sleep(0.1)  # Prevent rapid error loops
                    
        video_thread = threading.Thread(target=video_loop, name="FaceRegVideoThread")
        video_thread.daemon = True
        video_thread.start()

    def _add_registration_overlay(self, frame):
        """Add visual guidance overlay to the registration frame"""
        try:
            if not hasattr(self, 'registration_state') or not self.registration_state.get('active', False):
                # Frame is already mirrored from hand washing detector, just return it
                return frame
            
            # Frame is already mirrored from hand washing detector, work directly with it
            display_frame = frame.copy()
            
            phase = self.registration_state.get('phase', 0)
            current_capture = self.registration_state.get('current_capture', 0)
            max_captures = self.registration_state.get('captures_per_phase', 3)
            
            phase_names = ["Front", "Turn Left", "Turn Right", "Look Slightly Up", "Look Slightly Down"]
            if phase < len(phase_names):
                # Add phase indicator
                cv2.putText(display_frame, f"PHASE: {phase_names[phase]}", 
                          (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                
                # Add capture progress
                cv2.putText(display_frame, f"CAPTURE: {current_capture}/{max_captures}", 
                          (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                
                # Add directional guidance
                h, w = display_frame.shape[:2]
                center_x, center_y = w // 2, h // 2
                
                # Draw face guide circle
                cv2.circle(display_frame, (center_x, center_y), 100, (0, 255, 0), 2)
                
                # Draw direction arrows and text based on phase (adjusted for mirrored display)
                if phase == 1:  # Turn Left - arrow points right in mirrored view (user's left)
                    cv2.arrowedLine(display_frame, (center_x, center_y), 
                                  (center_x + 80, center_y), (255, 0, 0), 5)
                    cv2.putText(display_frame, "TURN LEFT", (center_x - 100, center_y + 140), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                elif phase == 2:  # Turn Right - arrow points left in mirrored view (user's right)
                    cv2.arrowedLine(display_frame, (center_x, center_y), 
                                  (center_x - 80, center_y), (255, 0, 0), 5)
                    cv2.putText(display_frame, "TURN RIGHT", (center_x - 50, center_y + 140), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                elif phase == 3:  # Up
                    cv2.arrowedLine(display_frame, (center_x, center_y), 
                                  (center_x, center_y - 80), (255, 0, 0), 5)
                    cv2.putText(display_frame, "LOOK UP", (center_x - 60, center_y + 140), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                elif phase == 4:  # Down
                    cv2.arrowedLine(display_frame, (center_x, center_y), 
                                  (center_x, center_y + 80), (255, 0, 0), 5)
                    cv2.putText(display_frame, "LOOK DOWN", (center_x - 70, center_y + 140), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                else:  # Front
                    cv2.putText(display_frame, "LOOK STRAIGHT", (center_x - 90, center_y + 140), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            
            return display_frame
            
        except Exception as e:
            logger.error(f"Error adding registration overlay: {e}")
            return frame  # Return original frame even if overlay fails

    def on_handwash_event(self, event_type, data):
        """Handle events from the hand washing detector"""
        try:
            logger.info(f"Hand washing event: {event_type} - {data}")
            
            if event_type == 'worker_detected':
                # data is now the worker name string directly
                worker_name = data if isinstance(data, str) else 'Unknown'
                self.handwash_current_worker = worker_name
                if self.gui:
                    self.gui.update_handwash_status(worker_name=worker_name)
                    
            elif event_type == 'washing_started':
                self.handwash_start_time = time.time()
                if self.gui:
                    self.gui.update_handwash_status(quality="Washing...", quality_color=self.gui.colors['accent_blue'])
                    
            elif event_type == 'washing_completed':
                # data is now a dictionary with duration, quality, etc.
                duration = data.get('duration', 0.0) if isinstance(data, dict) else 0.0
                quality = data.get('quality', 'unknown') if isinstance(data, dict) else 'unknown'
                worker_name = self.handwash_current_worker or 'Unknown'
                
                # Record in database
                if self.db:
                    self.db.add_hand_washing_record(worker_name, duration, quality)
                    
                # Update GUI with final results
                if self.gui:
                    quality_colors = {
                        'good': self.gui.colors['success'],
                        'moderate': self.gui.colors['warning'],
                        'bad': self.gui.colors['error']
                    }
                    quality_color = quality_colors.get(quality, self.gui.colors['text_primary'])
                    self.gui.update_handwash_status(
                        duration=f"{duration:.1f}s",
                        quality=quality.title(),
                        quality_color=quality_color
                    )
                    
                logger.info(f"Hand washing completed: {worker_name}, {duration:.1f}s, {quality}")
                
        except Exception as e:
            logger.error(f"Error handling hand washing event: {e}", exc_info=True)

    def on_handwash_critical_error(self, error_message):
        """Handle critical errors from hand washing detector"""
        logger.critical(f"Critical hand washing detector error: {error_message}")
        
        # Stop monitoring
        self.stop_handwash_monitoring()
        
        # Show error to user
        if self.gui:
            messagebox.showerror("Critical Error", f"Hand washing monitoring failed: {error_message}", parent=self.root)

    def _start_handwash_video_loop(self):
        """Start video loop for hand washing display"""
        if not self.handwash_video_running or not self.gui:
            return
            
        def video_loop():
            frame_skip_counter = 0
            while self.handwash_video_running:
                try:
                    if self.hand_washing_detector:
                        frame = self.hand_washing_detector.get_current_frame()
                        if frame is not None and self.gui:
                            # Optimize: Skip fewer frames for higher FPS
                            frame_skip_counter += 1
                            if frame_skip_counter >= 2:  # Skip every 2nd frame instead of 3rd
                                frame_skip_counter = 0
                                # Use a queue-like approach to prevent GUI thread blocking
                                try:
                                    self.root.after_idle(self.gui.show_handwash_video, frame)
                                except:
                                    pass
                    time.sleep(0.03)  # ~33 FPS target - faster than before
                except Exception as e:
                    logger.error(f"Error in handwash video loop: {e}")
                    time.sleep(0.1)  # Prevent rapid error loops
                    
        video_thread = threading.Thread(target=video_loop, name="HandwashVideoThread")
        video_thread.daemon = True
        video_thread.start()

    def _next_registration_phase(self):
        """Move to the next phase of registration"""
        try:
            if not self.registration_state['active']:
                return
            
            phase = self.registration_state['phase']
            phase_names = ["Front", "Turn Left", "Turn Right", "Look Slightly Up", "Look Slightly Down"]
            phase_instructions = [
                "Look straight at the camera",
                "Turn your head slowly to the LEFT (your left)",
                "Turn your head slowly to the RIGHT (your right)", 
                "Tilt your head slightly UP",
                "Tilt your head slightly DOWN"
            ]
            
            if phase >= len(phase_names):
                # All phases completed
                self._complete_registration()
                return
            
            # Update status
            self.gui.update_face_reg_status(f"Phase {phase + 1}/5: {phase_names[phase]} - {phase_instructions[phase]}")
            
            # Reset capture counter for new phase
            self.registration_state['current_capture'] = 0
            
            # Start capturing for this phase
            self._capture_phase_images()
            
        except Exception as e:
            logger.error(f"Error in registration phase: {e}", exc_info=True)
            self.gui.update_face_reg_status(f"Error in registration: {str(e)}")

    def _capture_phase_images(self):
        """Capture images for the current phase"""
        try:
            if not self.registration_state['active'] or not self.gui:
                return
            
            # Define phase names at the beginning of the method
            phase_names = ["Front", "Turn Left", "Turn Right", "Look Slightly Up", "Look Slightly Down"]
            
            phase = self.registration_state['phase']
            capture = self.registration_state['current_capture']
            max_captures = self.registration_state['captures_per_phase']
            
            if capture >= max_captures:
                # Phase completed, move to next
                self.registration_state['phase'] += 1
                self.registration_state['current_capture'] = 0
                self.gui.update_face_reg_status(f"✓ Captured {capture + 1}/{max_captures} for {phase_names[phase]} - Hold position...")
                self.gui.face_reg_progress['value'] = (phase * max_captures) + capture + 1
                
                self.root.after(1000, self._next_registration_phase)
            else:
                # Get current frame
                frame = self.hand_washing_detector.get_current_frame()
                if frame is None:
                    # Retry after a short delay
                    self.root.after(200, self._capture_phase_images)
                    return
                
                # Make a copy of the frame to prevent memory issues during face recognition
                frame_copy = frame.copy()
                
                # Try to extract face encoding using the actual worker name
                worker_name = self.registration_state['worker_name']
                logger.debug(f"Attempting to capture face for worker: {worker_name}, Phase: {phase + 1}, Capture: {capture + 1}/{max_captures}")
                face_encoding = self.hand_washing_detector.add_worker_face(worker_name, frame_copy)
                
                if face_encoding is not False and face_encoding is not None:
                    # Successfully captured a face
                    self.registration_state['captured_encodings'].append(face_encoding)
                    self.registration_state['current_capture'] += 1
                    
                    # Update progress
                    total_progress = (phase * max_captures) + capture + 1
                    self.gui.face_reg_progress['value'] = total_progress
                    
                    self.gui.update_face_reg_status(f"✓ Captured {capture + 1}/{max_captures} for {phase_names[phase]} - Hold position...")
                    
                    logger.info(f"Successfully captured face encoding {capture + 1}/{max_captures} for phase {phase + 1} ({phase_names[phase]}) for worker {worker_name}")
                    
                    # Wait a bit before next capture
                    self.root.after(800, self._capture_phase_images)
                else:
                    # No face detected, retry
                    retry_msg = f"No face detected - {phase_names[phase]} position - Please position your face clearly in view"
                    self.gui.update_face_reg_status(retry_msg)
                    logger.debug(f"Face detection failed for worker {worker_name}, retrying...")
                    self.root.after(300, self._capture_phase_images)
                
        except Exception as e:
            logger.error(f"Error capturing phase images: {e}", exc_info=True)
            self.gui.update_face_reg_status(f"Error during capture: {str(e)}")

    def _complete_registration(self):
        """Complete the multi-angle registration process"""
        try:
            encodings = self.registration_state['captured_encodings']
            total_time = time.time() - self.registration_state['start_time']
            
            if len(encodings) < 5:  # Minimum 5 encodings (1 per phase)
                self.gui.update_face_reg_status(f"Registration incomplete - only {len(encodings)} face captures. Please try again.")
                logger.warning(f"Insufficient face captures: {len(encodings)}")
                return
            
            # Get worker name from dialog (assuming it's available in the dialog)
            # We'll need to pass this through the process
            worker_name = self.registration_state['worker_name']
            
            # Save all encodings
            success = self.hand_washing_detector.add_worker_face_multiple(worker_name, encodings)
            
            if success:
                self.gui.update_face_reg_status(f"✅ Registration completed successfully!")
                self.gui.face_reg_progress['value'] = self.gui.face_reg_progress['maximum']
                
                logger.info(f"Successfully registered {len(encodings)} face encodings for {worker_name} in {total_time:.1f} seconds")
                
                # Show success and close dialog after delay
                def close_with_success():
                    try:
                        if self.gui:
                            messagebox.showinfo("Success", 
                                f"Face registration completed successfully for {worker_name}!\n"
                                f"Captured {len(encodings)} face images from multiple angles.\n"
                                f"Process took {total_time:.1f} seconds.",
                                parent=self.root)
                            # Navigate back to the correct screen based on source
                            if self.gui.face_reg_source == 'handwash':
                                self.gui.show_handwash_screen()
                            else:
                                self.gui.show_main_screen()
                    except:
                        pass
                
                self.root.after(2000, close_with_success)
            else:
                self.gui.update_face_reg_status("❌ Failed to save face registration")
                
        except Exception as e:
            logger.error(f"Error completing registration: {e}", exc_info=True)
            self.gui.update_face_reg_status(f"Error completing registration: {str(e)}")

    def register_worker_face_independent(self):
        """Transition to face registration screen independently from main menu"""
        try:
            if not self.hand_washing_detector:
                logger.error("Hand washing detector not available")
                if self.gui:
                    messagebox.showerror("Error", "Hand washing detector not available", parent=self.root)
                return
            
            # Start the camera specifically for face registration if not already running
            if not self.hand_washing_detector.running:
                logger.info("Starting camera for independent face registration")
                if not self.hand_washing_detector.start(camera_only=True):
                    logger.error("Failed to start camera for face registration")
                    if self.gui:
                        messagebox.showerror("Error", "Failed to start camera for face registration", parent=self.root)
                    return
                # Give it a moment to start
                time.sleep(1.0)
            
            # Transition to face registration screen
            if self.gui:
                self.gui.show_face_registration_screen()
                # Start the video update loop for the face registration screen
                self._start_face_reg_video_loop()
                # Update initial status
                self.gui.update_face_reg_status("Face registration ready. Enter worker name and click Start Registration.")
                    
        except Exception as e:
            logger.error(f"Error transitioning to independent face registration: {e}", exc_info=True)
            if self.gui:
                messagebox.showerror("Error", f"Failed to start face registration: {str(e)}", parent=self.root)

if __name__ == "__main__":
    try:
        logger.info("--- Starting Cleanroom Access System ---")
        app = CleanroomAccessApp(load_gui=True) # Assume GUI is desired when run directly
        app.run()
    except SystemExit as e:
         logger.warning(f"System exit requested during initialization: {e}")
    except Exception as e:
        # Catch errors during initialization itself
        logger.critical(f"--- FATAL APPLICATION INITIALIZATION ERROR ---", exc_info=True)
        # Try showing a simple Tkinter error box if possible
        try:
             root = tk.Tk()
             root.withdraw() # Hide the main window
             messagebox.showerror("Fatal Initialization Error", f"Application failed to start:\n{str(e)}")
             root.destroy()
        except:
             print(f"FATAL ERROR: {e}") # Fallback to console print
    finally:
         logging.shutdown() # Ensure logs are flushed
         logger.info("--- Cleanroom Access System Stopped ---")

# --- END OF FILE app.py ---