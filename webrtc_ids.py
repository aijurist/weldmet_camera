import asyncio
import json
import logging
import cv2
import numpy as np
import av
import aiohttp_cors
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension

logging.basicConfig(level=logging.INFO)

# Constants
TARGET_PIXEL_FORMAT = ids_peak_ipl.PixelFormatName_BGRa8

class Camera:
    def __init__(self, device_manager, device_index=0):
        self.device_manager = device_manager
        self.device_index = device_index
        self._device = None
        self._datastream = None
        self._acquisition_running = False
        self.target_fps = 20000
        self.max_fps = 0
        self.target_gain = 1
        self.max_gain = 1
        self._node_map = None
        self.image_width = None
        self.image_height = None
        self.target_size = None
        self.killed = False
        self._get_device()
        if self._device:
            self._setup_device_and_datastream()

    def __del__(self):
        self.close()

    def _get_device(self):
        self.device_manager.Update()
        if self.device_manager.Devices().empty():
            raise RuntimeError("No devices found")
        if self.device_index >= len(self.device_manager.Devices()):
            raise IndexError("Invalid device index")
        self._device = self.device_manager.Devices()[self.device_index].OpenDevice(ids_peak.DeviceAccessType_Control)
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

    def close(self):
        self.stop_acquisition()
        if self._datastream:
            try:
                self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for buffer in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(buffer)
            except Exception as e:
                logging.error("Exception (close): %s", e)
            finally:
                self._datastream = None

    def _find_and_set_remote_device_enumeration(self, name: str, value: str):
        entries = self._node_map.FindNode(name).Entries()
        available_entries = [entry.SymbolicValue() for entry in entries if entry.IsAvailable()]
        if value in available_entries:
            self._node_map.FindNode(name).SetCurrentEntry(value)

    def start_acquisition(self):
        if self._device is None or self._acquisition_running:
            return False
        try:
            self.max_fps = self._node_map.FindNode("AcquisitionFrameRate").Maximum()
            self.target_fps = self.max_fps
            self._node_map.FindNode("AcquisitionFrameRate").SetValue(self.target_fps)
        except ids_peak.Exception:
            pass
        try:
            self._node_map.FindNode("TLParamsLocked").SetValue(1)
            self.image_width = self._node_map.FindNode("Width").Value()
            self.image_height = self._node_map.FindNode("Height").Value()
            input_pixel_format = ids_peak_ipl.PixelFormat(
                self._node_map.FindNode("PixelFormat").CurrentEntry().Value())
            self._image_converter = ids_peak_ipl.ImageConverter()
            self._image_converter.PreAllocateConversion(
                input_pixel_format, TARGET_PIXEL_FORMAT,
                self.image_width, self.image_height)
            self._datastream.StartAcquisition()
            self._node_map.FindNode("AcquisitionStart").Execute()
            self._node_map.FindNode("AcquisitionStart").WaitUntilDone()
            self._acquisition_running = True
            return True
        except Exception as e:
            logging.error("Exception (start acquisition): %s", e)
            return False

    def stop_acquisition(self):
        if not self._acquisition_running:
            return
        try:
            self._node_map.FindNode("AcquisitionStop").Execute()
            self._datastream.KillWait()
            self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
            self._acquisition_running = False
            self._node_map.FindNode("TLParamsLocked").SetValue(0)
        except Exception as e:
            logging.error("Exception (stop acquisition): %s", e)

    def get_jpeg_frame(self):
        buffer = None
        try:
            buffer = self._datastream.WaitForFinishedBuffer(1000)
            image = ids_peak_ipl_extension.BufferToImage(buffer)
            converted_image = image.ConvertTo(ids_peak_ipl.PixelFormatName_BGR8)
            np_image = converted_image.get_numpy_3D()
            if self.target_size:
                np_image = cv2.resize(np_image, self.target_size)
            success, jpeg_buffer = cv2.imencode('.jpg', np_image, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            return jpeg_buffer.tobytes() if success else None
        except Exception as e:
            logging.error("Error capturing frame: %s", e)
            raise
        finally:
            if buffer:
                self._datastream.QueueBuffer(buffer)

# Video track that reads frames from the camera and converts them to AV frames.
class CameraVideoStreamTrack(VideoStreamTrack):
    def __init__(self, camera: Camera):
        super().__init__()
        self.camera = camera

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        try:
            jpeg_bytes = self.camera.get_jpeg_frame()
        except Exception as e:
            logging.error("Error in video track: %s", e)
            await asyncio.sleep(0.01)
            return None
        if jpeg_bytes is None:
            await asyncio.sleep(0.01)
            return None
        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            await asyncio.sleep(0.01)
            return None
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame

pcs = set()

async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    pc = RTCPeerConnection()
    pcs.add(pc)
    logging.info("Created PeerConnection: %s", pc)

    # Create a Camera instance (using device index 0)
    device_manager = ids_peak.DeviceManager.Instance()
    camera = Camera(device_manager, device_index=0)
    if not camera.start_acquisition():
        return web.Response(status=500, text="Failed to start camera acquisition")

    # Add the video track using addTrack and then set the corresponding transceiver's direction
    sender = pc.addTrack(CameraVideoStreamTrack(camera))
    for transceiver in pc.getTransceivers():
        if transceiver.sender == sender:
            transceiver.direction = "sendonly"
            break

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info("ICE connection state is %s", pc.iceConnectionState)
        if pc.iceConnectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })
    )

async def on_shutdown(app):
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()

if __name__ == "__main__":
    ids_peak.Library.Initialize()
    app = web.Application()
    app.router.add_post("/offer", offer)
    
    # Set up CORS using aiohttp_cors with defaults
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*"
        )
    })
    for route in list(app.router.routes()):
        cors.add(route)
    
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, port=8765)
