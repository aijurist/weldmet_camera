import asyncio
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
from MvImport.MvCameraControl_class import *
from CamOperation_class import *
import cv2
import numpy as np
import websockets
import json

app = Flask(__name__)
CORS(app)

# Global variables
deviceList = MV_CC_DEVICE_INFO_LIST()
tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE
cam = MvCamera()
obj_cam_operation = None
b_is_run = False
current_frame = None
frame_lock = threading.Lock()

# Convert error code to hexadecimal representation
def ToHexStr(num):
    chaDic = {10: 'a', 11: 'b', 12: 'c', 13: 'd', 14: 'e', 15: 'f'}
    hexStr = ""
    if num < 0:
        num = num + 2**32
    while num >= 16:
        digit = num % 16
        hexStr = chaDic.get(digit, str(digit)) + hexStr
        num //= 16
    hexStr = chaDic.get(num, str(num)) + hexStr
    return hexStr

# Flask app setup
app = Flask(__name__)

# Global variables
deviceList = MV_CC_DEVICE_INFO_LIST()
tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE

def enum_devices():
    global deviceList
    deviceList = MV_CC_DEVICE_INFO_LIST()
    ret = MvCamera.MV_CC_EnumDevices(tlayerType, deviceList)
    if ret != 0:
        return f"Error: Enum devices failed! ret = {ToHexStr(ret)}"

    devices = []
    if deviceList.nDeviceNum == 0:
        return {"message": "No devices found!", "devices": []}

    for i in range(0, deviceList.nDeviceNum):
        mvcc_dev_info = cast(deviceList.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
        device_info = {}
        if mvcc_dev_info.nTLayerType == MV_GIGE_DEVICE:
            chUserDefinedName = "".join(
                chr(per) for per in mvcc_dev_info.SpecialInfo.stGigEInfo.chUserDefinedName if per != 0
            )
            nip1 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0xff000000) >> 24)
            nip2 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x00ff0000) >> 16)
            nip3 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x0000ff00) >> 8)
            nip4 = (mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x000000ff)
            device_info["type"] = "GigE"
            device_info["name"] = chUserDefinedName
            device_info["ip"] = f"{nip1}.{nip2}.{nip3}.{nip4}"
        elif mvcc_dev_info.nTLayerType == MV_USB_DEVICE:
            chUserDefinedName = "".join(
                chr(per) for per in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chUserDefinedName if per != 0
            )
            strSerialNumber = "".join(
                chr(per) for per in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chSerialNumber if per != 0
            )
            device_info["type"] = "USB"
            device_info["name"] = chUserDefinedName
            device_info["serial"] = strSerialNumber
        devices.append(device_info)
    
    return {"message": f"Found {deviceList.nDeviceNum} devices.", "devices": devices}

@app.route("/devices", methods=["GET"])
def get_devices():
    device_data = enum_devices()
    return jsonify(device_data)

@app.route("/connect", methods=["POST"])
def connect_camera():
    global obj_cam_operation, b_is_run
    device_index = request.json.get('index')
    
    if obj_cam_operation:
        obj_cam_operation.Close_device()
    
    obj_cam_operation = CameraOperation(cam, deviceList, device_index)
    ret = obj_cam_operation.Open_device()
    if ret == 0:
        obj_cam_operation.Start_grabbing()
        b_is_run = True
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})

async def video_feed(websocket, path):
    global current_frame
    while b_is_run:
        with frame_lock:
            if current_frame is not None:
                _, jpeg = cv2.imencode('.jpg', current_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                await websocket.send(jpeg.tobytes())
        await asyncio.sleep(1/6)  # 6 FPS

def frame_grabber():
    global current_frame
    while True:
        if b_is_run and obj_cam_operation:
            frame = obj_cam_operation.Get_frame()
            if frame is not None:
                with frame_lock:
                    current_frame = cv2.resize(frame, (1024, 680))  # Downscale for transmission

def start_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    start_server = websockets.serve(video_feed, "0.0.0.0", 8765)
    loop.run_until_complete(start_server)
    loop.run_forever()

if __name__ == "__main__":
    enum_devices()
    threading.Thread(target=frame_grabber, daemon=True).start()
    threading.Thread(target=start_websocket, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
