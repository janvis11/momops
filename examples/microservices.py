from momops import mom

app = mom("I need scalable microservices with a database and async jobs", dry_run=True)
print(app.preview())
print(app.project_cost(months=6))
