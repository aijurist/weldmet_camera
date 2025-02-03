import os
import time

from dataclasses import dataclass

from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension
import cv2


TARGET_PIXEL_FORMAT = ids_peak_ipl.PixelFormatName_BGRa8


class Camera:
    """
    Camera class for IDS Cameras
    """
    def __init__(self, device_manager):
        self.device_manager = device_manager

        self._device = None
        self._datastream = None
        self._acquisition_running = False
        self.target_fps = 20000
        self.max_fps = 0
        self.target_gain = 1
        self.max_gain = 1
        self._node_map = None

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
            print("No device found. Exiting Program.")
            return

        if len(self.device_manager.Devices()) == 1:
            selected_device = 0
        else:
            for i, device in enumerate(self.device_manager.Devices()):
                print(
                    f"{str(i)}:  {device.ModelName()} ("
                    f"{device.ParentInterface().DisplayName()} ; "
                    f"{device.ParentInterface().ParentSystem().DisplayName()} v." 
                    f"{device.ParentInterface().ParentSystem().Version()})")
            while True:
                try:
                    selected_device = int(input("Select device to open: "))
                    if selected_device < len(self.device_manager.Devices()):
                        break
                    else:
                        print("Invalid ID.")
                except ValueError:
                    print("Please enter a correct id.")
                    continue

        self._device = self.device_manager.Devices()[selected_device].OpenDevice(
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

    def get_data_stream_image(self):
        buffer = self._datastream.WaitForFinishedBuffer(500)
        ipl_image = ids_peak_ipl_extension.BufferToImage(buffer)
        converted_ipl_image = self._image_converter.Convert(
            ipl_image, TARGET_PIXEL_FORMAT)
        self._datastream.QueueBuffer(buffer)
        return converted_ipl_image

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

            image_width = self._node_map.FindNode("Width").Value()
            image_height = self._node_map.FindNode("Height").Value()
            input_pixel_format = ids_peak_ipl.PixelFormat(
                self._node_map.FindNode("PixelFormat").CurrentEntry().Value())

            self._image_converter = ids_peak_ipl.ImageConverter()
            self._image_converter.PreAllocateConversion(
                input_pixel_format, TARGET_PIXEL_FORMAT,
                image_width, image_height)

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

    def save_frame_as_jpeg(self, filename: str, quality: int = 95):
        """
        Captures a single frame and saves it as JPEG using OpenCV
        :param filename: Output file path
        :param quality: JPEG quality (0-100)
        """
        if not self._acquisition_running:
            raise RuntimeError("Acquisition not running. Call start_acquisition() first.")

        buffer = None
        try:
            buffer = self._datastream.WaitForFinishedBuffer(5000)
            image = ids_peak_ipl_extension.BufferToImage(buffer)
            converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
            np_image = converted_image.get_numpy_3D()
            success, jpeg_buffer = cv2.imencode('.jpg', np_image, 
                                            [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            
            if success:
                with open(filename, 'wb') as f:
                    f.write(jpeg_buffer.tobytes())
                print(f"Saved JPEG to {filename}")
            else:
                raise RuntimeError("Failed to encode JPEG")

        except Exception as e:
            print(f"Error saving frame: {str(e)}")
            raise
        finally:
            if buffer is not None:
                # Always return buffer to the stream
                self._datastream.QueueBuffer(buffer)
        
# def main():
#     ids_peak.Library.Initialize()
#     device_manager = ids_peak.DeviceManager.Instance()
#     camera = Camera(device_manager)
#     camera.start_acquisition()
#     camera.save_frame_as_jpeg("output.jpg")
#     camera.stop_acquisition()
#     camera.close()

def main():
    ids_peak.Library.Initialize()
    device_manager = ids_peak.DeviceManager.Instance()
    camera = Camera(device_manager)
    
    try:
        camera.start_acquisition()
        cv2.namedWindow('Live Feed', cv2.WINDOW_FREERATIO)
        
        while True:
            buffer = None
            try:
                buffer = camera._datastream.WaitForFinishedBuffer(5000)
                image = ids_peak_ipl_extension.BufferToImage(buffer)
                converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
                np_image = converted_image.get_numpy_3D()

                cv2.imshow('Live Feed', np_image)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                    
            finally:
                if buffer is not None:
                    camera._datastream.QueueBuffer(buffer)
                    
    finally:
        camera.stop_acquisition()
        camera.close()
        cv2.destroyAllWindows()
        ids_peak.Library.Close()
    
if __name__ == "__main__":
    main()