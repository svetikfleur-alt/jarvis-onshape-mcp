# Onshape MCP Server - Test Suite

Comprehensive unit test suite for the Onshape MCP server.

## Test Structure

```
tests/
├── conftest.py           # Shared fixtures and pytest configuration
├── api/                  # API layer tests
│   ├── test_client.py    # OnshapeClient HTTP operations
│   ├── test_partstudio.py # Part Studio manager tests
│   └── test_variables.py # Variable manager tests
└── builders/             # Builder pattern tests
    ├── test_sketch.py    # Sketch builder tests
    └── test_extrude.py   # Extrude builder tests
```

## Running Tests

### Quick Start

```bash
# Install dependencies
make install

# Run all tests
make test

# Run with coverage
make test-cov

# Run unit tests only
make test-unit
```

### Using pytest directly

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/api/test_client.py

# Run specific test class
pytest tests/api/test_client.py::TestOnshapeClient

# Run specific test method
pytest tests/api/test_client.py::TestOnshapeClient::test_get_request_success

# Run tests matching pattern
pytest -k "test_add_rectangle"

# Run with coverage
pytest --cov=onshape_mcp --cov-report=html
```

## Test Categories

Tests are marked with the following markers:

- `@pytest.mark.asyncio` - Async tests
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slow running tests

### Run tests by category

```bash
# Run only async tests
pytest -m asyncio

# Skip slow tests
pytest -m "not slow"

# Run unit tests only
pytest -m unit
```

## Coverage Reports

### Generate HTML coverage report

```bash
make coverage-html
# Open htmlcov/index.html in browser
```

### Generate XML coverage report (for CI)

```bash
make coverage-xml
```

### Coverage thresholds

The test suite enforces a minimum coverage of **80%**. Tests will fail if coverage falls below this threshold.

## Writing Tests

### Test Structure

All tests follow the Arrange-Act-Assert (AAA) pattern:

```python
def test_example():
    # Arrange - Set up test data and mocks
    client = OnshapeClient(credentials)

    # Act - Execute the code being tested
    result = client.do_something()

    # Assert - Verify the results
    assert result == expected_value
```

### Using Fixtures

Common fixtures are defined in `conftest.py`:

```python
def test_with_fixtures(onshape_client, sample_document_ids):
    """Test using shared fixtures."""
    result = onshape_client.get_features(**sample_document_ids)
    assert result is not None
```

### Async Tests

Use `@pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_async_operation(onshape_client):
    """Test async operation."""
    result = await onshape_client.get("/api/test")
    assert result["success"] is True
```

### Mocking HTTP Requests

Use the `mock_httpx_client` fixture for mocking HTTP responses:

```python
@pytest.mark.asyncio
async def test_api_call(onshape_client, mock_httpx_client):
    """Test API call with mocked response."""
    mock_response = Mock()
    mock_response.json.return_value = {"data": "test"}
    mock_httpx_client.get.return_value = mock_response

    result = await onshape_client.get("/api/endpoint")
    assert result["data"] == "test"
```

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pip install -e ".[dev]"
    pytest --cov=onshape_mcp --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

## Test Coverage by Module

| Module | Coverage | Tests |
|--------|----------|-------|
| api/client.py | ~95% | 15 tests |
| api/partstudio.py | ~90% | 9 tests |
| api/variables.py | ~90% | 12 tests |
| builders/sketch.py | ~95% | 20 tests |
| builders/extrude.py | ~95% | 18 tests |

## Troubleshooting

### Tests failing with import errors

```bash
# Ensure package is installed in editable mode
pip install -e .
```

### Coverage report not generating

```bash
# Clean cache and regenerate
make clean
pytest --cov=onshape_mcp --cov-report=html
```

### Async tests not running

```bash
# Ensure pytest-asyncio is installed
pip install pytest-asyncio
```

## Best Practices

1. **Test isolation** - Each test should be independent
2. **Mock external dependencies** - Use fixtures for HTTP clients
3. **Descriptive names** - Test names should describe what they test
4. **One assertion per test** - Keep tests focused
5. **Arrange-Act-Assert** - Follow AAA pattern
6. **Edge cases** - Test boundary conditions and error cases
7. **Coverage** - Maintain 80%+ code coverage

## Adding New Tests

When adding new functionality:

1. Write tests first (TDD approach)
2. Create test file matching module name: `test_<module>.py`
3. Add fixtures to `conftest.py` if reusable
4. Update this README with new test information
5. Ensure coverage stays above 80%

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [coverage.py documentation](https://coverage.readthedocs.io/)

## Offline and Local-only Live Command Contract

Normal pytest and CI are credential-free and offline. Use these exact commands;
coverage is explicit rather than part of pytest's defaults.

```bash
# Fast offline suite
python -m pytest -m "not live_onshape" -q --maxfail=1

