import os
import json

import re

from datetime import date as Date, datetime, time, timedelta, timezone

from decimal import Decimal, ROUND_HALF_UP

from typing import Annotated, Literal, Optional



from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field, field_validator, model_validator

from pymongo import MongoClient

from pymongo.errors import DuplicateKeyError

from bson import json_util



load_dotenv()



client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))

db = client[os.getenv("MONGO_DB", "attendance_db")]



IST = timezone(timedelta(hours=5, minutes=30))



EpochMilliseconds = Annotated[

    int,

    Field(

        strict=True,

        ge=100_000_000_000,

        le=4_102_444_800_000,

    ),

]



app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")



@app.on_event("startup")
def create_indexes():
    # Required unique indexes
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_date",
    )

    # Employee filtering by department and joining date
    db.employees.create_index(
        [("department", 1), ("joined_on", 1)],
        name="idx_employees_department_joined",
    )

    # Attendance date filtering and late-arrival queries
    db.attendance_logs.create_index(
        [("date", 1), ("late_minutes", 1)],
        name="idx_attendance_date_late",
    )


# Helpers

# --------------------------------------------------------------------------- #

def truncate_to_seconds(value: datetime) -> datetime:

    return value.replace(microsecond=0)





def compute_attendance_date(

    punch_in: datetime,

    shift_start: str,

    shift_end: str,

) -> Date:

    local = punch_in.astimezone(IST)

    start = time.fromisoformat(shift_start)

    end = time.fromisoformat(shift_end)

    overnight = end <= start

    if overnight and local.time().replace(tzinfo=None) < end:

        return local.date() - timedelta(days=1)

    return local.date()





def compute_late_minutes(

    punch_in: datetime,

    shift_start: str,

    attendance_date: Date,

) -> int:

    local = punch_in.astimezone(IST)



    start_time = time.fromisoformat(shift_start)



    scheduled_start = datetime.combine(

        attendance_date,

        start_time,

        tzinfo=IST,

    )



    elapsed_seconds = (

        local - scheduled_start

    ).total_seconds()



    # No late minutes within the 10-minute grace period.

    if elapsed_seconds <= 600:

        return 0



    # Count complete elapsed minutes after the shift start.

    return int(elapsed_seconds // 60)





def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:

    elapsed_seconds = Decimal(

        str((punch_out - punch_in).total_seconds())

    )

    hours = elapsed_seconds / Decimal("3600")

    rounded_hours = hours.quantize(

        Decimal("0.01"),

        rounding=ROUND_HALF_UP,

    )

    return float(rounded_hours)





def compute_overtime(

    punch_out: datetime,

    shift_start: str,

    shift_end: str,

    attendance_date: str,

) -> int:

    day = Date.fromisoformat(attendance_date)

    start_time = time.fromisoformat(shift_start)

    end_time = time.fromisoformat(shift_end)

    # An overnight shift ends on the following calendar day.

    if end_time <= start_time:

        day += timedelta(days=1)



    scheduled_end = datetime.combine(

        day,

        end_time,

        tzinfo=IST,

    )



    elapsed_seconds = (

        punch_out.astimezone(IST) - scheduled_end

    ).total_seconds()



    whole_minutes = int(elapsed_seconds // 60)



    # R3: Overtime is counted only when it reaches 30 whole minutes.

    return whole_minutes if whole_minutes >= 30 else 0





def to_utc_datetime(value: datetime) -> datetime:

    """Interpret MongoDB's naive datetimes as UTC."""

    if value.tzinfo is None:

        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)





def epoch_ms(value: Optional[datetime]) -> Optional[int]:

    """Convert a BSON datetime to epoch milliseconds."""

    if value is None:

        return None

    return int(to_utc_datetime(value).timestamp() * 1000)





def attendance_response(doc: dict) -> dict:

    """Serialize an attendance document without exposing MongoDB _id."""

    result = dict(doc)

    result.pop("_id", None)

    for field in ("punch_in", "punch_out"):

        result[field] = epoch_ms(result.get(field))



    result["history"] = result.get("history", [])

    result["half_day"] = result.get("half_day", False)



    for entry in result["history"]:

        entry["at"] = epoch_ms(entry.get("at"))

        changes = entry.get("changes", {})

        for field in ("punch_in", "punch_out"):

            if field in changes:

                changes[field]["from"] = epoch_ms(changes[field].get("from"))

                changes[field]["to"] = epoch_ms(changes[field].get("to"))



    return result





# --------------------------------------------------------------------------- #

# Models

# --------------------------------------------------------------------------- #



class EmployeeIn(BaseModel):

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")

    name: str = Field(min_length=1, max_length=100)

    email: str = Field(max_length=120)

    department: str = Field(min_length=1, max_length=50)

    shift_start: str = Field(

        default="09:30",

        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",

    )

    shift_end: str = Field(

        default="18:30",

        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",

    )

    joined_on: str



    @field_validator("email")

    @classmethod

    def validate_email(cls, value: str) -> str:

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):

            raise ValueError("Invalid email format")

        return value



    @field_validator("joined_on")

    @classmethod

    def validate_joined_on(cls, value: str) -> str:

        try:

            parsed = Date.fromisoformat(value)

        except ValueError:

            raise ValueError("joined_on must be YYYY-MM-DD")



        if parsed.isoformat() != value:

            raise ValueError("joined_on must be YYYY-MM-DD")



        return value



    @model_validator(mode="after")

    def validate_shift(self):

        if self.shift_start == self.shift_end:

            raise ValueError("shift_start and shift_end must differ")

        return self





