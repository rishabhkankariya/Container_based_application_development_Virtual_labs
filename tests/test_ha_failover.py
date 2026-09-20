"""
Automated Test Suite for Two-Node High-Availability Container Execution System
Tests:
- Database initialization and schema migration
- Host agent registration and telemetry heartbeats
- Resource-aware scheduling (CPU/RAM overflow spillover)
- Heartbeat timeout and automatic Hot Standby failover promotion
- Student workspace persistence and recovery
- Manual failover simulation & cluster role reset
"""

import os
import sys
import json
import unittest
import datetime

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.db import init_db, get_db, save_workspace_file, get_workspace_files
from backend.ha_manager import ha_cluster_manager


class TestTwoNodeHighAvailability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        # Reset hosts and failover events table for clean test state
        conn = get_db()
        conn.execute("DELETE FROM hosts")
        conn.execute("DELETE FROM host_heartbeats")
        conn.execute("DELETE FROM failover_events")
        conn.execute("DELETE FROM student_workspaces")
        conn.commit()
        conn.close()

    def test_01_db_initialization_and_schema(self):
        """Verifies all required HA tables are present."""
        conn = get_db()
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r["name"] for r in cur.fetchall()]
        conn.close()

        expected_tables = ["hosts", "host_heartbeats", "failover_events", "student_workspaces", "ha_config", "audit_logs"]
        for t in expected_tables:
            self.assertIn(t, tables, f"Expected table '{t}' was not created in database.")

    def test_02_host_registration_roles(self):
        """Verifies Host 1 registers as PRIMARY and Host 2 registers as HOT_STANDBY."""
        # Register Host 1
        h1_data = {
            "host_id": "HOST1",
            "hostname": "LAB-PC-PRIMARY",
            "ip_address": "192.168.1.101",
            "port": 5051,
            "role": "PRIMARY",
            "secret": "vlab-cluster-secret-2026",
            "cpu_cores": 8,
            "ram_total_mb": 16384,
            "disk_total_gb": 500,
            "docker_version": "Docker 27.0.3",
            "docker_available": 1
        }
        ok1, msg1 = ha_cluster_manager.register_host(h1_data)
        self.assertTrue(ok1, f"Failed to register Host 1: {msg1}")

        # Register Host 2
        h2_data = {
            "host_id": "HOST2",
            "hostname": "LAB-PC-STANDBY",
            "ip_address": "192.168.1.102",
            "port": 5052,
            "role": "HOT_STANDBY",
            "secret": "vlab-cluster-secret-2026",
            "cpu_cores": 8,
            "ram_total_mb": 16384,
            "disk_total_gb": 500,
            "docker_version": "Docker 27.0.3",
            "docker_available": 1
        }
        ok2, msg2 = ha_cluster_manager.register_host(h2_data)
        self.assertTrue(ok2, f"Failed to register Host 2: {msg2}")

        # Verify roles
        status = ha_cluster_manager.get_cluster_status()
        self.assertEqual(status["cluster_mode"], "MULTI_NODE")
        self.assertEqual(len(status["nodes"]), 2)

        node1 = next(n for n in status["nodes"] if n["host_id"] == "HOST1")
        node2 = next(n for n in status["nodes"] if n["host_id"] == "HOST2")
        self.assertEqual(node1["role"], "PRIMARY")
        self.assertEqual(node2["role"], "HOT_STANDBY")

    def test_03_heartbeat_recording(self):
        """Verifies heartbeats update resource usage and active containers."""
        self.test_02_host_registration_roles()

        hb_data = {
            "host_id": "HOST1",
            "secret": "vlab-cluster-secret-2026",
            "cpu_percent": 34.5,
            "ram_percent": 58.2,
            "disk_percent": 45.0,
            "active_containers": 3,
            "docker_available": 1,
            "docker_version": "Docker 27.0.3"
        }
        ok, msg = ha_cluster_manager.record_heartbeat(hb_data)
        self.assertTrue(ok, f"Failed to record heartbeat: {msg}")

        conn = get_db()
        host = conn.execute("SELECT current_cpu, current_ram, active_containers FROM hosts WHERE host_id = 'HOST1'").fetchone()
        conn.close()

        self.assertAlmostEqual(host["current_cpu"], 34.5)
        self.assertAlmostEqual(host["current_ram"], 58.2)
        self.assertEqual(host["active_containers"], 3)

    def test_04_resource_aware_overflow_scheduling(self):
        """Verifies scheduler spills over to Host 2 when Host 1 exceeds 85% CPU."""
        self.test_02_host_registration_roles()

        # Under normal load: Primary is selected
        conn = get_db()
        conn.execute("UPDATE hosts SET current_cpu = 30.0, current_ram = 40.0 WHERE host_id = 'HOST1'")
        conn.commit()
        conn.close()

        selected_node, overflow = ha_cluster_manager.select_execution_node()
        self.assertIsNotNone(selected_node)
        self.assertEqual(selected_node["host_id"], "HOST1")
        self.assertIsNone(overflow)

        # Under heavy load: Primary CPU is 88% (exceeds 85% threshold) -> routes to Host 2
        conn = get_db()
        conn.execute("UPDATE hosts SET current_cpu = 88.0, current_ram = 40.0 WHERE host_id = 'HOST1'")
        conn.execute("UPDATE hosts SET current_cpu = 15.0, current_ram = 25.0 WHERE host_id = 'HOST2'")
        conn.commit()
        conn.close()

        selected_node, overflow = ha_cluster_manager.select_execution_node()
        self.assertIsNotNone(selected_node)
        self.assertEqual(selected_node["host_id"], "HOST2", "Scheduler did not spillover to Host 2 under high Primary load.")
        self.assertIsNotNone(overflow)
        self.assertTrue(overflow["spillover"])

    def test_05_heartbeat_timeout_automatic_failover(self):
        """Verifies that 3 missed heartbeats marks Host 1 OFFLINE and promotes Host 2 to PRIMARY."""
        self.test_02_host_registration_roles()

        # Simulate Host 1 stopped sending heartbeats 30 seconds ago
        stale_time = (datetime.datetime.utcnow() - datetime.timedelta(seconds=30)).isoformat()
        fresh_time = datetime.datetime.utcnow().isoformat()

        conn = get_db()
        conn.execute("UPDATE hosts SET last_heartbeat = ?, missed_heartbeats = 2 WHERE host_id = 'HOST1'", (stale_time,))
        conn.execute("UPDATE hosts SET last_heartbeat = ?, missed_heartbeats = 0 WHERE host_id = 'HOST2'", (fresh_time,))
        conn.commit()
        conn.close()

        # Run liveness check
        ha_cluster_manager._check_hosts_liveness()

        # Verify failover
        conn = get_db()
        h1 = conn.execute("SELECT role, status FROM hosts WHERE host_id = 'HOST1'").fetchone()
        h2 = conn.execute("SELECT role, status FROM hosts WHERE host_id = 'HOST2'").fetchone()
        events = conn.execute("SELECT * FROM failover_events").fetchall()
        conn.close()

        self.assertEqual(h1["status"], "OFFLINE")
        self.assertEqual(h2["role"], "PRIMARY", "Host 2 was not promoted to PRIMARY upon Host 1 failure.")
        self.assertGreaterEqual(len(events), 1, "No failover event was recorded.")
        self.assertEqual(events[0]["from_host"], "HOST1")
        self.assertEqual(events[0]["to_host"], "HOST2")

    def test_06_workspace_persistence_and_recovery(self):
        """Verifies student files are saved to central database snapshot and restorable."""
        student_id = "STU_TEST_001"
        save_workspace_file(student_id, "index.html", "<h1>Hello High Availability!</h1>")
        save_workspace_file(student_id, "Dockerfile", "FROM nginx:alpine\nCOPY . /usr/share/nginx/html")

        files = get_workspace_files(student_id)
        self.assertIn("index.html", files)
        self.assertIn("Dockerfile", files)
        self.assertEqual(files["index.html"], "<h1>Hello High Availability!</h1>")

    def test_07_simulate_and_reset_failover(self):
        """Tests the Faculty Dashboard manual failover button and role reset."""
        self.test_02_host_registration_roles()

        # Trigger manual failover
        res = ha_cluster_manager.simulate_failover()
        self.assertTrue(res["ok"])

        conn = get_db()
        h2 = conn.execute("SELECT role FROM hosts WHERE host_id = 'HOST2'").fetchone()
        self.assertEqual(h2["role"], "PRIMARY")

        # Reset roles
        res_reset = ha_cluster_manager.reset_cluster()
        self.assertTrue(res_reset["ok"])

        h1 = conn.execute("SELECT role, status FROM hosts WHERE host_id = 'HOST1'").fetchone()
        h2 = conn.execute("SELECT role, status FROM hosts WHERE host_id = 'HOST2'").fetchone()
        conn.close()

        self.assertEqual(h1["role"], "PRIMARY")
        self.assertEqual(h2["role"], "HOT_STANDBY")

    def test_08_main_host_permission_control(self):
        """Verifies Main Host can grant/revoke compute node execution permissions and block container runs."""
        ha_cluster_manager.reset_cluster()
        self.test_02_host_registration_roles()

        # 1. Verify default: Global compute enabled
        self.assertTrue(ha_cluster_manager.is_global_compute_enabled())

        # 2. Main Host pauses global compute access
        ok = ha_cluster_manager.set_global_compute_permission(False)
        self.assertTrue(ok)
        self.assertFalse(ha_cluster_manager.is_global_compute_enabled())

        # Command dispatch MUST be blocked with permission_denied
        res_blocked = ha_cluster_manager.dispatch_command("STU001", "docker run hello-world", {})
        self.assertTrue(res_blocked.get("permission_denied"))
        self.assertIn("MAIN HOST ACCESS DENIED", res_blocked.get("output", ""))

        # 3. Main Host re-grants global compute access
        ha_cluster_manager.set_global_compute_permission(True)
        self.assertTrue(ha_cluster_manager.is_global_compute_enabled())

        # 4. Main Host revokes permission specifically for HOST1
        ha_cluster_manager.set_host_permission("HOST1", False, "Revoked for maintenance by Main Host")
        node, routing = ha_cluster_manager.select_execution_node()
        # Should bypass HOST1 and select HOST2 because HOST2 is authorized
        self.assertIsNotNone(node)
        self.assertEqual(node["host_id"], "HOST2")

        # 5. Main Host revokes permission for all hosts
        ha_cluster_manager.set_host_permission("HOST2", False)
        node_none, routing_denied = ha_cluster_manager.select_execution_node()
        self.assertIsNone(node_none)
        self.assertTrue(routing_denied.get("permission_denied"))

        # 6. Re-grant permission to HOST1 & HOST2
        ha_cluster_manager.set_host_permission("HOST1", True)
        ha_cluster_manager.set_host_permission("HOST2", True)
        node_restored, _ = ha_cluster_manager.select_execution_node()
        self.assertIsNotNone(node_restored)
        self.assertEqual(node_restored["host_id"], "HOST1")


if __name__ == "__main__":
    unittest.main()
