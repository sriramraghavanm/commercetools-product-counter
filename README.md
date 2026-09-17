# commercetools Product Counter

A production-oriented Python command-line utility for counting products and product variants in a commercetools Composable Commerce project.

The utility authenticates with the commercetools OAuth 2.0 service using the Client Credentials flow, retrieves products from the commercetools Product API, and reports product and variant totals. It supports both staged and current product data, cursor-style pagination based on product IDs, retry handling for transient GET failures, structured JSON output, and configurable logging for automation and operational use.

## Features

- Counts all products returned by the commercetools Product API.
- Counts master variants and additional product variants separately.
- Reports a combined product-variant count.
- Supports either:
  - `staged` product data, which is the default and is useful for the latest catalog configuration.
  - `current` product data, which represents the currently published product data.
- Uses cursor-style pagination by sorting products by ID and querying for IDs greater than the previous page's final ID.
- Avoids relying on offset pagination limits by continuing until no products remain.
- Requests up to 500 products per page by default, with a configurable page size.
- Retries transient GET failures, including HTTP `429`, `500`, `502`, `503`, and `504` responses.
- Respects the server's `Retry-After` header when retrying rate-limited requests.
- Produces machine-readable JSON or human-readable text output.
- Optionally writes results atomically to an output file.
- Validates required environment variables, HTTPS endpoint URLs, timeout values, API responses, and pagination progress.
- Sends application logs to `stderr`, keeping `stdout` clean for count results and pipeline consumption.

> **Scope note:** This utility currently counts products and variants across the project. It does not implement category-based filtering, inventory-sync checks, or inventory quantity reconciliation.

## Prerequisites

- Python 3.10 or newer. The code uses modern type-hint syntax such as `str | None`.
- A commercetools Composable Commerce project.
- Access to the commercetools Merchant Center or equivalent project administration capability to create and manage API clients.
- A commercetools API client with:
  - A client ID.
  - A client secret.
  - Permission to obtain an OAuth access token.
  - The required product-view scope for the selected project data.
- Network access from the execution environment to the commercetools OAuth and HTTP API endpoints for the project's region.
- A shell capable of running Python commands.

## Environment Configuration

Copy the example file to `.env` and replace the placeholder values with credentials and endpoints for your commercetools project:

```dotenv
# commercetools project identifier
CTP_PROJECT_KEY=your-project-key

# OAuth API client credentials
CTP_CLIENT_ID=your-client-id
CTP_CLIENT_SECRET=your-client-secret

# Use the URLs associated with your project's region.
# Do not append /oauth/token to CTP_AUTH_URL.
CTP_AUTH_URL=https://auth.us-central1.gcp.commercetools.com
CTP_API_URL=https://api.us-central1.gcp.commercetools.com

# Required when retrieving staged product data
CTP_SCOPE=view_products:your-project-key

# Per-request HTTP timeout, in seconds
CTP_HTTP_TIMEOUT_SECONDS=30
```

### Configuration variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `CTP_PROJECT_KEY` | Yes | — | commercetools project key used in Product API requests. |
| `CTP_CLIENT_ID` | Yes | — | OAuth API client ID. |
| `CTP_CLIENT_SECRET` | Yes | — | OAuth API client secret. Keep this value private. |
| `CTP_AUTH_URL` | Yes | — | HTTPS base URL for the project's commercetools authorization service. Do not include `/oauth/token`. |
| `CTP_API_URL` | Yes | — | HTTPS base URL for the project's commercetools HTTP API. |
| `CTP_SCOPE` | No | None | OAuth scope sent when requesting a token. The example uses `view_products:<project-key>`. |
| `CTP_HTTP_TIMEOUT_SECONDS` | No | `30` | Positive per-request timeout in seconds. |

The application loads variables from the process environment and from a local `.env` file through `python-dotenv`. Do not commit `.env` or any credential-containing file; `.env` is excluded by `.gitignore`.

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/sriramraghavanm/commercetools-product-counter.git
   cd commercetools-product-counter
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell, use:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the pinned dependency ranges:

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Create the local environment file and configure it:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and provide valid commercetools project credentials and regional URLs.

## Usage

Run the script with its default behavior. It uses staged product data, requests up to 500 products per page, and prints JSON to standard output:

```bash
python commercetools_counter.py
```

The script can also be invoked directly on Unix-like systems:

```bash
chmod +x commercetools_counter.py
./commercetools_counter.py
```

### Common commands

Count currently published product data:

```bash
python commercetools_counter.py --catalog-data current
```

