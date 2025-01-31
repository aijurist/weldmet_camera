# \file    camera.py
# \author  IDS Imaging Development Systems GmbH
# \date    2024-02-20
#
# \brief   This sample showcases the usage of the ids_peak API
#          in setting camera parameters, starting/stopping the image acquisition
#          and how to record a video using the ids_peak_ipl API.
#
# \version 1.0
#
# Copyright (C) 2024, IDS Imaging Development Systems GmbH.
#
# The information in this document is subject to change without notice
# and should not be construed as a commitment by IDS Imaging Development Systems GmbH.
# IDS Imaging Development Systems GmbH does not assume any responsibility for any errors
# that may appear in this document.
#
# This document, or source code, is provided solely as an example of how to utilize
# IDS Imaging Development Systems GmbH software libraries in a sample application.
# IDS Imaging Development Systems GmbH does not assume any responsibility
# for the use or reliability of any portion of this document.
#
# General permission to copy or modify is hereby granted.

import os
import time

from os.path import exists
from dataclasses import dataclass

from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension


TARGET_PIXEL_FORMAT = ids_peak_ipl.PixelFormatName_BGRa8


@dataclass
class RecordingStatistics:
    frames_encoded: int
    frames_stream_dropped: int
    frames_video_dropped: int
    frames_lost_stream: int
    duration: int

    def fps(self):
        return self.frames_encoded / self.duration


