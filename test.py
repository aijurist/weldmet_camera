import usb.backend
import usb.backend.libusb1
import usb.core
import usb.util
import usb

# Find all connected USB devices
backend = usb.backend.libusb1.get_backend(find_library=lambda x: r"C:\Users\ADMIN\Downloads\libusb-1.0.20\MS32\dll\libusb-1.0.dll")
devices = usb.core.find(backend=backend, find_all=True)

# Iterate through USB devices and extract information
for dev in devices:
    try:
        manufacturer = usb.util.get_string(dev, dev.iManufacturer)
        product = usb.util.get_string(dev, dev.iProduct)
        vendor_id = hex(dev.idVendor)
        product_id = hex(dev.idProduct)
        
        print(f"Manufacturer: {manufacturer}, Product: {product}, Vendor ID: {vendor_id}, Product ID: {product_id}")
        
        # Identify based on manufacturer name
        if manufacturer and "IDS" in manufacturer:
            print(f"Detected as: IDS Camera - {product}")
        elif manufacturer and "Hikvision" in manufacturer:
            print(f"Detected as: Hikvision Camera - {product}")
        else:
            print("Device not identified as IDS or Hikvision")
    except usb.core.USBError as e:
        print(f"Error accessing device: {e}")
