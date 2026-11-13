import json
import os
import uuid
import hashlib
import secrets
import hmac
import traceback

from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


# =====================================================
# DATABASE CONFIGURATION
# =====================================================

dynamodb = boto3.resource("dynamodb")

QUIZ_TABLE_NAME = os.environ.get("QUIZ_TABLE", "CloudQuiz-Quizzes")
RESULTS_TABLE_NAME = os.environ.get("RESULTS_TABLE", "CloudQuiz-Results")
ATTEMPTS_TABLE_NAME = os.environ.get(
    "ATTEMPTS_TABLE", "CloudQuiz-Attempts"
)

quiz_table = dynamodb.Table(QUIZ_TABLE_NAME)
results_table = dynamodb.Table(RESULTS_TABLE_NAME)
attempts_table = dynamodb.Table(ATTEMPTS_TABLE_NAME)

dynamodb_client = boto3.client("dynamodb")


# =====================================================
# COMMON HELPERS
# =====================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str)
    }


def now():
    return datetime.now(timezone.utc).isoformat()


def get_authenticated_user(event):
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    user_id = claims.get("sub")

    if not user_id:
        return None

    return {
        "userId": user_id,
        "email": claims.get("email", "")
    }


def hash_access_key(key):
    return hashlib.sha256(key.strip().upper().encode()).hexdigest()


def public_questions(questions):
    return [
        {
            "questionId": q.get("questionId"),
            "question": q.get("question"),
            "options": q.get("options", [])
        }
        for q in questions
    ]


def parse_body(event):
    raw_body = event.get("body") or "{}"

    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    return json.loads(raw_body)


def get_quiz(quiz_id):
    result = quiz_table.get_item(
        Key={"quizId": quiz_id},
        ConsistentRead=True
    )
    return result.get("Item")


def safe_test_details(quiz):
    return {
        "quizId": quiz.get("quizId"),
        "title": quiz.get("title"),
        "description": quiz.get("description", ""),
        "visibility": quiz.get("visibility", "public"),
        "maxAttempts": int(quiz.get("maxAttempts", 1)),
        "questions": public_questions(quiz.get("questions", []))
    }


# =====================================================
# PUBLIC QUIZ LIST
# =====================================================

def get_quizzes():
    result = quiz_table.scan()
    quizzes = result.get("Items", [])

    while result.get("LastEvaluatedKey"):
        result = quiz_table.scan(
            ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        quizzes.extend(result.get("Items", []))

    public_list = []

    for quiz in quizzes:
        # Legacy quizzes without visibility remain public.
        if quiz.get("visibility", "public") != "public":
            continue
        if quiz.get("status", "active") != "active":
            continue

        public_list.append(safe_test_details(quiz))

    return response(200, {
        "success": True,
        "count": len(public_list),
        "quizzes": public_list
    })


# =====================================================
# CREATE TEST
# =====================================================

def create_test(event, body):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    title = str(body.get("title", "")).strip()
    description = str(body.get("description", "")).strip()
    questions = body.get("questions")
    visibility = body.get("visibility", "private")
    max_attempts = body.get("maxAttempts", 1)

    if not title or len(title) > 150:
        return response(400, {
            "success": False,
            "message": "A title between 1 and 150 characters is required."
        })

    if visibility not in ("public", "private"):
        return response(400, {
            "success": False,
            "message": "Visibility must be public or private."
        })

    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= 100
    ):
        return response(400, {
            "success": False,
            "message": "maxAttempts must be an integer between 1 and 100."
        })

    if not isinstance(questions, list) or not 1 <= len(questions) <= 200:
        return response(400, {
            "success": False,
            "message": "A test must contain between 1 and 200 questions."
        })

    clean_questions = []

    for question in questions:
        if not isinstance(question, dict):
            return response(400, {
                "success": False,
                "message": "Invalid question format."
            })

        text = str(question.get("question", "")).strip()
        options = question.get("options")
        correct = question.get("correctAnswer")

        if (
            not text
            or len(text) > 2000
            or not isinstance(options, list)
            or not 2 <= len(options) <= 10
        ):
            return response(400, {
                "success": False,
                "message": "Each question needs text and 2 to 10 options."
            })

        options = [
            str(option).strip()
            for option in options
        ]

        if any(not option or len(option) > 500 for option in options):
            return response(400, {
                "success": False,
                "message": "Options must contain 1 to 500 characters."
            })

        if correct not in options:
            return response(400, {
                "success": False,
                "message": "The correct answer must match one of the options."
            })

        clean_questions.append({
            "questionId": str(uuid.uuid4()),
            "question": text,
            "options": options,
            "correctAnswer": correct
        })

    quiz_id = str(uuid.uuid4())
    created_at = now()

    # Private keys are generated once and stored only as hashes.
    access_key = None

    quiz_item = {
        "quizId": quiz_id,
        "title": title,
        "description": description,
        "ownerId": user["userId"],
        "ownerEmail": user["email"],
        "visibility": visibility,
        "maxAttempts": max_attempts,
        "questions": clean_questions,
        "createdAt": created_at,
        "status": "active"
    }

    if visibility == "private":
        access_key = secrets.token_urlsafe(12).upper()
        quiz_item["accessKeyHash"] = hash_access_key(access_key)

    quiz_table.put_item(
        Item=quiz_item,
        ConditionExpression="attribute_not_exists(quizId)"
    )

    result = {
        "success": True,
        "message": "Test created successfully.",
        "test": {
            "quizId": quiz_id,
            "title": title,
            "visibility": visibility,
            "maxAttempts": max_attempts,
            "createdAt": created_at
        }
    }

    if access_key:
        result["test"]["accessKey"] = access_key

    return response(201, result)


