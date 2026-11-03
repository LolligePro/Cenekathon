import os
import unittest

os.environ["SKILLMARKET_SOURCE"] = "stub"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from visualization.web import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class VizWebTests(unittest.TestCase):
    def test_every_exposed_function_answers(self):
        for url in [
            "/api/viz/list_job_types",
            "/api/viz/skill_trends?job_type=Data Analyst&skills=Python,SQL&metric=postings",
            "/api/viz/top_skills?job_type=Data Analyst&n=3",
            "/api/viz/job_type_skills?job_type=Data Analyst",
            "/api/viz/skill_trend?job_type=Data Analyst&skill=Python",
            "/api/viz/rising_and_falling_skills?job_type=Data Analyst&min_share=0.01",
            "/api/viz/career_path?from_job=Risk Manager&to_job=Data Analyst",
        ]:
            self.assertEqual(client.get(url).status_code, 200, url)

    def test_bad_requests_are_400_or_404(self):
        self.assertEqual(client.get("/api/viz/nope").status_code, 404)
        self.assertEqual(client.get("/api/viz/top_skills?job_type=Data Analyst&n=999").status_code, 400)
        self.assertEqual(client.get("/api/viz/top_skills?job_type=Data Analyst&n=x").status_code, 400)
        self.assertEqual(client.get("/api/viz/top_skills?job_type=Data Analyst&evil=1").status_code, 400)
        self.assertEqual(client.get("/api/viz/career_path?from_job=Risk Manager&to_job=Risk Manager").status_code, 400)

    def test_dashboard_page(self):
        self.assertIn("Skill Market", client.get("/dashboard").text)


if __name__ == "__main__":
    unittest.main()
