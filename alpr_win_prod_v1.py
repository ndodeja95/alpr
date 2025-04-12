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
import socket
import struct
# Jetson.GPIO commented out for Windows testing
# import Jetson.GPIO as GPIO

from color_detection import get_dominant_color
from ocr.fast_ocr import detect_truck_number, detect_text
from ocr.usdot_extractor import TruckInfoExtractor

# RTSP Camera Setup
CAMERA_MAC = "28:18:FD:08:B2:DC"  # Replace with your camera's MAC address
RTSP_TEMPLATE = "rtsp://admin:admin123@{}:554/cam/realmonitor?channel=1&subtype=0"
MOTION_THRESHOLD = 1500  # Increased to reduce false positives
PAUSE_AFTER_TOGGLE = 10  # Seconds to pause after gate opens
DETECTION_TICKS = 10  # Reduced for faster detection
TICK_INTERVAL = 0.2  # Faster ticks

# GPIO Functions (Modified for Windows)
def setup_gpio():
    print("GPIO setup simulated for Windows")

def toggle_gate(action="open"):
    print(f"Gate {action}ing")
    time.sleep(0.1)
    print(f"Gate {action} in progress...")
    time.sleep(0.5)
    print(f"Gate {action} complete")



# Camera IP Discovery
def get_local_subnet():
    """Detect local subnet (e.g., 192.168.0)"""
    # Create a dummy socket to get local IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a public IP (won't actually send data)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        # Extract subnet (first 3 octets)
        subnet = ".".join(local_ip.split(".")[:-1])
        return subnet
    except Exception as e:
        print("Error detecting subnet: {}".format(e))
        return "192.168.0"  # Fallback to common default
    finally:
        s.close()

def ping_sweep(subnet):
    """Ping all IPs in the subnet to populate ARP table"""
    print("Pinging subnet {}.0-255 to find camera...".format(subnet))
    for i in range(1, 255):
        ip = "{}.{}".format(subnet, i)
        # Windows ping: -n 1 (count), -w 1000 (timeout in ms)
        subprocess.Popen(["ping", "-n", "1", "-w", "1000", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Give some time for ARP table to update
    time.sleep(2)

def get_ip_from_mac(mac_address):
    """Find IP address associated with a MAC address using arp -a"""
    mac_address = mac_address.lower()
    try:
        result = subprocess.check_output(["arp", "-a"], text=True).strip()
    except subprocess.CalledProcessError:
        print("Error running arp -a")
        return None
    
    pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([\w:-]+)")
    for line in result.splitlines():
        match = pattern.search(line)
        if match:
            ip, mac = match.groups()
            mac = mac.lower().replace("-", ":")
            if mac == mac_address:
                print("Found camera IP: {} for MAC: {}".format(ip, mac))
                return ip
    
    print("Camera with MAC {} not found in ARP table".format(mac_address))
    return None

def find_camera_ip(mac_address, retries=3, delay=5):
    """Attempt to find camera IP with retries and subnet ping"""
    subnet = get_local_subnet()
    for attempt in range(retries):
        ip = get_ip_from_mac(mac_address)
        if ip:
            return ip 
        
        print("Attempt {}/{}: Camera not found, pinging subnet...".format(attempt + 1, retries))
        ping_sweep(subnet)
        time.sleep(delay)  # Wait for ARP table to update
    
    raise Exception("Could not find IP for MAC {} after {} attempts".format(mac_address, retries))

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
    # Use FFmpeg with low-latency flags
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|fflags;nobuffer|flags;low_delay"
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("Error: Could not open RTSP stream")
        return None
    # Optimize settings
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 15)  # Lower FPS to reduce load
    # Flush buffer
    start_time = time.time()
    while time.time() - start_time < 1.0:  # 1-second flush
        cap.read()
    return cap

def main():
    setup_gpio()
    
    # Initialize RTSP stream
    camera_ip = find_camera_ip(CAMERA_MAC)
    rtsp_url = RTSP_TEMPLATE.format(camera_ip)
    print(f"Connecting to RTSP: {rtsp_url}")
    cap = initialize_rtsp(rtsp_url)
    if cap is None:
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
    while True:  # Infinite loop for idle state
        # Reconnect stream every cycle
        print("Reconnecting RTSP stream for freshness...")
        cap.release()
        cap = initialize_rtsp(rtsp_url)
        if cap is None:
            exit(1)
        
        # Clear buffer
        start_time = time.time()
        while time.time() - start_time < 0.5:
            cap.read()
        
        # read_start = time.time()
        ret, frame = cap.read()
        # read_time = time.time() - read_start
        if not ret:
            print("Error: Could not read frame from RTSP, reconnecting...")
            continue
        
        # Resize frame to reduce processing load
        frame = cv2.resize(frame, (640, 480))
        
        if not detect_motion(frame):
            print("No motion detected (idle)")
            time.sleep(0.1)
            continue

        cycle_count += 1
        print(f"Motion detected! Starting OCR for {DETECTION_TICKS} ticks (cycle {cycle_count})...")
        # Initialize OCR once per cycle
        truck_info_extractor = TruckInfoExtractor()
        ticks = 0
        success = False
        temp_path = "./output/temp_frame.jpg"
        while ticks < DETECTION_TICKS and not success:
            # Clear buffer
            start_time = time.time()
            while time.time() - start_time < 0.5:
                cap.read()
            
            read_start = time.time()
            ret, frame = cap.read()
            read_time = time.time() - read_start
            if not ret:
                print("Error: Could not read frame, breaking tick...")
                break
            print(f"Frame read took {read_time:.3f} seconds")
            
            # Resize frame
            frame = cv2.resize(frame, (640, 480))
            
            # Write temp frame
            # temp_path = f"./output/temp_frame_{ticks}.jpg"
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
                        print(f"Temp frame deleted: {temp_path}")
                    except OSError as e:
                        print(f"Error deleting temp frame: {e}")
                    print("Returning to idle mode...")
                else:
                    print("🚗 Vehicle not in allowed list")
            else:
                print("No text detected in frame")
            
            # Clean up temp frame
            if not success:
                try:
                    os.remove(temp_path)
                    print(f"Temp frame deleted: {temp_path}")
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
        # GPIO.cleanup()  # Commented out for Windows

