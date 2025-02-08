import asyncio
import json
import websockets
from threading import Thread
from queue import Queue
import cv2
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension
from turbojpeg import TurboJPEG, TJPF_BGR

# Constants
TARGET_PIXEL_FORMAT = ids_peak_ipl.PixelFormatName_BGRa8
JPEG_QUALITY = 75          # Reduced JPEG quality for faster encoding
BUFFER_TIMEOUT = 1000      # Reduced wait time (in ms) for a finished buffer

class Camera:
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
        self.image_width = None
        self.image_height = None
        self.target_size = None
        self.killed = False
        self._get_device()
        self.jpeg_encoder = TurboJPEG(r"C:\libjpeg-turbo-gcc64\bin\libturbojpeg.dll")
        if self._device:
            self._setup_device_and_datastream()

    def __del__(self):
        self.close()

    def _get_device(self):
        self.device_manager.Update()
        if self.device_manager.Devices().empty():
            raise RuntimeError("No devices found")
        if self.device_index >= len(self.device_manager.Devices()):
            raise IndexError("Invalid device index")
        self._device = self.device_manager.Devices()[self.device_index].OpenDevice(ids_peak.DeviceAccessType_Control)
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

    def close(self):
        self.stop_acquisition()
        if self._datastream:
            try:
                self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for buffer in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(buffer)
            except Exception as e:
                print(f"Exception (close): {str(e)}")
            finally:
                self._datastream = None

    def connect(self, device_index):
        if self.device is not None:
            self.disconnect()
        devices = self.device_manager.Devices()
        if device_index >= len(devices):
            raise ValueError("Invalid device index")
        self.device = devices[device_index].OpenDevice(ids_peak.DeviceAccessType_Control)
        self.node_map = self.device.RemoteDevice().NodeMaps()[0]
        print(f"Connected to {self.device.ModelName()}")

    def _find_and_set_remote_device_enumeration(self, name: str, value: str):
        entries = self._node_map.FindNode(name).Entries()
        available_entries = [entry.SymbolicValue() for entry in entries if entry.IsAvailable()]
        if value in available_entries:
            self._node_map.FindNode(name).SetCurrentEntry(value)

    def set_remote_device_value(self, name: str, value: any):
        try:
            self._node_map.FindNode(name).SetValue(value)
        except ids_peak.Exception:
            print(f"Could not set value for {name}!")

    def start_acquisition(self):
        if self._device is None or self._acquisition_running:
            return False
        try:
            self.max_fps = self._node_map.FindNode("AcquisitionFrameRate").Maximum()
            self.target_fps = self.max_fps
            self.set_remote_device_value("AcquisitionFrameRate", self.target_fps)
        except ids_peak.Exception:
            pass
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
            self._acquisition_running = True
            return True
        except Exception as e:
            print(f"Exception (start acquisition): {str(e)}")
            return False

    def stop_acquisition(self):
        if not self._acquisition_running:
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

    # def get_jpeg_frame(self):
    #     buffer = None
    #     try:
    #         buffer = self._datastream.WaitForFinishedBuffer(BUFFER_TIMEOUT)
    #         image = ids_peak_ipl_extension.BufferToImage(buffer)
    #         converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
    #         np_image = converted_image.get_numpy_3D()
    #         if self.target_size:
    #             np_image = cv2.resize(np_image, self.target_size)
    #         success, jpeg_buffer = cv2.imencode('.jpg', np_image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    #         return jpeg_buffer.tobytes() if success else None
    #     except Exception as e:
    #         print(f"Error capturing frame: {str(e)}")
    #         raise
    #     finally:
    #         if buffer:
    #             self._datastream.QueueBuffer(buffer)
    
    def get_jpeg_frame(self):
        buffer = None
        try:
            buffer = self._datastream.WaitForFinishedBuffer(BUFFER_TIMEOUT)
            image = ids_peak_ipl_extension.BufferToImage(buffer)
            converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
            np_image = converted_image.get_numpy_3D()
            if self.target_size:
                np_image = cv2.resize(np_image, self.target_size)
            # Use TurboJPEG for faster encoding
            jpeg_bytes = self.jpeg_encoder.encode(np_image, quality=JPEG_QUALITY)
            return jpeg_bytes
        except Exception as e:
            print(f"Error capturing frame: {str(e)}")
            raise
        finally:
            if buffer:
                self._datastream.QueueBuffer(buffer)


    def get_all_max(self):
        max_values = {}
        target_parameters = [
            "ExposureTime", "Gain", "AcquisitionFrameRate",
            "Width", "Height", "Gamma", "BlackLevel"
        ]
        for param in target_parameters:
            try:
                node = self._node_map.FindNode(param)
                if isinstance(node, (ids_peak.FloatNode, ids_peak.IntegerNode)):
                    max_values[param] = node.Maximum()
            except:
                continue
        return max_values

    def get_all_min(self):
        min_values = {}
        target_parameters = [
            "ExposureTime", "Gain", "AcquisitionFrameRate",
            "Width", "Height", "Gamma", "BlackLevel"
        ]
        for param in target_parameters:
            try:
                node = self._node_map.FindNode(param)
                if isinstance(node, (ids_peak.FloatNode, ids_peak.IntegerNode)):
                    min_values[param] = node.Minimum()
            except:
                continue
        return min_values

    def get_all_current(self):
        current_values = {}
        target_parameters = [
            "ExposureTime", "Gain", "AcquisitionFrameRate",
            "Width", "Height", "PixelFormat", "BalanceWhiteAuto",
            "Gamma", "BlackLevel", "ReverseX", "ReverseY"
        ]
        for param in target_parameters:
            try:
                node = self._node_map.FindNode(param)
                if isinstance(node, ids_peak.EnumerationNode):
                    current_entry = node.CurrentEntry()
                    current_values[param] = current_entry.SymbolicValue()
                elif isinstance(node, ids_peak.BooleanNode):
                    current_values[param] = bool(node.Value())
                elif isinstance(node, (ids_peak.IntegerNode, ids_peak.FloatNode)):
                    current_values[param] = node.Value()
            except:
                continue
        return current_values

    def set_parameter(self, name: str, value):
        try:
            node = self._node_map.FindNode(name)
            if isinstance(node, ids_peak.FloatNode):
                value = float(value)
                min_val = node.Minimum()
                max_val = node.Maximum()
                inc = node.Inc() if hasattr(node, 'Inc') and node.Inc() > 0 else None
                if inc:
                    value = round(value / inc) * inc
                value = max(min(value, max_val), min_val)
                node.SetValue(value)
            elif isinstance(node, ids_peak.IntegerNode):
                value = int(value)
                value = max(min(value, node.Maximum()), node.Minimum())
                node.SetValue(value)
            elif isinstance(node, ids_peak.EnumerationNode):
                entries = [entry.SymbolicValue() for entry in node.Entries() if entry.IsAvailable()]
                if value in entries:
                    node.SetCurrentEntry(value)
                else:
                    raise ValueError(f"Value {value} not available for {name}")
            elif isinstance(node, ids_peak.BooleanNode):
                node.SetValue(bool(value))
            else:
                raise ValueError(f"Unsupported node type: {type(node)}")
            return True
        except Exception as e:
            print(f"Error setting {name}: {str(e)}")
            return False

