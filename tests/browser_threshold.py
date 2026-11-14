"""浏览器端门槛分析验证：沿真实页面交互，覆盖保存、重开与规格对照。"""
from pathlib import Path
import importlib
import json
import sys
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ThresholdBrowserWorkflow(unittest.TestCase):
    def test_threshold_run_compare_save_and_reopen(self):
        package = json.loads((ROOT / "package.json").read_text())
        module = package["scripts"]["start"].split()[2]
        server_module = importlib.import_module(module + ".server")
        with tempfile.TemporaryDirectory() as state:
            server = server_module.make_server(ROOT, state, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as runtime:
                    browser = runtime.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto("http://127.0.0.1:" + str(server.server_port), wait_until="networkidle")
                        page.locator("#status").filter(has_text="已更新描述").wait_for()
                        page.locator("#threshold").click()
                        page.locator("#status").filter(has_text="已更新门槛分析").wait_for()
                        self.assertTrue(page.locator("#threshold-status").inner_text())
                        self.assertEqual(page.locator("#threshold-estimates tbody tr").count(), 3)
                        self.assertGreater(page.locator("#threshold-covariates tbody tr").count(), 3)
                        self.assertGreater(page.locator("#threshold-exclusions tbody tr").count(), 3)
                        self.assertIn("不能读作补贴效果", page.locator("#threshold-descriptive").inner_text())
                        page.locator("#threshold-window").fill("45")
                        page.locator("#threshold").click()
                        page.locator("#status").filter(has_text="已更新门槛分析").wait_for()
                        page.locator("#title").fill("门槛分析 2026")
                        page.locator("#save-threshold").click()
                        page.locator("#status").filter(has_text="已保存分析").wait_for()
                        page.locator("#compare").click()
                        page.locator("#status").filter(has_text="已更新分析选择对照").wait_for()
                        self.assertGreater(page.locator("#comparison-table tbody tr").count(), 4)
                        self.assertFalse(page.locator("#threshold-covariate-section").is_visible())
                        page.locator("#archive button").first.click()
                        page.locator("#status").filter(has_text="已读取保存的分析").wait_for()
                        self.assertEqual(page.locator("#threshold-window").input_value(), "45")
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
