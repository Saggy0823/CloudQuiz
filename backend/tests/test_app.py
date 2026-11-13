import json
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app


class CloudQuizBackendTests(unittest.TestCase):

    def test_health_endpoint(self):
        event = {
            "rawPath": "/health",
            "requestContext": {"http": {"method": "GET"}}
        }
        result = app.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)

    def test_unknown_endpoint(self):
        event = {
            "rawPath": "/unknown",
            "requestContext": {"http": {"method": "GET"}}
        }
        result = app.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 404)

    def test_unauthenticated_submission(self):
        event = {
            "rawPath": "/submit",
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"quizId": "test", "answers": {}})
        }
        result = app.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 401)

    def test_invalid_json_submission(self):
        event = {
            "rawPath": "/submit",
            "requestContext": {
                "http": {"method": "POST"},
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": "test-user-123",
                            "email": "test@example.com"
                        }
                    }
                }
            },
            "body": "{invalid json"
        }
        result = app.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)


if __name__ == "__main__":
    unittest.main()