class WebSocketServer:
    def __init__(self):
        self.clients = set()
        self.streaming = False
        self.current_camera = None
        self.device_manager = ids_peak.DeviceManager.Instance()
        self.frame_queue = Queue(maxsize=1)
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
            data = json.loads(message)
            command = data.get("command")

            if command == "get_devices":
                await self.send_devices_list(websocket)
            elif command == "connect":
                await self.connect(data, websocket)
            elif command == "disconnect":
                await self.disconnect(websocket)
            elif command == "start_stream":
                await self.start_stream(data, websocket)
            elif command == "stop_stream":
                await self.stop_stream(websocket)
            elif command == "getMax":
                await self.send_max_values(websocket)
            elif command == "getMin":
                await self.send_min_values(websocket)
            elif command == "getCurrent":
                await self.send_current_values(websocket)
            elif command == "setValue":
                await self.set_parameter_value(data, websocket)
            else:
                await websocket.send(json.dumps({"error": "Unknown command"}))
        except Exception as e:
            await websocket.send(json.dumps({"error": str(e)}))

    async def send_devices_list(self, websocket):
        self.device_manager.Update()
        devices = []
        for idx, device in enumerate(self.device_manager.Devices()):
            devices.append({
                "index": idx,
                "model": device.ModelName(),
                "serial": device.SerialNumber(),
                "interface": device.ParentInterface().DisplayName()
            })
        await websocket.send(json.dumps({"devices": devices}))

    async def connect(self, data, websocket):
        device_index = data.get("index", 0)
        try:
            if self.streaming:
                await self.stop_stream(websocket)
            self.current_camera = Camera(self.device_manager, device_index)
            await websocket.send(json.dumps({
                "message": f"Connected to {self.current_camera._device.ModelName()}"
            }))
        except Exception as e:
            await websocket.send(json.dumps({"error": str(e)}))

    async def disconnect(self, websocket):
        if self.streaming:
            await self.stop_stream(websocket)
        if self.current_camera:
            self.current_camera.close()
            self.current_camera = None
            await websocket.send(json.dumps({"message": "Disconnected from camera"}))
        else:
            await websocket.send(json.dumps({"error": "No camera connected"}))

    async def start_stream(self, data, websocket):
        if self.streaming:
            await websocket.send(json.dumps({"error": "Stream already running"}))
            return
        device_index = data.get("index", 0)
        target_size = (data.get("width"), data.get("height"))
        try:
            self.current_camera = Camera(self.device_manager, device_index)
            if not self.current_camera.start_acquisition():
                raise RuntimeError("Failed to start acquisition")
            if all(target_size):
                self.current_camera.target_size = (int(target_size[0]), int(target_size[1]))
            self.streaming = True
            Thread(target=self.frame_producer, daemon=True).start()
            asyncio.create_task(self.frame_consumer(websocket))
            await websocket.send(json.dumps({"message": "Stream started"}))
        except Exception as e:
            await websocket.send(json.dumps({"error": str(e)}))

    async def stop_stream(self, websocket):
        if self.streaming and self.current_camera:
            self.streaming = False
            self.current_camera.stop_acquisition()
            await asyncio.sleep(0.1)
            self.current_camera.close()
            self.current_camera = None
            with self.frame_queue.mutex:
                self.frame_queue.queue.clear()
            await websocket.send(json.dumps({"message": "Stream stopped"}))
        else:
            await websocket.send(json.dumps({"error": "No active stream"}))

    def frame_producer(self):
        while self.streaming:
            try:
                jpeg_bytes = self.current_camera.get_jpeg_frame()
                if jpeg_bytes:
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except Exception:
                            pass
                    self.frame_queue.put(jpeg_bytes)
            except Exception as e:
                print(f"Frame producer error: {e}")
                break

    async def frame_consumer(self, websocket):
        while self.streaming:
            try:
                jpeg_bytes = self.frame_queue.get()
                await websocket.send(jpeg_bytes)
                self.frame_queue.task_done()
            except Exception as e:
                print(f"Frame consumer error: {e}")
                break

    async def send_max_values(self, websocket):
        if not self.current_camera:
            await websocket.send(json.dumps({"error": "No camera connected"}))
            return
        max_values = self.current_camera.get_all_max()
        await websocket.send(json.dumps({"max": max_values}))

    async def send_min_values(self, websocket):
        if not self.current_camera:
            await websocket.send(json.dumps({"error": "No camera connected"}))
            return
        min_values = self.current_camera.get_all_min()
        await websocket.send(json.dumps({"min": min_values}))

    async def send_current_values(self, websocket):
        if not self.current_camera:
            await websocket.send(json.dumps({"error": "No camera connected"}))
            return
        current_values = self.current_camera.get_all_current()
        await websocket.send(json.dumps({"current": current_values}))

    async def set_parameter_value(self, data, websocket):
        if not self.current_camera:
            await websocket.send(json.dumps({"error": "No camera connected"}))
            return
        param = data.get("parameter")
        value = data.get("value")
        if not param or value is None:
            await websocket.send(json.dumps({"error": "Missing parameter or value"}))
            return
        success = self.current_camera.set_parameter(param, value)
        if success:
            await websocket.send(json.dumps({"success": True}))
        else:
            await websocket.send(json.dumps({"error": "Failed to set parameter"}))

async def main():
    ids_peak.Library.Initialize()
    server = WebSocketServer()
    async with websockets.serve(
        server.handler, 
        "localhost", 8765, 
        compression=None,
    ):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
