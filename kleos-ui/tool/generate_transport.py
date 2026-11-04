#!/usr/bin/env python3
"""Generate transport DTOs from the committed API schema; no runtime dependencies.

This intentionally supports the response schema subset used by Kleos. Unknown
unions/inline object schemas fail generation rather than silently losing types.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ["ReferenceImageView", "ReferenceImageUpload", "AgentsPage", "ExternalJobsPage", "ExternalJobEventsPage", "EngineeringToolCatalogView", "ProjectsView", "WorkspaceView", "RunView", "ModelCatalogView",
         "EventView", "ArtifactDownloadView", "Command", "CommandAccepted",
         "CompanionPreviewRequest", "CompanionPreviewView", "CircuitSimulationView",
         "BambuCatalogView", "PrintPreparationView", "PrintPreparationsView", "PreparePrint", "PreparedPrint",
         "BambuTestPrintRequest", "BambuTestPrintView",
         "RentAHumanStatusView", "HumanSearchView", "HumanBountiesView", "HumanTaskDraftsView",
         "HumanTaskDraftView", "HumanTaskDraftRequest", "HumanTaskDraftAction", "HumanTaskLinkRequest",
         "BambuLocalState", "BambuCertificateView", "BambuAddressRequest", "BambuConnectionRequest", "BambuDiscoveryView",
         "BambuDispatchRequest", "BambuDispatchView", "BambuDispatchesView", "BambuReconcileRequest"]


def name(value):
    return "Api" + re.sub(r"[^a-zA-Z0-9]", "", value)


def normalized(schema):
    variants = schema.get("anyOf", schema.get("oneOf"))
    if variants:
        real = [v for v in variants if v.get("type") != "null"]
        if len(real) != 1:
            raise ValueError(f"Unsupported union: {schema}")
        return real[0], len(real) != len(variants)
    return schema, schema.get("type") == "null"


def dart_type(schema):
    schema, nullable = normalized(schema)
    if "$ref" in schema:
        value = name(schema["$ref"].split("/")[-1])
    else:
        kind = schema.get("type")
        if kind == "array":
            value = f"List<{dart_type(schema.get('items', {}))}>"
        elif kind == "object":
            if schema.get("properties"):
                raise ValueError("Inline object must have a named OpenAPI component")
            additional = schema.get("additionalProperties")
            value = f"Map<String, {dart_type(additional) if isinstance(additional, dict) else 'dynamic'}>"
        else:
            value = {"string": "String", "integer": "int", "number": "double",
                     "boolean": "bool"}.get(kind, "Object?")
    return value + ("?" if nullable and not value.endswith("?") else "")


def read_value(schema, expression, optional=False):
    base, nullable = normalized(schema)
    if optional or nullable:
        return f"{expression} == null ? null : {read_value(base, expression)}"
    if "$ref" in base:
        return f"{name(base['$ref'].split('/')[-1])}.fromJson(_object({expression}))"
    kind = base.get("type")
    if kind in {"integer", "number"}:
        method = "toInt" if kind == "integer" else "toDouble"
        return f"({expression} as num).{method}()"
    if kind == "array":
        item = base.get("items", {})
        return f"List<{dart_type(item)}>.unmodifiable(({expression} as List).map((v) => {read_value(item, 'v')}))"
    if kind == "object":
        item = base.get("additionalProperties")
        if isinstance(item, dict):
            return f"Map<String, {dart_type(item)}>.unmodifiable(_object({expression}).map((k, v) => MapEntry(k, {read_value(item, 'v')})))"
        return f"Map<String, dynamic>.unmodifiable(_object({expression}))"
    return expression if not kind else f"{expression} as {dart_type(base)}"


def write_value(schema, expression, optional=False):
    base, nullable = normalized(schema)
    if optional or nullable:
        plain = write_value(base, expression + "!")
        if plain == expression + "!":
            return expression
        return plain.replace(expression + "!.", expression + "?.", 1)
    if "$ref" in base:
        return expression + ".toJson()"
    if base.get("type") == "array":
        return f"{expression}.map((v) => {write_value(base.get('items', {}), 'v')}).toList(growable: false)"
    item = base.get("additionalProperties")
    if isinstance(item, dict):
        return f"{expression}.map((k, v) => MapEntry(k, {write_value(item, 'v')}))"
    return expression


def generate(document):
    schemas = document["components"]["schemas"]
    selected = {}

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"].split("/")[-1]
                if ref not in selected:
                    selected[ref] = schemas[ref]
                    visit(schemas[ref])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for root in ROOTS:
        visit({"$ref": f"#/components/schemas/{root}"})
    digest = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    lines = ["// GENERATED CODE - DO NOT EDIT.",
             "// Source: backend/contracts/openapi.json", f"// Schema SHA-256: {digest}",
             "// Regenerate: python3 tool/generate_transport.py", "",
             "Map<String, dynamic> _object(Object? value) => Map<String, dynamic>.from(value as Map);", ""]
    for schema_name, schema in sorted(selected.items()):
        if schema.get("type") != "object":
            raise ValueError(f"Unsupported component {schema_name}")
        cls = name(schema_name)
        fields = schema.get("properties", {})
        required = set(schema.get("required", []))
        lines += [f"class {cls} {{", f"const {cls}({{"]
        for key in fields:
            lines.append(f"{'required ' if key in required else ''}this.{key},")
        lines += ["});", f"factory {cls}.fromJson(Map<String, dynamic> json) => {cls}("]
        for key, field in fields.items():
            lines.append(f"{key}: {read_value(field, 'json[' + repr(key) + ']', key not in required)},")
        lines += [");"]
        for key, field in fields.items():
            dtype = dart_type(field)
            if key not in required and not dtype.endswith("?"):
                dtype += "?"
            lines.append(f"final {dtype} {key};")
        lines += ["Map<String, dynamic> toJson() => {"]
        for key, field in fields.items():
            prefix = f"if ({key} != null) " if key not in required else ""
            lines.append(f"{prefix}'{key}': {write_value(field, key, key not in required)},")
        lines += ["};", "}", ""]
    result = subprocess.run(["dart", "format", "--output=show", "--summary=none",
                             "--stdin-name", str(ROOT / "lib/data/models/api.generated.dart")],
                            input="\n".join(lines), text=True, capture_output=True, check=True)
    return result.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", type=Path, default=ROOT.parent / "backend/contracts/openapi.json")
    parser.add_argument("--output", type=Path, default=ROOT / "lib/data/models/api.generated.dart")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = generate(json.loads(args.schema.read_text()))
    if args.check:
        if not args.output.exists() or args.output.read_text() != output:
            raise SystemExit("Generated transport is stale; run python3 tool/generate_transport.py")
        print("Generated transport matches OpenAPI.")
    else:
        args.output.write_text(output)
        print(f"Generated {args.output}")
