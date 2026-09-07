# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Contract tests for the hand-authored openapi.yaml.

The spec is written by hand rather than generated, so it can drift from the code.
These tests make drift a build failure: every route the app serves must appear in
the document, and every path in the document must be served by the app.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from app.config import Settings
from app.main import create_app

SPEC_PATH = pathlib.Path(__file__).resolve().parents[2] / "openapi.yaml"

#: Routes that reach GitHub, and therefore depend on the server-side token.
GITHUB_BACKED = {
    ("/issues", "post"),
    ("/issues", "get"),
    ("/issues/{number}", "get"),
    ("/issues/{number}", "patch"),
    ("/issues/{number}/comments", "post"),
    ("/issues/{number}/comments", "get"),
}


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text())
    return loaded


@pytest.fixture(scope="module")
def spec_operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    return {
        (path, method)
        for path, item in spec["paths"].items()
        for method in item
        if method in methods
    }


@pytest.fixture(scope="module")
def app_operations() -> set[tuple[str, str]]:
    """Routes the app actually serves.

    Read from FastAPI's generated schema rather than by walking ``app.routes``:
    recent FastAPI versions wrap included routers in an internal ``_IncludedRouter``
    object, so a naive walk silently sees only the top-level routes — and a drift
    test that silently sees nothing is worse than no drift test at all.
    """
    app = create_app(
        Settings(
            github_token="t",  # noqa: S106
            github_owner="o",
            github_repo="r",
            webhook_secret="s",  # noqa: S106
        )
    )
    generated = app.openapi()["paths"]
    return {(path, method) for path, item in generated.items() for method in item}


# ------------------------------------------------------------------- validity


def test_the_document_is_a_valid_openapi_spec() -> None:
    spec_dict, base_uri = read_from_filename(str(SPEC_PATH))
    validate(spec_dict, base_uri=base_uri)


def test_the_document_declares_openapi_3_1(spec: dict[str, Any]) -> None:
    assert spec["openapi"].startswith("3.1")


# --------------------------------------------------------------------- parity


def test_every_served_route_is_documented(
    app_operations: set[tuple[str, str]], spec_operations: set[tuple[str, str]]
) -> None:
    undocumented = app_operations - spec_operations
    assert not undocumented, f"routes missing from openapi.yaml: {sorted(undocumented)}"


def test_every_documented_route_is_served(
    app_operations: set[tuple[str, str]], spec_operations: set[tuple[str, str]]
) -> None:
    unimplemented = spec_operations - app_operations
    assert not unimplemented, (
        f"openapi.yaml documents routes the app does not serve: {sorted(unimplemented)}"
    )


def test_all_nine_operations_are_present(spec_operations: set[tuple[str, str]]) -> None:
    """A guard against a route quietly disappearing from both sides at once."""
    assert len(spec_operations) == 9


# ------------------------------------------------------------------ structure


def test_every_operation_has_an_id_summary_and_a_success_response(
    spec: dict[str, Any], spec_operations: set[tuple[str, str]]
) -> None:
    for path, method in sorted(spec_operations):
        operation = spec["paths"][path][method]
        assert operation.get("operationId"), f"{method} {path} has no operationId"
        assert operation.get("summary"), f"{method} {path} has no summary"
        assert operation.get("tags"), f"{method} {path} has no tags"
        successes = [code for code in operation["responses"] if code.startswith("2")]
        assert successes, f"{method} {path} documents no 2xx response"


def test_operation_ids_are_unique(
    spec: dict[str, Any], spec_operations: set[tuple[str, str]]
) -> None:
    ids = [spec["paths"][path][method]["operationId"] for path, method in spec_operations]
    assert len(ids) == len(set(ids))


def test_github_backed_routes_declare_the_token_security_scheme(spec: dict[str, Any]) -> None:
    for path, method in sorted(GITHUB_BACKED):
        security = spec["paths"][path][method].get("security")
        assert security == [{"githubToken": []}], f"{method} {path} does not declare githubToken"


def test_the_webhook_declares_the_signature_scheme(spec: dict[str, Any]) -> None:
    assert spec["paths"]["/webhook"]["post"]["security"] == [{"webhookSignature": []}]


