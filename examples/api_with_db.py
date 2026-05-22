from momops import mom

app = mom("I need a startup API with Postgres, login, and a $120 monthly budget", dry_run=True)
print(app.preview())
print(app.validate_all())