Use a smaller page size:

```bash
python commercetools_counter.py --page-size 100
```

Print human-readable text instead of JSON:

```bash
python commercetools_counter.py --output-format text
```

Write the result to a file while also printing it:

```bash
python commercetools_counter.py --output-file output.json
```

Enable detailed diagnostic logging:

```bash
python commercetools_counter.py --log-level DEBUG
```

Combine options for an automation-friendly run:

```bash
python commercetools_counter.py \
  --catalog-data staged \
  --page-size 500 \
  --output-format json \
  --output-file output.json \
  --log-level INFO
```

### JSON output

A successful JSON response includes fields similar to:

```json
{
  "project_key": "your-project-key",
  "catalog_data": "staged",
  "product_count": 1250,
  "variant_count": 3875,
  "master_variant_count": 1250,
  "additional_variant_count": 2625,
  "products_without_selected_data": 0,
  "pages_processed": 3
}
```

The exact values depend on the contents of the commercetools project at execution time.

### Exit codes

| Exit code | Meaning |
| ---: | --- |
| `0` | Counting completed successfully. |
| `1` | Unexpected application error. |
| `2` | Configuration error, such as a missing variable or invalid URL. |
| `3` | commercetools authentication or API error. |
| `4` | Local file-system error while writing output. |
| `130` | The operation was interrupted with `Ctrl+C`. |

## Project Structure

```text
commercetools-product-counter/
├── .env.example              # Example commercetools and HTTP configuration
├── .gitignore                # Excludes secrets, virtual environments, caches, logs, and output files
├── commercetools_counter.py   # CLI entry point, OAuth flow, API pagination, counting, output, and logging
├── requirements.txt           # Runtime dependencies
└── README.md                  # Project documentation
```

### Runtime flow

1. `load_configuration()` loads and validates environment variables and the optional `.env` file.
2. `create_http_session()` configures connection pooling and retry behavior for safe GET requests.
3. `obtain_access_token()` requests an OAuth 2.0 access token with the Client Credentials grant.
4. `count_products_and_variants()` retrieves products from `/{project-key}/products`, processes the selected `masterData` representation, and advances through pages using the last product ID as the cursor.
5. `write_output()` emits JSON or text and optionally writes the result to a temporary file before atomically replacing the requested output path.
6. `main()` maps configuration, API, file-system, interruption, and unexpected failures to documented exit codes.

## Error Handling and Logs

### Authentication and configuration errors

The application fails fast when required variables are missing or empty. `CTP_AUTH_URL` and `CTP_API_URL` must be valid HTTPS base URLs, and `CTP_HTTP_TIMEOUT_SECONDS` must be a positive integer.

OAuth failures are reported as commercetools API errors. The token request uses the configured client credentials and optional scope. Non-success responses, invalid JSON responses, or responses without a valid `access_token` are rejected with a clear error message and exit code `3`.

### Rate limiting and transient API failures

Product GET requests use a `requests` retry policy for connection failures, read failures, and HTTP `429`, `500`, `502`, `503`, and `504` responses. Retries use exponential backoff and honor the response's `Retry-After` header when provided. OAuth token POST requests are handled separately and are not automatically retried as part of the GET retry policy.

If retries are exhausted or the API returns another unsuccessful response, the program raises a `CommercetoolsAPIError`, logs the request method, URL, status code, and a safely truncated response body, and exits with code `3`.

### Logging behavior

Logs are emitted to `stderr` using timestamped records. Standard output remains reserved for the requested count result, which makes the command suitable for shell pipelines and automation. Log levels are controlled with `--log-level` and support `DEBUG`, `INFO`, `WARNING`, and `ERROR`.

Warnings are emitted when a product does not contain the selected `masterData.current` or `masterData.staged` representation. The product is counted, but its variants are not included for the missing representation.

## Security Considerations

- Never commit `.env`, client secrets, access tokens, or generated output containing sensitive data.
- Use a least-privilege commercetools API client with only the product permissions required by the operation.
- Use the authorization and API URLs for the same commercetools region as the project.
- Treat logs and output files as potentially sensitive because they include project identifiers and catalog metrics.
- Rotate API client credentials according to your organization's security policy.

## Dependencies

- [`requests`](https://pypi.org/project/requests/) for HTTP communication, sessions, connection pooling, and retry adapters.
- [`python-dotenv`](https://pypi.org/project/python-dotenv/) for loading local environment configuration from `.env` files.

## License

No license file is currently included in the repository. Add a license before distributing or accepting external contributions if this project is intended to be reused as open-source software.
