import cv2
import time
import json
import random
import datetime
import threading
import os
import subprocess
import re
import numpy as np
# Jetson.GPIO commented out for Windows testing
# import Jetson.GPIO as GPIO

from color_detection import get_dominant_color
from ocr.fast_ocr import detect_truck_number, detect_text
from ocr.usdot_extractor import TruckInfoExtractor

# GPIO Setup (commented out for Windows)
# GPIO_PIN = 50

# Camera Setup with Hardcoded IP
CAMERA_IP = "192.168.0.100"  # Replace with your camera's IP address
RTSP_URL = "rtsp://admin:admin123@{}:554/cam/realmonitor?channel=1&subtype=0".format(CAMERA_IP)
MOTION_THRESHOLD = 1000

# GPIO Functions (Modified for Windows)
def setup_gpio():
    # No GPIO setup needed for Windows, just print a message
    print("GPIO setup simulated for Windows")

def toggle_gate():
    # Simulate gate toggle with a print statement instead of GPIO
    print("Gate toggled")
    time.sleep(0.1)  # Keep timing consistent with original
    print("Gate toggle in progress...")
    time.sleep(0.5)
    print("Gate toggle complete")

# ANPR Functions
truck_info_extractor = TruckInfoExtractor()

prev_frame = None
motion_detected = False

def detect_motion(frame):
    global prev_frame, motion_detected
    
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    
    if prev_frame is None:
        prev_frame = gray
        return False
    
    frame_diff = cv2.absdiff(prev_frame, gray)
    thresh = cv2.threshold(frame_diff, 25, 255, cv2.THRESH_BINARY)[1]
    motion_score = np.sum(thresh)
    motion_detected = motion_score > MOTION_THRESHOLD
    prev_frame = gray
    
    return motion_detected

def get_vehicle_data(detected_number, frame):
    temp_path = "./output/temp_frame.jpg"
    cv2.imwrite(temp_path, frame)
    
    dominant_color = get_dominant_color(temp_path)
    print("Dominant color: {}".format(dominant_color))
    
    usdot_number, vin_number = truck_info_extractor.extract_info(temp_path)
    print("Truck info: {}, {}".format(usdot_number, vin_number))

    usdot_number = usdot_number if usdot_number else "N/A"
    vin_number = vin_number if vin_number else "N/A"

    timestamp = datetime.datetime.now().isoformat()
    test_data = [{
        "metadata": {
            "location_id": "Test_Location",
            "box_id": "Test_Camera",
            "datetime": timestamp
        },
        "vehicle_data": {
            "truck_number": detected_number,
            "usdot": usdot_number,
            "vin": vin_number,
            "truck_color": dominant_color,
            "trailers": ["TRAILER{}".format(random.randint(100, 999)) for _ in range(random.randint(0, 2))]
        },
        "model_version": "1.0"
    }]

    json_output_path = "./output/output.txt"
    with open(json_output_path, "w") as json_file:
        json.dump(test_data, json_file, indent=2)
    print("Output written to {}".format(json_output_path))
    
    os.remove(temp_path)

def main():
    setup_gpio()
    
    # Use hardcoded RTSP URL
    print("Using RTSP URL: {}".format(RTSP_URL))
    
    # Open RTSP stream
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        print("Error: Could not open RTSP stream")
        exit(1)

    allowed_vehicles = json.load(open("allowed_vehicles.json"))
    allowed_numbers = [vehicle['vehicle_num'] for vehicle in allowed_vehicles['vehicles']]

    print("\n\n")
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
    print("🚗🚗 Starting vehicle detection...")
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
    print("\n")

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Error: Could not read frame")
            time.sleep(1)
            continue
        
        if not detect_motion(frame):
            print("No motion detected")
            time.sleep(0.1)
            continue

        temp_path = "./output/temp_frame.jpg"
        cv2.imwrite(temp_path, frame)

        text_is_detected = detect_text(temp_path)
        
        if text_is_detected:
            result = detect_truck_number(temp_path, allowed_numbers)
            
            if result:
                print("🚗 Vehicle found! Number: {}".format(result))
                threading.Thread(target=toggle_gate).start()
                threading.Thread(target=get_vehicle_data, args=(result, frame)).start()
            else:
                print("🚗 Vehicle not in allowed list")
        else:
            print("No text detected in frame")
        
        os.remove(temp_path)
        
        time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    finally:
        if 'cap' in locals():
            cap.release()
        # GPIO.cleanup()  # Commented out for Windows