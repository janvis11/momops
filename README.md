# momops

momops is a python tool that turns plain english into cloud infrastructure.

you say what you need, like `i need an api with postgres`, and momops turns it into a safe aws deployment plan with a cost estimate before anything is deployed.

## what problem it solves

deploying cloud infrastructure is still too hard for small teams.

to ship a normal app, developers often need to understand vpcs, load balancers, databases, backups, iam, ssl, monitoring, pricing, and rollback.

momops makes that first step simple:

- describe the app in normal language
- see the expected monthly cost
- get a production-minded architecture
- validate security defaults
- dry-run or deploy from python or the cli

## features

- natural language infrastructure planning
- aws recipes for apis, blogs, static sites, realtime apps, ml endpoints, databases, ecommerce, and microservices
- cost preview before deployment
- budget guardrails
- security defaults for ssl, backups, vpc isolation, monitoring, encryption, and least-privilege iam
- dry-run mode that touches no cloud resources
- async deployment progress events
- automatic rollback flow on failure
- local deployment state in `~/.momops`
- cli commands for `preview`, `deploy`, `list`, `update`, `status`, `logs`, `destroy`, `auth`, and `talk`
- claude-powered parsing and optimization when `anthropic_api_key` is set
- local fallback parser for tests, demos, and offline previews

## tech stack

momops uses a modern stack chosen to stay useful through 2027-28:

- python 3.12+ with async-first code
- claude sonnet for intent parsing and optimization
- pydantic v2 and pydantic-settings for typed models and config
- boto3 and aioboto3 for aws
- typer and rich for the cli
- httpx, anyio, and asyncio for network and async workflows
- uv for packaging and dependency management
- pytest, pytest-asyncio, moto, ruff, and mypy for testing and quality

## how to use

install momops:

```bash
pip install momops
```

for local development:

```bash
uv sync --extra dev
```

preview a deployment:

```bash
momops preview "i need a startup api with postgres and login"
```

run a safe dry-run:

```bash
momops deploy "i need a blog with images" --dry-run --yes
```

use it from python:

```python
from momops import mom

app = mom("i need a startup api with postgres and login", dry_run=True)

cost = app.preview()
print(cost)

app.dry_run()
```

optional environment variables:

```bash
anthropic_api_key=...
aws_access_key_id=...
aws_secret_access_key=...
aws_default_region=us-east-1
momops_budget_limit=100
momops_dry_run=true
```

run tests:

```bash
python -m pytest
```