# =====================================================
# BUILDER: MY TESTS
# =====================================================

def get_my_tests(event):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    result = quiz_table.query(
        IndexName="OwnerCreatedAtIndex",
        KeyConditionExpression=Key("ownerId").eq(user["userId"]),
        ScanIndexForward=False
    )

    quizzes = result.get("Items", [])

    while result.get("LastEvaluatedKey"):
        result = quiz_table.query(
            IndexName="OwnerCreatedAtIndex",
            KeyConditionExpression=Key("ownerId").eq(user["userId"]),
            ScanIndexForward=False,
            ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        quizzes.extend(result.get("Items", []))

    tests = []

    for quiz in quizzes:
        tests.append({
            "quizId": quiz.get("quizId"),
            "title": quiz.get("title"),
            "description": quiz.get("description", ""),
            "visibility": quiz.get("visibility", "public"),
            "maxAttempts": int(quiz.get("maxAttempts", 1)),
            "createdAt": quiz.get("createdAt"),
            "status": quiz.get("status", "active"),
            "questionCount": len(quiz.get("questions", []))
        })

    return response(200, {
        "success": True,
        "tests": tests
    })


# =====================================================
# STUDENT: ACCESS TEST USING KEY
# =====================================================

def access_test(event, body):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    access_key = str(body.get("accessKey", "")).strip()
    quiz_id = body.get("quizId")

    if not access_key or not quiz_id:
        return response(400, {
            "success": False,
            "message": "quizId and accessKey are required."
        })

    quiz = get_quiz(quiz_id)

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Test not found."
        })

    if quiz.get("status", "active") != "active":
        return response(403, {
            "success": False,
            "message": "This test is not currently active."
        })

    if quiz.get("visibility") != "private":
        return response(400, {
            "success": False,
            "message": "This test is public. Open it from the public test list."
        })

    stored_hash = quiz.get("accessKeyHash", "")
    supplied_hash = hash_access_key(access_key)

    if not stored_hash or not hmac.compare_digest(
        stored_hash, supplied_hash
    ):
        return response(403, {
            "success": False,
            "message": "Invalid test access key."
        })

    return response(200, {
        "success": True,
        "message": "Test access granted.",
        "test": safe_test_details(quiz)
    })


# =====================================================
# GET ONE TEST
# =====================================================

def get_test(event, quiz_id):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    quiz = get_quiz(quiz_id)

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Test not found."
        })

    is_owner = quiz.get("ownerId") == user["userId"]
    is_public = quiz.get("visibility", "public") == "public"
    status = quiz.get("status", "active")

    if not is_owner and status != "active":
        return response(403, {
            "success": False,
            "message": "This test is not currently active."
        })

    if not is_owner and not is_public:
        return response(403, {
            "success": False,
            "message": "Use the private test access key to enter this test."
        })

    return response(200, {
        "success": True,
        "test": safe_test_details(quiz)
    })


# =====================================================
# SUBMIT TEST AND GRADE
# =====================================================

