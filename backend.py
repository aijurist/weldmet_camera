# -- coding: utf-8 --
import sys
import threading
import asyncio
import json
import base64
import websockets
from ctypes import *
from MvImport.MvCameraControl_class import *
import os
import time

sys.path.append("../MvImport")

# Global variables
streaming_active = False
current_cam = None

def enumerate_devices():
    deviceList = MV_CC_DEVICE_INFO_LIST()
    tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE
    ret = MvCamera.MV_CC_EnumDevices(tlayerType, deviceList)
    if ret != 0:
        return []
    
    devices = []
    for i in range(deviceList.nDeviceNum):
        mvcc_dev_info = cast(deviceList.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
        device_info = {"index": i}
        
        if mvcc_dev_info.nTLayerType == MV_GIGE_DEVICE:
            device_info["type"] = "GigE"
            # Model name
            model_name = ""
            for c in mvcc_dev_info.SpecialInfo.stGigEInfo.chModelName:
                if c == 0: break
                model_name += chr(c)
            device_info["model"] = model_name
            
            # IP address
            ip = mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp
            device_info["ip"] = ".".join([
                str((ip >> 24) & 0xFF),
                str((ip >> 16) & 0xFF),
                str((ip >> 8) & 0xFF),
                str(ip & 0xFF)
            ])
            
        elif mvcc_dev_info.nTLayerType == MV_USB_DEVICE:
            device_info["type"] = "USB"
            # Model name
            model_name = ""
            for c in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chModelName:
                if c == 0: break
                model_name += chr(c)
            device_info["model"] = model_name
            
            # Serial number
            serial = ""
            for c in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chSerialNumber:
                if c == 0: break
                serial += chr(c)
            device_info["serial"] = serial
            
        devices.append(device_info)
    
    return devices

def get_pixel_format(pixel_type):
    formats = {
        PixelType_Gvsp_Mono8: "Mono8",
        PixelType_Gvsp_BayerRG8: "BayerRG8",
        PixelType_Gvsp_BayerGR8: "BayerGR8",
        PixelType_Gvsp_BayerGB8: "BayerGB8",
        PixelType_Gvsp_BayerBG8: "BayerBG8",
        PixelType_Gvsp_RGB8_Packed: "RGB8",
        PixelType_Gvsp_BGR8_Packed: "BGR8",
    }
    return formats.get(pixel_type, "Unknown")

def get_frame(cam):
    stOutFrame = MV_FRAME_OUT()
    memset(byref(stOutFrame), 0, sizeof(stOutFrame))
    ret = cam.MV_CC_GetImageBuffer(stOutFrame, 1000)
    if ret != 0:
        return None
    
    try:
        buffer_length = int(stOutFrame.stFrameInfo.nFrameLen)
        buffer = (c_ubyte * buffer_length).from_address(stOutFrame.pBufAddr)
        data = bytes(buffer)
        return {
            "data": data,
            "width": int(stOutFrame.stFrameInfo.nWidth),
            "height": int(stOutFrame.stFrameInfo.nHeight),
            "format": get_pixel_format(stOutFrame.stFrameInfo.enPixelType)
        }
    except Exception as e:
        print(f"Error processing frame: {e}")
        return None
    finally:
        cam.MV_CC_FreeImageBuffer(stOutFrame)

async def stream_frames(websocket, cam):
    global streaming_active
    frame_count = 0
    try:
        while streaming_active:
            loop = asyncio.get_event_loop()
            frame = await loop.run_in_executor(None, get_frame, cam)
            if not frame:
                print("No frame received")
                continue
            
            # Save the first frame to disk for debugging
            if frame_count == 0:
                timestamp = int(time.time())
                filename = f"frame_{timestamp}.raw"
                with open(filename, "wb") as f:
                    f.write(frame["data"])
                print(f"Saved first frame to {filename}")
                frame_count += 1
            
            frame_data = base64.b64encode(frame["data"]).decode("utf-8")
            await websocket.send(json.dumps({
                "type": "frame",
                "width": frame["width"],
                "height": frame["height"],
                "format": frame["format"],
                "data": frame_data
            }))
    except Exception as e:
        await websocket.send(json.dumps({"error": f"frame error: {str(e)}"}))
    finally:
        streaming_active = False

async def handle_client(websocket):
    global streaming_active, current_cam
    current_cam = None
    
    async for message in websocket:
        try:
            msg = json.loads(message)
            cmd = msg.get("command")
            
            if cmd == "get_devices":
                devices = enumerate_devices()
                await websocket.send(json.dumps({
                    "command": "devices",
                    "count": len(devices),
                    "devices": devices
                }))
                
            elif cmd == "start_stream":
                if streaming_active:
                    await websocket.send(json.dumps({"error": "Stream already active"}))
                    continue
                
                device_index = msg.get("device_index", 0)
                deviceList = MV_CC_DEVICE_INFO_LIST()
                ret = MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE | MV_USB_DEVICE, deviceList)
                if ret != 0 or deviceList.nDeviceNum == 0:
                    await websocket.send(json.dumps({"error": "No devices found"}))
                    continue
                
                if device_index >= deviceList.nDeviceNum:
                    await websocket.send(json.dumps({"error": "Invalid device index"}))
                    continue
                
                stDevice = cast(deviceList.pDeviceInfo[device_index], POINTER(MV_CC_DEVICE_INFO)).contents
                cam = MvCamera()
                ret = cam.MV_CC_CreateHandle(stDevice)
                if ret != 0:
                    await websocket.send(json.dumps({"error": "Create handle failed"}))
                    continue
                
                ret = cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
                if ret != 0:
                    await websocket.send(json.dumps({"error": "Open device failed"}))
                    cam.MV_CC_DestroyHandle()
                    continue
                
                if stDevice.nTLayerType == MV_GIGE_DEVICE:
                    packet_size = cam.MV_CC_GetOptimalPacketSize()
                    if packet_size > 0:
                        cam.MV_CC_SetIntValue("GevSCPSPacketSize", packet_size)
                
                ret = cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
                if ret != 0:
                    await websocket.send(json.dumps({"error": "Set trigger mode failed"}))
                    cam.MV_CC_CloseDevice()
                    cam.MV_CC_DestroyHandle()
                    continue
                
                ret = cam.MV_CC_StartGrabbing()
                if ret != 0:
                    await websocket.send(json.dumps({"error": "Start grabbing failed"}))
                    cam.MV_CC_CloseDevice()
                    cam.MV_CC_DestroyHandle()
                    continue
                
                streaming_active = True
                current_cam = cam
                asyncio.create_task(stream_frames(websocket, cam))
                
            elif cmd == "stop_stream":
                streaming_active = False
                if current_cam:
                    current_cam.MV_CC_StopGrabbing()
                    current_cam.MV_CC_CloseDevice()
                    current_cam.MV_CC_DestroyHandle()
                    current_cam = None
                await websocket.send(json.dumps({"status": "stream_stopped"}))
                
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"error": "Invalid JSON"}))
        except Exception as e:
            await websocket.send(json.dumps({"error": str(e)}))

async def main():
    async with websockets.serve(handle_client, "0.0.0.0", 5000):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())