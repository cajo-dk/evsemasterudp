#!/usr/bin/env python3
"""
Basic EVSE Python protocol test
Minimal version for quick validation
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

async def test_basic_import():
    """Test basic import"""
    try:
        print("🔍 Testing module imports...")
        
        # Test import datagram
        from protocol.datagram import Datagram
        print("  ✅ Datagram imported")
        
        # Test import communicator
        from protocol.communicator import Communicator
        print("  ✅ Communicator imported")
        
        # Test import datagrams
        from protocol.datagrams import RequestLogin, Heading, SingleACStatus
        print("  ✅ Datagrams imported")
        
        return True, None
        
    except Exception as e:
        return False, str(e)

async def test_datagram_creation():
    """Test datagram creation"""
    try:
        print("🔧 Testing datagram creation...")
        
        from protocol.datagrams import RequestLogin, Heading
        
        # Test RequestLogin creation
        login = RequestLogin()
        print(f"  ✅ RequestLogin created (command: 0x{login.COMMAND:04x})")
        
        # Test Heading creation
        heading = Heading()
        print(f"  ✅ Heading created (command: 0x{heading.COMMAND:04x})")
        
        return True, None
        
    except Exception as e:
        return False, str(e)

async def test_datagram_packing():
    """Test encoding/decoding"""
    try:
        print("📦 Testing encoding/decoding...")
        
        from protocol.datagrams import RequestLogin
        
        # Create a datagram
        login = RequestLogin()
        login.serial = "1368844619649410"
        login.password = "123456"
        
        # Encode
        packed = login.pack()
        print(f"  ✅ Datagram encoded ({len(packed)} bytes)")
        print(f"     Hex: {packed.hex()}")
        
        return True, None
        
    except Exception as e:
        return False, str(e)

async def test_communicator_creation():
    """Test communicator creation"""
    try:
        print("📡 Testing communicator creation...")
        
        from protocol.communicator import Communicator
        
        # Create communicator
        comm = Communicator()
        print("  ✅ Communicator created")
        print(f"     Port: {comm.port}")
        
        return True, None
        
    except Exception as e:
        return False, str(e)

async def test_network_socket():
    """Test UDP socket creation"""
    try:
        print("🌐 Test socket UDP...")
        
        import socket
        
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Test bind
        sock.bind(('', 0))  # Automatic port
        port = sock.getsockname()[1]
        print(f"  ✅ UDP socket created on port {port}")
        
        sock.close()
        
        return True, None
        
    except Exception as e:
        return False, str(e)

async def main():
    """Main tests"""
    print("🧪 === QUICK EVSE PYTHON PROTOCOL TEST ===\n")
    
    tests = [
        ("Module imports", test_basic_import),
        ("Datagram creation", test_datagram_creation),
        ("Encoding/decoding", test_datagram_packing),
        ("Communicator", test_communicator_creation),
        ("Network socket", test_network_socket),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"🔬 {test_name}...")
        try:
            success, error = await test_func()
            if success:
                print(f"   ✅ {test_name} OK\n")
                results.append((test_name, True, None))
            else:
                print(f"   ❌ {test_name} ERROR: {error}\n")
                results.append((test_name, False, error))
        except Exception as e:
                print(f"   ❌ {test_name} EXCEPTION: {e}\n")
            results.append((test_name, False, str(e)))
    
    # Summary
    print("=" * 50)
    print("📋 TEST SUMMARY:")
    
    passed = 0
    for test_name, success, error in results:
        status = "✅ OK" if success else "❌ KO"
        print(f"   {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\n📊 Result: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("\n🎉 All basic tests passed!")
        print("   You can now test with a real EVSE:")
        print(f"   python test_python_protocol.py")
    else:
        print("\n⚠️ Some tests failed - check the configuration")
        print("   Errors detected:")
        for test_name, success, error in results:
            if not success:
                print(f"     • {test_name}: {error}")

if __name__ == "__main__":
    asyncio.run(main())
