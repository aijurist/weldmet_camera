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

    