def test_public_routes_opt_out_of_security(spec: dict[str, Any]) -> None:
    """healthz and events never touch GitHub, so they carry no security requirement."""
    assert spec["paths"]["/healthz"]["get"]["security"] == []
    assert spec["paths"]["/events"]["get"]["security"] == []


def test_security_schemes_are_declared_as_bearer_and_signature(spec: dict[str, Any]) -> None:
    schemes = spec["components"]["securitySchemes"]
    assert schemes["githubToken"]["type"] == "http"
    assert schemes["githubToken"]["scheme"] == "bearer"
    assert schemes["webhookSignature"]["name"] == "X-Hub-Signature-256"


# ------------------------------------------------------- reusable components


@pytest.mark.parametrize(
    "name",
    ["Issue", "Comment", "Error", "ErrorDetail", "EventRecord", "IssueCreate", "IssuePatch"],
)
def test_the_expected_schemas_are_defined(spec: dict[str, Any], name: str) -> None:
    assert name in spec["components"]["schemas"]


def test_error_responses_reuse_the_error_schema(
    spec: dict[str, Any], spec_operations: set[tuple[str, str]]
) -> None:
    for path, method in sorted(spec_operations):
        operation = spec["paths"][path][method]
        for code, response in operation["responses"].items():
            if not code.startswith(("4", "5")) or "$ref" in response:
                continue
            schema = response["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/Error"}, (
                f"{method} {path} → {code} does not use the shared Error schema"
            )


def test_create_routes_document_the_location_header(spec: dict[str, Any]) -> None:
    for path in ("/issues", "/issues/{number}/comments"):
        headers = spec["paths"][path]["post"]["responses"]["201"]["headers"]
        assert "Location" in headers


def test_list_issues_documents_pagination_and_caching_headers(spec: dict[str, Any]) -> None:
    headers = spec["paths"]["/issues"]["get"]["responses"]["200"]["headers"]
    assert {"Link", "ETag", "X-Page", "X-Per-Page"} <= set(headers)
    assert "304" in spec["paths"]["/issues"]["get"]["responses"]


def test_rate_limited_response_documents_retry_after(spec: dict[str, Any]) -> None:
    assert "Retry-After" in spec["components"]["responses"]["RateLimited"]["headers"]


def test_error_codes_in_the_spec_match_the_codes_the_app_raises(spec: dict[str, Any]) -> None:
    from app import errors

    documented = set(spec["components"]["schemas"]["ErrorDetail"]["properties"]["code"]["enum"])
    raised = {
        cls.code
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.AppError)
    }
    assert raised <= documented, f"undocumented error codes: {sorted(raised - documented)}"


# --------------------------------------------------------------------- examples


def test_success_and_failure_examples_are_provided(
    spec: dict[str, Any], spec_operations: set[tuple[str, str]]
) -> None:
    for path, method in sorted(spec_operations):
        operation = spec["paths"][path][method]
        for code, response in operation["responses"].items():
            if "$ref" in response or "content" not in response:
                continue
            for media in response["content"].values():
                assert media.get("examples"), f"{method} {path} → {code} has no examples"


def test_shared_error_responses_all_carry_examples(spec: dict[str, Any]) -> None:
    for name, response in spec["components"]["responses"].items():
        media = response["content"]["application/json"]
        assert media.get("examples"), f"components.responses.{name} has no examples"


def test_request_bodies_carry_examples(
    spec: dict[str, Any], spec_operations: set[tuple[str, str]]
) -> None:
    for path, method in sorted(spec_operations):
        body = spec["paths"][path][method].get("requestBody")
        if body is None:
            continue
        for media in body["content"].values():
            assert media.get("examples"), f"{method} {path} request body has no examples"


def test_every_internal_ref_resolves(spec: dict[str, Any]) -> None:
    def refs(node: Any) -> list[str]:
        if isinstance(node, dict):
            found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
            for value in node.values():
                found.extend(refs(value))
            return found
        if isinstance(node, list):
            return [ref for item in node for ref in refs(item)]
        return []

    for ref in refs(spec):
        assert ref.startswith("#/"), f"unexpected external ref: {ref}"
        target: Any = spec
        for part in ref.removeprefix("#/").split("/"):
            assert part in target, f"unresolved ref: {ref}"
            target = target[part]
