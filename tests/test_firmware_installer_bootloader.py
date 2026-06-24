import threading
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

import firmware_installer as installer


class FirmwareInstallerBootloaderTests(unittest.TestCase):
    def test_reflex_prism_catalog_enables_serial_bootloader(self):
        item = installer.get_catalog_item("reflex-prism")

        self.assertIsNotNone(item.pre_flash_bootloader)
        assert item.pre_flash_bootloader is not None
        self.assertEqual("bootloader", item.pre_flash_bootloader["command"])
        self.assertEqual("0x16D0", item.pre_flash_bootloader["vid"])
        self.assertEqual("0x14F6", item.pre_flash_bootloader["pid"])

    def test_wait_for_flash_mounts_accepts_existing_bootloader_drive(self):
        stop_event = threading.Event()
        mount = installer.Mount(Path("R:/"), installer.RPI_RP2_LABEL)
        bootloader = {
            "vid": "0x16D0",
            "pid": "0x14F6",
            "baud": 115200,
            "command": "bootloader",
        }

        with (
            patch.object(installer, "find_rpi_rp2_mounts", return_value=[mount]),
            patch.object(installer, "find_serial_vid_pid") as find_serial_vid_pid,
        ):
            mounts = installer.wait_for_flash_mounts(bootloader, stop_event, lambda _message: None, poll_seconds=0)

        self.assertEqual(mounts, [mount])
        find_serial_vid_pid.assert_not_called()

    def test_wait_for_flash_mounts_enters_bootloader_from_matching_serial_device(self):
        stop_event = threading.Event()
        mount = installer.Mount(Path("R:/"), installer.RPI_RP2_LABEL)
        bootloader = {
            "vid": "0x16D0",
            "pid": "0x14F6",
            "baud": 115200,
            "command": "bootloader",
        }

        with (
            patch.object(installer, "find_rpi_rp2_mounts", side_effect=[[], [mount]]),
            patch.object(installer, "find_serial_vid_pid", return_value="COM9"),
            patch.object(installer, "run_serial_bootloader_command") as send_bootloader,
        ):
            mounts = installer.wait_for_flash_mounts(bootloader, stop_event, lambda _message: None, poll_seconds=0)

        self.assertEqual(mounts, [mount])
        send_bootloader.assert_called_once_with(bootloader, "COM9", stop_event=stop_event, log=ANY)

    def test_install_loop_uses_pre_flash_bootloader_config(self):
        stop_event = threading.Event()
        mount = installer.Mount(Path("R:/"), installer.RPI_RP2_LABEL)
        firmware = installer.FirmwareSource(Path("prism_dac.uf2"))
        bootloader = {
            "vid": "0x16D0",
            "pid": "0x14F6",
            "baud": 115200,
            "command": "bootloader",
        }
        logs = []
        statuses = []

        with (
            patch.object(installer, "list_game_controllers", return_value=set()),
            patch.object(installer, "wait_for_flash_mounts", return_value=[mount]) as wait_for_flash_mounts,
            patch.object(installer, "copy_firmware_to_mount", return_value=Path("R:/firmware.uf2")),
            patch.object(installer, "wait_for_detach"),
            patch.object(installer, "run_post_flash_check"),
        ):
            installer.run_install_loop(
                firmware=firmware,
                verify_controller=False,
                post_flash_check=None,
                pre_flash_bootloader=bootloader,
                stop_event=stop_event,
                log=logs.append,
                status=lambda message, color: statuses.append((message, color)),
                once=True,
            )

        wait_for_flash_mounts.assert_called_once_with(bootloader, stop_event, logs.append)


if __name__ == "__main__":
    unittest.main()
