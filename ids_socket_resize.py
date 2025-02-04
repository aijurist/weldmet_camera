import asyncio
import json
import websockets
from threading import Thread
from queue import Queue
import cv2
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension

# Constants
TARGET_PIXEL_FORMAT = ids_peak_ipl.PixelFormatName_BGRa8


class Camera:
    """
    Camera class for IDS Cameras
    """

    def __init__(self, device_manager, device_index=0):
        self.device_manager = device_manager
        self.device_index = device_index

        self._device = None
        self._datastream = None
        self._acquisition_running = False
        self.target_fps = 20000
        self.max_fps = 0
        self.target_gain = 1
        self.max_gain = 1
        self._node_map = None
        self.image_width = None  # Original image width
        self.image_height = None  # Original image height
        self.target_size = None  # Target resize dimensions (width, height)

        self.killed = False

        self._get_device()
        if not self._device:
            print("Error: Device not found")
        self._setup_device_and_datastream()

        self._image_converter = ids_peak_ipl.ImageConverter()

    def __del__(self):
        self.close()

    def _get_device(self):
        self.device_manager.Update()
        if self.device_manager.Devices().empty():
            raise RuntimeError("No devices found")

        if self.device_index >= len(self.device_manager.Devices()):
            raise IndexError("Invalid device index")

        self._device = self.device_manager.Devices()[self.device_index].OpenDevice(
            ids_peak.DeviceAccessType_Control)
        self._node_map = self._device.RemoteDevice().NodeMaps()[0]

        self.max_gain = self._node_map.FindNode("Gain").Maximum()

        self._node_map.FindNode("UserSetSelector").SetCurrentEntry("Default")
        self._node_map.FindNode("UserSetLoad").Execute()
        self._node_map.FindNode("UserSetLoad").WaitUntilDone()
        
    def _setup_device_and_datastream(self):
        self._datastream = self._device.DataStreams()[0].OpenDataStream()
        self._find_and_set_remote_device_enumeration("GainAuto", "Off")
        self._find_and_set_remote_device_enumeration("ExposureAuto", "Off")

        payload_size = self._node_map.FindNode("PayloadSize").Value()
        max_buffer = self._datastream.NumBuffersAnnouncedMinRequired() * 5
        for idx in range(max_buffer):
            buffer = self._datastream.AllocAndAnnounceBuffer(payload_size)
            self._datastream.QueueBuffer(buffer)
        print("Allocated buffers, finished opening device")

    def close(self):
        self.stop_acquisition()

        if self._datastream is not None:
            try:
                for buffer in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(buffer)
            except Exception as e:
                print(f"Exception (close): {str(e)}")

    def _find_and_set_remote_device_enumeration(self, name: str, value: str):
        all_entries = self._node_map.FindNode(name).Entries()
        available_entries = []
        for entry in all_entries:
            if (entry.AccessStatus() != ids_peak.NodeAccessStatus_NotAvailable
                    and entry.AccessStatus() != ids_peak.NodeAccessStatus_NotImplemented):
                available_entries.append(entry.SymbolicValue())
        if value in available_entries:
            self._node_map.FindNode(name).SetCurrentEntry(value)

    def set_remote_device_value(self, name: str, value: any):
        try:
            self._node_map.FindNode(name).SetValue(value)
        except ids_peak.Exception:
            print(f"Could not set value for {name}!")

    def print_camera_info(self):
        print(
            f"{self._device.ModelName()}: ("
            f"{self._device.ParentInterface().DisplayName()} ; "
            f"{self._device.ParentInterface().ParentSystem().DisplayName()} v."
            f"{self._device.ParentInterface().ParentSystem().Version()})")

    def start_acquisition(self):
        if self._device is None:
            return False
        if self._acquisition_running:
            return True

        self.target_fps = 0
        try:
            self.max_fps = self._node_map.FindNode("AcquisitionFrameRate").Maximum()
            self.target_fps = self.max_fps
            self.set_remote_device_value("AcquisitionFrameRate", self.target_fps)
        except ids_peak.Exception:
            print("Warning: Unable to limit fps, node AcquisitionFrameRate not supported")

        try:
            self._node_map.FindNode("TLParamsLocked").SetValue(1)

            self.image_width = self._node_map.FindNode("Width").Value()
            self.image_height = self._node_map.FindNode("Height").Value()
            input_pixel_format = ids_peak_ipl.PixelFormat(
                self._node_map.FindNode("PixelFormat").CurrentEntry().Value())

            self._image_converter = ids_peak_ipl.ImageConverter()
            self._image_converter.PreAllocateConversion(
                input_pixel_format, TARGET_PIXEL_FORMAT,
                self.image_width, self.image_height)

            self._datastream.StartAcquisition()
            self._node_map.FindNode("AcquisitionStart").Execute()
            self._node_map.FindNode("AcquisitionStart").WaitUntilDone()
        except Exception as e:
            print(f"Exception (start acquisition): {str(e)}")
            return False
        self._acquisition_running = True
        return True

    def stop_acquisition(self):
        if self._device is None or not self._acquisition_running:
            return
        try:
            self._node_map.FindNode("AcquisitionStop").Execute()
            self._datastream.KillWait()
            self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
            self._acquisition_running = False
            self._node_map.FindNode("TLParamsLocked").SetValue(0)
        except Exception as e:
            print(f"Exception (stop acquisition): {str(e)}")

    def get_jpeg_frame(self):
        """
        Captures a single frame and returns it as JPEG bytes
        """
        buffer = None
        try:
            buffer = self._datastream.WaitForFinishedBuffer(5000)
            image = ids_peak_ipl_extension.BufferToImage(buffer)
            converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
            np_image = converted_image.get_numpy_3D()

            # Resize image if target_size is specified
            if self.target_size is not None:
                np_image = cv2.resize(np_image, self.target_size)

            success, jpeg_buffer = cv2.imencode('.jpg', np_image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if success:
                return jpeg_buffer.tobytes()
            else:
                raise RuntimeError("Failed to encode JPEG")
        except Exception as e:
            print(f"Error capturing frame: {str(e)}")
            raise
        finally:
            if buffer is not None:
                self._datastream.QueueBuffer(buffer)


class WebSocketServer:
    def __init__(self):
        self.clients = set()
        self.streaming = False
        self.current_camera = None
        self.device_manager = ids_peak.DeviceManager.Instance()
        self.frame_queue = Queue(maxsize=10)  # Limit queue size to prevent memory issues

        # Initialize IDS Peak library
        ids_peak.Library.Initialize()

    async def handler(self, websocket):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                await self.handle_command(message, websocket)
        finally:
            self.clients.remove(websocket)

    async def handle_command(self, message, websocket):
        try:
            command_data = json.loads(message)
            command = command_data.get("command")

            if command == "get_devices":
                await self.send_devices_list(websocket)
            elif command == "start_stream":
                await self.start_stream(command_data, websocket)
            elif command == "stop_stream":
                await self.stop_stream(websocket)
        except Exception as e:
            error_msg = {"error": str(e)}
            await websocket.send(json.dumps(error_msg))

    async def send_devices_list(self, websocket):
        self.device_manager.Update()
        devices = []
        for idx, device in enumerate(self.device_manager.Devices()):
            devices.append({
                "index": idx,
                "type": device.ParentInterface().DisplayName(),
                "model": device.ModelName(),
                "serial": device.SerialNumber()
            })
        response = {
            "message": f"Found {len(devices)} devices",
            "devices": devices
        }
        await websocket.send(json.dumps(response))

    async def start_stream(self, command_data, websocket):
        if self.streaming:
            await websocket.send(json.dumps({"error": "Stream already running"}))
            return

        device_index = command_data.get("index", 0)
        target_width = command_data.get("width")
        target_height = command_data.get("height")

        self.current_camera = Camera(self.device_manager, device_index)
        if not self.current_camera.start_acquisition():
            raise RuntimeError("Failed to start camera acquisition")

        # Set target resize dimensions if provided
        if target_width is not None and target_height is not None:
            self.current_camera.target_size = (int(target_width), int(target_height))
            frame_width = int(target_width)
            frame_height = int(target_height)
        else:
            frame_width = self.current_camera.image_width
            frame_height = self.current_camera.image_height

        self.streaming = True
        # Start frame producer in a separate thread
        Thread(target=self.frame_producer, daemon=True).start()
        # Start frame consumer in asyncio loop
        asyncio.create_task(self.frame_consumer(websocket))
        await websocket.send(json.dumps({
            "message": "Stream started",
            "frame_width": frame_width,
            "frame_height": frame_height
        }))

    async def stop_stream(self, websocket):
        if self.streaming and self.current_camera:
            self.streaming = False
            self.current_camera.stop_acquisition()
            self.current_camera.close()
            self.current_camera = None
            await websocket.send(json.dumps({"message": "Stream stopped"}))
        else:
            await websocket.send(json.dumps({"error": "No active stream"}))

    def frame_producer(self):
        while self.streaming:
            try:
                # Get frame as JPEG bytes
                jpeg_bytes = self.current_camera.get_jpeg_frame()
                if jpeg_bytes and not self.frame_queue.full():
                    self.frame_queue.put(jpeg_bytes)
            except Exception as e:
                print(f"Frame production error: {str(e)}")
                break

    async def frame_consumer(self, websocket):
        while self.streaming:
            try:
                jpeg_bytes = self.frame_queue.get()
                await websocket.send(jpeg_bytes)
                self.frame_queue.task_done()
            except Exception as e:
                print(f"Frame consumption error: {str(e)}")
                break


async def main():
    ids_peak.Library.Initialize()
    server = WebSocketServer()
    async with websockets.serve(server.handler, "localhost", 8765):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())