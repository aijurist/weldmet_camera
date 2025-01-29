import cv2
import numpy as np
import time
from MvImport.MvCameraControl_class import *
from ctypes import *
from datetime import datetime

# Initialize camera
cam = MvCamera()
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

# Open the camera device
def open_camera():
    global cam, b_is_run
    deviceList = MV_CC_DEVICE_INFO_LIST()
    ret = MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE | MV_USB_DEVICE, deviceList)
    if ret != 0 or deviceList.nDeviceNum == 0:
        print("No devices found or error in enumeration")
        return False

    # Open the first device (modify this if you want to open a different device)
    mvcc_dev_info = cast(deviceList.pDeviceInfo[0], POINTER(MV_CC_DEVICE_INFO)).contents
    cam = MvCamera()
    ret = cam.MV_CC_CreateHandle(mvcc_dev_info)
    if ret != 0:
        print(f"Create Handle failed! ret = {ToHexStr(ret)}")
        return False
    
    ret = cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
    if ret != 0:
        print(f"Open Device failed! ret = {ToHexStr(ret)}")
        return False
    
    ret = cam.MV_CC_StartGrabbing()
    if ret != 0:
        print(f"Start Grabbing failed! ret = {ToHexStr(ret)}")
        return False
    
    b_is_run = True
    return True

# Grab frames and display them
def grab_frames():
    global cam
    stFrameInfo = MV_FRAME_OUT_INFO_EX()
    memset(byref(stFrameInfo), 0, sizeof(stFrameInfo))

    while b_is_run:
        data_buf = (c_ubyte * 4096 * 4096)()

        ret = cam.MV_CC_GetImageBuffer(stFrameInfo, data_buf)
        if ret == 0:
            print(f"Got frame: Width[{stFrameInfo.nWidth}], Height[{stFrameInfo.nHeight}], FrameNum[{stFrameInfo.nFrameNum}]")
            data = np.frombuffer(data_buf, dtype=np.uint8, count=stFrameInfo.nFrameLen)
            img = data.reshape((stFrameInfo.nHeight, stFrameInfo.nWidth))

            if stFrameInfo.enPixelType == PixelType_Gvsp_Mono8:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

            img = cv2.resize(img, (1024, 768))

            # Display frame in OpenCV window
            cv2.imshow("Camera Frame", img)

            # Close the window when 'q' is pressed
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            # Release the frame buffer
            cam.MV_CC_FreeImageBuffer(stFrameInfo)
        else:
            print(f"Get Image failed! ret = {ret}")
            time.sleep(0.01)

    # Close the OpenCV window and stop grabbing
    cv2.destroyAllWindows()
    cam.MV_CC_StopGrabbing()
    cam.MV_CC_CloseDevice()

if __name__ == "__main__":
    if open_camera():
        grab_frames()
