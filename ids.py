from ctypes import cast, POINTER
from MvImport.MvCameraControl_class import *
from ids_peak import ids_peak
def enum_devices():
    device_list = []
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
        
        device_list.append(device_info)
    
    return device_list

# res = enum_devices()
# for i in res:
#     print(i)

def open_camera(device_index):
    """
    Opens a camera using the device manager and retrieves basic device information.

    Parameters:
        device_index (int): The index of the device to open.

    Returns:
        device: The opened device object.
    """
    ids_peak.Library.Initialize()
    device_manager = ids_peak.DeviceManager.Instance()

    try:
        device_manager.Update()
        if device_manager.Devices().empty():
            print("No device found. Exiting function.")
            return None
        if device_index not in range(len(device_manager.Devices())):
            print("Invalid device index.")
            return None

        device = device_manager.Devices()[device_index].OpenDevice(ids_peak.DeviceAccessType_Control)
        nodemap_remote_device = device.RemoteDevice().NodeMaps()[0]
        print("Model Name: " + nodemap_remote_device.FindNode("DeviceModelName").Value())
        try:
            print("User ID: " + nodemap_remote_device.FindNode("DeviceUserID").Value())
        except ids_peak.Exception:
            print("User ID: (unknown)")
        try:
            print("Sensor Name: " + nodemap_remote_device.FindNode("SensorName").Value())
        except ids_peak.Exception:
            print("Sensor Name: (unknown)")

        # Print resolution
        try:
            print("Max. resolution (w x h): "
                  + str(nodemap_remote_device.FindNode("WidthMax").Value()) + " x "
                  + str(nodemap_remote_device.FindNode("HeightMax").Value()))
        except ids_peak.Exception:
            print("Max. resolution (w x h): (unknown)")

        return device

    except Exception as e:
        print("Exception: " + str(e))
        return None

def close_camera():
    """
    Closes the camera and cleans up the library.
    """
    try:
        ids_peak.Library.Close()
        print("Camera closed and library cleaned up.")
    except Exception as e:
        print("Exception while closing camera: " + str(e))

# Example usage:
if __name__ == '__main__':
    device_index = 0
    res = enum_devices()
    for i in res:
        print(i)
    device = open_camera(device_index)
    if device:
        input("Press Enter to close the camera...")
        close_camera()