class PunchInIn(BaseModel):

    emp_code: str

    punched_at: Optional[EpochMilliseconds] = None

    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"





class PunchOutIn(BaseModel):

    emp_code: str

    punched_at: Optional[EpochMilliseconds] = None





class AttendanceUpdate(BaseModel):

    status: Optional[

        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]

    ] = None

    punch_in: Optional[EpochMilliseconds] = None

    punch_out: Optional[EpochMilliseconds] = None

    reason: str = Field(min_length=5, max_length=200)

    regularized_by: str = Field(min_length=1, max_length=50)





# --------------------------------------------------------------------------- #

# Endpoints

# --------------------------------------------------------------------------- #


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


@app.post("/employees", status_code=201)

def create_employee(body: EmployeeIn):

    doc = body.model_dump()

    created_at = datetime.now(timezone.utc)

    doc["created_at"] = created_at



    try:

        db.employees.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(

            status_code=409,

            detail="emp_code already exists",

        )



    doc.pop("_id", None)

    doc["created_at"] = int(created_at.timestamp() * 1000)



    return doc





@app.get("/employees")

def list_employees(

    department: Optional[str] = None,

    page: int = Query(default=1, ge=1),

    page_size: int = Query(default=20, ge=1, le=100),

):

    query = {}



    if department:

        query["department"] = department



    total = db.employees.count_documents(query)



    skip = (page - 1) * page_size



    items = list(

        db.employees.find(query, {"_id": 0})

        .sort("emp_code", 1)

        .skip(skip)

        .limit(page_size)

    )



    for item in items:

        created_at = item.get("created_at")

        if created_at is not None:

            if created_at.tzinfo is None:

                created_at = created_at.replace(tzinfo=timezone.utc)

            item["created_at"] = int(created_at.timestamp() * 1000)



    return {

        "items": items,

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@app.post("/attendance/punch-in", status_code=201)

def punch_in(body: PunchInIn):

    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:

        raise HTTPException(404, "Employee not found")



    ts = truncate_to_seconds(

        datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)

        if body.punched_at

        else datetime.now(timezone.utc)

    )



    att_date = compute_attendance_date(ts, emp["shift_start"], emp["shift_end"])

    date_str = att_date.isoformat()

    late_mins = compute_late_minutes(ts, emp["shift_start"], att_date)



    doc = {

        "emp_code": body.emp_code,

        "date": date_str,

        "status": body.status,

        "punch_in": ts,

        "punch_out": None,

        "work_hours": None,

        "late_minutes": late_mins,

        "overtime_minutes": 0,

        "half_day": False,

        "history": [],

    }



    try:

        res = db.attendance_logs.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(409, "already punched in for this date")



    doc["id"] = str(res.inserted_id)

    doc.pop("_id", None)

    return doc





@app.post("/attendance/punch-out", status_code=200)

