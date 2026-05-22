from momops import mom

app = mom("I need a hobby blog with images")
print("Cost Preview:")
print(app.preview())

print("\nDeploying...")
deployed = app.deploy()
print(f"Live at: {deployed.endpoint}")
