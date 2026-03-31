#!/usr/bin/env python3
"""
Basic EVSE protocol sanity tests.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta


test_dir = os.path.dirname(__file__)
project_root = os.path.dirname(test_dir)
evse_module_path = os.path.join(project_root, "custom_components", "evsemasterudp")
sys.path.insert(0, project_root)
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


async def test_datagram_invalid_tail_rejected():
    """Packets with a wrong tail marker must be rejected."""
    try:
        print("Testing invalid tail rejection...")

        from protocol.datagram import parse_datagrams
        from protocol.datagrams import RequestLogin

        login = RequestLogin()
        login.set_device_serial("1368844619649410")
        login.set_device_password("123456")
        packed = bytearray(login.pack())
        packed[-1] ^= 0x01

        parsed = parse_datagrams(bytes(packed))
        if parsed:
            return False, "invalid-tail packet was accepted"

        print("  OK Invalid tail was rejected")
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
    """A configured static EVSE endpoint should stay pinned."""
    try:
        print("Testing static endpoint pinning...")

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

        if evse.info.port != 28376:
            return False, f"expected pinned port 28376, got {evse.info.port}"
        if evse.is_logged_in():
            return False, "discovery packet should not mark EVSE logged in"

        print("  OK Direct endpoint remained pinned to the configured port")
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


async def test_login_fallback_after_endpoint_refresh():
    """A login should retry after discovery updates the endpoint."""
    try:
        print("Testing login fallback after endpoint refresh...")

        from protocol.communicator import Communicator, EVSE
        from protocol.datagrams import Heading, LoginConfirm, LoginResponse, RequestLogin

        comm = Communicator()
        evse = EVSE(comm, "7794824171431560", "10.254.2.39", 28376)
        comm.evses[evse.info.serial] = evse
        request_attempts = 0

        async def fake_send_datagram(datagram):
            nonlocal request_attempts
            if isinstance(datagram, RequestLogin):
                request_attempts += 1
                if request_attempts == 1:
                    async def refresh_endpoint():
                        await asyncio.sleep(0.1)
                        evse.info.port = 48076
                    asyncio.create_task(refresh_endpoint())
                else:
                    response = LoginResponse()
                    response.set_device_serial(evse.info.serial)
                    await comm._process_datagram(response, (evse.info.ip, evse.info.port))
            elif isinstance(datagram, (LoginConfirm, Heading)):
                return 0
            return 0

        evse.send_datagram = fake_send_datagram
        ok = await evse.login("032818")

        if not ok:
            return False, f"login fallback failed with reason {evse.auth_failure_reason}"
        if request_attempts != 2:
            return False, f"expected 2 login attempts, got {request_attempts}"
        if evse.info.port != 48076:
            return False, f"expected refreshed port 48076, got {evse.info.port}"

        print("  OK Login retried after endpoint refresh")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_direct_login_does_not_wait_for_discovery():
    """A pinned endpoint should not retry on a discovered port."""
    try:
        print("Testing direct login mode...")

        from protocol.communicator import Communicator
        from protocol.datagrams import RequestLogin

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 28376)
        request_attempts = 0

        async def fake_send_datagram(datagram):
            nonlocal request_attempts
            if isinstance(datagram, RequestLogin):
                request_attempts += 1
            return 0

        evse.send_datagram = fake_send_datagram
        ok = await evse.login("032818")

        if ok:
            return False, "expected direct login without a response to fail"
        if request_attempts != 1:
            return False, f"expected 1 login attempt in direct mode, got {request_attempts}"
        if evse.info.port != 28376:
            return False, f"expected pinned port 28376 after failure, got {evse.info.port}"
        if evse.auth_failure_reason != "no_login_response":
            return False, f"unexpected auth failure reason {evse.auth_failure_reason}"

        print("  OK Direct mode stayed on the configured endpoint")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_login_failure_reason():
    """Login state should retain a failure reason for diagnostics."""
    try:
        print("Testing login failure reason...")

        from protocol.communicator import Communicator

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.auth_failure_reason = "no_login_response"

        if evse.auth_failure_reason != "no_login_response":
            return False, "auth failure reason was not retained on the EVSE object"

        print("  OK Login failure reason retained for diagnostics")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_charge_status_ack_uses_0x800d():
    """Charge status updates should be acknowledged with 0x800d."""
    try:
        print("Testing charge status ACK opcode...")

        from protocol.communicator import Communicator
        from protocol.datagrams import CurrentChargeRecordResponse, SingleACChargingStatusPublicAuto

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        sent = []

        async def fake_send(datagram, target_evse):
            sent.append((datagram.get_command(), datagram.__class__.__name__))
            return 0

        comm.send = fake_send

        datagram = SingleACChargingStatusPublicAuto()
        datagram.set_device_serial(evse.info.serial)
        datagram.unpack_payload(bytes(74))
        await comm._handle_charging_status(evse, datagram)

        if not sent:
            return False, "no ACK was sent"
        if sent[0][0] != CurrentChargeRecordResponse.COMMAND:
            return False, f"expected ACK 0x{CurrentChargeRecordResponse.COMMAND:04x}, got 0x{sent[0][0]:04x}"

        print("  OK Charge status ACK uses 0x800d")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_charge_start_waits_for_confirmation():
    """Charge start should only succeed after a response is received."""
    try:
        print("Testing charge start confirmation...")

        from protocol.communicator import Communicator
        from protocol.datagrams import ChargeStart, ChargeStartResponse

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        sent = []

        async def fake_send_datagram(datagram):
            sent.append(datagram.get_command())
            if isinstance(datagram, ChargeStart):
                response = ChargeStartResponse()
                response.set_device_serial(evse.info.serial)
                await comm._process_datagram(response, (evse.info.ip, evse.info.port))
            return 0

        evse.send_datagram = fake_send_datagram
        ok = await evse.charge_start(16)

        if not ok:
            return False, "charge start did not wait for the response"
        if ChargeStart.COMMAND not in sent:
            return False, "charge start command was not sent"

        print("  OK Charge start waits for confirmation")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_charge_stop_waits_for_confirmation():
    """Charge stop should only succeed after a response is received."""
    try:
        print("Testing charge stop confirmation...")

        from protocol.communicator import Communicator
        from protocol.datagrams import ChargeStop, ChargeStopResponse

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        sent = []

        async def fake_send_datagram(datagram):
            sent.append(datagram.get_command())
            if isinstance(datagram, ChargeStop):
                response = ChargeStopResponse()
                response.set_device_serial(evse.info.serial)
                await comm._process_datagram(response, (evse.info.ip, evse.info.port))
            return 0

        evse.send_datagram = fake_send_datagram
        ok = await evse.charge_stop()

        if not ok:
            return False, "charge stop did not wait for the response"
        if ChargeStop.COMMAND not in sent:
            return False, "charge stop command was not sent"

        print("  OK Charge stop waits for confirmation")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_sync_time_waits_for_confirmation():
    """System time sync should wait for its protocol response."""
    try:
        print("Testing time sync confirmation...")

        from protocol.communicator import Communicator
        from protocol.datagrams import SetAndGetSystemTime, SetAndGetSystemTimeResponse

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        sent = []

        async def fake_send_datagram(datagram):
            sent.append(datagram.get_command())
            if isinstance(datagram, SetAndGetSystemTime):
                response = SetAndGetSystemTimeResponse()
                response.set_device_serial(evse.info.serial)
                await comm._process_datagram(response, (evse.info.ip, evse.info.port))
            return 0

        evse.send_datagram = fake_send_datagram
        ok = await evse.sync_time()

        if not ok:
            return False, "time sync did not wait for the response"
        if SetAndGetSystemTime.COMMAND not in sent:
            return False, "time sync command was not sent"

        print("  OK Time sync waits for confirmation")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_charge_data_stale_detection():
    """Charge/session freshness should be tracked separately from connectivity."""
    try:
        print("Testing charge data staleness...")

        from protocol.communicator import Communicator

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse._logged_in = True
        evse.last_seen = datetime.now()
        evse.last_poll_request = datetime.now()

        if not evse.is_charge_data_stale():
            return False, "missing charge data after polling should be stale"

        evse.last_charge_record_update = datetime.now()
        if evse.is_charge_data_stale():
            return False, "fresh charge data should not be stale"

        evse.last_charge_record_update = datetime.now() - timedelta(seconds=25)
        if not evse.is_charge_data_stale():
            return False, "old charge data should be stale"

        print("  OK Charge data staleness is tracked")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_polling_tracks_failures_and_requests():
    """Polling should record requests and increment failures for missed responses."""
    try:
        print("Testing poll tracking...")

        from protocol.communicator import Communicator
        from protocol.datagrams import RequestChargeStatusRecord

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        evse.last_seen = datetime.now()
        sent = []

        async def fake_send_datagram(datagram):
            sent.append(datagram.get_command())
            return 0

        evse.send_datagram = fake_send_datagram

        await comm._poll_charge_status(evse)
        first_poll = evse.last_poll_request
        if first_poll is None:
            return False, "poll request timestamp was not recorded"
        if evse.poll_failures != 0:
            return False, f"unexpected failures after first poll: {evse.poll_failures}"

        await comm._poll_charge_status(evse)
        if evse.poll_failures != 1:
            return False, f"expected 1 poll failure after missed response, got {evse.poll_failures}"
        if RequestChargeStatusRecord.COMMAND not in sent:
            return False, "poll command was not sent"

        print("  OK Poll requests and failures are tracked")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_realtime_status_poll_request():
    """Maintenance should issue a real-time status poll request."""
    try:
        print("Testing real-time status polling...")

        from protocol.communicator import Communicator
        from protocol.datagrams import RequestStatusRecord

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        evse.last_seen = datetime.now()
        sent = []

        async def fake_send_datagram(datagram):
            sent.append(datagram.get_command())
            return 0

        evse.send_datagram = fake_send_datagram
        await comm._poll_realtime_status(evse)

        if evse.last_status_poll_request is None:
            return False, "status poll timestamp was not recorded"
        if RequestStatusRecord.COMMAND not in sent:
            return False, "real-time status poll command was not sent"

        print("  OK Real-time status polling is issued")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_poll_response_resets_failures():
    """A received charge record should clear accumulated poll failures."""
    try:
        print("Testing poll response recovery...")

        from protocol.communicator import Communicator
        from protocol.datagrams import CurrentChargeRecord

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse.password = "123456"
        evse._logged_in = True
        evse.poll_failures = 2
        evse.last_poll_request = datetime.now() - timedelta(seconds=1)

        record = CurrentChargeRecord()
        record.set_device_serial(evse.info.serial)
        record.unpack_payload(bytes(97))

        await comm._handle_charge_record(evse, record)

        if evse.poll_failures != 0:
            return False, "poll failures were not reset after a response"
        if evse.last_poll_response is None:
            return False, "poll response timestamp was not recorded"

        print("  OK Poll failures reset after a response")
        return True, None
    except Exception as e:
        return False, str(e)


async def test_evse_polling_diagnostics_fields():
    """EVSE instances should expose polling and staleness diagnostics."""
    try:
        print("Testing EVSE polling diagnostics...")

        from protocol.communicator import Communicator

        comm = Communicator()
        evse = comm.ensure_evse("7794824171431560", "10.254.2.39", 48076)
        evse._logged_in = True
        evse.last_seen = datetime.now()
        evse.last_poll_request = datetime.now()
        evse.poll_failures = 3

        if not hasattr(evse, "last_poll_request") or not hasattr(evse, "last_poll_response"):
            return False, "poll timestamp fields are missing"
        if not hasattr(evse, "poll_failures"):
            return False, "poll failure counter is missing"
        if not evse.is_charge_data_stale():
            return False, "expected missing charge data after polling to be stale"
        if evse.poll_failures != 3:
            return False, f"expected poll_failures=3, got {evse.poll_failures}"

        print("  OK EVSE exposes polling diagnostics")
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
        ("Invalid tail rejection", test_datagram_invalid_tail_rejected),
        ("Communicator", test_communicator_creation),
        ("Network socket", test_network_socket),
        ("Static endpoint pinning", test_static_endpoint_port_refresh),
        ("Login response buffering", test_login_response_not_overwritten_by_discovery),
        ("Login fallback", test_login_fallback_after_endpoint_refresh),
        ("Direct login mode", test_direct_login_does_not_wait_for_discovery),
        ("Login failure reason", test_login_failure_reason),
        ("Charge status ACK opcode", test_charge_status_ack_uses_0x800d),
        ("Charge start confirmation", test_charge_start_waits_for_confirmation),
        ("Charge stop confirmation", test_charge_stop_waits_for_confirmation),
        ("Time sync confirmation", test_sync_time_waits_for_confirmation),
        ("Charge data staleness", test_charge_data_stale_detection),
        ("Poll tracking", test_polling_tracks_failures_and_requests),
        ("Real-time status poll", test_realtime_status_poll_request),
        ("Poll response recovery", test_poll_response_resets_failures),
        ("EVSE polling diagnostics", test_evse_polling_diagnostics_fields),
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