def punch_out(body: PunchOutIn):

    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:

        raise HTTPException(404, "Employee not found")

    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=IST) if body.punched_at else datetime.now(IST)

    d = ts.date().isoformat()

    log = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d})

    if not log:

        raise HTTPException(404, "No punch-in record found for this date")

    if log.get("punch_out"):

        raise HTTPException(409, "Already punched out for this date")



    punch_in_dt = log["punch_in"]



    # PyMongo returns naive datetimes in UTC by default.

    # Attach UTC first, then convert to IST.

    if punch_in_dt.tzinfo is None:

        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)



    punch_in_dt = punch_in_dt.astimezone(IST)



    work_hours = compute_work_hours(punch_in_dt, ts)

    overtime_minutes = compute_overtime(

        ts,

        emp["shift_start"],

        emp["shift_end"],

        log["date"],

    )



    update_data = {

        "punch_out": ts,

        "work_hours": work_hours,

        "overtime_minutes": overtime_minutes,

    }

    db.attendance_logs.update_one({"_id": log["_id"]}, {"$set": update_data})



    updated_log = db.attendance_logs.find_one({"_id": log["_id"]})

    updated_log["id"] = str(updated_log.pop("_id"))

    return updated_log





@app.patch("/attendance/{emp_code}/{date}")

def update_attendance(emp_code: str, date: str, body: AttendanceUpdate):

    try:

        parsed_date = Date.fromisoformat(date)

    except (ValueError, AttributeError):

        raise HTTPException(422, "date must be YYYY-MM-DD")



    log = db.attendance_logs.find_one(

        {"emp_code": emp_code, "date": parsed_date.isoformat()}

    )

    if not log:

        raise HTTPException(404, "Attendance record not found")



    emp = db.employees.find_one({"emp_code": emp_code})

    if not emp:

        raise HTTPException(404, "Employee not found")



    old = dict(log)

    old.setdefault("history", [])

    old.setdefault("half_day", False)

    old.setdefault("late_minutes", 0)

    old.setdefault("overtime_minutes", 0)



    new = dict(old)



    if body.status is not None:

        new["status"] = body.status

    if body.punch_in is not None:

        new["punch_in"] = datetime.fromtimestamp(

            body.punch_in / 1000, tz=timezone.utc

        )

    if body.punch_out is not None:

        new["punch_out"] = datetime.fromtimestamp(

            body.punch_out / 1000, tz=timezone.utc

        )



    status = new["status"]

    if status in ("ABSENT", "LEAVE"):

        supplied_fields = body.model_fields_set



        if "punch_in" in supplied_fields or "punch_out" in supplied_fields:

            raise HTTPException(

                422, "ABSENT and LEAVE cannot include punch times"

            )

        new["punch_in"] = None

        new["punch_out"] = None

        new["late_minutes"] = 0

        new["work_hours"] = None

        new["overtime_minutes"] = 0

        new["half_day"] = False

    else:

        if new.get("punch_in") is None:

            raise HTTPException(

                422, "Presence status requires a punch_in"

            )



        punch_in_dt = to_utc_datetime(new["punch_in"]).replace(microsecond=0)

        new["punch_in"] = punch_in_dt



        actual_attendance_date = compute_attendance_date(

            punch_in_dt,

            emp["shift_start"],

            emp["shift_end"],

        )



        if actual_attendance_date != parsed_date:

            raise HTTPException(

                422, "punch_in does not belong to the attendance date"

            )



        new["late_minutes"] = compute_late_minutes(

            punch_in_dt, emp["shift_start"], parsed_date

        )



        punch_out_value = new.get("punch_out")

        if punch_out_value is None:

            new["work_hours"] = None

            new["overtime_minutes"] = 0

            new["half_day"] = False

        else:

            punch_out_dt = to_utc_datetime(punch_out_value).replace(

                microsecond=0

            )

            new["punch_out"] = punch_out_dt



            elapsed = (punch_out_dt - punch_in_dt).total_seconds()

            if elapsed <= 0 or elapsed > 24 * 3600:

                raise HTTPException(

                    422, "punch_out must be after punch_in and within 24 hours"

                )



            new["work_hours"] = compute_work_hours(

                punch_in_dt, punch_out_dt

            )

            new["overtime_minutes"] = compute_overtime(

                punch_out_dt,

                emp["shift_start"],

                emp["shift_end"],

                parsed_date.isoformat(),

            )

            new["half_day"] = new["work_hours"] < 4.50



    tracked_fields = (

        "status",

        "punch_in",

        "punch_out",

        "work_hours",

        "late_minutes",

        "overtime_minutes",

        "half_day",

    )



    def comparable(field, value):

        if field in ("punch_in", "punch_out") and value is not None:

            return to_utc_datetime(value)

        return value



    changes = {}

    for field in tracked_fields:

        before = old.get(field)

        after = new.get(field)

        if comparable(field, before) != comparable(field, after):

            changes[field] = {"from": before, "to": after}



    if not changes:

        raise HTTPException(422, "Request does not change the record")



    now = datetime.now(timezone.utc)

    history_entry = {

        "at": now,

        "by": body.regularized_by,

        "reason": body.reason,

        "changes": changes,

    }



    expected = {"_id": log["_id"]}

    for field in ("status", "punch_in", "punch_out", "history"):

        if field in log:

            expected[field] = log[field]

        else:

            expected[field] = {"$exists": False}



    update_fields = {

        field: new.get(field)

        for field in tracked_fields

        if field in changes

    }



    result = db.attendance_logs.update_one(

        expected,

        {

            "$set": update_fields,

            "$push": {"history": history_entry},

        },

    )



    if result.modified_count != 1:

        raise HTTPException(409, "Attendance record changed concurrently")



    updated = db.attendance_logs.find_one(

        {"emp_code": emp_code, "date": parsed_date.isoformat()}

    )

    return attendance_response(updated)