# Focused harness policy tests
python -m pytest tests/test_test_harness.py -q --maxfail=1

# Explicit offline branch-coverage gate
python -m pytest -m "not live_onshape" --cov=onshape_mcp --cov-branch --cov-report=term-missing --cov-fail-under=80
```

Local live execution requires exactly one complete credential pair:
`ONSHAPE_API_KEY` with `ONSHAPE_API_SECRET`, or `ONSHAPE_ACCESS_KEY` with
`ONSHAPE_SECRET_KEY`. It also requires `JARVIS_LIVE_DOCUMENT_ID`,
`JARVIS_LIVE_WORKSPACE_ID`, and `JARVIS_LIVE_ELEMENT_ID`. Replace all
angle-bracket placeholders; neither helper prints a secret or ID. The exact
budgets are 3 physical sends for read-only tests, 8 for mutation tests, and
`JARVIS_LIVE_SUITE_BUDGET=30` for the suite. `live_budget(N)` can only lower a
per-test limit. Keep execution serial: the only network permit is scoped to the
serial guarded transport.

Define this fail-fast Bash helper:

```bash
run_local_live() (
  set -euo pipefail
  marker=$1
  mutation_opt_in=$2
  api_pair=0
  access_pair=0

  if [[ -n "${ONSHAPE_API_KEY:-}" || -n "${ONSHAPE_API_SECRET:-}" ]]; then
    [[ -n "${ONSHAPE_API_KEY:-}" && -n "${ONSHAPE_API_SECRET:-}" ]] || {
      echo "The API credential pair is incomplete" >&2; exit 2;
    }
    api_pair=1
  fi
  if [[ -n "${ONSHAPE_ACCESS_KEY:-}" || -n "${ONSHAPE_SECRET_KEY:-}" ]]; then
    [[ -n "${ONSHAPE_ACCESS_KEY:-}" && -n "${ONSHAPE_SECRET_KEY:-}" ]] || {
      echo "The access credential pair is incomplete" >&2; exit 2;
    }
    access_pair=1
  fi
  [[ $((api_pair + access_pair)) -eq 1 ]] || {
    echo "Configure exactly one complete credential pair" >&2; exit 2;
  }
  for name in JARVIS_LIVE_DOCUMENT_ID JARVIS_LIVE_WORKSPACE_ID JARVIS_LIVE_ELEMENT_ID; do
    [[ -n "${!name:-}" ]] || { echo "Missing required live sandbox ID" >&2; exit 2; }
  done

  JARVIS_LIVE_TESTS=1 JARVIS_LIVE_MUTATIONS="$mutation_opt_in" JARVIS_LIVE_SUITE_BUDGET=30 \
    python -m pytest -m "$marker" -q --maxfail=1
)
```

Run exactly one command; its assignments and opt-in flags are command-scoped:

```bash
# Read-only
ONSHAPE_API_KEY='<access-key>' ONSHAPE_API_SECRET='<secret-key>' \
JARVIS_LIVE_DOCUMENT_ID='<document-id>' JARVIS_LIVE_WORKSPACE_ID='<workspace-id>' \
JARVIS_LIVE_ELEMENT_ID='<element-id>' \
run_local_live 'live_onshape and live_readonly' 0