def submit_quiz(event, body):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    quiz_id = body.get("quizId")
    answers = body.get("answers")
    access_key = str(body.get("accessKey", "")).strip()

    if not quiz_id or not isinstance(answers, dict) or not answers:
        return response(400, {
            "success": False,
            "message": "quizId and a non-empty answers object are required."
        })

    quiz = get_quiz(quiz_id)

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Test not found."
        })

    if quiz.get("status", "active") != "active":
        return response(403, {
            "success": False,
            "message": "This test is not currently active."
        })

    # Private tests require the key again at submission.
    if quiz.get("visibility") == "private":
        expected = quiz.get("accessKeyHash", "")
        supplied = hash_access_key(access_key) if access_key else ""

        if not expected or not hmac.compare_digest(expected, supplied):
            return response(403, {
                "success": False,
                "message": "A valid private test key is required."
            })

    questions = quiz.get("questions", [])

    if not questions:
        return response(400, {
            "success": False,
            "message": "This test has no questions."
        })

    question_ids = {
        question.get("questionId")
        for question in questions
    }

    if set(answers.keys()) != question_ids:
        return response(400, {
            "success": False,
            "message": "Submit exactly one answer for every question."
        })

    score = 0
    answer_details = []

    for question in questions:
        question_id = question["questionId"]
        correct_answer = question.get("correctAnswer")
        submitted_answer = answers.get(question_id)

        if submitted_answer not in question.get("options", []):
            return response(400, {
                "success": False,
                "message": "One or more submitted answers are invalid."
            })

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
    submitted_at = now()

    max_attempts = int(quiz.get("maxAttempts", 1))
    attempt_key = f"{quiz_id}#{user['userId']}"

    previous = attempts_table.get_item(
        Key={"attemptKey": attempt_key},
        ConsistentRead=True
    ).get("Item", {})

    previous_count = int(previous.get("attemptCount", 0))

    if previous_count >= max_attempts:
        return response(403, {
            "success": False,
            "message": "You have reached the maximum number of attempts."
        })

    attempt_number = previous_count + 1
    result_id = str(uuid.uuid4())

    result_item = {
        "resultId": result_id,
        "quizId": quiz_id,
        "studentId": user["userId"],
        "studentEmail": user["email"],
        "score": score,
        "totalQuestions": total,
        "percentage": Decimal(str(percentage)),
        "attemptNumber": attempt_number,
        "submittedAt": submitted_at,
        "answers": answer_details
    }

    update_item = {
        "TableName": ATTEMPTS_TABLE_NAME,
        "Key": {
            "attemptKey": {"S": attempt_key}
        },
        "UpdateExpression": "SET attemptCount = :next",
        "ExpressionAttributeValues": {
            ":next": {"N": str(attempt_number)}
        }
    }

    if previous_count == 0:
        update_item["ConditionExpression"] = (
            "attribute_not_exists(attemptCount)"
        )
    else:
        update_item["ConditionExpression"] = (
            "attemptCount = :previous"
        )
        update_item["ExpressionAttributeValues"][":previous"] = {
            "N": str(previous_count)
        }

    try:
        dynamodb_client.transact_write_items(
            TransactItems=[
                {"Update": update_item},
                {
                    "Put": {
                        "TableName": RESULTS_TABLE_NAME,
                        "Item": {
                            "resultId": {"S": result_id},
                            "quizId": {"S": quiz_id},
                            "studentId": {"S": user["userId"]},
                            "studentEmail": {"S": user["email"]},
                            "score": {"N": str(score)},
                            "totalQuestions": {"N": str(total)},
                            "percentage": {"N": str(percentage)},
                            "attemptNumber": {"N": str(attempt_number)},
                            "submittedAt": {"S": submitted_at},
                            "answers": {
                                "L": [
                                    {
                                        "M": {
                                            "questionId": {"S": a["questionId"]},
                                            "submittedAnswer": {"S": a["submittedAnswer"]},
                                            "isCorrect": {"BOOL": a["isCorrect"]}
                                        }
                                    }
                                    for a in answer_details
                                ]
                            }
                        },
                        "ConditionExpression": "attribute_not_exists(resultId)"
                    }
                }
            ]
        )

    except dynamodb_client.exceptions.TransactionCanceledException:
        return response(403, {
            "success": False,
            "message": "Your attempt could not be recorded. Check your attempt limit and try again."
        })

    return response(200, {
        "success": True,
        "message": "Test submitted successfully.",
        "result": {
            "resultId": result_id,
            "quizId": quiz_id,
            "studentId": user["userId"],
            "studentEmail": user["email"],
            "score": score,
            "totalQuestions": total,
            "percentage": percentage,
            "attemptNumber": attempt_number,
            "submittedAt": submitted_at
        }
    })


# =====================================================
# BUILDER: CLOSE, REOPEN OR SOFT-DELETE TEST
# =====================================================