@app.get("/attendance")

def list_attendance(

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = Query(default=1, ge=1),

    page_size: int = Query(default=20, ge=1, le=100),

):

    q = {}

    if emp_code:

        q["emp_code"] = emp_code



    parsed_from = None

    parsed_to = None



    if date_from:

        try:

            parsed_from = Date.fromisoformat(date_from)

        except ValueError:

            raise HTTPException(422, "date_from must be YYYY-MM-DD")

        if parsed_from.isoformat() != date_from:

            raise HTTPException(422, "date_from must be YYYY-MM-DD")



    if date_to:

        try:

            parsed_to = Date.fromisoformat(date_to)

        except ValueError:

            raise HTTPException(422, "date_to must be YYYY-MM-DD")

        if parsed_to.isoformat() != date_to:

            raise HTTPException(422, "date_to must be YYYY-MM-DD")



    if parsed_from and parsed_to and parsed_from > parsed_to:

        raise HTTPException(422, "date_from must be less than or equal to date_to")



    if parsed_from or parsed_to:

        date_filter = {}

        if parsed_from:

            date_filter["$gte"] = parsed_from.isoformat()

        if parsed_to:

            date_filter["$lte"] = parsed_to.isoformat()

        q["date"] = date_filter



    if status:

        q["status"] = status



    total = db.attendance_logs.count_documents(q)

    cursor = (

        db.attendance_logs.find(q)

        .sort([("date", -1), ("emp_code", 1)])

        .skip((page - 1) * page_size)

        .limit(page_size)

    )

    items = [attendance_response(doc) for doc in cursor]

    return {

        "items": items,

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@app.get("/analytics/employees/{emp_code}/monthly")

def employee_monthly_analytics(

    emp_code: str,

    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),

):

    emp = db.employees.find_one({"emp_code": emp_code})

    if not emp:

        raise HTTPException(404, "Employee not found")



    year_num, month_num = map(int, month.split("-"))

    start_date = Date(year_num, month_num, 1)

    next_month = (

        Date(year_num + 1, 1, 1)

        if month_num == 12

        else Date(year_num, month_num + 1, 1)

    )

    end_date = next_month - timedelta(days=1)



    joined_on = Date.fromisoformat(emp["joined_on"])

    current = max(start_date, joined_on)

    working_days = 0

    while current <= end_date:

        if current.weekday() < 5:

            working_days += 1

        current += timedelta(days=1)



    pipeline = [

        {"$match": {

            "emp_code": emp_code,

            "date": {"$gte": start_date.isoformat(),

                     "$lte": end_date.isoformat()},

        }},

        {"$addFields": {

            "_parsed_date": {"$dateFromString": {"dateString": "$date"}}

        }},

        {"$addFields": {

            "_is_weekday": {"$and": [

                {"$gte": [{"$dayOfWeek": "$_parsed_date"}, 2]},

                {"$lte": [{"$dayOfWeek": "$_parsed_date"}, 6]},

            ]}

        }},

        {"$group": {

            "_id": None,

            "present_days": {"$sum": {"$cond": [

                {"$and": [

                    "$_is_weekday",

                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},

                ]},

                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},

                0,

            ]}},

            "leave_days": {"$sum": {"$cond": [

                {"$eq": ["$status", "LEAVE"]}, 1, 0

            ]}},

            "late_count": {"$sum": {"$cond": [

                {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0

            ]}},

            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},

            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},

        }},

    ]



    results = list(db.attendance_logs.aggregate(pipeline))

    stats = results[0] if results else {}

    present_days = stats.get("present_days", 0)



    attendance_pct = (

        float((Decimal(str(present_days)) * Decimal("100") /

               Decimal(working_days)).quantize(

                   Decimal("0.0001"), rounding=ROUND_HALF_UP

               ))

        if working_days else None

    )



    return {

        "emp_code": emp_code,

        "month": month,

        "working_days": working_days,

        "present_days": present_days,

        "leave_days": stats.get("leave_days", 0),

        "late_count": stats.get("late_count", 0),

        "total_late_minutes": stats.get("total_late_minutes", 0),

        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),

        "attendance_pct": attendance_pct,

    }





