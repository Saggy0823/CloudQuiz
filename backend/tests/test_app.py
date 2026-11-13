import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app


def make_event(method, path, body=None, authenticated=True):
    event = {
        "rawPath": path,
        "requestContext": {
            "http": {"method": method}
        }
    }

    if authenticated:
        event["requestContext"]["authorizer"] = {
            "jwt": {
                "claims": {
                    "sub": "student-123",
                    "email": "student@example.com"
                }
            }
        }

    if body is not None:
        event["body"] = json.dumps(body)

    return event


def decode(result):
    return json.loads(result["body"])


def sample_quiz(visibility="private", owner="creator-123", max_attempts=2):
    quiz = {
        "quizId": "quiz-123",
        "title": "Python Basics",
        "description": "Basic Python questions",
        "ownerId": owner,
        "ownerEmail": "creator@example.com",
        "visibility": visibility,
        "maxAttempts": max_attempts,
        "status": "active",
        "createdAt": "2026-11-14T10:00:00+00:00",
        "questions": [
            {
                "questionId": "q1",
                "question": "What is Python?",
                "options": ["Language", "Database"],
                "correctAnswer": "Language"
            },
            {
                "questionId": "q2",
                "question": "Which is a Python data type?",
                "options": ["List", "Browser"],
                "correctAnswer": "List"
            }
        ]
    }

    if visibility == "private":
        quiz["accessKeyHash"] = app.hash_access_key("TEST-ACCESS-KEY")

    return quiz


