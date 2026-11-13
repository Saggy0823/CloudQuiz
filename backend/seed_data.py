
import boto3
import os

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE_NAME = "CloudQuiz-Quizzes"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

quiz = {
    "quizId": "aws-basics-001",
    "title": "AWS Cloud Fundamentals",
    "description": "Test your knowledge of AWS cloud computing.",
    "questions": [
        {
            "questionId": "q1",
            "question": "What does AWS stand for?",
            "options": [
                "Amazon Web Services",
                "Advanced Web System",
                "Automated Web Software",
                "Amazon World Server"
            ],
            "correctAnswer": "Amazon Web Services"
        },
        {
            "questionId": "q2",
            "question": "Which AWS service provides serverless computing?",
            "options": [
                "Amazon EC2",
                "AWS Lambda",
                "Amazon S3",
                "Amazon RDS"
            ],
            "correctAnswer": "AWS Lambda"
        },
        {
            "questionId": "q3",
            "question": "Which AWS service is a NoSQL database?",
            "options": [
                "Amazon RDS",
                "Amazon Redshift",
                "Amazon DynamoDB",
                "Amazon EBS"
            ],
            "correctAnswer": "Amazon DynamoDB"
        },
        {
            "questionId": "q4",
            "question": "Which service stores objects such as images and files?",
            "options": [
                "Amazon S3",
                "AWS Lambda",
                "Amazon VPC",
                "Amazon SQS"
            ],
            "correctAnswer": "Amazon S3"
        },
        {
            "questionId": "q5",
            "question": "Which AWS service manages user authentication?",
            "options": [
                "Amazon CloudWatch",
                "Amazon Cognito",
                "Amazon EC2",
                "AWS CloudFormation"
            ],
            "correctAnswer": "Amazon Cognito"
        }
    ]
}

table.put_item(Item=quiz)

print("Sample quiz inserted successfully!")
print("Quiz ID:", quiz["quizId"])

