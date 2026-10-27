"""Offline protocol tests; real serial construction is forbidden."""
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
import workpoint_measure_fast as fast


class CaptureTransport:
    def __init__(self, *, hardware_length=8192, corrupt_frame=None,
                 replace_sequence=False, change_final_length=False,
                 capture_flags=0, final_flags=None):
        self.registers = {0x35: 0, 0x3D: 40, 0x3E: hardware_length, 0x36: 0,
                          0x30: 3, 0x3C: 1143, 0x05: 1130}
        self.buffer = bytearray()
        self.read_indices = []
        self.commands = []
        self.current_index = 0
        self.corrupt_frame = corrupt_frame
        self.replace_sequence = replace_sequence
        self.change_final_length = change_final_length
        self.capture_flags = capture_flags
        self.final_flags = final_flags
        self.sequence_reads = 0
        self.length_reads = 0

    def register_write(self, address, value):
        self.commands.append((address, value))
        if address == 0x34:
            if value == 1:
                self.registers[0x3D] += 1
                self.registers[0x36] = 2 | self.capture_flags
            elif value == 2:
                self.registers[0x36] = 0
        else:
            self.registers[address] = value

    def register_read(self, address):
        if address == 0x3D:
            self.sequence_reads += 1
            if self.replace_sequence and self.sequence_reads == 3:
                return self.registers[address] + 1
        if address == 0x3E:
            self.length_reads += 1
            if self.change_final_length and self.length_reads == 2:
                return 4096
        if address == 0x36 and self.read_indices and self.final_flags is not None:
            return 2 | self.final_flags
        return self.registers[address]

    def write(self, payload):
        if len(payload) % 8:
            raise AssertionError("Partial command frame")
        for offset in range(0, len(payload), 8):
            frame = payload[offset:offset + 8]
            if frame[1:3] == bytes([1, 0x37]):
                self.current_index = frame[3] | frame[4] << 8
            elif frame[1:3] == bytes([2, 0x3B]):
                self.read_indices.append(self.current_index)
                reply = bytearray(fast.base.Device.frame(2, 0x3B, self.current_index * 4))
                if self.current_index == self.corrupt_frame:
                    reply[0] = 0
                self.buffer.extend(reply)
            else:
                raise AssertionError("Unexpected batch command")
        return len(payload)

    def read(self, count):
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value