class CloudQuizBackendTests(unittest.TestCase):

    # -------------------------------------------------
    # Existing basic tests
    # -------------------------------------------------

    def test_health_endpoint(self):
        event = make_event("GET", "/health", authenticated=False)
        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.assertTrue(decode(result)["success"])

    def test_unknown_endpoint(self):
        event = make_event("GET", "/unknown")
        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 404)

    def test_unauthenticated_submission(self):
        event = make_event(
            "POST",
            "/submit",
            {"quizId": "test", "answers": {}},
            authenticated=False
        )
        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 401)

    def test_invalid_json_submission(self):
        event = make_event("POST", "/submit")
        event["body"] = "{invalid json"

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 400)

    # -------------------------------------------------
    # Test creation
    # -------------------------------------------------

    def test_create_requires_authentication(self):
        event = make_event(
            "POST",
            "/tests",
            {"title": "Test", "questions": []},
            authenticated=False
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 401)

    def test_create_rejects_invalid_question(self):
        event = make_event(
            "POST",
            "/tests",
            {
                "title": "Invalid Test",
                "visibility": "public",
                "maxAttempts": 1,
                "questions": [
                    {
                        "question": "Question?",
                        "options": ["A", "B"],
                        "correctAnswer": "C"
                    }
                ]
            }
        )

        with patch.object(app.quiz_table, "put_item") as put_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 400)
        put_item.assert_not_called()

    def test_private_test_stores_only_access_key_hash(self):
        event = make_event(
            "POST",
            "/tests",
            {
                "title": "Private Test",
                "description": "Private test description",
                "visibility": "private",
                "maxAttempts": 2,
                "questions": [
                    {
                        "question": "What is Python?",
                        "options": ["Language", "Database"],
                        "correctAnswer": "Language"
                    }
                ]
            }
        )

        with patch.object(app.quiz_table, "put_item") as put_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 201)

        data = decode(result)
        access_key = data["test"]["accessKey"]
        stored_item = put_item.call_args.kwargs["Item"]

        self.assertTrue(access_key)
        self.assertEqual(
            stored_item["accessKeyHash"],
            app.hash_access_key(access_key)
        )
        self.assertNotIn("accessKey", stored_item)
        self.assertNotIn(
            "correctAnswer",
            data["test"]
        )

    # -------------------------------------------------
    # Private test access
    # -------------------------------------------------

    def test_private_test_rejects_wrong_access_key(self):
        quiz = sample_quiz()

        event = make_event(
            "POST",
            "/tests/access",
            {
                "quizId": "quiz-123",
                "accessKey": "WRONG-KEY"
            }
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)

    def test_private_test_access_hides_correct_answers(self):
        quiz = sample_quiz()

        event = make_event(
            "POST",
            "/tests/access",
            {
                "quizId": "quiz-123",
                "accessKey": "TEST-ACCESS-KEY"
            }
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)

        data = decode(result)
        questions = data["test"]["questions"]

        self.assertEqual(len(questions), 2)

        for question in questions:
            self.assertNotIn("correctAnswer", question)

    def test_private_test_direct_access_denied_to_non_owner(self):
        quiz = sample_quiz(owner="another-user")

        event = make_event("GET", "/tests/quiz-123")

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)

    # -------------------------------------------------
    # Public test list
    # -------------------------------------------------

    def test_public_quiz_list_excludes_private_tests(self):
        public_quiz = sample_quiz(visibility="public")
        private_quiz = sample_quiz(visibility="private")
        private_quiz["quizId"] = "private-quiz"

        event = make_event("GET", "/quizzes", authenticated=False)

        with patch.object(
            app.quiz_table,
            "scan",
            return_value={
                "Items": [public_quiz, private_quiz]
            }
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)

        quizzes = decode(result)["quizzes"]

        self.assertEqual(len(quizzes), 1)
        self.assertEqual(quizzes[0]["visibility"], "public")

        for question in quizzes[0]["questions"]:
            self.assertNotIn("correctAnswer", question)

    # -------------------------------------------------
    # Submission and grading
    # -------------------------------------------------

    def test_submission_grades_answers_and_records_attempt(self):
        quiz = sample_quiz(visibility="public")

        event = make_event(
            "POST",
            "/submit",
            {
                "quizId": "quiz-123",
                "answers": {
                    "q1": "Language",
                    "q2": "Browser"
                }
            }
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(
            app.attempts_table,
            "get_item",
            return_value={}
        ), patch.object(
            app.dynamodb_client,
            "transact_write_items"
        ) as transaction:

            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)

        submitted = decode(result)["result"]

        self.assertEqual(submitted["score"], 1)
        self.assertEqual(submitted["totalQuestions"], 2)
        self.assertEqual(submitted["percentage"], 50.0)
        self.assertEqual(submitted["attemptNumber"], 1)

        transaction.assert_called_once()

    def test_submission_rejects_attempt_limit(self):
        quiz = sample_quiz(
            visibility="public",
            max_attempts=1
        )

        event = make_event(
            "POST",
            "/submit",
            {
                "quizId": "quiz-123",
                "answers": {
                    "q1": "Language",
                    "q2": "List"
                }
            }
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(
            app.attempts_table,
            "get_item",
            return_value={
                "Item": {
                    "attemptKey": "quiz-123#student-123",
                    "attemptCount": 1
                }
            }
        ), patch.object(
            app.dynamodb_client,
            "transact_write_items"
        ) as transaction:

            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)
        transaction.assert_not_called()

    def test_private_submission_requires_access_key(self):
        quiz = sample_quiz(visibility="private")

        event = make_event(
            "POST",
            "/submit",
            {
                "quizId": "quiz-123",
                "answers": {
                    "q1": "Language",
                    "q2": "List"
                }
            }
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)

    # -------------------------------------------------
    # Builder and result permissions
    # -------------------------------------------------

    def test_non_owner_cannot_view_test_results(self):
        quiz = sample_quiz(owner="another-user")

        event = make_event("GET", "/tests/quiz-123/results")

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(
            app.results_table,
            "query"
        ) as query:

            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)
        query.assert_not_called()

    def test_owner_can_view_test_results(self):
        quiz = sample_quiz(owner="student-123")

        event = make_event("GET", "/tests/quiz-123/results")

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(
            app.results_table,
            "query",
            return_value={
                "Items": [
                    {
                        "resultId": "result-1",
                        "studentId": "student-456",
                        "studentEmail": "learner@example.com",
                        "score": 2,
                        "totalQuestions": 2,
                        "percentage": 100,
                        "attemptNumber": 1,
                        "submittedAt": "2026-11-14T10:00:00+00:00"
                    }
                ]
            }
        ):
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)

        data = decode(result)

        self.assertEqual(data["count"], 1)
        self.assertEqual(
            data["results"][0]["studentEmail"],
            "learner@example.com"
        )


    # -------------------------------------------------
    # Test management: close, reopen and soft-delete
    # -------------------------------------------------

    def test_owner_can_close_test(self):
        event = make_event(
            "PATCH",
            "/tests/quiz-123",
            {"status": "closed"}
        )
        event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "creator-123"

        quiz = sample_quiz()

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(app.quiz_table, "update_item") as update_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(decode(result)["status"], "closed")
        update_item.assert_called_once()

    def test_owner_can_reopen_test(self):
        event = make_event(
            "PATCH",
            "/tests/quiz-123",
            {"status": "active"}
        )
        event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "creator-123"

        quiz = sample_quiz()
        quiz["status"] = "closed"

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": quiz}
        ), patch.object(app.quiz_table, "update_item") as update_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(decode(result)["status"], "active")
        update_item.assert_called_once()

    def test_non_owner_cannot_manage_test(self):
        event = make_event(
            "PATCH",
            "/tests/quiz-123",
            {"status": "closed"}
        )

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": sample_quiz()}
        ), patch.object(app.quiz_table, "update_item") as update_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)
        update_item.assert_not_called()

    def test_manage_test_rejects_invalid_status(self):
        event = make_event(
            "PATCH",
            "/tests/quiz-123",
            {"status": "deleted"}
        )
        event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "creator-123"

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": sample_quiz()}
        ), patch.object(app.quiz_table, "update_item") as update_item:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 400)
        update_item.assert_not_called()

    def test_delete_soft_deletes_test_and_preserves_results(self):
        event = make_event("DELETE", "/tests/quiz-123")
        event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "creator-123"

        with patch.object(
            app.quiz_table,
            "get_item",
            return_value={"Item": sample_quiz()}
        ), patch.object(app.quiz_table, "update_item") as update_item, patch.object(
            app.results_table,
            "delete_item"
        ) as delete_result:
            result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.assertIn("preserved", decode(result)["message"].lower())

        update_item.assert_called_once()
        self.assertIn(
            "deleted",
            update_item.call_args.kwargs["ExpressionAttributeValues"].values()
        )
        delete_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
