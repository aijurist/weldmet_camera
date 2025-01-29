from flask import Flask, jsonify
from MvImport.MvCameraControl_class import *
from CamOperation_class import *
from flask_cors import CORS


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
CORS(app, origins='http://localhost:5173')

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