class FastCaptureTests(unittest.TestCase):
    def setUp(self):
        self.block_serial = patch.object(fast.base.serial, "Serial",
                                          side_effect=AssertionError("Real serial is forbidden"))
        self.block_serial.start()
        self.addCleanup(self.block_serial.stop)

    def device(self, count=2048, **options):
        transport = CaptureTransport(**options)
        device = object.__new__(fast.FastLoggedDevice)
        device.read_samples = count
        device.raw_fs = 5e6
        device.audit = SimpleNamespace(event=lambda *args, **kwargs: None)
        device.s = transport
        device.write = transport.register_write
        device.read = transport.register_read
        return device, transport

    def test_reads_only_leading_contiguous_indices_and_preserves_sample_rate(self):
        for count in (2048, 4096, 8192):
            with self.subTest(count=count):
                device, transport = self.device(count)
                samples, meta = device.capture(5, threading.Event())
                self.assertEqual(transport.read_indices, list(range(count)))
                self.assertEqual(len(samples), count)
                self.assertEqual(float(samples[0]), 0)
                self.assertEqual(float(samples[-1]), count - 1)
                self.assertEqual(meta["fs"], 156250)
                self.assertEqual(meta["raw_fs"], 5e6)
                self.assertEqual(meta["decimation"], 32)
                self.assertEqual(meta["hardware_capture_samples"], 8192)
                self.assertEqual(meta["returned_samples"], count)
                self.assertEqual(meta["start_index"], 0)
                self.assertEqual(meta["sequence"], 41)
                self.assertEqual(meta["status"], 2)
                self.assertEqual(meta["probe_control"], 3)
                self.assertEqual(meta["probe_control_before"], 3)
                self.assertEqual(meta["probe_control_after"], 3)
                self.assertEqual(meta["hold_DAC_code"], 1143)
                self.assertEqual(meta["hold_DAC_code_before"], 1143)
                self.assertEqual(meta["hold_DAC_code_after"], 1143)
                self.assertEqual(meta["actual_DAC_code_before"], 1130)
                self.assertEqual(meta["actual_DAC_code_after"], 1130)

    def test_corrupt_reply_frame_is_rejected(self):
        device, unused = self.device(corrupt_frame=7)
        with self.assertRaisesRegex(RuntimeError, "frame is misaligned"):
            device.capture(5, threading.Event())

    def test_incomplete_hardware_record_rejected_before_any_read_transfer(self):
        device, transport = self.device(hardware_length=4096)
        with self.assertRaisesRegex(RuntimeError, "8192-sample length"):
            device.capture(5, threading.Event())
        self.assertEqual(transport.read_indices, [])

    def test_capture_replaced_while_transferring_is_rejected(self):
        device, unused = self.device(replace_sequence=True)
        with self.assertRaisesRegex(RuntimeError, "replaced during"):
            device.capture(5, threading.Event())

    def test_hardware_length_changed_while_transferring_is_rejected(self):
        device, unused = self.device(change_final_length=True)
        with self.assertRaisesRegex(RuntimeError, "length changed"):
            device.capture(5, threading.Event())

    def test_capture_fault_and_clip_flags_remain_visible_for_record_rejection(self):
        for flags in (4, 8, 12):
            with self.subTest(flags=flags):
                device, unused = self.device(capture_flags=flags)
                unused_samples, meta = device.capture(5, threading.Event())
                self.assertEqual(meta["status"] & 12, flags)
        device, unused = self.device(capture_flags=8, final_flags=4)
        unused_samples, meta = device.capture(5, threading.Event())
        self.assertEqual(meta["status"] & 12, 12)

    def test_cancel_sends_hardware_abort(self):
        device, transport = self.device()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            device.capture(5, cancel)
        self.assertIn((0x34, 2), transport.commands)
        self.assertEqual(transport.read_indices, [])

    def test_other_record_lengths_are_rejected(self):
        device, transport = self.device(1024)
        with self.assertRaises(ValueError):
            device.capture(5, threading.Event())
        self.assertEqual(transport.commands, [])

    def test_explicit_frequency_list_validation(self):
        self.assertEqual(fast.parse_frequencies("10000,48000,55000"), [10000, 48000, 55000])
        for text in ("", "1000", "10000,10000", "nan", "10000.5", "10000,"):
            with self.subTest(text=text), self.assertRaises(fast.argparse.ArgumentTypeError):
                fast.parse_frequencies(text)

    def test_frequency_override_is_in_memory_and_restored_even_on_failure(self):
        original = fast.base.FREQUENCIES["screen"]
        original_validator = fast.base.validate_snapshot
        observed = []

        def fake_run(*args, **kwargs):
            observed.append(list(fast.base.FREQUENCIES["screen"]))
            raise RuntimeError("offline stop before hardware")

        argv = ["workpoint_measure_fast.py", "screen", "--workpoint-label", "offline_only",
                "--frequencies", "10000,48000,55000"]
        with patch.object(sys, "argv", argv), patch.object(fast.base, "run_session", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "offline stop"):
                fast.main()
        self.assertEqual(observed, [[10000, 48000, 55000]])
        self.assertIs(fast.base.FREQUENCIES["screen"], original)
        self.assertIs(fast.base.validate_snapshot, original_validator)

    def test_128_code_amplitude_requires_full_headroom_and_preserves_other_guards(self):
        from test_workpoint_measure import baseline
        original = baseline()
        original["registers"]["0x5"] = 1143
        original["registers"]["0x1e"] = 700
        self.assertEqual(fast.fast_validate_snapshot(original, [10000], 128), 5e6)
        with self.assertRaises(ValueError):
            fast.STRICT_BASE_VALIDATE_SNAPSHOT(original, [10000], 128)
        original["registers"]["0x5"] = 900
        with self.assertRaisesRegex(RuntimeError, "full requested disturbance headroom"):
            fast.fast_validate_snapshot(original, [10000], 128)
        original["registers"]["0x5"] = 1143
        original["registers"]["0x1"] = 4
        with self.assertRaisesRegex(RuntimeError, "status is nonzero"):
            fast.fast_validate_snapshot(original, [10000], 128)
        original["registers"]["0x1"] = 2
        self.assertEqual(fast.fast_validate_snapshot(original, [10000], 128,
                                                    allow_latched_otr=True), 5e6)
        original["registers"]["0x3f"] = 0xA623
        with self.assertRaisesRegex(RuntimeError, "A624"):
            fast.fast_validate_snapshot(original, [10000], 128, allow_latched_otr=True)

    def test_fast_parser_accepts_128_without_changing_base_parser(self):
        args = fast.parser().parse_args(["screen", "--workpoint-label", "offline", "--amplitude", "128"])
        self.assertEqual(args.amplitude, 128)
        self.assertEqual(args.read_samples, 2048)
        self.assertEqual(fast.FastLoggedDevice.READ_SAMPLES, 2048)
        base_amplitude = next(action for action in fast.base.parser()._actions if action.dest == "amplitude")
        self.assertNotIn(128, base_amplitude.choices)


if __name__ == "__main__":
    unittest.main()
