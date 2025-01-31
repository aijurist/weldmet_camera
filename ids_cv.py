import cv2
import time
from camera import Camera  # Assuming Camera class exists in camera.py

class CameraController:
    def __init__(self, device_id=0):
        self.camera = Camera(device_id)
        self.is_capturing = False

    def start_camera(self):
        """Initialize and start the camera"""
        if not self.camera.initialize():
            raise RuntimeError("Camera initialization failed")
        if not self.camera.start():
            raise RuntimeError("Failed to start camera stream")
        self.is_capturing = True
        print("Camera started successfully")

    def capture_frames(self, duration=10):
        """Capture frames for specified duration"""
        start_time = time.time()
        frame_count = 0
        
        while self.is_capturing and (time.time() - start_time) < duration:
            try:
                frame = self.camera.capture_frame()
                if frame is None:
                    print("Warning: Received empty frame")
                    continue
                
                # Process frame (example: display using OpenCV)
                cv2.imshow('Camera Feed', frame)
                frame_count += 1
                
                # Exit on 'q' key press
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                    
            except Exception as e:
                print(f"Error capturing frame: {str(e)}")
                self.stop_camera()
                break

        print(f"Captured {frame_count} frames in {duration} seconds")

    def stop_camera(self):
        """Stop capturing and release resources"""
        if self.is_capturing:
            self.camera.stop()
            self.is_capturing = False
            print("Camera stream stopped")

    def close_camera(self):
        """Close camera connection"""
        self.camera.close()
        cv2.destroyAllWindows()
        print("Camera connection closed")

if __name__ == "__main__":
    # Usage example
    controller = CameraController()
    
    try:
        controller.start_camera()
        controller.capture_frames(duration=15)  # Capture for 15 seconds
    except Exception as e:
        print(f"Camera error: {str(e)}")
    finally:
        controller.stop_camera()
        controller.close_camera()