#!/usr/bin/env python3

"""
Count products and variants in a commercetools project.

The program:
  1. Obtains an OAuth 2.0 access token using Client Credentials.
  2. Retrieves all products using cursor-based pagination.
  3. Counts the master variant and additional variants.
  4. Supports current or staged product data.
  5. Retries transient HTTP failures.
  6. Produces JSON output suitable for automation.

A variant count includes:
  - The masterVariant of each product
  - Every variant in the variants array
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger("commercetools_counter")

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 500
DEFAULT_TIMEOUT_SECONDS = 30


class ConfigurationError(Exception):
    """Raised when required application configuration is invalid."""


class CommercetoolsAPIError(Exception):
    """Raised when a commercetools API operation fails."""


@dataclass(frozen=True)
class Configuration:
    project_key: str
    client_id: str
    client_secret: str
    auth_url: str
    api_url: str
    scope: str | None
    timeout_seconds: int


@dataclass
class CountResult:
    project_key: str
    catalog_data: str
    product_count: int = 0
    variant_count: int = 0
    master_variant_count: int = 0
    additional_variant_count: int = 0
    products_without_selected_data: int = 0
    pages_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_environment_variable(name: str) -> str:
    """
    Return a required environment variable.

    Raises:
        ConfigurationError: If the variable is missing or empty.
    """
    value = os.getenv(name, "").strip()

    if not value:
        raise ConfigurationError(
            f"Required environment variable '{name}' is not configured."
        )

    return value


def normalize_base_url(value: str, variable_name: str) -> str:
    """
    Validate and normalize an HTTPS base URL.
    """
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)

    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigurationError(
            f"{variable_name} must be a valid HTTPS URL. Received: {value!r}"
        )

    return normalized


def load_configuration() -> Configuration:
    """
    Load configuration from environment variables or a local .env file.
    """
    load_dotenv()

    timeout_text = os.getenv(
        "CTP_HTTP_TIMEOUT_SECONDS",
        str(DEFAULT_TIMEOUT_SECONDS),
    ).strip()

    try:
        timeout_seconds = int(timeout_text)
    except ValueError as exc:
        raise ConfigurationError(
            "CTP_HTTP_TIMEOUT_SECONDS must be an integer."
        ) from exc

    if timeout_seconds <= 0:
        raise ConfigurationError(
            "CTP_HTTP_TIMEOUT_SECONDS must be greater than zero."
        )

    scope = os.getenv("CTP_SCOPE", "").strip() or None

    return Configuration(
        project_key=require_environment_variable("CTP_PROJECT_KEY"),
        client_id=require_environment_variable("CTP_CLIENT_ID"),
        client_secret=require_environment_variable("CTP_CLIENT_SECRET"),
        auth_url=normalize_base_url(
            require_environment_variable("CTP_AUTH_URL"),
            "CTP_AUTH_URL",
        ),
        api_url=normalize_base_url(
            require_environment_variable("CTP_API_URL"),
            "CTP_API_URL",
        ),
        scope=scope,
        timeout_seconds=timeout_seconds,
    )


def create_http_session() -> requests.Session:
    """
    Create an HTTP session with connection pooling and retry handling.

    Retries are limited to safe/idempotent GET requests. OAuth token POST
    requests are handled separately to avoid uncontrolled POST retries.
    """
    retry_policy = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=10,
        pool_maxsize=10,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "commercetools-product-counter/1.0",
        }
    )

    return session


def extract_error_message(response: requests.Response) -> str:
    """
    Extract a readable and safely truncated error from an HTTP response.
    """
    try:
        body = response.json()
    except ValueError:
        body = response.text[:1000]

    return (
        f"HTTP {response.status_code} returned by "
        f"{response.request.method} {response.url}: {body}"
    )


def obtain_access_token(
    session: requests.Session,
    config: Configuration,
) -> str:
    """
    Obtain a commercetools OAuth access token using Client Credentials.
    """
    token_url = f"{config.auth_url}/oauth/token"

    form_data = {
        "grant_type": "client_credentials",
    }

    if config.scope:
        form_data["scope"] = config.scope

    LOGGER.info("Requesting OAuth access token")

    try:
        response = session.post(
            token_url,
            auth=(config.client_id, config.client_secret),
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise CommercetoolsAPIError(
            f"Unable to connect to the commercetools authorization service: {exc}"
        ) from exc

    if not response.ok:
        raise CommercetoolsAPIError(extract_error_message(response))

    try:
        response_body = response.json()
    except ValueError as exc:
        raise CommercetoolsAPIError(
            "Authorization service returned invalid JSON."
        ) from exc

    access_token = response_body.get("access_token")

    if not isinstance(access_token, str) or not access_token:
        raise CommercetoolsAPIError(
            "Authorization response did not contain a valid access_token."
        )

    LOGGER.info("OAuth access token obtained successfully")
    return access_token


def count_variants_in_product_data(
    product_data: dict[str, Any],
) -> tuple[int, int]:
    """
    Count master and additional variants in ProductData.

    Returns:
        A tuple containing:
          (master_variant_count, additional_variant_count)
    """
    master_variant = product_data.get("masterVariant")
    variants = product_data.get("variants", [])

    master_count = 1 if isinstance(master_variant, dict) else 0
    additional_count = len(variants) if isinstance(variants, list) else 0

    return master_count, additional_count


def count_products_and_variants(
    session: requests.Session,
    config: Configuration,
    access_token: str,
    catalog_data: str,
    page_size: int,
) -> CountResult:
    """
    Retrieve all products and calculate product and variant counts.

    Cursor-based pagination is implemented by sorting on product ID and
    querying for IDs greater than the last product ID from the previous page.
    This avoids the API offset pagination limit.
    """
    endpoint = f"{config.api_url}/{config.project_key}/products"

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    result = CountResult(
        project_key=config.project_key,
        catalog_data=catalog_data,
    )

    last_product_id: str | None = None

    while True:
        params: list[tuple[str, str | int | bool]] = [
            ("limit", page_size),
            ("sort", "id asc"),
            ("withTotal", "false"),
        ]

        if last_product_id is not None:
            params.append(
                (
                    "where",
                    f'id > "{last_product_id}"',
                )
            )

        try:
            response = session.get(
                endpoint,
                headers=headers,
                params=params,
                timeout=config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CommercetoolsAPIError(
                f"Could not retrieve products: {exc}"
            ) from exc

        if not response.ok:
            raise CommercetoolsAPIError(extract_error_message(response))

        try:
            response_body = response.json()
        except ValueError as exc:
            raise CommercetoolsAPIError(
                "Products API returned invalid JSON."
            ) from exc

        products = response_body.get("results")

        if not isinstance(products, list):
            raise CommercetoolsAPIError(
                "Products API response does not contain a valid results array."
            )

        if not products:
            break

        result.pages_processed += 1

        for product in products:
            if not isinstance(product, dict):
                raise CommercetoolsAPIError(
                    "Products API returned an invalid product object."
                )

            product_id = product.get("id")

            if not isinstance(product_id, str) or not product_id:
                raise CommercetoolsAPIError(
                    "A product was returned without a valid ID."
                )

            result.product_count += 1

            master_data = product.get("masterData", {})
            selected_product_data = master_data.get(catalog_data)

            if not isinstance(selected_product_data, dict):
                result.products_without_selected_data += 1
                LOGGER.warning(
                    "Product %s does not contain masterData.%s",
                    product_id,
                    catalog_data,
                )
                continue

            master_count, additional_count = count_variants_in_product_data(
                selected_product_data
            )

            result.master_variant_count += master_count
            result.additional_variant_count += additional_count
            result.variant_count += master_count + additional_count

        newest_product_id = products[-1].get("id")

        if not isinstance(newest_product_id, str) or not newest_product_id:
            raise CommercetoolsAPIError(
                "Unable to determine the pagination cursor."
            )

        if newest_product_id == last_product_id:
            raise CommercetoolsAPIError(
                "Pagination cursor did not advance. Processing stopped "
                "to prevent an infinite loop."
            )

        last_product_id = newest_product_id

        LOGGER.info(
            "Processed page %d: products=%d, variants=%d",
            result.pages_processed,
            result.product_count,
            result.variant_count,
        )

        if len(products) < page_size:
            break

    return result


def write_output(
    result: CountResult,
    output_format: str,
    output_file: str | None,
) -> None:
    """
    Print the result and optionally write it to a file.
    """
    result_dictionary = result.to_dict()

    if output_format == "json":
        output = json.dumps(result_dictionary, indent=2)
    else:
        output = (
            f"Project key: {result.project_key}\n"
            f"Catalog data: {result.catalog_data}\n"
            f"Product count: {result.product_count}\n"
            f"Variant count: {result.variant_count}\n"
            f"Master variants: {result.master_variant_count}\n"
            f"Additional variants: {result.additional_variant_count}\n"
            f"Products without selected data: "
            f"{result.products_without_selected_data}\n"
            f"Pages processed: {result.pages_processed}"
        )

    print(output)

    if output_file:
        temporary_file = f"{output_file}.tmp"

        with open(temporary_file, "w", encoding="utf-8") as file_handle:
            file_handle.write(output)
            file_handle.write("\n")

        os.replace(temporary_file, output_file)
        LOGGER.info("Results written to %s", output_file)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Count products and variants in a commercetools project."
        )
    )

    parser.add_argument(
        "--catalog-data",
        choices=("current", "staged"),
        default="staged",
        help=(
            "Product data representation to count. "
            "Use 'staged' for the latest catalog configuration or "
            "'current' for the current published representation. "
            "Default: staged."
        ),
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=(
            f"Products requested per API call. Maximum: {MAX_PAGE_SIZE}. "
            f"Default: {DEFAULT_PAGE_SIZE}."
        ),
    )

    parser.add_argument(
        "--output-format",
        choices=("json", "text"),
        default="json",
        help="Output format. Default: json.",
    )

    parser.add_argument(
        "--output-file",
        help="Optional file to which the result should be written.",
    )

    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Application logging level. Default: INFO.",
    )

    arguments = parser.parse_args()

    if arguments.page_size < 1 or arguments.page_size > MAX_PAGE_SIZE:
        parser.error(
            f"--page-size must be between 1 and {MAX_PAGE_SIZE}."
        )

    return arguments


def configure_logging(log_level: str) -> None:
    """
    Configure application logging.

    Logs go to stderr so that stdout contains only the count result.
    """
    logging.basicConfig(
        level=getattr(logging, log_level),
        format=(
            "%(asctime)s %(levelname)s %(name)s "
            "%(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )


def main() -> int:
    arguments = parse_arguments()
    configure_logging(arguments.log_level)

    started_at = time.monotonic()

    try:
        config = load_configuration()
        session = create_http_session()

        try:
            access_token = obtain_access_token(session, config)

            result = count_products_and_variants(
                session=session,
                config=config,
                access_token=access_token,
                catalog_data=arguments.catalog_data,
                page_size=arguments.page_size,
            )
        finally:
            session.close()

        write_output(
            result=result,
            output_format=arguments.output_format,
            output_file=arguments.output_file,
        )

        LOGGER.info(
            "Counting completed in %.2f seconds",
            time.monotonic() - started_at,
        )

        return 0

    except ConfigurationError as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2

    except CommercetoolsAPIError as exc:
        LOGGER.error("commercetools API error: %s", exc)
        return 3

    except OSError as exc:
        LOGGER.error("File system error: %s", exc)
        return 4

    except KeyboardInterrupt:
        LOGGER.warning("Operation interrupted")
        return 130

    except Exception:
        LOGGER.exception("Unexpected application error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