@app.get("/analytics/departments/summary")

def department_summary(

    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),

    department: Optional[str] = None,

):

    year_num, month_num = map(int, month.split("-"))

    start_date = Date(year_num, month_num, 1)

    next_month = (

        Date(year_num + 1, 1, 1)

        if month_num == 12

        else Date(year_num, month_num + 1, 1)

    )

    end_date = next_month - timedelta(days=1)



    employee_match = {"joined_on": {"$lte": end_date.isoformat()}}

    if department:

        employee_match["department"] = department



    pipeline = [

        {"$match": employee_match},

        {"$lookup": {

            "from": "attendance_logs",

            "let": {"employee_code": "$emp_code"},

            "pipeline": [

                {"$match": {"$expr": {"$and": [

                    {"$eq": ["$emp_code", "$$employee_code"]},

                    {"$gte": ["$date", start_date.isoformat()]},

                    {"$lte": ["$date", end_date.isoformat()]},

                ]}}}

            ],

            "as": "attendance",

        }},

        {"$unwind": {

            "path": "$attendance",

            "preserveNullAndEmptyArrays": True,

        }},

        {"$addFields": {

            "_is_present_status": {

                "$in": ["$attendance.status", ["PRESENT", "WFH", "ON_DUTY"]]

            },

            "_is_weekday": {

                "$cond": [

                    {"$ne": [{"$ifNull": ["$attendance.date", None]}, None]},

                    {"$let": {

                        "vars": {"parsed": {"$dateFromString": {

                            "dateString": "$attendance.date"

                        }}},

                        "in": {"$and": [

                            {"$gte": [{"$dayOfWeek": "$$parsed"}, 2]},

                            {"$lte": [{"$dayOfWeek": "$$parsed"}, 6]},

                        ]},

                    }},

                    False,

                ]

            },

        }},

        {"$group": {

            "_id": "$department",

            "employee_codes": {"$addToSet": "$emp_code"},

            "present_days": {"$sum": {"$cond": [

                {"$and": ["$_is_present_status", "$_is_weekday"]},

                {"$cond": [

                    {"$eq": ["$attendance.half_day", True]}, 0.5, 1

                ]},

                0,

            ]}},

            "work_hours": {"$push": {"$cond": [

                {"$and": [

                    "$_is_present_status",

                    {"$ne": [{"$ifNull": ["$attendance.work_hours", None]}, None]},

                ]},

                "$attendance.work_hours",

                None,

            ]}},

            "late_count": {"$sum": {"$cond": [

                {"$gt": [{"$ifNull": ["$attendance.late_minutes", 0]}, 0]},

                1, 0,

            ]}},

            "total_late_minutes": {

                "$sum": {"$ifNull": ["$attendance.late_minutes", 0]}

            },

            "leave_count": {"$sum": {"$cond": [

                {"$eq": ["$attendance.status", "LEAVE"]}, 1, 0

            ]}},

            "on_duty_count": {"$sum": {"$cond": [

                {"$eq": ["$attendance.status", "ON_DUTY"]}, 1, 0

            ]}},

        }},

        {"$project": {

            "_id": 0,

            "department": "$_id",

            "headcount": {"$size": "$employee_codes"},

            "present_days": 1,

            "avg_work_hours": {"$let": {

                "vars": {"valid": {"$filter": {

                    "input": "$work_hours",

                    "as": "hours",

                    "cond": {"$ne": ["$$hours", None]},

                }}},

                "in": {"$cond": [

                    {"$gt": [{"$size": "$$valid"}, 0]},

                    {"$round": [{"$avg": "$$valid"}, 2]},

                    None,

                ]},

            }},

            "late_count": 1,

            "total_late_minutes": 1,

            "leave_count": 1,

            "on_duty_count": 1,

        }},

        {"$sort": {"department": 1}},

    ]



    departments = list(db.employees.aggregate(pipeline))

    return {"month": month, "departments": departments}





