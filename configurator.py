import ids_peak
import sys
from ids_peak import ids_peak

class CameraConfigurator:
    def __init__(self):
        self.device = None
        self.node_map = None
        self.device_manager = ids_peak.DeviceManager.Instance()
        
    def list_devices(self):
        self.device_manager.Update()
        devices = []
        for idx, device in enumerate(self.device_manager.Devices()):
            devices.append({
                "index": idx,
                "model": device.ModelName(),
                "serial": device.SerialNumber(),
                "interface": device.ParentInterface().DisplayName()
            })
        return devices
    
    def connect(self, device_index):
        if self.device is not None:
            self.disconnect()
            
        devices = self.device_manager.Devices()
        if device_index >= len(devices):
            raise ValueError("Invalid device index")
            
        self.device = devices[device_index].OpenDevice(ids_peak.DeviceAccessType_Control)
        self.node_map = self.device.RemoteDevice().NodeMaps()[0]
        print(f"Connected to {self.device.ModelName()}")
        
    def disconnect(self):
        if self.device:
            self.device = None
            self.node_map = None
            print("Disconnected from camera")
    
    def get_parameter(self, name):
        try:
            node = self.node_map.FindNode(name)
            print(f"{name}:")
            print(f"  Current Value: {node.Value()}")
            if not isinstance(node, (ids_peak.BooleanNode, ids_peak.EnumerationNode)):
                if hasattr(node, 'Minimum'):
                    print(f"  Min Value: {node.Minimum()}")
                if hasattr(node, 'Maximum'):
                    print(f"  Max Value: {node.Maximum()}")
                if hasattr(node, 'Unit'):
                    unit = node.Unit()
                    if unit: 
                        print(f"  Unit: {unit}")
                if hasattr(node, 'Inc'):
                    print(f"  Increment: {node.Inc()}")
                    
            # Special handling for enumeration nodes
            if isinstance(node, ids_peak.EnumerationNode):
                print("  Available Options:")
                for entry in node.Entries():
                    if entry.IsAvailable():
                        print(f"    - {entry.SymbolicValue()}")
        
        except ids_peak.Exception as e:
            print(f"Error accessing {name}: {str(e)}")
    
    def set_parameter(self, name, value):
        try:
            node = self.node_map.FindNode(name)
            
            if isinstance(node, ids_peak.FloatNode):
                value = float(value)
                min_val = node.Minimum()
                max_val = node.Maximum()
                
                # Handle increment only if available and > 0
                inc = node.Inc() if hasattr(node, 'Inc') and node.Inc() > 0 else None
                
                if inc:
                    value = round(value / inc) * inc  # Round to nearest increment
                    print(f"Rounded to nearest {inc} increment")
                
                # Clamp to valid range
                value = max(min(value, max_val), min_val)
                node.SetValue(value)
                
            elif isinstance(node, ids_peak.IntegerNode):
                # Similar handling for integers
                value = int(value)
                value = max(min(value, node.Maximum()), node.Minimum())
                node.SetValue(value)
                
            # ... rest of the method remains the same ...
            
            print(f"Successfully set {name} to {node.Value()}")
            return True
            
        except Exception as e:
            print(f"Error setting parameter: {str(e)}")
            return False
        
    def list_all_parameters(self):
        for node in self.node_map.Nodes():
            print(f"{node.DisplayName()} ({node.Name()})")

def print_help():
    print("\nAvailable commands:")
    print("  list                          - List available cameras")
    print("  connect [index]               - Connect to camera by index")
    print("  get [parameter]               - Get current parameter value")
    print("  set [parameter] [value]       - Set parameter value")
    print("  params                        - List available parameters")
    print("  disconnect                    - Disconnect from camera")
    print("  exit                          - Exit program")
    print("\nCommon parameters: ExposureTime, Gain, AcquisitionFrameRate, Width, Height")

def main():
    ids_peak.Library.Initialize()
    configurator = CameraConfigurator()
    
    print("IDS Camera Configuration Tool")
    print_help()
    
    while True:
        try:
            command = input("\n> ").strip().split()
            if not command:
                continue
                
            if command[0] == "exit":
                configurator.disconnect()
                break
                
            elif command[0] == "list":
                devices = configurator.list_devices()
                if not devices:
                    print("No cameras found")
                    continue
                    
                print("\nConnected Cameras:")
                for dev in devices:
                    print(f"Index {dev['index']}: {dev['model']} (S/N: {dev['serial']})")
                    
            elif command[0] == "connect":
                if len(command) < 2:
                    print("Please specify device index")
                    continue
                try:
                    index = int(command[1])
                    configurator.connect(index)
                except Exception as e:
                    print(f"Connection failed: {str(e)}")
                    
            elif command[0] == "disconnect":
                configurator.disconnect()
                
            elif command[0] == "get":
                if len(command) < 2:
                    print("Please specify parameter name")
                    continue
                if configurator.node_map is None:
                    print("Not connected to any camera")
                    continue
                configurator.get_parameter(command[1])
                
            elif command[0] == "set":
                if len(command) < 3:
                    print("Please specify parameter and value")
                    continue
                if configurator.node_map is None:
                    print("Not connected to any camera")
                    continue
                configurator.set_parameter(command[1], command[2])
                
            elif command[0] == "params":
                if configurator.node_map is None:
                    print("Not connected to any camera")
                    continue
                print("\nAvailable parameters:")
                print("- ExposureTime")
                print("- Gain")
                print("- AcquisitionFrameRate")
                print("- Width")
                print("- Height")
                print("- PixelFormat")
                print("- BalanceWhiteAuto")
                print("- Gamma")
                print("- BlackLevel")
                print("- ReverseX")
                print("- ReverseY")
                print("(Note: Available parameters may vary by camera model)")
                # configurator.list_all_parameters()
                
            else:
                print("Invalid command")
                print_help()
                
        except KeyboardInterrupt:
            print("\nExiting...")
            configurator.disconnect()
            break

if __name__ == "__main__":
    main()