def manage_test(event, quiz_id, method, body=None):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    quiz = get_quiz(quiz_id)

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Test not found."
        })

    if quiz.get("ownerId") != user["userId"]:
        return response(403, {
            "success": False,
            "message": "Only the test creator can manage this test."
        })

    current_status = quiz.get("status", "active")

    if current_status == "deleted":
        return response(404, {
            "success": False,
            "message": "This test has been deleted."
        })

    if method == "PATCH":
        new_status = (body or {}).get("status")

        if new_status not in ("active", "closed"):
            return response(400, {
                "success": False,
                "message": "Status must be active or closed."
            })

        quiz_table.update_item(
            Key={"quizId": quiz_id},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": new_status}
        )

        return response(200, {
            "success": True,
            "message": "Test " + new_status + " successfully.",
            "status": new_status
        })

    if method == "DELETE":
        deleted_at = now()

        quiz_table.update_item(
            Key={"quizId": quiz_id},
            UpdateExpression="SET #status = :status, deletedAt = :deletedAt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "deleted",
                ":deletedAt": deleted_at
            }
        )

        return response(200, {
            "success": True,
            "message": "Test deleted. Historical results have been preserved."
        })

    return response(405, {
        "success": False,
        "message": "Method not allowed."
    })


# =====================================================
# BUILDER: VIEW TEST RESULTS
# =====================================================

def get_test_results(event, quiz_id):
    user = get_authenticated_user(event)

    if not user:
        return response(401, {
            "success": False,
            "message": "Authentication is required."
        })

    quiz = get_quiz(quiz_id)

    if not quiz:
        return response(404, {
            "success": False,
            "message": "Test not found."
        })

    if quiz.get("ownerId") != user["userId"]:
        return response(403, {
            "success": False,
            "message": "Only the test creator can view these results."
        })

    result = results_table.query(
        IndexName="QuizSubmittedAtIndex",
        KeyConditionExpression=Key("quizId").eq(quiz_id),
        ScanIndexForward=False
    )

    result_items = result.get("Items", [])

    while result.get("LastEvaluatedKey"):
        result = results_table.query(
            IndexName="QuizSubmittedAtIndex",
            KeyConditionExpression=Key("quizId").eq(quiz_id),
            ScanIndexForward=False,
            ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        result_items.extend(result.get("Items", []))

    students = []

    for item in result_items:
        students.append({
            "resultId": item.get("resultId"),
            "studentId": item.get("studentId"),
            "studentEmail": item.get("studentEmail"),
            "score": int(item.get("score", 0)),
            "totalQuestions": int(item.get("totalQuestions", 0)),
            "percentage": float(item.get("percentage", 0)),
            "attemptNumber": int(item.get("attemptNumber", 1)),
            "submittedAt": item.get("submittedAt")
        })

    return response(200, {
        "success": True,
        "quizId": quiz_id,
        "title": quiz.get("title"),
        "count": len(students),
        "results": students
    })


# =====================================================
# MAIN API ROUTER
# =====================================================

def lambda_handler(event, context):
    try:
        method = (
            event.get("requestContext", {})
            .get("http", {})
            .get("method", "")
        )

        path = event.get("rawPath", "")
        stage = event.get("requestContext", {}).get("stage", "")

        if stage and path.startswith(f"/{stage}/"):
            path = path[len(stage) + 1:]
        elif stage and path == f"/{stage}":
            path = "/"

        # Public health endpoint
        if method == "GET" and path == "/health":
            return response(200, {
                "success": True,
                "message": "CloudQuiz API is running.",
                "timestamp": now()
            })

        # Public quizzes
        if method == "GET" and path == "/quizzes":
            return get_quizzes()

        # Create test
        if method == "POST" and path == "/tests":
            return create_test(event, parse_body(event))

        # List builder tests
        if method == "GET" and path == "/my-tests":
            return get_my_tests(event)

        # Access private test
        if method == "POST" and path == "/tests/access":
            return access_test(event, parse_body(event))

        # Submit test
        if method == "POST" and path == "/submit":
            return submit_quiz(event, parse_body(event))

        # Individual test routes
        if path.startswith("/tests/"):
            parts = path.strip("/").split("/")

            if len(parts) == 3 and parts[2] == "results":
                if method == "GET":
                    return get_test_results(event, parts[1])

            if len(parts) == 2:
                if method == "GET":
                    return get_test(event, parts[1])
                if method == "PATCH":
                    return manage_test(event, parts[1], method, parse_body(event))
                if method == "DELETE":
                    return manage_test(event, parts[1], method)

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
