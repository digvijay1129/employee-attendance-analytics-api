# Employee Attendance \& Analytics API

A REST API built with **FastAPI, Python, and MongoDB** for employee management, attendance tracking, attendance regularization, and analytics.

## Features

* Employee creation with duplicate employee-code protection.
* Attendance punch-in and punch-out with timestamp validation.
* Attendance regularization with an audit history.
* Filtered, sorted, and paginated attendance records.
* Monthly employee attendance analytics.
* Department summaries and late-arrival leaderboard.
* Department attendance trends with seven-day moving averages.
* MongoDB health checks and query execution plans.
* Unique and compound indexes for data integrity and query performance.

## Tech Stack

* Python 3.12+
* FastAPI and Uvicorn
* MongoDB 6.0+
* PyMongo and Pydantic

## Setup

Create and activate a virtual environment, then install dependencies.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

**Linux/macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` with your MongoDB connection details:

```env
MONGO\_URI=mongodb://localhost:27017
MONGO\_DB=hrone\_attendance
```

Ensure MongoDB is running, then load the sample data:

```bash
python sample\_seed.py
```

Start the API:

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Documentation

Once the server is running, open:

* Swagger UI: `http://127.0.0.1:8000/docs`
* OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
* Health check: `http://127.0.0.1:8000/health`

## Main Endpoints

|Method|Endpoint|Purpose|
|-|-|-|
|GET|`/health`|Check API and database availability|
|POST|`/employees`|Create an employee|
|GET|`/employees`|List employees|
|POST|`/attendance/punch-in`|Record attendance punch-in|
|POST|`/attendance/punch-out`|Record attendance punch-out|
|GET|`/attendance`|Filter and paginate attendance records|
|PATCH|`/attendance/{emp\_code}/{date}`|Regularize an attendance record|
|GET|`/analytics/employees/{emp\_code}/monthly`|Monthly employee analytics|
|GET|`/analytics/departments/summary`|Department attendance summary|
|GET|`/analytics/leaderboard/late`|Rank employees by late minutes|
|GET|`/analytics/departments/{department}/trend`|Department attendance trends|
|GET|`/admin/explain/{endpoint}`|Inspect MongoDB query execution statistics|

Refer to `openapi.yaml` for the API contract, parameters, response schemas, and business rules.

## Project Structure

```text
.

|-- app/

|   |-- \_\_init\_\_.py

|   `-- main.py

|-- sample\_data/

|   |-- attendance\_logs.json

|   `-- employees.json

|-- .env.example

|-- DATA\_MODEL.md

|-- DECISIONS.md

|-- REVIEW.md

|-- openapi.yaml

|-- requirements.txt

`-- sample\_seed.py

```

## Security

Keep `.env` and all database credentials private. Never commit secrets or local virtual-environment files to the repository.