# Mutation harness contract (no WP-003 scenario is collected)
ONSHAPE_API_KEY='<access-key>' ONSHAPE_API_SECRET='<secret-key>' \
JARVIS_LIVE_DOCUMENT_ID='<document-id>' JARVIS_LIVE_WORKSPACE_ID='<workspace-id>' \
JARVIS_LIVE_ELEMENT_ID='<element-id>' \
run_local_live 'live_onshape and live_mutation' 1
```

For PowerShell, the helper restores all process variables in `finally`, checks
every prerequisite, and propagates pytest's nonzero `$LASTEXITCODE`:

```powershell
function Invoke-LocalLive {
  param(
    [string]$Marker, [string]$MutationOptIn,
    [string]$ApiKey, [string]$ApiSecret, [string]$AccessKey, [string]$SecretKey,
    [string]$DocumentId, [string]$WorkspaceId, [string]$ElementId
  )
  $names = @(
    'ONSHAPE_API_KEY', 'ONSHAPE_API_SECRET', 'ONSHAPE_ACCESS_KEY', 'ONSHAPE_SECRET_KEY',
    'JARVIS_LIVE_DOCUMENT_ID', 'JARVIS_LIVE_WORKSPACE_ID', 'JARVIS_LIVE_ELEMENT_ID',
    'JARVIS_LIVE_TESTS', 'JARVIS_LIVE_MUTATIONS', 'JARVIS_LIVE_SUITE_BUDGET'
  )
  $prior = @{}
  foreach ($name in $names) { $prior[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
  $exitCode = 1
  try {
    $env:ONSHAPE_API_KEY = $ApiKey; $env:ONSHAPE_API_SECRET = $ApiSecret
    $env:ONSHAPE_ACCESS_KEY = $AccessKey; $env:ONSHAPE_SECRET_KEY = $SecretKey
    $env:JARVIS_LIVE_DOCUMENT_ID = $DocumentId; $env:JARVIS_LIVE_WORKSPACE_ID = $WorkspaceId
    $env:JARVIS_LIVE_ELEMENT_ID = $ElementId
    $apiComplete = -not [string]::IsNullOrWhiteSpace($env:ONSHAPE_API_KEY) -and -not [string]::IsNullOrWhiteSpace($env:ONSHAPE_API_SECRET)
    $accessComplete = -not [string]::IsNullOrWhiteSpace($env:ONSHAPE_ACCESS_KEY) -and -not [string]::IsNullOrWhiteSpace($env:ONSHAPE_SECRET_KEY)
    if ([int]$apiComplete + [int]$accessComplete -ne 1) { throw 'Configure exactly one complete credential pair' }
    foreach ($name in @('JARVIS_LIVE_DOCUMENT_ID','JARVIS_LIVE_WORKSPACE_ID','JARVIS_LIVE_ELEMENT_ID')) {
      if ([string]::IsNullOrWhiteSpace((Get-Item "Env:$name").Value)) { throw 'Missing required live sandbox ID' }
    }
    $env:JARVIS_LIVE_TESTS = '1'; $env:JARVIS_LIVE_MUTATIONS = $MutationOptIn
    $env:JARVIS_LIVE_SUITE_BUDGET = '30'
    python -m pytest -m $Marker -q --maxfail=1
    $exitCode = $LASTEXITCODE
  } finally {
    foreach ($name in $names) {
      if ($null -eq $prior[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
      else { Set-Item "Env:$name" $prior[$name] }
    }
  }
  if ($exitCode -ne 0) { exit $exitCode }
}
```

```powershell
# Read-only
Invoke-LocalLive -Marker 'live_onshape and live_readonly' -MutationOptIn '0' `
  -ApiKey '<access-key>' -ApiSecret '<secret-key>' -DocumentId '<document-id>' `
  -WorkspaceId '<workspace-id>' -ElementId '<element-id>'

# Mutation harness contract (no WP-003 scenario is collected)
Invoke-LocalLive -Marker 'live_onshape and live_mutation' -MutationOptIn '1' `
  -ApiKey '<access-key>' -ApiSecret '<secret-key>' -DocumentId '<document-id>' `
  -WorkspaceId '<workspace-id>' -ElementId '<element-id>'
```

WP-003 ships no live mutation scenario. Mutation gating exists only as a
harness contract; WP-003 does not collect or run a mutating live test.