@app.get("/analytics/leaderboard/late")

def late_leaderboard(

    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),

    limit: int = Query(default=10, ge=1, le=50),

    department: Optional[str] = None,

):

    year_num, month_num = map(int, month.split("-"))

    start_date = Date(year_num, month_num, 1)

    next_month = (

        Date(year_num + 1, 1, 1)

        if month_num == 12

        else Date(year_num, month_num + 1, 1)

    )

    end_date = next_month - timedelta(days=1)



    employee_match = {}

    if department:

        employee_match["department"] = department



    pipeline = [

        {"$match": {

            "date": {

                "$gte": start_date.isoformat(),

                "$lte": end_date.isoformat(),

            },

            "late_minutes": {"$gt": 0},

        }},

        {"$group": {

            "_id": "$emp_code",

            "total_late_minutes": {"$sum": "$late_minutes"},

            "late_count": {"$sum": 1},

        }},

        {"$match": {"total_late_minutes": {"$gt": 0}}},

        {"$lookup": {

            "from": "employees",

            "localField": "_id",

            "foreignField": "emp_code",

            "as": "employee",

        }},

        {"$unwind": "$employee"},

    ]



    if employee_match:

        pipeline.append({"$match": {

            "employee.department": employee_match["department"]

        }})



    pipeline.extend([

        {"$setWindowFields": {

            "sortBy": {"total_late_minutes": -1},

            "output": {

                "rank": {"$rank": {}},

            },

        }},

        {"$match": {"rank": {"$lte": limit}}},

        {"$sort": {"total_late_minutes": -1, "_id": 1}},

        {"$project": {

            "_id": 0,

            "rank": 1,

            "emp_code": "$_id",

            "name": "$employee.name",

            "department": "$employee.department",

            "total_late_minutes": 1,

            "late_count": 1,

        }},

    ])



    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}





@app.get("/analytics/departments/{department}/trend")

