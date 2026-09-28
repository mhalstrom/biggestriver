import io
import json
import os
import tempfile
import unittest

from river import core, mcp


class Mcp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        os.environ.pop("RIVER_AGENT", None)
        c = core.connect()
        core.project_add(c, "demo", path=self.dir.name)
        core.item_add(c, "demo", "first job", context="start here")
        c.close()

    def tearDown(self):
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def talk(self, *msgs):
        out = io.StringIO()
        mcp.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
        return [json.loads(x) for x in out.getvalue().splitlines()]

    def test_initialize_list_and_work_loop(self):
        call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}}
        r = self.talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                      call(3, "go", {"cwd": self.dir.name}),
                      call(4, "done", {"id": 1, "output": "did it"}),
                      call(5, "river", {"args": ["show", "1"]}),
                      call(6, "river", {"args": ["claim", "99"]}),
                      call(7, "river", {"args": ["--nonsense"]}))
        self.assertEqual([x["id"] for x in r], [1, 2, 3, 4, 5, 6, 7])  # no reply to the notification
        self.assertEqual(r[0]["result"]["capabilities"], {"tools": {}})
        self.assertIn("go", [t["name"] for t in r[1]["result"]["tools"]])
        go = r[2]["result"]["content"][0]["text"]
        self.assertIn("YOUR ITEM #1: first job", go)
        self.assertFalse(r[3]["result"]["isError"])  # done ran as the agent go named
        self.assertIn("did it", r[4]["result"]["content"][0]["text"])
        self.assertTrue(r[5]["result"]["isError"])
        self.assertIn("no item 99", r[5]["result"]["content"][0]["text"])
        self.assertTrue(r[6]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
