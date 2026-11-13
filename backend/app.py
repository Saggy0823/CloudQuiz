
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

QUIZ_TABLE_NAME = os.environ.get("QUIZ_TABLE", "CloudQuiz-Quizzes")
RESULTS_TABLE_NAME = os.environ.get("RESULTS_TABLE", "CloudQuiz-Results")

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


def get_quizzes():
    result = quiz_table.scan()
    quizzes = result.get("Items", [])

    while "LastEvaluatedKey" in result:
        result = quiz_table.scan(
            ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        quizzes.extend(result.get("Items", []))

    # Never expose correct answers through the quiz endpoint.
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


def submit_quiz(body):
    quiz_id = body.get("quizId")
    student_name = body.get("studentName", "").strip()
    answers = body.get("answers")

    if not quiz_id or not student_name or not isinstance(answers, dict):
        return response(400, {
            "success": False,
            "message": "quizId, studentName and answers are required."
        })

    if len(student_name) > 100:
        return response(400, {
            "success": False,
            "message": "Student name must be 100 characters or fewer."
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
        "quizId": quiz_id,
        "studentName": student_name,
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
            "quizId": quiz_id,
            "studentName": student_name,
            "score": score,
            "totalQuestions": total,
            "percentage": percentage,
            "submittedAt": submitted_at
        }
    })


def lambda_handler(event, context):
    try:
        method = event.get("requestContext", {}).get(
            "http", {}
        ).get("method", "")

        path = event.get("rawPath", "")

        if method == "GET" and path == "/health":
            return response(200, {
                "success": True,
                "message": "CloudQuiz API is running.",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        if method == "GET" and path == "/quizzes":
            return get_quizzes()

        if method == "POST" and path == "/submit":
            raw_body = event.get("body") or "{}"

            if event.get("isBase64Encoded"):
                import base64
                raw_body = base64.b64decode(raw_body).decode("utf-8")

            body = json.loads(raw_body)
            return submit_quiz(body)

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
        print("Unexpected backend error:", __import__("traceback").format_exc())
        return response(500, {
            "success": False,
            "message": "Internal server error."
        })

