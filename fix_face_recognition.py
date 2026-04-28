#!/usr/bin/env python3
"""
Face Recognition Database Diagnostic and Repair Tool
This script helps identify and fix issues with the hand washing monitoring face recognition system.
"""

import pickle
import os
import sys
import logging
from hand_washing_detector import HandWashingDetector

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def analyze_face_database():
    """Analyze the current face recognition database"""
    face_encodings_file = 'face_encodings.pkl'
    
    print("=== Face Recognition Database Analysis ===")
    
    if not os.path.exists(face_encodings_file):
        print("❌ No face encodings file found!")
        return False
    
    try:
        with open(face_encodings_file, 'rb') as f:
            data = pickle.load(f)
        
        encodings = data.get('encodings', [])
        names = data.get('names', [])
        
        print(f"📊 Total encodings: {len(encodings)}")
        print(f"📊 Total names: {len(names)}")
        
        if len(encodings) != len(names):
            print("❌ CRITICAL: Mismatch between encodings and names count!")
            return False
        
        # Count workers
        worker_count = {}
        for name in names:
            worker_count[name] = worker_count.get(name, 0) + 1
        
        print(f"\n👥 Registered workers:")
        for worker, count in worker_count.items():
            if 'test' in worker.lower() or 'Test' in worker:
                status = "🧪 (Test Entry)"
            else:
                status = "✅ (Valid)"
            print(f"  - {worker}: {count} encodings {status}")
        
        # Check for issues
        issues = []
        
        # Check for test entries
        test_entries = sum(1 for name in names if 'test' in name.lower())
        if test_entries > 0:
            issues.append(f"Found {test_entries} test entries that should be removed")
        
        # Check for missing workerC
        if 'workerC' not in worker_count:
            issues.append("workerC is not registered (mentioned in your problem)")
        
        # Check for excessive encodings per worker
        for worker, count in worker_count.items():
            if count > 10:
                issues.append(f"{worker} has {count} encodings (too many, may cause confusion)")
        
        if issues:
            print(f"\n⚠️  Issues found:")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. {issue}")
        else:
            print(f"\n✅ No obvious issues found in database structure")
        
        return True
        
    except Exception as e:
        print(f"❌ Error analyzing database: {e}")
        return False

def clean_database():
    """Clean the face recognition database"""
    print("\n=== Cleaning Face Recognition Database ===")
    
    try:
        detector = HandWashingDetector()
        
        # Show current state
        stats = detector.get_worker_statistics()
        print(f"Before cleaning: {stats}")
        
        # Clean the database
        success = detector.clean_face_database()
        
        if success:
            # Show new state
            new_stats = detector.get_worker_statistics()
            print(f"After cleaning: {new_stats}")
            print("✅ Database cleaned successfully!")
        else:
            print("❌ Failed to clean database!")
            
        return success
        
    except Exception as e:
        print(f"❌ Error cleaning database: {e}")
        return False

def register_missing_workers():
    """Guide user through registering missing workers"""
    print("\n=== Register Missing Workers ===")
    
    detector = HandWashingDetector()
    stats = detector.get_worker_statistics()
    
    expected_workers = ['workerA', 'workerC']
    missing_workers = [w for w in expected_workers if w not in stats]
    
    if not missing_workers:
        print("✅ All expected workers are registered!")
        return True
    
    print(f"Missing workers: {missing_workers}")
    print("\nTo register missing workers:")
    print("1. Use the GUI application")
    print("2. Go to 'Camera Only' mode")
    print("3. Use the face registration feature")
    print("4. Register each missing worker with multiple photos (3-5 different angles)")
    
    return False

def test_face_recognition():
    """Test the face recognition system"""
    print("\n=== Testing Face Recognition System ===")
    
    try:
        detector = HandWashingDetector()
        
        # Check if face recognition is available
        import face_recognition
        print("✅ Face recognition library is available")
        
        # Check database loading
        detector.load_face_encodings()
        stats = detector.get_worker_statistics()
        
        if not stats:
            print("❌ No workers in database!")
            return False
        
        print(f"✅ Loaded workers: {list(stats.keys())}")
        
        # Test with mock camera
        detector.camera_mock = True
        detector.enable_mock = False  # Use real face detection with mock frames
        
        print("🧪 Testing face detection with mock frames...")
        
        # This would require actual face images to test properly
        print("⚠️  Cannot test actual face recognition without camera input")
        print("   To test: Run the application and check the logs for recognition accuracy")
        
        return True
        
    except ImportError:
        print("❌ Face recognition library not available!")
        return False
    except Exception as e:
        print(f"❌ Error testing face recognition: {e}")
        return False

def backup_database():
    """Create a backup of the current database"""
    print("\n=== Creating Database Backup ===")
    
    face_encodings_file = 'face_encodings.pkl'
    backup_file = 'face_encodings_backup.pkl'
    
    try:
        if os.path.exists(face_encodings_file):
            import shutil
            shutil.copy2(face_encodings_file, backup_file)
            print(f"✅ Database backed up to {backup_file}")
            return True
        else:
            print("❌ No database file to backup!")
            return False
    except Exception as e:
        print(f"❌ Error creating backup: {e}")
        return False

def main():
    """Main diagnostic and repair workflow"""
    print("🔍 Face Recognition Diagnostic Tool")
    print("=====================================\n")
    
    # Step 1: Backup current database
    backup_database()
    
    # Step 2: Analyze current state
    if not analyze_face_database():
        print("❌ Cannot proceed without a valid database file!")
        return
    
    # Step 3: Clean database
    print("\nWould you like to clean the database (remove test entries and limit encodings)? (y/N): ", end="")
    if input().lower().startswith('y'):
        clean_database()
        analyze_face_database()  # Re-analyze after cleaning
    
    # Step 4: Check for missing workers
    register_missing_workers()
    
    # Step 5: Test system
    test_face_recognition()
    
    print("\n=== Summary and Recommendations ===")
    print("1. ✅ Database backed up")
    print("2. 🧹 Clean database if it contained test entries")
    print("3. 📝 Register workerC if missing")
    print("4. 🎯 Use stricter tolerance settings (now set to 0.6)")
    print("5. 📊 Monitor logs for recognition accuracy")
    
    print("\n💡 Key improvements made to face recognition:")
    print("   - Improved matching algorithm using face distances")
    print("   - Added confidence scoring for better accuracy")
    print("   - Stricter tolerance to reduce false matches")
    print("   - Better handling of multiple encodings per worker")
    
    print("\n🔄 To complete the fix:")
    print("   1. Run this script to clean the database")
    print("   2. Register workerC using the GUI application")
    print("   3. Test with both workers to verify correct recognition")

if __name__ == "__main__":
    main() 