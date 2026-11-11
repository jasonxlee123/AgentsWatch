import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "automation" / "catalog.json"


@dataclass(frozen=True)
class OperationDefinition:
    code: str
    name: str
    description: str
    category: str
    scope: str
    version: str
    playbook: Path
    source_paths: tuple[Path, ...]
    risk_level: str
    requires_become: bool
    supports_batch: bool
    timeout_seconds: int
    parameters: dict[str, Any]
    version_id: str | None = None


class OperationCatalog:
    def __init__(self, path: Path = DEFAULT_CATALOG):
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported automation catalog schema version")
        self._operations = {}
        for item in payload.get("operations", []):
            code = item["code"]
            if code in self._operations:
                raise ValueError(f"duplicate operation code: {code}")
            playbook = ROOT / "automation" / item["playbook"]
            if not playbook.is_file():
                raise ValueError(f"playbook does not exist for {code}: {playbook}")
            source_paths = []
            for source in item.get("sources", []):
                source_path = (ROOT / "automation" / source).resolve()
                automation_root = (ROOT / "automation").resolve()
                if automation_root not in source_path.parents or not source_path.is_file():
                    raise ValueError(f"task source does not exist or is outside automation root for {code}: {source}")
                source_paths.append(source_path)
            if item["scope"] not in {"server", "runtime", "agent"}:
                raise ValueError(f"unsupported operation scope for {code}: {item['scope']}")
            if item["risk_level"] not in {"low", "medium", "high"}:
                raise ValueError(f"unsupported risk level for {code}: {item['risk_level']}")
            parameters = item.get("parameters", {})
            for name, definition in parameters.items():
                if definition.get("type") not in {"string", "integer", "number", "boolean", "array"}:
                    raise ValueError(f"unsupported parameter type for {code}.{name}")
                if definition.get("type") == "array" and definition.get("items") not in {
                    "string", "integer", "number", "boolean",
                }:
                    raise ValueError(f"unsupported array item type for {code}.{name}")
            self._operations[code] = OperationDefinition(
                code=code,
                name=item["name"],
                description=item.get("description", ""),
                category=item.get("category", "未分类"),
                scope=item["scope"],
                version=item.get("version", "1.0.0"),
                playbook=playbook,
                source_paths=tuple(source_paths),
                risk_level=item["risk_level"],
                requires_become=bool(item["requires_become"]),
                supports_batch=bool(item.get("supports_batch", False)),
                timeout_seconds=int(item["timeout_seconds"]),
                parameters=parameters,
            )

    def get(self, code: str) -> OperationDefinition:
        try:
            return self._operations[code]
        except KeyError as exc:
            raise KeyError(f"unknown or unapproved operation: {code}") from exc

    def list(self, scope: str | None = None) -> list[OperationDefinition]:
        values = self._operations.values()
        if scope is not None:
            values = (item for item in values if item.scope == scope)
        return sorted(values, key=lambda item: item.code)

    def validate_parameters(self, operation_code: str, parameters: dict[str, Any] | None) -> dict[str, Any]:
        operation = self.get(operation_code)
        values = dict(parameters or {})
        unknown = set(values) - set(operation.parameters)
        if unknown:
            raise ValueError(f"unsupported parameters for {operation_code}: {', '.join(sorted(unknown))}")

        missing = [
            name for name, definition in operation.parameters.items()
            if definition.get("required", False) and name not in values
        ]
        if missing:
            raise ValueError(f"missing required parameters for {operation_code}: {', '.join(sorted(missing))}")

        for name, value in values.items():
            definition = operation.parameters[name]
            self._validate_parameter(operation_code, name, value, definition)
        return values

    @staticmethod
    def _validate_parameter(operation_code: str, name: str, value: Any, definition: dict[str, Any]):
        parameter_type = definition["type"]
        type_matches = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "array": lambda item: isinstance(item, list),
        }
        if not type_matches[parameter_type](value):
            raise ValueError(f"parameter {name} for {operation_code} must be {parameter_type}")

        if parameter_type == "string":
            if not definition.get("allow_empty", False) and not value.strip():
                raise ValueError(f"parameter {name} for {operation_code} cannot be empty")
            if definition.get("max_length") is not None and len(value) > int(definition["max_length"]):
                raise ValueError(f"parameter {name} for {operation_code} exceeds max_length")
        elif parameter_type == "array":
            minimum = int(definition.get("min_items", 0))
            maximum = definition.get("max_items")
            if len(value) < minimum:
                raise ValueError(f"parameter {name} for {operation_code} requires at least {minimum} items")
            if maximum is not None and len(value) > int(maximum):
                raise ValueError(f"parameter {name} for {operation_code} exceeds max_items")
            item_type = definition["items"]
            for item in value:
                if not type_matches[item_type](item):
                    raise ValueError(f"parameter {name} for {operation_code} contains invalid {item_type} item")
                if item_type == "string" and not item.strip():
                    raise ValueError(f"parameter {name} for {operation_code} contains an empty item")
        elif parameter_type in {"integer", "number"}:
            if definition.get("minimum") is not None and value < definition["minimum"]:
                raise ValueError(f"parameter {name} for {operation_code} is below minimum")
            if definition.get("maximum") is not None and value > definition["maximum"]:
                raise ValueError(f"parameter {name} for {operation_code} exceeds maximum")

        choices = definition.get("enum")
        if choices is not None and value not in choices:
            raise ValueError(f"parameter {name} for {operation_code} is not an allowed value")
