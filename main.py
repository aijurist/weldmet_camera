import sys
import cv2
import numpy as np
import threading
import time
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from ctypes import *
from MvImport.MvCameraControl_class import *
from CamOperation_class import *
import uuid
from datetime import datetime

# Flask setup
app = Flask(__name__)
CORS(app, origins=['http://localhost:5173'])

# Global variables
latest_frame = None
frame_lock = threading.Lock()
stream_active = False
deviceList = MV_CC_DEVICE_INFO_LIST()
tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE
cam = MvCamera()
obj_cam_operation = None
b_is_run = False

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

def enum_devices():
    global deviceList
    deviceList = MV_CC_DEVICE_INFO_LIST()
    ret = MvCamera.MV_CC_EnumDevices(tlayerType, deviceList)
    if ret != 0:
        return {"error": f"Enum devices failed! ret = {ToHexStr(ret)}", "devices": []}

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
            device_info["type"] = "GigE"
            device_info["name"] = chUserDefinedName
            device_info["serial"] = str(mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp)
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

def frame_grabbing_thread():
    global latest_frame, stream_active, obj_cam_operation, cam
    stFrameInfo = MV_FRAME_OUT_INFO_EX()
    memset(byref(stFrameInfo), 0, sizeof(stFrameInfo))
    
    while stream_active and obj_cam_operation:
        data_buf = (c_ubyte * 4096 * 4096)()
        ret = cam.MV_CC_GetImageBuffer(stFrameInfo, data_buf, 1000)
        
        if ret == 0:
            print(f"Got frame: Width[{stFrameInfo.nWidth}], Height[{stFrameInfo.nHeight}], FrameNum[{stFrameInfo.nFrameNum}]")
            data = np.frombuffer(data_buf, dtype=np.uint8, count=stFrameInfo.nFrameLen)
            img = data.reshape((stFrameInfo.nHeight, stFrameInfo.nWidth))
            if stFrameInfo.enPixelType == PixelType_Gvsp_Mono8:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            img = cv2.resize(img, (1024, 768))
            u_id = datetime.time()
            cv2.imwrite(f"test_frame.jpg - {u_id}", cv2.imdecode(np.frombuffer(latest_frame, dtype=np.uint8), cv2.IMREAD_COLOR))
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            _, jpeg = cv2.imencode('.jpg', img, encode_param)
            
            # Update latest frame with lock
            with frame_lock:
                latest_frame = jpeg.tobytes()
            cam.MV_CC_FreeImageBuffer(stFrameInfo)
        else:
            print(f"Get Image failed! ret = {ret}")
            time.sleep(0.01)

@app.route("/devices", methods=["GET"])
def get_devices():
    device_data = enum_devices()
    return jsonify(device_data)

@app.route("/stream/start", methods=["POST"])
def start_stream():
    global stream_active, obj_cam_operation, b_is_run
    
    data = request.get_json()
    device_serial = data.get('serial')
    
    if not device_serial:
        return jsonify({"error": "Device serial number required"}), 400
        
    if not b_is_run:
        obj_cam_operation = CameraOperation(cam, deviceList, 0)  # Assuming first device for now
        ret = obj_cam_operation.Open_device()
        if ret != 0:
            return jsonify({"error": "Failed to open device"}), 500
        b_is_run = True
    
    if not stream_active:
        ret = obj_cam_operation.Start_grabbing()
        if ret != 0:
            return jsonify({"error": "Failed to start grabbing"}), 500
            
        stream_active = True
        thread = threading.Thread(target=frame_grabbing_thread)
        thread.daemon = True
        thread.start()
        return jsonify({"message": "Stream started successfully"})
    return jsonify({"message": "Stream is already active"})

@app.route("/stream/stop", methods=["POST"])
def stop_stream():
    global stream_active, obj_cam_operation, b_is_run
    
    if stream_active:
        stream_active = False
        if obj_cam_operation:
            obj_cam_operation.Stop_grabbing()
            obj_cam_operation.Close_device()
            b_is_run = False
        return jsonify({"message": "Stream stopped successfully"})
    return jsonify({"message": "No active stream to stop"})

@app.route('/video_feed')
def video_feed():
    def generate():
        while True:
            with frame_lock:
                if latest_frame is not None and stream_active:
                    # Proper MJPEG format with correct boundaries
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
            time.sleep(0.01)  # Small delay to prevent overwhelming the connection
            
    return Response(generate(), 
                   mimetype='multipart/x-mixed-replace; boundary=frame',
                   headers={
                       'Cache-Control': 'no-cache, no-store, must-revalidate',
                       'Pragma': 'no-cache',
                       'Expires': '0'
                   })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, threaded=True)