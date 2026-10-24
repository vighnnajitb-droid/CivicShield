import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "streamlit_app.py")


class APIResponse:
    def __init__(self, payload, ok=True):
        self.ok = ok
        self.payload = payload
        self.text = ""

    def json(self):
        return self.payload


class StreamlitReportTests(unittest.TestCase):
    def test_report_draft_survives_step_two_back_navigation(self):
        calls = []

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            path = url.removeprefix("http://test-api")
            if path == "/api/auth/login":
                return APIResponse({"token": "test-token"})
            if path == "/api/me":
                return APIResponse({"id": 21, "email": "citizen@example.test", "full_name": "Test Citizen",
                                    "role": "citizen", "authority_id": None, "organisation": None})
            if path == "/api/meta":
                return APIResponse({"categories": [{"id": 7, "department": "Municipal Services", "name": "Road maintenance"}],
                                    "jurisdictions": ["Chennai Zone 5"]})
            if method == "GET" and path == "/api/complaints":
                return APIResponse([])
            if method == "POST" and path == "/api/complaints":
                return APIResponse({"id": 44})
            if path == "/api/public/stats":
                return APIResponse({"complaint_volume": 0, "overdue_cases": 0, "caution": "Process metric."})
            self.fail(f"Unexpected API request: {method} {url}")

        with patch.dict(os.environ, {"BACKEND_URL": "http://test-api"}), patch("requests.request", side_effect=request):
            app = AppTest.from_file(APP)
            app.session_state["token"] = "test-token"
            app.run()
            app.radio[1].set_value("Report incident").run()

            app.text_input[0].set_value("Water interruption")
            app.text_input[1].set_value("Central road")
            app.text_area[0].set_value("The water supply interruption has continued for several days without a repair update.")
            next(button for button in app.button if button.label == "Save draft and continue").click().run()
            self.assertEqual(app.session_state["report_step"], 2)
            self.assertEqual(app.session_state["report_id"], 44)

            next(button for button in app.button if button.label == "Back").click().run()

        self.assertEqual(app.session_state["report_step"], 1)
        values = {widget.label: widget.value for widget in app.text_input}
        self.assertEqual(values["Title"], "Water interruption")
        self.assertEqual(values["Location"], "Central road")
        self.assertIn("continued for several days", app.text_area[0].value)
        create_call = next(call for call in calls if call[0] == "POST" and call[1].endswith("/api/complaints"))
        self.assertEqual(create_call[2]["title"], "Water interruption")
        self.assertEqual(create_call[2]["incident_date"], "")
        self.assertFalse(app.exception)

    def test_case_summary_is_rendered_as_readable_sections_not_raw_json(self):
        user = {"id": 21, "email": "citizen@example.test", "full_name": "Test Citizen",
                "role": "citizen", "authority_id": None, "organisation": None}
        case = {"id": 44, "citizen_id": 21, "code": "CS-2026-000044", "title": "Water service delay",
                "status": "assigned", "stage": "allegation", "current_authority_id": 8,
                "current_authority": "Municipal Office", "citizen": {"name": "Test Citizen"},
                "ack_deadline": None, "resolution_deadline": None, "original_hash": "a" * 64,
                "original_description": "Water service has been interrupted for multiple days.",
                "ai_analysis": None, "duplicate_flags": [], "evidence": [], "can_download_evidence": False,
                "status_history": [], "responses": [], "decisions": [], "appeals": [], "escalations": [],
                "transfers": [], "audit_chain": {"valid": True, "events": 2}, "legal_share": False,
                "legal_assistance": None}
        summary = {"complaint_id": "CS-2026-000044", "title": "Water service delay",
                   "incident_summary": "Water service was interrupted for several days.",
                   "timeline": [{"when": "Last week", "event": "Water service stopped."}],
                   "authorities_involved": [{"name": "Municipal Office", "level": "Local Office"}],
                   "evidence_inventory": [{"id": "EV-INTERNAL-123", "filename": "service_notice.pdf", "sha256": "b" * 64}],
                   "responses": [{"kind": "acknowledgement", "text": "The report was received."}],
                   "decisions": [], "appeals": [], "current_status": "assigned", "stage": "allegation",
                   "audit_chain": {"valid": True, "events": 2},
                   "next_procedural_options": [{"option": "Request an update", "what": "Ask the office for progress."}],
                   "note": "Allegations are unverified unless stage is 'verified_finding'."}

        def request(method, url, **kwargs):
            path = url.removeprefix("http://test-api")
            if path == "/api/me":
                return APIResponse(user)
            if path == "/api/meta":
                return APIResponse({"categories": [], "jurisdictions": []})
            if path == "/api/complaints/44":
                return APIResponse(case)
            if path == "/api/complaints/44/summary":
                return APIResponse(summary)
            self.fail(f"Unexpected API request: {method} {url}")

        with patch.dict(os.environ, {"BACKEND_URL": "http://test-api"}), patch("requests.request", side_effect=request):
            app = AppTest.from_file(APP)
            app.session_state["token"] = "test-token"
            app.session_state["page"] = "Case details"
            app.session_state["selected_case_id"] = 44
            app.run()
            next(button for button in app.button if button.label == "Case summary (n8n)").click().run()

        self.assertFalse(app.exception)
        self.assertIn("Case summary", [item.value for item in app.subheader])
        self.assertTrue(any("Water service was interrupted" in item.value for item in app.markdown))
        self.assertTrue(any("service_notice.pdf" in item.value for item in app.markdown))
        self.assertFalse(any("EV-INTERNAL-123" in item.value or '"complaint_id"' in item.value for item in app.markdown))

    def test_unavailable_n8n_summary_shows_labeled_local_overview(self):
        user = {"id": 21, "email": "citizen@example.test", "full_name": "Test Citizen",
                "role": "citizen", "authority_id": None, "organisation": None}
        case = {"id": 44, "citizen_id": 21, "code": "CS-2026-000044", "title": "Water service delay",
                "status": "assigned", "stage": "allegation", "current_authority_id": 8,
                "current_authority": "Municipal Office", "citizen": {"name": "Test Citizen"},
                "ack_deadline": None, "resolution_deadline": None, "original_hash": "a" * 64,
            "original_description": "CIVICSHIELD — INCIDENT REPORT\n\nData Status: SIMULATED DEMO DATA\n\n1. Incident Description\n\nStreet lights were installed near the community hall.\n\n2. Background\nOther template details.",
            "citizen_report": {"incident_summary": "CIVICSHIELD — INCIDENT REPORT Data Status: SIMULATED DEMO DATA"},
                "ai_analysis": None, "duplicate_flags": [], "evidence": [], "can_download_evidence": False,
                "status_history": [{"created_at": "today", "to_status": "assigned", "reason": "Reported", "actor": "user"}],
                "responses": [], "decisions": [], "appeals": [], "escalations": [], "transfers": [],
                "audit_chain": {"valid": True, "events": 2}, "legal_share": False, "legal_assistance": None}

        def request(method, url, **kwargs):
            path = url.removeprefix("http://test-api")
            if path == "/api/me":
                return APIResponse(user)
            if path == "/api/meta":
                return APIResponse({"categories": [], "jurisdictions": []})
            if path == "/api/complaints/44":
                return APIResponse(case)
            if path == "/api/complaints/44/summary":
                return APIResponse({"detail": "Case-summary workflow unavailable"}, ok=False)
            self.fail(f"Unexpected API request: {method} {url}")

        with patch.dict(os.environ, {"BACKEND_URL": "http://test-api"}), patch("requests.request", side_effect=request):
            app = AppTest.from_file(APP)
            app.session_state["token"] = "test-token"
            app.session_state["page"] = "Case details"
            app.session_state["selected_case_id"] = 44
            app.run()
            next(button for button in app.button if button.label == "Case summary (n8n)").click().run()

        self.assertFalse(app.exception)
        self.assertIn("Local case overview", [item.value for item in app.subheader])
        self.assertTrue(any("not an n8n summary" in item.value for item in app.warning))
        self.assertTrue(any("Street lights were installed" in item.value for item in app.markdown))
        self.assertFalse(any("SIMULATED DEMO DATA" in item.value for item in app.markdown))
        self.assertFalse(any("CS-2026-000044" in item.value or '"complaint_id"' in item.value for item in app.markdown))


if __name__ == "__main__":
    unittest.main()