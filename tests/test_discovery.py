#!/usr/bin/env python3
"""
EVSE automatic discovery test
"""

import asyncio
import sys
import os

# Add the protocol path from custom_components
# Use the relative path from this file
test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, 'custom_components', 'evsemasterudp')
sys.path.insert(0, evse_module_path)

async def test_discovery():
    """Test automatic discovery"""
    try:
        from protocol.communicator import Communicator
        
        print("🔍 Starting EVSE discovery...")
        comm = Communicator()
        port = await comm.start()
        print(f"   ✅ Listening on port {port}")
        
        print("⏳ Attente de broadcasts EVSE (15s)...")
        
        # Wait for EVSEs to be discovered
        for i in range(15):
            await asyncio.sleep(1.0)
            
            if comm.evses:
                print(f"\n🎉 EVSEs discovered: {len(comm.evses)}")
                for serial, evse in comm.evses.items():
                    print(f"   📱 {serial} @ {evse.info.ip}:{evse.info.port}")
                    print(f"      🏷️ Brand: {getattr(evse.info, 'brand', 'N/A')}")
                    print(f"      🏷️ Model: {getattr(evse.info, 'model', 'N/A')}")
                break
            else:
                print(f"   ⏳ {i+1}/15s - No EVSE found...")
        
        if not comm.evses:
            print("   ❌ No EVSE discovered")
        
        print("\n🛑 Stopping communicator...")
        await comm.stop()
        print("   ✅ Stopped")
        
        return len(comm.evses) > 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔍 EVSE automatic discovery test")
    print("📡 This test listens for discovery broadcasts on the network")
    print("🔌 Make sure your EVSE is connected and powered on\n")
    
    success = asyncio.run(test_discovery())
    
    if success:
        print("\n🎉 Discovery succeeded! The EVSE was detected automatically.")
    else:
        print("\n⚠️ No EVSE was discovered automatically.")
        print("   Check that:")
        print("   • The EVSE is on the same network (192.168.42.x)")
        print("   • UDP port 28376 is not blocked") 
        print("   • The EVSE is actually sending broadcasts")
