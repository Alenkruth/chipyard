import json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PARSER = os.path.join(HERE, "..", "parse_util.py")
FIX = os.path.join(HERE, "fixture_util_hier.rpt")  # slice of a real FireSim post_synth_utilization.rpt

class T(unittest.TestCase):
    def run_parser(self, rpt):
        out = os.path.join(tempfile.mkdtemp(), "result.json")
        subprocess.run([sys.executable, "-I", PARSER, "--rpt", rpt, "--out", out, "--point", "Rob/ift/entries=128,width=4",
                        "--meta", "wall_s=5", "--meta", "peak_mb=100", "--meta", "top=Rob"], check=True)
        return json.load(open(out))
    def test_fixture(self):
        r = self.run_parser(FIX)
        self.assertEqual((r["module"], r["variant"], r["params"], r["top"], r["status"]), ("Rob", "ift", "entries=128,width=4", "Rob", "ok"))
        self.assertEqual(r["total"], dict(total_luts=876737, logic_luts=832745, lutram=40631, srl=3361, ffs=380574, ramb36=r["total"]["ramb36"], ramb18=r["total"]["ramb18"], uram=r["total"]["uram"], dsp=r["total"]["dsp"]))
        ch = {c["inst"]: c for c in r["children"]}
        self.assertEqual(len(r["children"]), 7 - 1)   # depth-3 row excluded
        self.assertEqual(ch["CPUManagedStreamEngine_0"]["total_luts"], 6050)
        ift = [c["inst"] for c in r["children"] if c["ift"]]
        self.assertEqual(len(ift), 1)
        self.assertTrue(ift[0].startswith("IFTBRIDGEMODULE"))
        self.assertFalse(ch["crFile"]["ift"])
        self.assertEqual(ch["crFile"]["total_luts"], 35)
    def test_bad_input_fails(self):
        p = os.path.join(tempfile.mkdtemp(), "x.rpt"); open(p, "w").write("nothing\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_parser(p)
if __name__ == "__main__": unittest.main()
