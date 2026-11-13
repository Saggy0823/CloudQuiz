
import json
import unittest

from backend.app import lambda_handler


class CloudQuizBackendTests(unittest.TestCase):

    def test_health_endpoint(self):
        event = {
            "rawPath": "/health",
            "requestContext": {
                "http": {"method": "GET"}
            }
        }

        result = lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "CloudQuiz API is running.")

    def test_unknown_endpoint(self):
        event = {
            "rawPath": "/unknown",
            "requestContext": {
                "http": {"method": "GET"}
            }
        }

        result = lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 404)

    def test_invalid_json_submission(self):
        event = {
            "rawPath": "/submit",
            "requestContext": {
                "http": {"method": "POST"}
            },
            "body": "{invalid-json"
        }

        result = lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 400)

        body = json.loads(result["body"])
        self.assertFalse(body["success"])


if __name__ == "__main__":
    unittest.main()