class Camera:
    """
    This class showcases the usage of the ids_peak API in
    setting camera parameters, starting/stopping acquisition and
    how to record a video using the ids_peak_ipl API.
    """
    def __init__(self, device_manager, interface):
        self.device_manager = device_manager

        self._device = None
        self._datastream = None
        self._acquisition_running = False
        self._interface = interface
        self.target_fps = 20000
        self.max_fps = 0
        self.target_gain = 1
        self.max_gain = 1
        self._node_map = None

        self.killed = False

        self._interface.set_camera(self)

        self.start_recording = False

        self._get_device()
        if not self._device:
            print("Error: Device not found")
        self._setup_device_and_datastream()

        self._image_converter = ids_peak_ipl.ImageConverter()

    def __del__(self):
        self.close()

    def _get_device(self):
        # Update device manager to refresh the camera list
        self.device_manager.Update()
        if self.device_manager.Devices().empty():
            print("No device found. Exiting Program.")
            return
        selected_device = None

        # Initialize first device found if only one is available
        if len(self.device_manager.Devices()) == 1:
            selected_device = 0
        else:
            # List all available devices
            for i, device in enumerate(self.device_manager.Devices()):
                print(
                    f"{str(i)}:  {device.ModelName()} ("
                    f"{device.ParentInterface().DisplayName()} ; "
                    f"{device.ParentInterface().ParentSystem().DisplayName()} v." 
                    f"{device.ParentInterface().ParentSystem().Version()})")
            while True:
                try:
                    # Let the user decide which device to open
                    selected_device = int(input("Select device to open: "))
                    if selected_device < len(self.device_manager.Devices()):
                        break
                    else:
                        print("Invalid ID.")
                except ValueError:
                    print("Please enter a correct id.")
                    continue

        # Opens the selected device in control mode
        self._device = self.device_manager.Devices()[selected_device].OpenDevice(
            ids_peak.DeviceAccessType_Control)
        self._node_map = self._device.RemoteDevice().NodeMaps()[0]

        self.max_gain = self._node_map.FindNode("Gain").Maximum()

        # Load the default settings
        self._node_map.FindNode("UserSetSelector").SetCurrentEntry("Default")
        self._node_map.FindNode("UserSetLoad").Execute()
        self._node_map.FindNode("UserSetLoad").WaitUntilDone()

    def _setup_device_and_datastream(self):
        self._datastream = self._device.DataStreams()[0].OpenDataStream()
        # Disable auto gain and auto exposure to enable custom gain in program
        self._find_and_set_remote_device_enumeration("GainAuto", "Off")
        self._find_and_set_remote_device_enumeration("ExposureAuto", "Off")
        
        # Allocate image buffer for image acquisition
        payload_size = self._node_map.FindNode("PayloadSize").Value()
        # Use more buffers
        max_buffer = self._datastream.NumBuffersAnnouncedMinRequired() * 5
        for idx in range(max_buffer):
            buffer = self._datastream.AllocAndAnnounceBuffer(payload_size)
            self._datastream.QueueBuffer(buffer)
        print("Allocated buffers, finished opening device")

    def close(self):
        self.stop_acquisition()

        # If datastream has been opened, revoke and deallocate all buffers
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
            self._interface.warning(f"Could not set value for {name}!")

    def print(self):
        print(
            f"{self._device.ModelName()}: ("
            f"{self._device.ParentInterface().DisplayName()} ; "
            f"{self._device.ParentInterface().ParentSystem().DisplayName()} v."
            f"{self._device.ParentInterface().ParentSystem().Version()})")

    def get_data_stream_image(self):
        # Wait until the image is completed
        buffer = self._datastream.WaitForFinishedBuffer(500)

        # Create IDS peak IPL image for debayering and convert it to RGBa8 format
        ipl_image = ids_peak_ipl_extension.BufferToImage(buffer)

        # This creates a copy the image, so the buffer is free to use again after queuing
        # NOTE: Use `ImageConverter`, since the `ConvertTo` function re-allocates
        #       the conversion buffers on every call
        converted_ipl_image = self._image_converter.Convert(
            ipl_image, TARGET_PIXEL_FORMAT)

        self._datastream.QueueBuffer(buffer)

        return converted_ipl_image

    def start_acquisition(self):
        if self._device is None:
            return False
        if self._acquisition_running is True:
            return True

        self.target_fps = 0
        try:
            # Get cameras maximums possible FPS
            self.max_fps = self._node_map.FindNode("AcquisitionFrameRate").Maximum()
            # Set frames per second to given maximum
            self.target_fps = self.max_fps
            self.set_remote_device_value("AcquisitionFrameRate", self.target_fps)
        except ids_peak.Exception:
            self._interface.warning(
                "Warning Unable to limit fps, "
                "since node AcquisitionFrameRate is not supported."
                "Program will continue without set limit.")

        # Lock parameters that should not be accessed during acquisition
        try:
            self._node_map.FindNode("TLParamsLocked").SetValue(1)

            image_width = self._node_map.FindNode("Width").Value()
            image_height = self._node_map.FindNode("Height").Value()
            input_pixel_format = ids_peak_ipl.PixelFormat(
                self._node_map.FindNode("PixelFormat").CurrentEntry().Value())

            # Pre-allocate conversion buffers to speed up first image conversion
            # while the acquisition is running
            # NOTE: Re-create the image converter, so old conversion buffers
            #       get freed
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
        if self._device is None or self._acquisition_running is False:
            return
        try:
            self._node_map.FindNode("AcquisitionStop").Execute()

            # Kill the datastream to exit out of pending `WaitForFinishedBuffer`
            # calls
            self._datastream.KillWait()
            self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            # Discard all buffers from the acquisition engine
            # They remain in the announced buffer pool
            self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)

            self._acquisition_running = False

            # Unlock parameters
            self._node_map.FindNode("TLParamsLocked").SetValue(0)

        except Exception as e:
            print(f"Exception (stop acquisition): {str(e)}")

    def _valid_name(self, path: str, ext: str):
        num = 0

        def build_string():
            return f"{path}_{num}{ext}"

        while exists(build_string()):
            num += 1
        return build_string()

    def record(self, timer: int):
        """
        Records image frames into an AVI-container and saves it to {CWD}/video.avi
        :param timer: video length in seconds
        """

        # Create video writing object
        video = ids_peak_ipl.VideoWriter()
        cwd = os.getcwd()

        dropped_before = 0
        lost_before = 0

        try:
            # Create a new file the video will be saved in.
            video.Open(self._valid_name(cwd + "/" + "video", ".avi"))

            # Set target frame rate and gain
            self.set_remote_device_value("AcquisitionFrameRate", self.target_fps)
            self.set_remote_device_value("Gain", self.target_gain)

            video.Container().SetFramerate(self.target_fps)

            print("Recording with: ")
            var_name = "AcquisitionFrameRate"
            print(f"  Framerate: {self._node_map.FindNode(var_name).Value():.2f}")
            var_name = "Gain"
            print(f"  Gain: {self._node_map.FindNode(var_name).Value():.2f}")
            data_stream_node_map = self._datastream.NodeMaps()[0]
            dropped_before = data_stream_node_map.FindNode("StreamDroppedFrameCount").Value()
            lost_before = data_stream_node_map.FindNode("StreamLostFrameCount").Value()

        except Exception as e:
            self._interface.warning(str(e))
            raise

        print("Recording...")
        # Set target time
        limit = timer + time.time()
        while (limit - time.time()) > 0 and not self.killed:
            try:
                # Receive image from datastream
                # Wait until the image is completed
                buffer = self._datastream.WaitForFinishedBuffer(500)

                # Get an image from a buffer
                # NOTE: This still uses the buffer's underlying memory
                ipl_image = ids_peak_ipl_extension.BufferToImage(buffer)

                # Create IDS peak IPL image for debayering and convert it to RGBa8 format
                # this creates a copy of the image, so the buffer is free to be used again
                # NOTE: Use `ImageConverter`, since the `ConvertTo` function re-allocates
                #       the conversion buffers on every call
                converted_ipl_image = self._image_converter.Convert(
                    ipl_image, TARGET_PIXEL_FORMAT)
                # Passes the image to the (QT) interface
                self._interface.on_image_received(converted_ipl_image)

                # Append image to video
                video.Append(converted_ipl_image)
                # Give buffer back into the queue so it can be used again
                self._datastream.QueueBuffer(buffer)

            except Exception as e:
                print(f"Warning: Exception caught: {str(e)}")

        if self.killed:
            return

        # See if the acquisition was lossless. Note that between the last
        # acquisition and the next acquisition some frames will be lost
        # (seen after the second recording).
        data_stream_node_map = self._datastream.NodeMaps()[0]
        dropped_stream_frames = data_stream_node_map.FindNode(
            "StreamDroppedFrameCount").Value() - dropped_before
        lost_stream_frames = data_stream_node_map.FindNode(
            "StreamLostFrameCount").Value() - lost_before

        stats = RecordingStatistics(
            frames_encoded=video.NumFramesEncoded(),
            frames_video_dropped=video.NumFramesDropped(),
            frames_stream_dropped=dropped_stream_frames,
            frames_lost_stream=lost_stream_frames,
            duration=timer)

        # AVI framerate sets the playback speed.
        # You can calculate that with the amount of frames captured in the
        # time duration the video was recorded
        video.Container().SetFramerate(stats.fps())
        # Wait until all frames are written to the file
        video.WaitUntilFrameDone(10000)
        video.Close()
        self._interface.done_recording(stats)

    def acquisition_thread(self):
        while not self.killed:
            try:
                if self.start_recording is True:
                    # Start recording a 10 seconds long video
                    self.record(10)
                    self.start_recording = False
                else:
                    # Forward image to interface
                    image = self.get_data_stream_image()
                    self._interface.on_image_received(image)
            except Exception as e:
                self._interface.warning(str(e))
                self.start_recording = False
                self._interface.done_recording(RecordingStatistics(0, 0, 0, 0, 0))
