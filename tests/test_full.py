#!/usr/bin/env python3
"""
Full EVSE authentication and communication test
"""

import asyncio
import sys
import os
import getpass

# Add the protocol path from custom_components
# Use the relative path from this file
test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, 'custom_components', 'evsemasterudp')
sys.path.insert(0, evse_module_path)

async def test_full_communication():
    """Test full communication with EVSE"""
    try:
        from protocol.communicator import Communicator
        from protocol.datagrams import RequestLogin, Heading
        
        print("🔍 Starting EVSE discovery and communication...")
        comm = Communicator()
        port = await comm.start()
        print(f"   ✅ Listening on port {port}")
        
        print("⏳ Waiting for EVSE discovery (5s)...")
        
        # Wait for discovery
        evse = None
        for i in range(5):
            await asyncio.sleep(1.0)
            if comm.evses:
                evse = list(comm.evses.values())[0]
                print(f"   🎯 EVSE found: {evse.info.serial} @ {evse.info.ip}")
                break
        
        if not evse:
            print("❌ No EVSE discovered")
            return False
        
        # Request the password interactively
        print(f"\n🔑 EVSE detected: {evse.info.serial}")
        password = getpass.getpass("🔐 Enter the EVSE password: ")
        print(f"   ✅ Password entered")

        # Test authentication with the new method
        print("🔐 Testing authentication...")
        auth_success = await evse.login(password)
        
        if auth_success:
            print("   🎉 Authentication succeeded!")
        else:
            print("   ❌ Authentication failed")
        
        # Test status retrieval (only if connected)
        print("📊 Testing status retrieval...")
        if auth_success:
            # Wait a bit for data to arrive
            await asyncio.sleep(2.0)
        else:
            print("   ⚠️ Not connected - status test skipped")
        
        # Wait for the response
        await asyncio.sleep(2.0)
        
        if evse.state:
            print("   🎉 Status received!")
            print(f"      ⚡ Gun state: {evse.state.gun_state}")
            print(f"      🔌 Output state: {evse.state.output_state}")
            print(f"      📏 Voltage L1: {getattr(evse.state, 'l1_voltage', 'N/A')}V")
            print(f"      🔋 Current L1: {getattr(evse.state, 'l1_current', 'N/A')}A")
            print(f"      🌡️ Temp inner: {getattr(evse.state, 'inner_temp', 'N/A')}°C")
            print(f"      🌡️ Temp outer: {getattr(evse.state, 'outer_temp', 'N/A')}°C")
        else:
            print("   ⚠️ No status received")
        
        print("\n🛑 Stopping communicator...")
        await comm.stop()
        print("   ✅ Stopped")
        
        # Evaluate the actual success of the test
        data_received = evse.state is not None if hasattr(evse, 'state') else False
        
        print(f"\n📊 ACTUAL RESULTS:")
        print(f"   🔐 Authentication: {'✅ Succeeded' if auth_success else '❌ Failed'}")
        print(f"   📡 Data received: {'✅ Yes' if data_received else '❌ No'}")
        
        if data_received:
            print(f"   📋 DATA RETRIEVED:")
            print(f"      ⚡ Voltage L1: {getattr(evse.state, 'l1_voltage', 'N/A')}V")
            print(f"      🌡️ Temperature: {getattr(evse.state, 'inner_temp', 'N/A')}°C") 
            print(f"      🔋 Current L1: {getattr(evse.state, 'l1_current', 'N/A')}A")
        
        return auth_success and data_received
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🧪 Full EVSE Python communication test")
    print("🔌 Test: Discovery → Authentication → Status")
    print("📱 The test will discover your EVSE and ask for the password\n")
    
    success = asyncio.run(test_full_communication())
    
    if success:
        print("\n🎉 Full test succeeded! The Python protocol is working correctly.")
        print("   ✅ Automatic discovery")
        print("   ✅ Authentication succeeded") 
        print("   ✅ Data received (voltage, temperature, current)")
        print("\n🏠 Your Home Assistant integration is ready!")
    else:
        print("\n❌ Test failed - check:")
        print("   🔐 The EVSE password")
        print("   📡 The network connection")
        print("   🔌 The EVSE state")
