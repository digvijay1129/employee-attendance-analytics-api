# Implementation Decisions

1. **Indexes.** I created a unique index on `employees.emp_code` to prevent duplicate employee codes and a unique compound index on `attendance_logs.emp_code` and `date` to prevent duplicate daily attendance. I added `employees.department` with `joined_on` for department headcount queries and `attendance_logs.date` with `late_minutes` for date-filtered late-arrival analytics. A separate status index can be considered if query plans and workload measurements justify it.

2. **Punch-in race.** If two identical punch-in requests arrive together, both may find the employee and attempt an insert. The unique `(emp_code, date)` index allows only one insert; the other raises `DuplicateKeyError`, which the API converts to HTTP 409.

3. **Ties.** The leaderboard uses MongoDB `$rank` on total late minutes. Employees tied at the cutoff share the same rank, and everyone whose rank is within the requested limit is returned, even if the result contains more rows than the limit.

4. **Headcount.** Department summary starts from employees and looks up their attendance records. Employees without attendance logs still contribute to headcount.

5. **Scaling.** With 100 times more data, I would measure query workload, inspect execution plans, and benchmark indexes before adding more. I would consider pre-aggregating frequently requested monthly analytics if live aggregation became too expensive.