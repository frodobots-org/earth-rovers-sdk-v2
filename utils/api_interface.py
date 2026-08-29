import time 
import requests
import base64
import pygame 
import threading
import matplotlib.pyplot as plt
from PIL import Image
from io import BytesIO
import sys
import os
import json

class JoyStickInterface: 
    pass

class APIInterface:
    def __init__(self, api_key, base_url, command_interface, joystick_id=0, frame_rate=30, data_rate=None):
        self.base_url = base_url
        self.api_key = api_key
        # Sending commands 
        self.command_interface = command_interface
        self.joystick_id = joystick_id

        if self.command_interface == "joystick":
            pygame.init()
            pygame.joystick.init()
            self.joystick = pygame.joystick.Joystick(self.joystick_id)
            self.joystick.init()
        elif self.command_interface == "keyboard":
            raise NotImplementedError("Keyboard interface not implemented yet")
            
        # Receive data
        self.frame_rate = frame_rate
        self.data_rate = data_rate

        # Start the API interface
        self.start()
    
    def decode_from_base64(self, base64_string):
        image = Image.open(BytesIO(base64.b64decode(base64_string)))
        return image

    def send_velocity_command(self, linear, angular):
        print("Linear: ", linear, "Angular: ", angular)
        url = f"{self.base_url}/control"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            }
        data = json.dumps({"command": {"linear": linear, "angular": angular}})
        response = requests.post(url, headers=headers, data=data)
        response = response.json()
        if response["message"] == 'Command sent successfully':
            return True 
        else:
            return False
    
    def data_request(self, lock):
        url = f"{self.base_url}/data"
        response = requests.get(url)
        response = response.text
        with lock:
            self.current_data = response
        return True
    
    def image_request(self, lock):
        url = f"{self.base_url}/screenshot"
        response = requests.get(url) 
        response = response.json()
        front_frame = self.decode_from_base64(response["front_video_frame"])
        rear_frame = self.decode_from_base64(response["rear_video_frame"])
        map_frame = self.decode_from_base64(response["map_frame"])
        timestamp = response["timestamp"]
        with lock:
            self.current_front_frame = (front_frame, timestamp)
            self.current_rear_frame = (rear_frame, timestamp) 
            self.current_map = (map_frame, timestamp)
        return True
    
    def send_joy_commands(self):
        events = pygame.event.get()
        if len(events) > 0:
            event = events[0]
        else:
            return False
        if event.type == pygame.JOYAXISMOTION:
            linear = self.joystick.get_axis(4)*-1
            angular = self.joystick.get_axis(3)*-1
            self.send_velocity_command(linear, angular)
        return True

    def image_loop(self, lock):
        while True:
            self.image_request(lock)
            time.sleep(1/self.frame_rate)
    
    def data_loop(self, lock):
        while True:
            self.data_request(lock)
            time.sleep(1/self.data_rate)

    # Main loop which receives data and sends commands
    def start(self):
        print("Starting API Interface")
        lock = threading.Lock()
        image_thread = threading.Thread(target=self.image_loop, args=(lock,))
        data_thread = threading.Thread(target=self.data_loop, args=(lock,))
        image_thread.start()
        data_thread.start()
        try: 
            while True:
                if self.command_interface == "joystick":
                    self.send_joy_commands()
                elif self.command_interface == "keyboard":
                    self.send_key_commands()
        except KeyboardInterrupt:
            image_thread.join()
            data_thread.join()
            pygame.quit()
            print("Exiting API Interface")
    
if __name__ == "__main__":
    api_key = os.getenv("SDK_API_TOKEN")
    api_interface = APIInterface(api_key, "http://localhost:8000", "joystick", frame_rate=30, data_rate=30)
    api_interface.start()




    
    