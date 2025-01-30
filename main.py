import sys
import json
import asyncio
import websockets
import threading
import numpy as np
import cv2
from ctypes import *
from MvImport.MvCameraControl_class import *

class CameraManager:
    def __init__(self):
        self.cam = None
        self.device_list = []
        self.streaming = False
        self.current_device_index = -1
        self.frame_convert_param = None
        self.buf_cache = None
        self.loop = None
        
    def set_event_loop(self, loop):
        self.loop = loop

    def enum_devices(self):
        self.device_list = []
        deviceList = MV_CC_DEVICE_INFO_LIST()
        tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayerType, deviceList)
        if ret != 0:
            return []

        for i in range(deviceList.nDeviceNum):
            device_ptr = deviceList.pDeviceInfo[i]
            mvcc_dev_info = cast(device_ptr, POINTER(MV_CC_DEVICE_INFO)).contents
            
            device_info = {
                "index": i,
                "ptr": device_ptr,
                "type": "GigE" if mvcc_dev_info.nTLayerType == MV_GIGE_DEVICE else "USB"
            }

            if mvcc_dev_info.nTLayerType == MV_GIGE_DEVICE:
                model_name = "".join([chr(c) for c in mvcc_dev_info.SpecialInfo.stGigEInfo.chModelName if c != 0])
                ip = mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp
                device_info.update({
                    "model": model_name,
                    "ip": f"{(ip>>24)&0xFF}.{(ip>>16)&0xFF}.{(ip>>8)&0xFF}.{ip&0xFF}"
                })
            else:
                model_name = "".join([chr(c) for c in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chModelName if c != 0])
                serial = "".join([chr(c) for c in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chSerialNumber if c != 0])
                device_info.update({
                    "model": model_name,
                    "serial": serial
                })
            
            self.device_list.append(device_info)
        
        return self.device_list

    def open_camera(self, index):
        if index < 0 or index >= len(self.device_list):
            raise ValueError("Invalid device index")
            
        self.current_device_index = index
        device_info = self.device_list[index]
        st_device_info = cast(device_info['ptr'], POINTER(MV_CC_DEVICE_INFO)).contents
        
        self.cam = MvCamera()
        ret = self.cam.MV_CC_CreateHandle(st_device_info)
        if ret != 0:
            raise Exception(f"Create handle failed: {ret}")
            
        ret = self.cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
        if ret != 0:
            raise Exception(f"Open device failed: {ret}")
            
        # Configure default settings
        self.cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
        self.cam.MV_CC_SetEnumValue("AcquisitionMode", MV_ACQ_MODE_CONTINUOUS)
        return True

    def start_stream(self, websocket):
        self.streaming = True
        stOutFrame = MV_FRAME_OUT()
        convert_param = MV_CC_PIXEL_CONVERT_PARAM()
        
        ret = self.cam.MV_CC_StartGrabbing()
        if ret != 0:
            raise Exception(f"Start grabbing failed: {ret}")

        while self.streaming:
            ret = self.cam.MV_CC_GetImageBuffer(stOutFrame, 1000)
            if ret == 0:
                try:
                    frame_info = stOutFrame.stFrameInfo
                    print(f"Received frame: {frame_info.nWidth}x{frame_info.nHeight}")

                    # Handle pixel conversion
                    if frame_info.enPixelType == PixelType_Gvsp_RGB8_Packed:
                        print("true")
                        # Directly use RGB data
                        buffer = (c_ubyte * frame_info.nFrameLen).from_address(stOutFrame.pBufAddr)
                    else:
                        # Convert to RGB
                        convert_param.nWidth = frame_info.nWidth
                        convert_param.nHeight = frame_info.nHeight
                        convert_param.pSrcData = stOutFrame.pBufAddr
                        convert_param.nSrcDataLen = frame_info.nFrameLen
                        convert_param.enSrcPixelType = frame_info.enPixelType
                        convert_param.enDstPixelType = PixelType_Gvsp_RGB8_Packed
                        
                        buffer_size = frame_info.nWidth * frame_info.nHeight * 3
                        convert_param.pDstBuffer = (c_ubyte * buffer_size)()
                        convert_param.nDstBufferSize = buffer_size
                        
                        ret = self.cam.MV_CC_ConvertPixelType(convert_param)
                        if ret != 0:
                            raise Exception(f"Pixel conversion failed: {ret}")
                        buffer = convert_param.pDstBuffer

                    # Create numpy array without reshaping
                    rgb_data = np.frombuffer(buffer, dtype=np.uint8)
                    _, jpeg_buffer = cv2.imencode('.jpg', rgb_data)
                    
                    # Send through WebSocket
                    asyncio.run_coroutine_threadsafe(
                        self.send_frame(websocket, jpeg_buffer.tobytes()),
                        self.loop
                    )
                finally:
                    self.cam.MV_CC_FreeImageBuffer(stOutFrame)
                    
    async def send_frame(self, websocket, data):
        try:
            await websocket.send(data)
        except Exception as e:
            print(f"Error sending frame: {str(e)}")

    def convert_to_jpeg(self, frame_out):
        frame_info = frame_out.stFrameInfo
        convert_param = MV_CC_PIXEL_CONVERT_PARAM()
        convert_param.nWidth = frame_info.nWidth
        convert_param.nHeight = frame_info.nHeight
        convert_param.pSrcData = frame_out.pBufAddr
        convert_param.nSrcDataLen = frame_info.nFrameLen
        convert_param.enSrcPixelType = frame_info.enPixelType
        convert_param.enDstPixelType = PixelType_Gvsp_RGB8_Packed
        
        buffer_size = frame_info.nWidth * frame_info.nHeight * 3
        convert_param.pDstBuffer = (c_ubyte * buffer_size)()
        convert_param.nDstBufferSize = buffer_size
        
        ret = self.cam.MV_CC_ConvertPixelType(convert_param)
        if ret != 0:
            raise Exception("Pixel conversion failed")
            
        # Convert to numpy array and encode as JPEG
        rgb_data = np.frombuffer(convert_param.pDstBuffer, dtype=np.uint8)
        rgb_data = rgb_data.reshape((frame_info.nHeight, frame_info.nWidth, 3))
        _, jpeg_buffer = cv2.imencode('.jpg', rgb_data)
        return jpeg_buffer.tobytes()

    def stop_stream(self):
        self.streaming = False
        self.cam.MV_CC_StopGrabbing()

    def close_camera(self):
        if self.cam:
            self.stop_stream()
            self.cam.MV_CC_CloseDevice()
            self.cam.MV_CC_DestroyHandle()
            self.cam = None