def department_trend(

    department: str,

    date_from: str = Query(..., alias="from"),

    date_to: str = Query(..., alias="to"),

):

    try:

        start_date = Date.fromisoformat(date_from)

        end_date = Date.fromisoformat(date_to)

    except ValueError:

        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")



    if start_date.isoformat() != date_from or end_date.isoformat() != date_to:

        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")

    if end_date < start_date:

        raise HTTPException(status_code=422, detail="'to' must be on or after 'from'")

    if (end_date - start_date).days + 1 > 92:

        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    if db.employees.count_documents({"department": department}) == 0:

        raise HTTPException(status_code=404, detail="Department not found")



    start_text = start_date.isoformat()

    end_text = end_date.isoformat()

    start_dt = datetime.combine(start_date, time.min)

    end_dt = datetime.combine(end_date, time.min) + timedelta(days=1)



    pipeline = [

        {"$match": {"department": department, "joined_on": {"$lte": end_text}}},

        {"$lookup": {

            "from": "attendance_logs",

            "let": {"code": "$emp_code"},

            "pipeline": [

                {"$match": {"$expr": {"$and": [

                    {"$eq": ["$emp_code", "$$code"]},

                    {"$gte": ["$date", start_text]},

                    {"$lte": ["$date", end_text]},

                ]}}},

            ],

            "as": "logs",

        }},

        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},

        {"$addFields": {

            "_date": {"$cond": [

                {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},

                {"$dateFromString": {"dateString": "$logs.date"}},

                None,

            ]},

            "_present": {"$cond": [

                {"$and": [

                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},

                    {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},

                ]},

                {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]},

                0,

            ]},

            "_late": {"$cond": [

                {"$and": [

                    {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},

                    {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},

                ]},

                1, 0,

            ]},

        }},

        {"$group": {

            "_id": "$_date",

            "present_count": {"$sum": "$_present"},

            "late_count": {"$sum": "$_late"},

        }},

        {"$match": {"_id": {"$ne": None}}},

        {"$densify": {"field": "_id", "range": {

            "step": 1, "unit": "day", "bounds": [start_dt, end_dt]

        }}},

        {"$sort": {"_id": 1}},

        {"$set": {

            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$_id"}},

            "is_working_day": {"$and": [

                {"$gte": [{"$dayOfWeek": "$_id"}, 2]},

                {"$lte": [{"$dayOfWeek": "$_id"}, 6]},

            ]},

            "present_count": {"$ifNull": ["$present_count", 0]},

            "late_count": {"$ifNull": ["$late_count", 0]},

        }},

        {"$lookup": {

            "from": "employees",

            "let": {"day": "$date"},

            "pipeline": [

                {"$match": {"$expr": {"$and": [

                    {"$eq": ["$department", department]},

                    {"$lte": ["$joined_on", "$$day"]},

                ]}}},

                {"$count": "count"},

            ],

            "as": "_headcount",

        }},

        {"$set": {

            "headcount": {"$ifNull": [{"$arrayElemAt": ["$_headcount.count", 0]}, 0]},

        }},

        {"$set": {"attendance_rate": {"$cond": [

            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},

            {"$round": [

                {"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]},

                4,

            ]},

            None,

        ]}}},

        {"$setWindowFields": {

            "sortBy": {"_id": 1},

            "output": {

                "_rates": {"$push": "$attendance_rate", "window": {"documents": [-6, 0]}}

            },

        }},

        {"$set": {"moving_avg_7d": {"$let": {

            "vars": {"valid_rates": {"$filter": {

                "input": "$_rates", "as": "rate",

                "cond": {"$ne": ["$$rate", None]},

            }}},

            "in": {"$cond": [

                {"$gt": [{"$size": "$$valid_rates"}, 0]},

                {"$round": [{"$avg": "$$valid_rates"}, 4]},

                None,

            ]},

        }}}},

        {"$project": {

            "_id": 0,

            "date": 1,

            "is_working_day": 1,

            "headcount": 1,

            "present_count": 1,

            "late_count": 1,

            "attendance_rate": 1,

            "moving_avg_7d": 1,

        }},

        {"$match": {"date": {"$gte": start_text, "$lte": end_text}}},

        {"$sort": {"date": 1}},

    ]



    return {

    "department": department,

    "from": start_text,

    "to": end_text,

    "trend": list(db.employees.aggregate(pipeline)),

}







@app.get("/admin/explain/{endpoint}")

