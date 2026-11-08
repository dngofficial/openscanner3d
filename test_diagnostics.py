import sqlite3
from pathlib import Path
import tempfile
import unittest
from diagnostics import database_summary,log_summary


class DiagnosticTests(unittest.TestCase):
    def test_verified_view_graph_counts_isolated_images(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"database.db"
            with sqlite3.connect(file) as db:
                db.execute("CREATE TABLE images(image_id INTEGER,name TEXT)")
                db.execute("CREATE TABLE keypoints(image_id INTEGER,rows INTEGER)")
                db.execute("CREATE TABLE two_view_geometries(pair_id INTEGER,rows INTEGER)")
                db.executemany("INSERT INTO images VALUES(?,?)",[(i,f"{i}.jpg") for i in range(1,5)])
                db.executemany("INSERT INTO keypoints VALUES(?,?)",[(i,50*i) for i in range(1,5)])
                db.executemany("INSERT INTO two_view_geometries VALUES(?,?)",
                               [(2147483647+2,50),(2*2147483647+3,30),(3*2147483647+4,5)])
            result=database_summary(file)
            self.assertEqual(result["images"],4)
            self.assertEqual(result["connected_images"],3)
            self.assertEqual(result["largest_connected_component"],3)
            self.assertEqual(result["verified_pairs"],2)
            self.assertEqual(result["features_median"],125)

    def test_empty_log_is_safe(self):
        self.assertFalse(log_summary("")["initial_pair_failure"])


if __name__=="__main__":
    unittest.main()