class WebSocketServer:
    def __init__(self):
        self.cam_manager = CameraManager()
        self.active_connections = set()

    async def handler(self, websocket):
        self.active_connections.add(websocket)
        try:
            # Pass the main event loop to camera manager
            self.cam_manager.set_event_loop(asyncio.get_running_loop())
            async for message in websocket:
                await self.handle_message(websocket, message)
        finally:
            self.active_connections.remove(websocket)

    async def handle_message(self, websocket, message):
        try:
            msg = json.loads(message)
            command = msg.get('command')
            
            if command == 'get_devices':
                devices = self.cam_manager.enum_devices()
                response = {
                    "message": f"Found {len(devices)} devices",
                    "devices": [{"index": d["index"], "type": d["type"], "model": d["model"]} for d in devices]
                }
                await websocket.send(json.dumps(response))
                
            elif command == 'start_stream':
                index = msg.get('index', 0)
                if index >= len(self.cam_manager.device_list):
                    raise ValueError("Invalid device index")
                
                self.cam_manager.open_camera(index)
                threading.Thread(
                    target=self.cam_manager.start_stream,
                    args=(websocket,),
                    daemon=True
                ).start()
                await websocket.send(json.dumps({"status": "streaming_started"}))
                
            elif command == 'stop_stream':
                self.cam_manager.stop_stream()
                await websocket.send(json.dumps({"status": "streaming_stopped"}))
                
        except Exception as e:
            error_msg = {"error": str(e)}
            await websocket.send(json.dumps(error_msg))

async def main():
    server = WebSocketServer()
    async with websockets.serve(server.handler, "localhost", 8765):
        print("WebSocket server started on ws://localhost:8765")
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    async def main():
        server = WebSocketServer()
        async with websockets.serve(server.handler, "localhost", 8765):
            print("WebSocket server started on ws://localhost:8765")
            await asyncio.Future()  # Run forever

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server stopped by user")