def explain_endpoint(

    endpoint: Literal[

        "attendance_list",

        "employee_monthly",

        "department_summary",

        "late_leaderboard",

        "department_trend",

    ],

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = Query(default=1, ge=1),

    page_size: int = Query(default=20, ge=1, le=100),

    month: Optional[str] = None,

    department: Optional[str] = None,

    limit: int = Query(default=10, ge=1, le=50),

    date_start: Optional[str] = Query(default=None, alias="from"),

    date_end: Optional[str] = Query(default=None, alias="to"),

):

    if endpoint == "attendance_list":

        query = {}

        if emp_code:

            query["emp_code"] = emp_code

        if status:

            query["status"] = status

        if date_from or date_to:

            query["date"] = {}

            if date_from:

                query["date"]["$gte"] = date_from

            if date_to:

                query["date"]["$lte"] = date_to



        result = db.command({

            "explain": {

                "find": "attendance_logs",

                "filter": query,

                "sort": {"date": -1, "emp_code": 1},

                "skip": (page - 1) * page_size,

                "limit": page_size,

            },

            "verbosity": "executionStats",

        })



    elif endpoint == "employee_monthly":

        if not emp_code or not month:

            raise HTTPException(

                status_code=422,

                detail="employee_monthly requires emp_code and month=YYYY-MM",

            )

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):

            raise HTTPException(status_code=422, detail="month must use YYYY-MM")



        year_num, month_num = map(int, month.split("-"))

        month_start = Date(year_num, month_num, 1)

        next_month = (

            Date(year_num + 1, 1, 1)

            if month_num == 12

            else Date(year_num, month_num + 1, 1)

        )

        month_end = next_month - timedelta(days=1)



        pipeline = [

            {"$match": {

                "emp_code": emp_code,

                "date": {

                    "$gte": month_start.isoformat(),

                    "$lte": month_end.isoformat(),

                },

            }},

            {"$group": {"_id": "$emp_code", "records": {"$sum": 1}}},

        ]

        result = db.command({

            "explain": {

                "aggregate": "attendance_logs",

                "pipeline": pipeline,

                "cursor": {},

            },

            "verbosity": "executionStats",

        })



    elif endpoint == "department_summary":

        if not month:

            raise HTTPException(status_code=422, detail="department_summary requires month=YYYY-MM")

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):

            raise HTTPException(status_code=422, detail="month must use YYYY-MM")



        year_num, month_num = map(int, month.split("-"))

        month_start = Date(year_num, month_num, 1)

        next_month = (

            Date(year_num + 1, 1, 1)

            if month_num == 12

            else Date(year_num, month_num + 1, 1)

        )

        month_end = next_month - timedelta(days=1)



        employee_filter = {"joined_on": {"$lte": month_end.isoformat()}}

        if department:

            employee_filter["department"] = department



        pipeline = [

            {"$match": employee_filter},

            {"$lookup": {

                "from": "attendance_logs",

                "let": {"code": "$emp_code"},

                "pipeline": [

                    {"$match": {"$expr": {"$and": [

                        {"$eq": ["$emp_code", "$$code"]},

                        {"$gte": ["$date", month_start.isoformat()]},

                        {"$lte": ["$date", month_end.isoformat()]},

                    ]}}},

                ],

                "as": "logs",

            }},

            {"$project": {"_id": 0, "emp_code": 1, "department": 1, "logs": 1}},

        ]

        result = db.command({

            "explain": {

                "aggregate": "employees",

                "pipeline": pipeline,

                "cursor": {},

            },

            "verbosity": "executionStats",

        })



    elif endpoint == "late_leaderboard":

        if not month:

            raise HTTPException(status_code=422, detail="late_leaderboard requires month=YYYY-MM")

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):

            raise HTTPException(status_code=422, detail="month must use YYYY-MM")



        year_num, month_num = map(int, month.split("-"))

        month_start = Date(year_num, month_num, 1)

        next_month = (

            Date(year_num + 1, 1, 1)

            if month_num == 12

            else Date(year_num, month_num + 1, 1)

        )

        month_end = next_month - timedelta(days=1)



        pipeline = [

            {"$match": {

                "date": {

                    "$gte": month_start.isoformat(),

                    "$lte": month_end.isoformat(),

                },

                "late_minutes": {"$gt": 0},

            }},

            {"$group": {

                "_id": "$emp_code",

                "total_late_minutes": {"$sum": "$late_minutes"},

            }},

            {"$match": {"total_late_minutes": {"$gt": 0}}},

            {"$sort": {"total_late_minutes": -1}},

            {"$limit": limit},

        ]

        if department:

            pipeline.insert(3, {"$lookup": {

                "from": "employees",

                "localField": "_id",

                "foreignField": "emp_code",

                "as": "employee",

            }})

            pipeline.insert(4, {"$unwind": "$employee"})

            pipeline.insert(5, {"$match": {"employee.department": department}})



        result = db.command({

            "explain": {

                "aggregate": "attendance_logs",

                "pipeline": pipeline,

                "cursor": {},

            },

            "verbosity": "executionStats",

        })



    else:

        if not department or not date_start or not date_end:

            raise HTTPException(

                status_code=422,

                detail="department_trend requires department, from, and to",

            )

        try:

            start_day = Date.fromisoformat(date_start)

            end_day = Date.fromisoformat(date_end)

        except ValueError:

            raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD")

        if start_day.isoformat() != date_start or end_day.isoformat() != date_end:

            raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD")

        if end_day < start_day or (end_day - start_day).days + 1 > 92:

            raise HTTPException(status_code=422, detail="Invalid trend date range")



        trend_pipeline = [

            {"$match": {"department": department, "joined_on": {"$lte": date_end}}},

            {"$lookup": {

                "from": "attendance_logs",

                "let": {"code": "$emp_code"},

                "pipeline": [

                    {"$match": {"$expr": {"$and": [

                        {"$eq": ["$emp_code", "$$code"]},

                        {"$gte": ["$date", date_start]},

                        {"$lte": ["$date", date_end]},

                    ]}}},

                ],

                "as": "logs",

            }},

            {"$project": {"_id": 0, "emp_code": 1, "joined_on": 1, "logs": 1}},

        ]

        result = db.command({

            "explain": {

                "aggregate": "employees",

                "pipeline": trend_pipeline,

                "cursor": {},

            },

            "verbosity": "executionStats",

        })



    return JSONResponse(
        content=json_util.dumps(result),
        media_type="application/json",
    )
