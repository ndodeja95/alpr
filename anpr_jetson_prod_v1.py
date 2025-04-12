import cv2
import time
import json
import random
import datetime
import threading
import os
import numpy as np
import subprocess
import re
import socket
import Jetson.GPIO as GPIO

from color_detection import get_dominant_color
from ocr.fast_ocr import detect_truck_number, detect_text
from ocr.usdot_extractor import TruckInfoExtractor

# Camera Setup
CAMERA_MAC = "00:1A:2B:3C:4D:5E"  # Replace with your camera's MAC address
RTSP_TEMPLATE = "rtsp://foo:bar@{}:554/cam/realmonitor?channel=4&subtype=0&tcp"
MOTION_THRESHOLD = 1500
PAUSE_AFTER_TOGGLE = 10  # Seconds to pause after gate opens
DETECTION_TICKS = 10
TICK_INTERVAL = 0.2

# GPIO Setup
GPIO_PIN = 11  # Jetson Nano pin 50

# GPIO Functions
def setup_gpio():
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(GPIO_PIN, GPIO.OUT, initial=GPIO.LOW)

def toggle_gate(action="open"):
    print(f"Gate {action}ing")
    GPIO.output(GPIO_PIN, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(GPIO_PIN, GPIO.LOW)
    time.sleep(0.5)
    print(f"Gate {action} complete")

# Camera IP Discovery
def get_local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        subnet = ".".join(local_ip.split(".")[:-1])
        return subnet
    except Exception:
        print("Error detecting subnet, using fallback")
        return "192.168.1"
    finally:
        s.close()

def ping_sweep(subnet):
    print(f"Pinging subnet {subnet}.0-255 to find camera...")
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        subprocess.Popen(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)

def get_ip_from_mac(mac_address):
    mac_address = mac_address.lower()
    try:
        result = subprocess.check_output(["arp", "-a"], text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running arp -a: {e.output}")
        return None
    
    pattern = re.compile(r"\?*\s*\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([\w:]+)\s+\[.*\]\s+on\s+\w+")
    for line in result.splitlines():
        match = pattern.search(line)
        if match:
            ip, mac = match.groups()
            if mac.lower() == mac_address:
                print(f"Found camera IP: {ip}")
                return ip
    
    try:
        result = subprocess.check_output(["ip", "-n", "neigh"], text=True, stderr=subprocess.STDOUT).strip()
        pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+dev\s+\w+\s+lladdr\s+([\w:]+)")
        for line in result.splitlines():
            match = pattern.search(line)
            if match:
                ip, mac = match.groups()
                if mac.lower() == mac_address:
                    print(f"Found camera IP: {ip}")
                    return ip
    except subprocess.CalledProcessError as e:
        print(f"Error running ip neigh: {e.output}")
    
    return None

def find_camera_ip(mac_address, retries=3, delay=3):
    subnet = get_local_subnet()
    for attempt in range(retries):
        ip = get_ip_from_mac(mac_address)
        if ip:
            return ip
        print(f"Attempt {attempt + 1}/{retries}: Camera not found, pinging subnet...")
        ping_sweep(subnet)
        time.sleep(delay)
    
    raise Exception(f"Could not find IP for MAC {mac_address}")

# ANPR Functions
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

def get_vehicle_data(detected_number, temp_path):
    print(f"Processing vehicle data for frame: {temp_path}")
    dominant_color = get_dominant_color(temp_path)
    print(f"Dominant color: {dominant_color}")
    
    usdot_number, vin_number = truck_info_extractor.extract_info(temp_path)
    print(f"Truck info: {usdot_number}, {vin_number}")

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
            "trailers": [f"TRAILER{random.randint(100, 999)}" for _ in range(random.randint(0, 2))]
        },
        "model_version": "1.0"
    }]

    json_output_path = "./output/output.txt"
    with open(json_output_path, "w") as json_file:
        json.dump(test_data, json_file, indent=2)
    print(f"Output written to {json_output_path}")

def initialize_rtsp(rtsp_url):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("Error: Could not open RTSP stream")
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 15)
    start_time = time.time()
    while time.time() - start_time < 1.0:
        cap.read()
    return cap

def main():
    setup_gpio()
    
    # Find camera IP
    camera_ip = find_camera_ip(CAMERA_MAC)
    rtsp_url = RTSP_TEMPLATE.format(camera_ip)
    print(f"Connecting to RTSP: {rtsp_url}")
    cap = initialize_rtsp(rtsp_url)
    if cap is None:
        GPIO.cleanup()
        exit(1)

    allowed_vehicles = json.load(open("allowed_vehicles.json"))
    allowed_numbers = [vehicle['vehicle_num'] for vehicle in allowed_vehicles['vehicles']]

    print("\n\n")
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
    print("🚗🚗 Starting vehicle detection (idle mode)...")
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
    print("\n")

    global truck_info_extractor
    cycle_count = 0
    while True:
        print("Reconnecting RTSP stream for freshness...")
        cap.release()
        cap = initialize_rtsp(rtsp_url)
        if cap is None:
            GPIO.cleanup()
            exit(1)
        
        start_time = time.time()
        while time.time() - start_time < 0.5:
            cap.read()
        
        ret, frame = cap.read()
        if not ret:
            print("Error: Could not read frame from RTSP, reconnecting...")
            continue
        
        frame = cv2.resize(frame, (640, 480))
        
        if not detect_motion(frame):
            print("No motion detected (idle)")
            time.sleep(0.1)
            continue

        cycle_count += 1
        print(f"Motion detected! Starting OCR for {DETECTION_TICKS} ticks (cycle {cycle_count})...")
        truck_info_extractor = TruckInfoExtractor()
        ticks = 0
        success = False
        temp_path = "./output/temp_frame.jpg"
        while ticks < DETECTION_TICKS and not success:
            start_time = time.time()
            while time.time() - start_time < 0.5:
                cap.read()
            
            ret, frame = cap.read()
            if not ret:
                print("Error: Could not read frame, breaking tick...")
                break
            
            frame = cv2.resize(frame, (640, 480))
            cv2.imwrite(temp_path, frame)

            text_is_detected = detect_text(temp_path)
            
            if text_is_detected:
                result = detect_truck_number(temp_path, allowed_numbers)
                
                if result:
                    print(f"🚗 Vehicle found! Number: {result}")
                    success = True
                    gate_thread = threading.Thread(target=toggle_gate, args=("open",))
                    data_thread = threading.Thread(target=get_vehicle_data, args=(result, temp_path))
                    gate_thread.start()
                    data_thread.start()
                    gate_thread.join()
                    data_thread.join()
                    print(f"Pausing for {PAUSE_AFTER_TOGGLE} seconds after gate opens...")
                    time.sleep(PAUSE_AFTER_TOGGLE)
                    print("Closing gate...")
                    toggle_gate(action="close")
                    try:
                        os.remove(temp_path)
                        print(f"Temp frame deleted")
                    except OSError as e:
                        print(f"Error deleting temp frame: {e}")
                    print("Returning to idle mode...")
                else:
                    print("🚗 Vehicle not in allowed list")
            else:
                print("No text detected in frame")
            
            if not success:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            
            ticks += 1
            time.sleep(TICK_INTERVAL)

        if not success:
            print(f"No valid vehicle found after {DETECTION_TICKS} ticks, returning to idle...")

if __name__ == "__main__":
    try:
        main()
    finally:
        if 'cap' in locals():
            cap.release()
        GPIO.cleanup()