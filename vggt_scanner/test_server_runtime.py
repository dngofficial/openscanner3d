import asyncio
import sys
import unittest
from unittest.mock import patch

import server


@unittest.skipUnless(sys.platform == "win32", "Windows event-loop workaround")
class WindowsServerTests(unittest.TestCase):
    def test_server_uses_real_selector_loop_and_restores_policy(self):
        original = asyncio.get_event_loop_policy()
        loops = []

        class ProbeServer:
            def __init__(self, config): self.config = config
            async def serve(self):
                loops.append(asyncio.get_running_loop())
                self.thread_result = await asyncio.to_thread(lambda: 42)
                if self.thread_result != 42: raise AssertionError("Thread worker failed")

        with patch.object(server.uvicorn, "Server", ProbeServer):
            server.run_server(host="127.0.0.1", port=0)
        self.assertIsInstance(loops[0], asyncio.SelectorEventLoop)
        self.assertTrue(loops[0].is_closed())
        self.assertIs(asyncio.get_event_loop_policy(), original)

    def test_application_errors_are_not_suppressed(self):
        original = asyncio.get_event_loop_policy()

        class FailingServer:
            def __init__(self, config): pass
            async def serve(self): raise RuntimeError("application startup failed")

        with patch.object(server.uvicorn, "Server", FailingServer):
            with self.assertRaisesRegex(RuntimeError, "application startup failed"):
                server.run_server(host="127.0.0.1", port=0)
        self.assertIs(asyncio.get_event_loop_policy(), original)
