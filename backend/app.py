
import json
import os
import uuid
import traceback
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# DynamoDB configuration
dynamodb = boto3.resource("dynamodb")

QUIZ_TABLE_NAME = os.environ.get(
    "QUIZ_TABLE", "CloudQuiz-Quizzes"
)

RESULTS_TABLE_NAME = os.environ.get(
    "RESULTS_TABLE", "CloudQuiz-Results"
)

quiz_table = dynamodb.Table(QUIZ_TABLE_NAME)
results_table = dynamodb.Table(RESULTS_TABLE_NAME)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str)
    }


def get_authenticated_user(event):
    """
    Read identity claims verified by the API Gateway
    Cognito JWT authorizer.
    """
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    user_id = claims.get("sub")
    email = claims.get("email", "")

    if not user_id:
        return None

    return {
        "userId": user_id,
        "email": email
    }


def get_quizzes():
    result = quiz_table.scan()
    quizzes = result.get("Items", [])

    while "LastEvaluatedKey" in result:
        result = quiz_table.scan(
            ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        quizzes.extend(result.get("Items", []))

    public_quizzes = []

    for quiz in quizzes:
        public_questions = []

        for question in quiz.get("questions", []):
            public_questions.append({
                "questionId": question.get("questionId"),
                "question": question.get("question"),
                "options": question.get("options", [])
            })

        public_quizzes.append({
            "quizId": quiz.get("quizId"),
            "title": quiz.get("title"),
            "description": quiz.get("description", ""),
            "questions": public_questions
        })

    return response(200, {
        "success": True,
        "count": len(public_quizzes),
        "quizzes": public_quizzes
    })


def submit_quiz(event, body):
    # Identity comes from verified Cognito claims,
    # never from user-supplied request fields.
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    quiz_id = body.get("quizId")
    answers = body.get("answers")

    if not quiz_id or not isinstance(answers, dict):
        return response(400, {
            "success": False,
            "message": "quizId and answers are required."
        })

    if not answers:
        return response(400, {
            "success": False,
            "message": "At least one answer is required."
        })

    quiz_result = quiz_table.get_item(
        Key={"quizId": quiz_id}
    )

    quiz = quiz_result.get("Item")

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Quiz not found."
        })

    questions = quiz.get("questions", [])

    if not questions:
        return response(400, {
            "success": False,
            "message": "This quiz has no questions."
        })

    score = 0
    answer_details = []

    for question in questions:
        question_id = question.get("questionId")
        correct_answer = question.get("correctAnswer")
        submitted_answer = answers.get(question_id)

        is_correct = submitted_answer == correct_answer

        if is_correct:
            score += 1

        answer_details.append({
            "questionId": question_id,
            "submittedAnswer": submitted_answer,
            "isCorrect": is_correct
        })

    total = len(questions)
    percentage = round((score / total) * 100, 2)

    result_id = str(uuid.uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()

    result_item = {
        "resultId": result_id,
        "studentId": user["userId"],
        "studentEmail": user["email"],
        "quizId": quiz_id,
        "score": score,
        "totalQuestions": total,
        "percentage": Decimal(str(percentage)),
        "submittedAt": submitted_at,
        "answers": answer_details
    }

    results_table.put_item(Item=result_item)

    return response(200, {
        "success": True,
        "message": "Quiz submitted successfully.",
        "result": {
            "resultId": result_id,
            "studentId": user["userId"],
            "studentEmail": user["email"],
            "quizId": quiz_id,
            "score": score,
            "totalQuestions": total,
            "percentage": percentage,
            "submittedAt": submitted_at
        }
    })


def lambda_handler(event, context):
    try:
        method = (
            event.get("requestContext", {})
            .get("http", {})
            .get("method", "")
        )

        path = event.get("rawPath", "")
        stage = event.get("requestContext", {}).get("stage", "")

        # Remove the API Gateway stage prefix when present.
        if stage and path == f"/{stage}":
            path = "/"
        elif stage and path.startswith(f"/{stage}/"):
            path = path[len(stage) + 1:]

        # Public health endpoint
        if method == "GET" and path == "/health":
            return response(200, {
                "success": True,
                "message": "CloudQuiz API is running.",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Public quiz listing
        if method == "GET" and path == "/quizzes":
            return get_quizzes()

        # Protected quiz submission
        if method == "POST" and path == "/submit":
            if not get_authenticated_user(event):
                return response(401, {
                    "success": False,
                    "message": "Authentication is required."
                })

            raw_body = event.get("body") or "{}"

            if event.get("isBase64Encoded"):
                import base64
                raw_body = base64.b64decode(
                    raw_body
                ).decode("utf-8")

            body = json.loads(raw_body)

            return submit_quiz(event, body)

        return response(404, {
            "success": False,
            "message": "Endpoint not found."
        })

    except json.JSONDecodeError:
        return response(400, {
            "success": False,
            "message": "Invalid JSON request."
        })

    except Exception:
        print("Unexpected backend error:")
        print(traceback.format_exc())

        return response(500, {
            "success": False,
            "message": "Internal server error."
        })

