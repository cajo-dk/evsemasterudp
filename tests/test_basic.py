#!/usr/bin/env python3
"""
Basic EVSE protocol sanity tests.
"""

import asyncio
import os
import sys


test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, "custom_components", "evsemasterudp")
sys.path.insert(0, evse_module_path)


async def test_basic_import():
    """Test basic imports."""
    try:
        print("Testing module imports...")

        from protocol.datagram import Datagram
        from protocol.communicator import Communicator
        from protocol.datagrams import RequestLogin, Heading, SingleACStatus

        assert Datagram is not None
        assert Communicator is not None
        assert RequestLogin is not None
        assert Heading is not None
        assert SingleACStatus is not None

        print("  OK imports loaded")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_datagram_creation():
    """Test datagram creation."""
    try:
        print("Testing datagram creation...")

        from protocol.datagrams import Heading, RequestLogin

        login = RequestLogin()
        heading = Heading()

        print(f"  OK RequestLogin command=0x{login.COMMAND:04x}")
        print(f"  OK Heading command=0x{heading.COMMAND:04x}")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_datagram_packing():
    """Test datagram packing."""
    try:
        print("Testing datagram packing...")

        from protocol.datagrams import RequestLogin

        login = RequestLogin()
        login.set_device_serial("1368844619649410")
        login.set_device_password("123456")

        packed = login.pack()
        print(f"  OK Datagram encoded ({len(packed)} bytes)")
        print(f"     Hex: {packed.hex()}")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_communicator_creation():
    """Test communicator creation."""
    try:
        print("Testing communicator creation...")

        from protocol.communicator import Communicator

        comm = Communicator()
        print(f"  OK Communicator created on port {comm.port}")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_network_socket():
    """Test UDP socket creation."""
    try:
        print("Testing UDP socket...")

        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        sock.close()

        print(f"  OK UDP socket created on port {port}")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_static_endpoint_port_refresh():
    """A discovered EVSE port should override the static config port."""
    try:
        print("Testing static endpoint port refresh...")

        from protocol.communicator import Communicator
        from protocol.datagrams import Login

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 28376)

        login = Login()
        login.set_device_serial("7794824171431560")
        login.brand = "EVSE"
        login.model = "BS20"
        login.hardware_version = "20.3251.114C00A3"
        login.max_power = 21120
        login.max_electricity = 32
        login.hot_line = "WWW.EVSE.COM"

        await comm._process_datagram(login, ("10.254.2.39", 48076))

        if evse.info.port != 48076:
            return False, f"expected discovered port 48076, got {evse.info.port}"
        if evse.is_logged_in():
            return False, "discovery packet should not mark EVSE logged in"

        print("  OK Discovery updated the EVSE endpoint port")
        print("  OK Discovery did not mark the EVSE logged in")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_login_response_not_overwritten_by_discovery():
    """A login response must survive later discovery packets."""
    try:
        print("Testing login response buffering...")

        from protocol.communicator import Communicator
        from protocol.datagrams import Login, LoginResponse

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)

        login_response = LoginResponse()
        login_response.set_device_serial("7794824171431560")
        await comm._process_datagram(login_response, ("10.254.2.39", 48076))

        discovery = Login()
        discovery.set_device_serial("7794824171431560")
        discovery.brand = "EVSE"
        discovery.model = "BS20"
        discovery.hardware_version = "20.3251.114C00A3"
        discovery.max_power = 21120
        discovery.max_electricity = 32
        discovery.hot_line = "WWW.EVSE.COM"
        await comm._process_datagram(discovery, ("10.254.2.39", 48076))

        response = await evse._wait_for_response([LoginResponse.COMMAND], 0.2)
        if response is None or response.get_command() != LoginResponse.COMMAND:
            return False, "login response was lost after discovery traffic"

        print("  OK LoginResponse remained available after discovery traffic")
        return True, None
    except Exception as e:
        return False, str(e)


async def main():
    """Run all tests."""
    print("=== QUICK EVSE PYTHON PROTOCOL TEST ===\n")

    tests = [
        ("Module imports", test_basic_import),
        ("Datagram creation", test_datagram_creation),
        ("Datagram packing", test_datagram_packing),
        ("Communicator", test_communicator_creation),
        ("Network socket", test_network_socket),
        ("Static endpoint refresh", test_static_endpoint_port_refresh),
        ("Login response buffering", test_login_response_not_overwritten_by_discovery),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        try:
            success, error = await test_func()
            if success:
                print(f"  PASS {test_name}\n")
                results.append((test_name, True, None))
            else:
                print(f"  FAIL {test_name}: {error}\n")
                results.append((test_name, False, error))
        except Exception as e:
            print(f"  EXCEPTION {test_name}: {e}\n")
            results.append((test_name, False, str(e)))

    print("=" * 50)
    print("TEST SUMMARY:")

    passed = 0
    for test_name, success, error in results:
        status = "PASS" if success else "FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1

    print(f"\nResult: {passed}/{len(tests)} tests passed")

    if passed != len(tests):
        print("\nErrors detected:")
        for test_name, success, error in results:
            if not success:
                print(f"  - {test_name}: {error}")


if __name__ == "__main__":
    asyncio.run(main())
