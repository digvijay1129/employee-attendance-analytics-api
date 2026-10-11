# REVIEW.md

## Defects Identified and Fixes

The following issues were identified during implementation and API testing. The exact original line numbers are omitted because the starter file was modified during development.

|#|Where (function / line)|What is wrong|How you'd notice it (test, input, or symptom)|How you fixed it|
|-|-|-|-|-|
|1|`health()`|Returning a successful health response without checking MongoDB can report a healthy service when the database is unavailable.|Make the database unavailable and request `GET /health`.|Added a MongoDB ping and return HTTP 503 when the database check fails.|
|2|Employee email validation|An overly restrictive email-validation expression can reject otherwise valid email addresses.|Submit a valid email to `POST /employees` and observe whether validation incorrectly rejects it.|Replaced the expression with a more suitable basic email-format check.|
|3|Punch-in timestamp validation|Timestamps must be epoch milliseconds, not epoch seconds, strings, or floating-point values.|Submit seconds, a string, a float, or a value outside the allowed range to the punch-in endpoint.|Added strict integer and range validation for epoch-millisecond timestamps.|
|4|`list_attendance()`|Loading all attendance records before filtering or paginating would be inefficient and could return incorrect pagination totals.|Test date/status filters, page boundaries, and invalid date ranges against a larger dataset.|Applied filtering, counting, sorting, skipping, and limiting through MongoDB operations.|
|5|`employee_monthly_analytics()`|Monthly metrics must account for weekdays, employee joining dates, half-days, attendance statuses, and the specified rounding rules.|Test employees with no logs, weekend logs, leave records, and half-day attendance.|Implemented monthly aggregation and calculated working days from the employee's joining date.|
|6|`department_summary()`|Departments must include employees who joined by the reporting period's end, even when they have no attendance records.|Add an eligible employee without attendance logs and request the department summary.|Started the aggregation from employees and looked up their attendance records.|
|7|`late_leaderboard()`|Ranking must handle equal late-minute totals using competition ranking and apply the limit after ranking.|Test tied totals, including ties at the requested ranking limit.|Used MongoDB `$rank` and filtered by rank so tied employees are retained.|
|8|`department_trend()`|The trend must include dates without attendance records and calculate headcount for each date.|Request a date range containing weekends and dates without attendance records.|Added date densification, daily headcount calculation, and trend metrics for the requested range.|
|9|`/admin/explain/{endpoint}`|Explain responses must be serializable and expose execution statistics for the selected query or aggregation.|Request explain output and inspect its response and execution stages.|Returned the explain result as JSON and configured endpoint-specific query plans.|

## Checks Considered

* **Duplicate employee codes:** The unique `employees.emp_code` index prevents duplicates; duplicate creation is handled as HTTP 409.
* **Duplicate daily attendance:** The unique `(emp_code, date)` index prevents two attendance records for the same employee and date.
* **Punch-out without an open record:** The API returns HTTP 404 when no eligible punch-in record exists.
* **Repeated punch-out:** An already-closed attendance record is rejected with HTTP 409.
* **Attendance regularization:** An unchanged update is rejected, and a successful change records its reason, regularizer, timestamp, and changed fields in history.
* **Date-range and pagination validation:** Invalid date ranges and page sizes outside the permitted range are rejected.
* **Database index usage:** Explain checks were run for the required analytics/list endpoints to inspect index usage.

These checks describe the behavior verified during implementation. They should not be interpreted as proof that every listed issue existed in the original starter file; each original defect should be confirmed against the starter implementation before being described as a pre-existing defect.
