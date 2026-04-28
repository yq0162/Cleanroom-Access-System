# --- START OF FILE firebase_database.py ---

import os
import time
import json
from datetime import datetime, timedelta, timezone # Added timezone
import logging
import threading
from config import CONFIG

# Import Firebase modules with error handling for offline mode
try:
    import firebase_admin
    from firebase_admin import credentials
    from firebase_admin import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter # Use FieldFilter for modern queries
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    FieldFilter = None # Define dummy if not available

logger = logging.getLogger(__name__)

class FirebaseDatabase:
    # Gown lifetime constant
    GOWN_LIFETIME_DAYS = 7

    def __init__(self, credential_path=None):
        """Initialize Firebase database connection"""
        # Get config values
        db_config = CONFIG.get('database', {})
        self.credential_path = credential_path or db_config.get('credential_path', 'firebase-credentials.json')
        self.offline_mode = db_config.get('enable_offline', False)

        # For offline mode, we'll store data in memory - INITIALIZE BEFORE _setup_database
        self.offline_data = {
            'worker_gowns': {},
            'washing_records': [],
            'compliance_records': [],
            'hand_washing_records': []
        }

        # Lock for thread safety in offline mode
        self.data_lock = threading.Lock()

        # Set up database
        self.db = None
        self._setup_database()

        # Create sample data if collections are empty (only if not offline or offline is empty)
        if not self.offline_mode or not self.offline_data['worker_gowns']:
             self._check_and_create_sample_data()

    def _setup_database(self):
        """Set up Firebase or offline database"""
        # Check if we should operate in offline mode
        if self.offline_mode or not FIREBASE_AVAILABLE:
            logger.warning("Firebase database running in OFFLINE mode")
            # Ensure offline data structure exists
            if 'worker_gowns' not in self.offline_data: self.offline_data['worker_gowns'] = {}
            if 'washing_records' not in self.offline_data: self.offline_data['washing_records'] = []
            if 'compliance_records' not in self.offline_data: self.offline_data['compliance_records'] = []
            if 'hand_washing_records' not in self.offline_data: self.offline_data['hand_washing_records'] = []
            return

        try:
            # Check if Firebase is already initialized globally
            if firebase_admin._apps:
                # Use existing Firebase app
                self.db = firestore.client()
                logger.info("Using existing Firebase connection")
                return
            
            # If the credential file doesn't exist yet, create a placeholder
            if not os.path.exists(self.credential_path):
                self._create_credential_instructions(self.credential_path)
                self.offline_mode = True
                logger.warning("Firebase credentials not found, switching to offline mode")
                return

            # Validate credential file format
            try:
                from config import sanitize_file_path
                self.credential_path = sanitize_file_path(self.credential_path)
                
                with open(self.credential_path, 'r') as f:
                    import json
                    cred_data = json.load(f)
                    # Basic validation of credential structure
                    required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
                    for field in required_fields:
                        if field not in cred_data:
                            raise ValueError(f"Missing required field '{field}' in Firebase credentials")
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Invalid Firebase credentials file: {e}")
                self.offline_mode = True
                logger.warning("Invalid credentials, switching to offline mode")
                return

            # Initialize Firebase with validated credentials
            cred = credentials.Certificate(self.credential_path)
            firebase_admin.initialize_app(cred)

            # Get Firestore client
            self.db = firestore.client()
            logger.info("Firebase database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}", exc_info=True)
            self.offline_mode = True
            logger.warning("Switching to offline mode due to initialization error")

    def _create_credential_instructions(self, path):
        """Create a placeholder file with instructions for getting Firebase credentials"""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)

        with open(path, 'w') as f:
            f.write("""
# Firebase Credentials

This is a placeholder file. To configure Firebase:

1. Go to your Firebase project console
2. Navigate to Project Settings > Service Accounts
3. Click "Generate New Private Key"
4. Download the JSON file and save it as firebase-credentials.json in this directory

DO NOT share this file - it contains sensitive credentials!
            """)
        logger.info(f"Created credential instructions file at {path}")

    def _check_and_create_sample_data(self):
        """Create sample data if collections are empty"""
        try:
            if self.offline_mode:
                # In offline mode, just add sample data if none exists
                if not self.offline_data['worker_gowns']:
                    logger.info("Creating sample data in offline mode")
                    self._create_sample_data()
                return

            if self.db is None:
                 logger.warning("Cannot check for sample data, DB not initialized.")
                 return

            # Check if worker_gowns collection is empty
            worker_gowns_query = self.db.collection('worker_gowns').limit(1)
            worker_gowns = list(worker_gowns_query.stream())

            if not worker_gowns:
                logger.info("Creating sample data in Firebase")
                self._create_sample_data()
        except Exception as e:
            logger.error(f"Error checking or creating sample data: {e}", exc_info=True)

    def _create_sample_data(self):
        """Create sample data in database"""
        # Add sample worker-gown associations
        self.add_worker_gown("0000000001", "John Smith") # Used, washed
        self.add_worker_gown("RFID1002", "Jane Doe")   # Used, washed
        self.add_worker_gown("RFID1003", "Mark Johnson") # New, never used or washed
        self.add_worker_gown("RFID1004", "Alice Brown")  # New, washed once before use
        self.add_washing_record("RFID1004")

        # Add sample washing records
        self.add_washing_record("0000000001")
        self.add_washing_record("RFID1002")

        # Add sample compliance records (pass serial for potential first use setting)
        self.add_compliance_record("John Smith", True, "0000000001")
        # Simulate setting first use on the first compliant record
        self.set_gown_first_use("0000000001")

        self.add_compliance_record("Jane Doe", True, "RFID1002")
        self.set_gown_first_use("RFID1002")

        logger.info("Sample data created")

    def check_gown_washed(self, serial_number):
        """
        Check if a gown is registered, ready for use, and within lifetime.
        Ready means: EITHER no prior compliant use OR washed since last compliant use.
        Returns: tuple (is_ready, worker_name, is_expired, first_use_timestamp)
                 is_ready (bool): True if ready for use based on wash/use history.
                 worker_name (str|None): Name of the worker or None if not found.
                 is_expired (bool): True if first use was > GOWN_LIFETIME_DAYS ago.
                 first_use_timestamp (datetime|None): Timestamp of first use or None.
        """
        if self.offline_mode:
            return self._offline_check_gown_washed(serial_number)

        if self.db is None:
             logger.error("Database not available for check_gown_washed")
             return False, None, False, None

        try:
            # Get worker associated with this gown
            worker_ref = self.db.collection('worker_gowns').document(serial_number)
            worker_doc = worker_ref.get()

            if not worker_doc.exists:
                logger.warning(f"Gown {serial_number} not registered.")
                return False, None, False, None # Gown not registered

            worker_data = worker_doc.to_dict()
            worker_name = worker_data.get('worker_name')
            first_use_timestamp = worker_data.get('first_use_timestamp') # Get first use time

            # --- Check Lifetime ---
            is_expired = False
            first_use_dt_naive_utc = None # For comparison
            if first_use_timestamp:
                 if hasattr(first_use_timestamp, 'to_datetime'):
                     first_use_dt_naive_utc = first_use_timestamp.to_datetime(tz=timezone.utc).replace(tzinfo=None)
                 elif isinstance(first_use_timestamp, datetime):
                     first_use_dt_naive_utc = first_use_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
                 else: logger.warning(f"Unexpected first_use_timestamp format: {type(first_use_timestamp)}")

                 if first_use_dt_naive_utc:
                     if datetime.utcnow() > first_use_dt_naive_utc + timedelta(days=self.GOWN_LIFETIME_DAYS):
                         is_expired = True
                         logger.info(f"Gown {serial_number} expired (First use UTC: {first_use_dt_naive_utc})")
            # --- End Lifetime Check ---

            # If expired, return immediately
            if is_expired:
                return False, worker_name, True, first_use_timestamp # is_ready=False

            # Get the latest *compliant* compliance record for this specific gown
            compliance_query = self.db.collection('compliance_records') \
                .where(filter=FieldFilter('serial_number', '==', serial_number)) \
                .where(filter=FieldFilter('compliant', '==', True)) \
                .order_by('timestamp', direction=firestore.Query.DESCENDING) \
                .limit(1)
            latest_compliance_docs = list(compliance_query.stream())

            # *** REVISED LOGIC V4 - SAFETY FIRST ***
            # If there's no prior *compliant* record for this gown, check washing status for safety.
            # First-time use still requires washing for cleanroom safety protocols.
            if not latest_compliance_docs:
                logger.info(f"Gown {serial_number}: No prior compliant use found. Checking wash status for first use.")
                
                # For first use, ensure the gown has been washed at least once
                wash_query = self.db.collection('washing_records') \
                    .where(filter=FieldFilter('serial_number', '==', serial_number)) \
                    .limit(1)
                wash_docs = list(wash_query.stream())
                
                if not wash_docs:
                    logger.warning(f"Gown {serial_number} has never been washed. Not ready for first use.")
                    return False, worker_name, is_expired, first_use_timestamp # Not ready - never washed
                else:
                    logger.info(f"Gown {serial_number} has been washed and ready for first use.")
                    return True, worker_name, is_expired, first_use_timestamp # Ready for first use
            # *** END REVISED LOGIC V4 ***

            # If we reach here, there IS a previous compliant record. We MUST check washing.
            latest_compliance_time = latest_compliance_docs[0].to_dict()['timestamp']

            # Get the latest washing record for this gown
            wash_query = self.db.collection('washing_records') \
                .where(filter=FieldFilter('serial_number', '==', serial_number)) \
                .order_by('wash_timestamp', direction=firestore.Query.DESCENDING) \
                .limit(1)
            latest_wash_docs = list(wash_query.stream())

            # If it has been used compliantly before, but NEVER washed, it's not ready.
            if not latest_wash_docs:
                logger.info(f"Gown {serial_number} has prior use but no wash record. Not ready.")
                return False, worker_name, is_expired, first_use_timestamp # Not ready

            # Compare wash time to last compliant use time
            latest_wash_time = latest_wash_docs[0].to_dict()['wash_timestamp']
            is_washed_since_last_use = latest_wash_time > latest_compliance_time
            logger.info(f"Gown {serial_number} wash check: Ready={is_washed_since_last_use} (Wash: {latest_wash_time}, Last Compliant Use: {latest_compliance_time})")
            return is_washed_since_last_use, worker_name, is_expired, first_use_timestamp

        except Exception as e:
            logger.error(f"Error checking gown washed status for {serial_number}: {e}", exc_info=True)
            # Default to safe value
            return False, None, False, None

    def _offline_check_gown_washed(self, serial_number):
        """Offline version of check_gown_washed"""
        with self.data_lock:
            # Check if gown is registered
            if serial_number not in self.offline_data['worker_gowns']:
                 logger.warning(f"[Offline] Gown {serial_number} not registered.")
                 return False, None, False, None

            worker_data = self.offline_data['worker_gowns'][serial_number]
            worker_name = worker_data.get('worker_name')
            first_use_iso = worker_data.get('first_use_timestamp')

            # --- Check Lifetime ---
            is_expired = False
            first_use_dt = None
            if first_use_iso:
                try:
                    if isinstance(first_use_iso, str) and first_use_iso.endswith('Z'):
                          first_use_iso = first_use_iso[:-1] + '+00:00'
                    first_use_dt_aware = datetime.fromisoformat(first_use_iso)
                    first_use_dt = first_use_dt_aware.astimezone(timezone.utc).replace(tzinfo=None)
                    if datetime.utcnow() > first_use_dt + timedelta(days=self.GOWN_LIFETIME_DAYS):
                        is_expired = True
                        logger.info(f"[Offline] Gown {serial_number} expired (First use UTC: {first_use_dt})")
                except Exception as e:
                     logger.warning(f"[Offline] Error processing first_use_timestamp {serial_number} ('{first_use_iso}'): {e}")
            # --- End Lifetime Check ---

            # If expired, return immediately
            if is_expired:
                return False, worker_name, True, first_use_dt # is_ready=False

            # Find the latest *compliant* compliance record for this gown
            compliance_records = [r for r in self.offline_data['compliance_records']
                                 if r.get('serial_number') == serial_number and r.get('compliant') is True]

            # *** REVISED OFFLINE LOGIC V4 - SAFETY FIRST ***
            # If no prior compliant record, check washing status for safety.
            # First-time use still requires washing for cleanroom safety protocols.
            if not compliance_records:
                logger.info(f"[Offline] Gown {serial_number}: No prior compliant use. Checking wash status for first use.")
                
                # For first use, ensure the gown has been washed at least once
                wash_records = [r for r in self.offline_data['washing_records']
                              if r.get('serial_number') == serial_number]
                
                if not wash_records:
                    logger.warning(f"[Offline] Gown {serial_number} has never been washed. Not ready for first use.")
                    return False, worker_name, is_expired, first_use_dt # Not ready - never washed
                else:
                    logger.info(f"[Offline] Gown {serial_number} has been washed and ready for first use.")
                    return True, worker_name, is_expired, first_use_dt # Ready for first use
            # *** END REVISED OFFLINE LOGIC V4 ***

            # If we reach here, there IS a prior compliant use. MUST check washing.
            latest_compliance_iso = max(r['timestamp'] for r in compliance_records if r.get('timestamp')) if compliance_records else ''

            # Find the latest washing record for this gown
            wash_records = [r for r in self.offline_data['washing_records']
                          if r.get('serial_number') == serial_number]

            # If used before but never washed, not ready.
            if not wash_records:
                 logger.info(f"[Offline] Gown {serial_number} has prior use but no wash record. Not ready.")
                 return False, worker_name, is_expired, first_use_dt # Not ready

            # Compare wash time to last compliant use time
            latest_wash_iso = max(r['wash_timestamp'] for r in wash_records if r.get('wash_timestamp')) if wash_records else ''
            is_washed_since_last_use = latest_wash_iso > latest_compliance_iso
            logger.info(f"[Offline] Gown {serial_number} wash check: Ready={is_washed_since_last_use} (Wash: {latest_wash_iso}, Last Compliant Use: {latest_compliance_iso})")
            return is_washed_since_last_use, worker_name, is_expired, first_use_dt


    # --- Rest of the file remains unchanged ---
    def add_worker_gown(self, serial_number, worker_name):
        """Add or update a worker-gown association, ensuring first_use_timestamp exists"""
        # Validate and sanitize inputs
        try:
            from config import sanitize_serial_number, sanitize_worker_name
            serial_number = sanitize_serial_number(serial_number)
            worker_name = sanitize_worker_name(worker_name)
        except ValueError as e:
            logger.error(f"Invalid input for add_worker_gown: {e}")
            return False
        
        if self.offline_mode:
            return self._offline_add_worker_gown(serial_number, worker_name)

        if self.db is None: return False
        try:
            doc_ref = self.db.collection('worker_gowns').document(serial_number)
            doc_snapshot = doc_ref.get()

            data_to_set = {
                'serial_number': serial_number,
                'worker_name': worker_name,
                'registered_on': firestore.SERVER_TIMESTAMP,
                # Only set first_use_timestamp if it doesn't exist yet
                'first_use_timestamp': doc_snapshot.to_dict().get('first_use_timestamp') if doc_snapshot.exists else None
            }

            # Use set with merge=True only if you want to preserve other fields not explicitly mentioned
            # doc_ref.set(data_to_set) # Overwrites completely
            # Let's explicitly check and set
            if doc_snapshot.exists:
                 # Update existing, preserving first_use if it exists
                 existing_data = doc_snapshot.to_dict()
                 update_data = {
                      'worker_name': worker_name,
                      'registered_on': firestore.SERVER_TIMESTAMP, # Update registration time on change? Or keep original? Let's update.
                      # Keep existing first_use_timestamp if present
                      'first_use_timestamp': existing_data.get('first_use_timestamp')
                 }
                 doc_ref.update(update_data)
                 logger.info(f"Updated worker-gown association: {worker_name} - {serial_number}")

            else:
                 # Create new, first_use is None initially
                 create_data = {
                    'serial_number': serial_number,
                    'worker_name': worker_name,
                    'registered_on': firestore.SERVER_TIMESTAMP,
                    'first_use_timestamp': None # Explicitly set to None on creation
                 }
                 doc_ref.set(create_data)
                 logger.info(f"Added worker-gown association: {worker_name} - {serial_number}")

            return True

        except Exception as e:
            logger.error(f"Error adding/updating worker-gown association {serial_number}: {e}", exc_info=True)
            return False

    def _offline_add_worker_gown(self, serial_number, worker_name):
        """Offline version of add_worker_gown"""
        with self.data_lock:
            try:
                now_iso = datetime.utcnow().isoformat() + "Z" # Add Z for UTC
                # Check if exists to preserve first_use
                if serial_number in self.offline_data['worker_gowns']:
                     # Update existing
                     self.offline_data['worker_gowns'][serial_number]['worker_name'] = worker_name
                     self.offline_data['worker_gowns'][serial_number]['registered_on'] = now_iso
                     # Keep existing first_use_timestamp
                     logger.info(f"Updated worker-gown association (offline): {worker_name} - {serial_number}")
                else:
                    # Add new
                    self.offline_data['worker_gowns'][serial_number] = {
                        'serial_number': serial_number,
                        'worker_name': worker_name,
                        'registered_on': now_iso,
                        'first_use_timestamp': None # Initialize to None
                    }
                    logger.info(f"Added worker-gown association (offline): {worker_name} - {serial_number}")
                return True
            except Exception as e:
                logger.error(f"Error adding worker-gown association (offline): {e}", exc_info=True)
                return False

    def set_gown_first_use(self, serial_number):
        """Sets the first_use_timestamp for a gown if it's currently null."""
        if self.offline_mode:
            return self._offline_set_gown_first_use(serial_number)

        if self.db is None: return False
        try:
            doc_ref = self.db.collection('worker_gowns').document(serial_number)
            doc_snapshot = doc_ref.get()

            if doc_snapshot.exists:
                 data = doc_snapshot.to_dict()
                 if data.get('first_use_timestamp') is None:
                     doc_ref.update({'first_use_timestamp': firestore.SERVER_TIMESTAMP})
                     logger.info(f"Set first use timestamp for gown {serial_number}")
                     return True
                 else:
                     # logger.info(f"First use timestamp already set for gown {serial_number}")
                     return True # Already set, consider it success
            else:
                 logger.warning(f"Cannot set first use: Gown {serial_number} not found.")
                 return False
        except Exception as e:
            logger.error(f"Error setting first use for gown {serial_number}: {e}", exc_info=True)
            return False

    def _offline_set_gown_first_use(self, serial_number):
        """Offline version of set_gown_first_use."""
        with self.data_lock:
            try:
                if serial_number in self.offline_data['worker_gowns']:
                    if self.offline_data['worker_gowns'][serial_number].get('first_use_timestamp') is None:
                        self.offline_data['worker_gowns'][serial_number]['first_use_timestamp'] = datetime.utcnow().isoformat() + "Z"
                        logger.info(f"[Offline] Set first use timestamp for gown {serial_number}")
                        return True
                    else:
                        # logger.info(f"[Offline] First use timestamp already set for gown {serial_number}")
                        return True
                else:
                    logger.warning(f"[Offline] Cannot set first use: Gown {serial_number} not found.")
                    return False
            except Exception as e:
                logger.error(f"Error setting first use (offline) for gown {serial_number}: {e}", exc_info=True)
                return False

    def reset_gown_first_use(self, serial_number):
        """Resets the first_use_timestamp for a gown to None."""
        if self.offline_mode:
            return self._offline_reset_gown_first_use(serial_number)

        if self.db is None: return False
        try:
            doc_ref = self.db.collection('worker_gowns').document(serial_number)
            doc_snapshot = doc_ref.get()

            if doc_snapshot.exists:
                 doc_ref.update({'first_use_timestamp': None})
                 logger.info(f"Reset first use timestamp for gown {serial_number}")
                 return True
            else:
                 logger.warning(f"Cannot reset first use: Gown {serial_number} not found.")
                 return False
        except Exception as e:
            logger.error(f"Error resetting first use for gown {serial_number}: {e}", exc_info=True)
            return False

    def _offline_reset_gown_first_use(self, serial_number):
        """Offline version of reset_gown_first_use."""
        with self.data_lock:
            try:
                if serial_number in self.offline_data['worker_gowns']:
                    self.offline_data['worker_gowns'][serial_number]['first_use_timestamp'] = None
                    logger.info(f"[Offline] Reset first use timestamp for gown {serial_number}")
                    return True
                else:
                    logger.warning(f"[Offline] Cannot reset first use: Gown {serial_number} not found.")
                    return False
            except Exception as e:
                logger.error(f"Error resetting first use (offline) for gown {serial_number}: {e}", exc_info=True)
                return False

    def delete_worker_gown(self, serial_number):
        """Delete a worker-gown association"""
        if self.offline_mode:
            return self._offline_delete_worker_gown(serial_number)

        if self.db is None: return False
        try:
            # Delete from Firebase
            self.db.collection('worker_gowns').document(serial_number).delete()
            logger.info(f"Deleted worker-gown association: {serial_number}")
            return True

        except Exception as e:
            logger.error(f"Error deleting worker-gown association: {e}", exc_info=True)
            return False

    def _offline_delete_worker_gown(self, serial_number):
        """Offline version of delete_worker_gown"""
        with self.data_lock:
            try:
                # Remove from offline data
                if serial_number in self.offline_data['worker_gowns']:
                    del self.offline_data['worker_gowns'][serial_number]
                    logger.info(f"Deleted worker-gown association (offline): {serial_number}")
                    return True
                return False # Return False if not found
            except Exception as e:
                logger.error(f"Error deleting worker-gown association (offline): {e}", exc_info=True)
                return False

    def add_washing_record(self, serial_number):
        """Record that a gown has been washed"""
        # Validate and sanitize input
        try:
            from config import sanitize_serial_number
            serial_number = sanitize_serial_number(serial_number)
        except ValueError as e:
            logger.error(f"Invalid serial number for add_washing_record: {e}")
            return False
        
        if self.offline_mode:
            return self._offline_add_washing_record(serial_number)

        if self.db is None: return False
        try:
            # Add to Firebase
            self.db.collection('washing_records').add({
                'serial_number': serial_number,
                'wash_timestamp': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Added washing record for gown: {serial_number}")
            return True

        except Exception as e:
            logger.error(f"Error adding washing record for {serial_number}: {e}", exc_info=True)
            return False

    def _offline_add_washing_record(self, serial_number):
        """Offline version of add_washing_record"""
        with self.data_lock:
            try:
                # Add to offline data
                self.offline_data['washing_records'].append({
                    'serial_number': serial_number,
                    'wash_timestamp': datetime.utcnow().isoformat() + "Z"
                })
                logger.info(f"Added washing record (offline) for gown: {serial_number}")
                return True
            except Exception as e:
                logger.error(f"Error adding washing record (offline): {e}", exc_info=True)
                return False

    def add_compliance_record(self, worker_name, compliant=True, serial_number=None):
        """Record a worker's compliance status, optionally including serial number"""
        # Validate and sanitize inputs
        try:
            from config import sanitize_worker_name, sanitize_serial_number
            worker_name = sanitize_worker_name(worker_name)
            if serial_number is not None:
                serial_number = sanitize_serial_number(serial_number)
        except ValueError as e:
            logger.error(f"Invalid input for add_compliance_record: {e}")
            return False
        
        if self.offline_mode:
            return self._offline_add_compliance_record(worker_name, compliant, serial_number)

        if self.db is None: return False
        try:
            record_data = {
                'worker_name': worker_name,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'compliant': compliant
            }
            # Include serial number if provided
            if serial_number:
                record_data['serial_number'] = serial_number

            # Add to Firebase
            self.db.collection('compliance_records').add(record_data)
            status = 'Compliant' if compliant else 'Non-compliant'
            logger.info(f"Added compliance record for {worker_name} ({status}) with gown {serial_number or 'N/A'}")
            return True

        except Exception as e:
            logger.error(f"Error adding compliance record for {worker_name}: {e}", exc_info=True)
            return False

    def _offline_add_compliance_record(self, worker_name, compliant=True, serial_number=None):
        """Offline version of add_compliance_record"""
        with self.data_lock:
            try:
                record_data = {
                    'worker_name': worker_name,
                    'timestamp': datetime.utcnow().isoformat() + "Z",
                    'compliant': compliant
                }
                if serial_number:
                    record_data['serial_number'] = serial_number

                # Add to offline data
                self.offline_data['compliance_records'].append(record_data)
                status = 'Compliant' if compliant else 'Non-compliant'
                logger.info(f"Added compliance record (offline) for {worker_name} ({status}) with gown {serial_number or 'N/A'}")
                return True
            except Exception as e:
                logger.error(f"Error adding compliance record (offline): {e}", exc_info=True)
                return False

    def get_worker_gowns(self):
        """Get basic worker-gown associations (serial, worker_name)"""
        if self.offline_mode:
            return self._offline_get_worker_gowns()

        if self.db is None: return []
        try:
            docs = self.db.collection('worker_gowns').stream()
            return [(doc.id, doc.to_dict().get('worker_name', 'N/A')) for doc in docs]

        except Exception as e:
            logger.error(f"Error getting worker-gowns: {e}", exc_info=True)
            return []

    def _offline_get_worker_gowns(self):
        """Offline version of get_worker_gowns"""
        with self.data_lock:
            try:
                # Get from offline data
                return [(serial, data.get('worker_name', 'N/A'))
                        for serial, data in self.offline_data['worker_gowns'].items()]
            except Exception as e:
                logger.error(f"Error getting worker-gowns (offline): {e}", exc_info=True)
                return []

    def get_worker_gowns_with_details(self):
        """Get worker-gown associations with registration and first use dates"""
        if self.offline_mode:
            return self._offline_get_worker_gowns_with_details()

        if self.db is None: return []
        results = []
        try:
            docs = self.db.collection('worker_gowns').stream()
            for doc in docs:
                 data = doc.to_dict()
                 serial = doc.id
                 worker = data.get('worker_name', 'N/A')

                 reg_ts = data.get('registered_on')
                 reg_str = self._format_timestamp(reg_ts, 'N/A')

                 first_ts = data.get('first_use_timestamp')
                 first_str = self._format_timestamp(first_ts, 'Not Used Yet')

                 results.append((serial, worker, reg_str, first_str))
            return results
        except Exception as e:
            logger.error(f"Error getting detailed worker-gowns: {e}", exc_info=True)
            return []

    def _offline_get_worker_gowns_with_details(self):
        """Offline version of get_worker_gowns_with_details"""
        with self.data_lock:
            results = []
            try:
                for serial, data in self.offline_data['worker_gowns'].items():
                    worker = data.get('worker_name', 'N/A')
                    reg_iso = data.get('registered_on')
                    reg_str = self._format_iso_timestamp(reg_iso, 'N/A')
                    first_iso = data.get('first_use_timestamp')
                    first_str = self._format_iso_timestamp(first_iso, 'Not Used Yet')
                    results.append((serial, worker, reg_str, first_str))
                return results
            except Exception as e:
                logger.error(f"Error getting detailed worker-gowns (offline): {e}", exc_info=True)
                return []

    def get_worker_name_for_gown(self, serial_number):
        """Retrieve worker name for a specific gown serial number"""
        if self.offline_mode:
             return self._offline_get_worker_name_for_gown(serial_number)

        if self.db is None: return None
        try:
            doc_ref = self.db.collection('worker_gowns').document(serial_number)
            doc = doc_ref.get()
            if doc.exists:
                return doc.to_dict().get('worker_name')
            else:
                return None
        except Exception as e:
            logger.error(f"Error getting worker name for gown {serial_number}: {e}", exc_info=True)
            return None

    def _offline_get_worker_name_for_gown(self, serial_number):
        with self.data_lock:
            if serial_number in self.offline_data['worker_gowns']:
                return self.offline_data['worker_gowns'][serial_number].get('worker_name')
            return None

    # Helper to format Firestore Timestamps or datetimes
    def _format_timestamp(self, ts, default_val=''):
        if ts is None:
            return default_val
        try:
            dt = None
            if isinstance(ts, datetime): # Check if it's already a python datetime
                dt = ts
            elif hasattr(ts, 'to_datetime'): # Handle Firestore Timestamp
                dt = ts.to_datetime()
            elif isinstance(ts, str): # Handle ISO string format (less likely from Firestore direct)
                if ts.endswith('Z'):
                     ts = ts[:-1] + '+00:00'
                dt = datetime.fromisoformat(ts)
            else:
                 logger.warning(f"Unhandled timestamp type: {type(ts)}")
                 return str(ts) # Fallback

            # Format the datetime object
            # If timezone aware, convert to local timezone? Or keep as UTC?
            # For simplicity, let's format directly. Add .astimezone(None) for local naive if needed.
            return dt.strftime('%Y-%m-%d %H:%M:%S')

        except Exception as e:
            logger.warning(f"Error formatting timestamp ({type(ts)} - value: {ts}): {e}")
            return str(ts) # Fallback to string representation


    # Helper to format ISO Timestamps for offline mode
    def _format_iso_timestamp(self, iso_str, default_val=''):
         if iso_str is None:
              return default_val
         try:
              # Handle potential 'Z' for UTC
              dt_aware = None
              if isinstance(iso_str, str):
                   if iso_str.endswith('Z'):
                         iso_str = iso_str[:-1] + '+00:00'
                   dt_aware = datetime.fromisoformat(iso_str)
              elif isinstance(iso_str, datetime): # If already datetime (unlikely in raw offline data)
                    dt_aware = iso_str
              else:
                   logger.warning(f"Unexpected type for ISO timestamp: {type(iso_str)}")
                   return str(iso_str)

              # Convert to local naive time for display? Or keep UTC? Let's keep UTC naive for consistency.
              # dt_local_naive = dt_aware.astimezone(None).replace(tzinfo=None)
              # return dt_local_naive.strftime('%Y-%m-%d %H:%M:%S')
              return dt_aware.strftime('%Y-%m-%d %H:%M:%S') # Format as desired, keeps original timezone offset info implicitly

         except (ValueError, TypeError) as e:
              logger.warning(f"Error formatting ISO timestamp '{iso_str}': {e}")
              return iso_str # Fallback

    def get_washing_records(self, limit=50):
        """Get washing records, with optional limit"""
        if self.offline_mode:
            return self._offline_get_washing_records(limit)

        if self.db is None: return []
        records = []
        try:
            # Fetch washing records
            query = self.db.collection('washing_records') \
                .order_by('wash_timestamp', direction=firestore.Query.DESCENDING) \
                .limit(limit)

            washing_docs = list(query.stream())

            # Fetch corresponding worker names (could be optimized)
            serials = list(set(doc.to_dict().get('serial_number') for doc in washing_docs if doc.to_dict().get('serial_number')))
            worker_map = {}
            if serials:
                 # Batch get worker names might be better if many unique serials
                 for serial in serials:
                      worker_map[serial] = self.get_worker_name_for_gown(serial) or "Unknown"

            for doc in washing_docs:
                data = doc.to_dict()
                serial = data.get('serial_number', 'N/A')
                worker_name = worker_map.get(serial, "Unknown")
                wash_time_str = self._format_timestamp(data.get('wash_timestamp'), 'N/A')
                records.append((serial, wash_time_str, worker_name))

            return records

        except Exception as e:
            logger.error(f"Error getting washing records: {e}", exc_info=True)
            return []

    def _offline_get_washing_records(self, limit=50):
        """Offline version of get_washing_records"""
        with self.data_lock:
            try:
                # Sort records by timestamp (newest first)
                sorted_records = sorted(
                    self.offline_data['washing_records'],
                    key=lambda r: r.get('wash_timestamp', ''),
                    reverse=True
                )

                # Apply limit
                limited_records = sorted_records[:limit]

                # Format records
                result = []
                for record in limited_records:
                    serial = record.get('serial_number', 'N/A')
                    worker_name = self._offline_get_worker_name_for_gown(serial) or "Unknown"
                    time_str = self._format_iso_timestamp(record.get('wash_timestamp'), 'N/A')
                    result.append((serial, time_str, worker_name))

                return result

            except Exception as e:
                logger.error(f"Error getting washing records (offline): {e}", exc_info=True)
                return []

    def get_compliance_records(self, limit=50):
        """Get compliance records, with optional limit"""
        if self.offline_mode:
            return self._offline_get_compliance_records(limit)

        if self.db is None: return []
        records = []
        try:
            # Get from Firebase
            query = self.db.collection('compliance_records') \
                .order_by('timestamp', direction=firestore.Query.DESCENDING) \
                .limit(limit)

            for doc in query.stream():
                data = doc.to_dict()
                time_str = self._format_timestamp(data.get('timestamp'), 'N/A')
                compliant_str = "Yes" if data.get('compliant') else "No"
                worker_name = data.get('worker_name', 'N/A')
                # Optionally add serial number if needed in the table
                # serial_num = data.get('serial_number', '')
                records.append((worker_name, time_str, compliant_str))

            return records

        except Exception as e:
            logger.error(f"Error getting compliance records: {e}", exc_info=True)
            return []

    def _offline_get_compliance_records(self, limit=50):
        """Offline version of get_compliance_records"""
        with self.data_lock:
            try:
                # Sort records by timestamp (newest first)
                sorted_records = sorted(
                    self.offline_data['compliance_records'],
                    key=lambda r: r.get('timestamp', ''),
                    reverse=True
                )

                # Apply limit
                limited_records = sorted_records[:limit]

                # Format records
                result = []
                for record in limited_records:
                    worker_name = record.get('worker_name', 'N/A')
                    time_str = self._format_iso_timestamp(record.get('timestamp'), 'N/A')
                    compliant = "Yes" if record.get('compliant') else "No"
                    result.append((worker_name, time_str, compliant))

                return result

            except Exception as e:
                logger.error(f"Error getting compliance records (offline): {e}", exc_info=True)
                return []

    def get_statistics(self):
        """Get summary statistics for dashboard"""
        if self.offline_mode:
            return self._offline_get_statistics()

        if self.db is None:
             # Return default stats if DB unavailable
             return {'total_gowns': 0, 'total_workers': 0, 'total_checks': 0, 'compliance_rate': 0, 'recent_activity': []}
        try:
            # Get statistics from Firebase
            stats = {}

            # Total number of gowns
            gowns_query = self.db.collection('worker_gowns')
            all_gowns = list(gowns_query.stream())
            stats['total_gowns'] = len(all_gowns)

            # Total number of workers (unique)
            workers = set(doc.to_dict().get('worker_name') for doc in all_gowns if doc.to_dict().get('worker_name'))
            stats['total_workers'] = len(workers)

            # Total compliance checks
            compliance_query = self.db.collection('compliance_records')
            all_checks = list(compliance_query.stream())
            stats['total_checks'] = len(all_checks)

            # Compliance rate
            total_compliant = sum(1 for doc in all_checks if doc.to_dict().get('compliant'))

            if stats['total_checks'] > 0:
                stats['compliance_rate'] = round((total_compliant / stats['total_checks']) * 100, 1)
            else:
                stats['compliance_rate'] = 0

            # Recent activity - last 5 compliance checks
            recent_query = compliance_query \
                .order_by('timestamp', direction=firestore.Query.DESCENDING) \
                .limit(5)

            recent_activity = []
            for doc in recent_query.stream():
                data = doc.to_dict()
                time_str = self._format_timestamp(data.get('timestamp'), 'N/A')
                recent_activity.append({
                    'worker': data.get('worker_name', 'N/A'),
                    'time': time_str,
                    'compliant': data.get('compliant', False)
                })

            stats['recent_activity'] = recent_activity

            return stats

        except Exception as e:
            logger.error(f"Error getting statistics: {e}", exc_info=True)
            return {
                'total_gowns': 'Err', 'total_workers': 'Err', 'total_checks': 'Err',
                'compliance_rate': 'Err', 'recent_activity': []
            }

    def _offline_get_statistics(self):
        """Offline version of get_statistics"""
        with self.data_lock:
            try:
                stats = {}

                # Total number of gowns
                stats['total_gowns'] = len(self.offline_data['worker_gowns'])

                # Total number of workers (unique)
                workers = set(data.get('worker_name') for data in self.offline_data['worker_gowns'].values() if data.get('worker_name'))
                stats['total_workers'] = len(workers)

                # Total compliance checks
                stats['total_checks'] = len(self.offline_data['compliance_records'])

                # Compliance rate
                compliant_records = [r for r in self.offline_data['compliance_records'] if r.get('compliant')]
                if stats['total_checks'] > 0:
                    stats['compliance_rate'] = round((len(compliant_records) / stats['total_checks']) * 100, 1)
                else:
                    stats['compliance_rate'] = 0

                # Recent activity
                sorted_activity = sorted(
                    self.offline_data['compliance_records'],
                    key=lambda r: r.get('timestamp', ''),
                    reverse=True
                )

                recent_activity = []
                for record in sorted_activity[:5]:
                    recent_activity.append({
                        'worker': record.get('worker_name', 'N/A'),
                        'time': self._format_iso_timestamp(record.get('timestamp'), 'N/A'),
                        'compliant': record.get('compliant', False)
                    })

                stats['recent_activity'] = recent_activity

                return stats

            except Exception as e:
                logger.error(f"Error getting statistics (offline): {e}", exc_info=True)
                return {
                    'total_gowns': 'Err', 'total_workers': 'Err', 'total_checks': 'Err',
                    'compliance_rate': 'Err', 'recent_activity': []
                }

    def add_hand_washing_record(self, worker_name, duration, quality, timestamp=None):
        """Record a hand washing session with duration and quality"""
        # Validate and sanitize inputs
        try:
            from config import sanitize_worker_name
            worker_name = sanitize_worker_name(worker_name)
            if not isinstance(duration, (int, float)) or duration < 0:
                raise ValueError("Duration must be a non-negative number")
            if quality not in ['bad', 'moderate', 'good']:
                raise ValueError("Quality must be 'bad', 'moderate', or 'good'")
        except ValueError as e:
            logger.error(f"Invalid input for add_hand_washing_record: {e}")
            return False
        
        if self.offline_mode:
            return self._offline_add_hand_washing_record(worker_name, duration, quality, timestamp)

        if self.db is None: 
            return False
        
        try:
            record_data = {
                'worker_name': worker_name,
                'duration': duration,
                'quality': quality,
                'timestamp': timestamp if timestamp else firestore.SERVER_TIMESTAMP
            }

            # Add to Firebase
            self.db.collection('hand_washing_records').add(record_data)
            logger.info(f"Added hand washing record for {worker_name} (Duration: {duration:.1f}s, Quality: {quality})")
            return True

        except Exception as e:
            logger.error(f"Error adding hand washing record for {worker_name}: {e}", exc_info=True)
            return False

    def _offline_add_hand_washing_record(self, worker_name, duration, quality, timestamp=None):
        """Offline version of add_hand_washing_record"""
        with self.data_lock:
            try:
                # Ensure hand_washing_records collection exists
                if 'hand_washing_records' not in self.offline_data:
                    self.offline_data['hand_washing_records'] = []

                record_data = {
                    'worker_name': worker_name,
                    'duration': duration,
                    'quality': quality,
                    'timestamp': timestamp.isoformat() + "Z" if timestamp else datetime.utcnow().isoformat() + "Z"
                }

                # Add to offline data
                self.offline_data['hand_washing_records'].append(record_data)
                logger.info(f"Added hand washing record (offline) for {worker_name} (Duration: {duration:.1f}s, Quality: {quality})")
                return True
            except Exception as e:
                logger.error(f"Error adding hand washing record (offline): {e}", exc_info=True)
                return False

    def get_hand_washing_records(self, limit=50):
        """Get hand washing records, with optional limit"""
        if self.offline_mode:
            return self._offline_get_hand_washing_records(limit)

        if self.db is None: 
            return []
        
        records = []
        try:
            # Get from Firebase
            query = self.db.collection('hand_washing_records') \
                .order_by('timestamp', direction=firestore.Query.DESCENDING) \
                .limit(limit)

            for doc in query.stream():
                data = doc.to_dict()
                worker_name = data.get('worker_name', 'N/A')
                duration = data.get('duration', 0.0)
                quality = data.get('quality', 'unknown')
                time_str = self._format_timestamp(data.get('timestamp'), 'N/A')
                records.append((worker_name, f"{duration:.1f}s", quality.title(), time_str))

            return records

        except Exception as e:
            logger.error(f"Error getting hand washing records: {e}", exc_info=True)
            return []

    def _offline_get_hand_washing_records(self, limit=50):
        """Offline version of get_hand_washing_records"""
        with self.data_lock:
            try:
                # Ensure hand_washing_records collection exists
                if 'hand_washing_records' not in self.offline_data:
                    self.offline_data['hand_washing_records'] = []

                # Sort records by timestamp (newest first)
                sorted_records = sorted(
                    self.offline_data['hand_washing_records'],
                    key=lambda r: r.get('timestamp', ''),
                    reverse=True
                )

                # Apply limit
                limited_records = sorted_records[:limit]

                # Format records
                result = []
                for record in limited_records:
                    worker_name = record.get('worker_name', 'N/A')
                    duration = record.get('duration', 0.0)
                    quality = record.get('quality', 'unknown')
                    time_str = self._format_iso_timestamp(record.get('timestamp'), 'N/A')
                    result.append((worker_name, f"{duration:.1f}s", quality.title(), time_str))

                return result

            except Exception as e:
                logger.error(f"Error getting hand washing records (offline): {e}", exc_info=True)
                return []

    def get_hand_washing_statistics(self):
        """Get hand washing statistics"""
        if self.offline_mode:
            return self._offline_get_hand_washing_statistics()

        if self.db is None:
            return {'total_sessions': 0, 'avg_duration': 0, 'quality_breakdown': {'good': 0, 'moderate': 0, 'bad': 0}}

        try:
            # Get all hand washing records
            query = self.db.collection('hand_washing_records')
            all_records = list(query.stream())
            
            total_sessions = len(all_records)
            total_duration = 0
            quality_breakdown = {'good': 0, 'moderate': 0, 'bad': 0}

            for doc in all_records:
                data = doc.to_dict()
                duration = data.get('duration', 0.0)
                quality = data.get('quality', 'bad')
                
                total_duration += duration
                if quality in quality_breakdown:
                    quality_breakdown[quality] += 1

            avg_duration = total_duration / total_sessions if total_sessions > 0 else 0

            return {
                'total_sessions': total_sessions,
                'avg_duration': round(avg_duration, 1),
                'quality_breakdown': quality_breakdown
            }

        except Exception as e:
            logger.error(f"Error getting hand washing statistics: {e}", exc_info=True)
            return {'total_sessions': 'Err', 'avg_duration': 'Err', 'quality_breakdown': {'good': 'Err', 'moderate': 'Err', 'bad': 'Err'}}

    def _offline_get_hand_washing_statistics(self):
        """Offline version of get_hand_washing_statistics"""
        with self.data_lock:
            try:
                # Ensure hand_washing_records collection exists
                if 'hand_washing_records' not in self.offline_data:
                    self.offline_data['hand_washing_records'] = []

                records = self.offline_data['hand_washing_records']
                total_sessions = len(records)
                total_duration = 0
                quality_breakdown = {'good': 0, 'moderate': 0, 'bad': 0}

                for record in records:
                    duration = record.get('duration', 0.0)
                    quality = record.get('quality', 'bad')
                    
                    total_duration += duration
                    if quality in quality_breakdown:
                        quality_breakdown[quality] += 1

                avg_duration = total_duration / total_sessions if total_sessions > 0 else 0

                return {
                    'total_sessions': total_sessions,
                    'avg_duration': round(avg_duration, 1),
                    'quality_breakdown': quality_breakdown
                }

            except Exception as e:
                logger.error(f"Error getting hand washing statistics (offline): {e}", exc_info=True)
                return {'total_sessions': 'Err', 'avg_duration': 'Err', 'quality_breakdown': {'good': 'Err', 'moderate': 'Err', 'bad': 'Err'}}

    def close(self):
        """Clean up any resources"""
        logger.info("Closing database connection (if applicable)")
        # No explicit close needed for Firestore client managed by firebase_admin
        # If using offline mode, maybe save data here? Or rely on export.
        pass

    def export_offline_data(self, filepath='cleanroom_data_backup.json'):
        """Export offline data to a JSON file"""
        if not self.offline_mode:
            logger.warning("Export only available in offline mode")
            return False

        try:
            with self.data_lock:
                # Make a copy to avoid issues if data changes during dump
                data_copy = self.offline_data.copy()
                with open(filepath, 'w') as f:
                    json.dump(data_copy, f, indent=2)
            logger.info(f"Offline data exported to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error exporting offline data: {e}", exc_info=True)
            return False

    def import_offline_data(self, filepath='cleanroom_data_backup.json'):
        """Import offline data from a JSON file, validating structure"""
        if not self.offline_mode:
            logger.warning("Import only available in offline mode")
            return False

        if not os.path.exists(filepath):
             logger.error(f"Import file not found: {filepath}")
             return False

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            # Basic validation
            if not isinstance(data, dict) or \
               'worker_gowns' not in data or \
               'washing_records' not in data or \
               'compliance_records' not in data:
                logger.error(f"Invalid data structure in import file: {filepath}")
                return False

            with self.data_lock:
                # Replace existing offline data
                self.offline_data = data
                # Ensure keys exist even if empty in imported file
                if 'worker_gowns' not in self.offline_data: self.offline_data['worker_gowns'] = {}
                if 'washing_records' not in self.offline_data: self.offline_data['washing_records'] = []
                if 'compliance_records' not in self.offline_data: self.offline_data['compliance_records'] = []
                if 'hand_washing_records' not in self.offline_data: self.offline_data['hand_washing_records'] = []


            logger.info(f"Offline data imported successfully from {filepath}")
            return True
        except json.JSONDecodeError as e:
             logger.error(f"Error decoding JSON from import file {filepath}: {e}", exc_info=True)
             return False
        except Exception as e:
            logger.error(f"Error importing offline data: {e}", exc_info=True)
            return False
# --- END OF FILE firebase_database.py ---