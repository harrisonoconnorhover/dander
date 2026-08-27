"""Explicit transport DTOs for ``io.dander.control.contracts/v1``.

These models describe JSON crossing the Control API boundary. They deliberately do not replace
the pipeline domain models: ``PipelineGraphDocument`` revalidates through Dander's canonical
``PipelineGraph`` and graph semantics before it can be constructed. The explicit transport layer
exists because validator-routed domain fields cannot be represented accurately by a raw
``PipelineGraph.model_json_schema()`` dump.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from dander.pipeline.graph import PipelineGraph
from dander.pipeline.graph_ops import validate_field_wiring
from dander.pipeline.node_config import ExecutableJoinType, PartitioningType
from dander.pipeline.operations import ComparisonOperator, MatchLogic
from dander.pipeline.request_spec import HttpMethod
from dander.warehouse import ProviderExtension  # noqa: TC001 - Pydantic resolves this enum
from dander.writer import SchemaEvolution, WriteMode

type JsonObject = dict[str, JsonValue]

_KNOWN_NODE_TYPES = ("source", "transform", "target")
_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_FUNCTION_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
_MAX_RESULT_INTEGER = 9_223_372_036_854_775_807


class ControlModel(BaseModel):
    """Closed, immutable JSON object at the public Control API boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ExtensibleConfig(BaseModel):
    """Typed node config whose additional JSON fields are intentionally preserved."""

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class EmptyObject(ControlModel):
    """A JSON object that must contain no properties."""


class NotNullFieldTest(ControlModel):
    kind: Literal["not_null"]
    values: list[JsonValue] = Field(default_factory=list, max_length=0)
    to: None = None
    field: None = None
    metadata: JsonObject = Field(default_factory=dict)


class UniqueFieldTest(ControlModel):
    kind: Literal["unique"]
    values: list[JsonValue] = Field(default_factory=list, max_length=0)
    to: None = None
    field: None = None
    metadata: JsonObject = Field(default_factory=dict)


class AcceptedValuesFieldTest(ControlModel):
    kind: Literal["accepted_values"]
    values: list[JsonValue] = Field(min_length=1)
    to: None = None
    field: None = None
    metadata: JsonObject = Field(default_factory=dict)


class RelationshipsFieldTest(ControlModel):
    kind: Literal["relationships"]
    values: list[JsonValue] = Field(default_factory=list, max_length=0)
    to: str = Field(min_length=1)
    field: str = Field(min_length=1)
    metadata: JsonObject = Field(default_factory=dict)


type FieldTestDocument = (
    NotNullFieldTest | UniqueFieldTest | AcceptedValuesFieldTest | RelationshipsFieldTest
)


class NodeFieldDocument(ControlModel):
    name: str
    type: str
    cast_to: str | None = None
    nullable: bool = True
    description: str | None = None
    tests: list[FieldTestDocument] = Field(default_factory=list)
    extensions: tuple[ProviderExtension, ...] = Field(default_factory=tuple)
    metadata: JsonObject = Field(default_factory=dict)


class ScheduleTrigger(ControlModel):
    kind: Literal["schedule"]
    cron: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list, max_length=0)
    event: None = None
    metadata: JsonObject = Field(default_factory=dict)


class DependencyTrigger(ControlModel):
    kind: Literal["dependency"]
    cron: None = None
    depends_on: list[str] = Field(min_length=1)
    event: None = None
    metadata: JsonObject = Field(default_factory=dict)


class ManualTrigger(ControlModel):
    kind: Literal["manual"]
    cron: None = None
    depends_on: list[str] = Field(default_factory=list, max_length=0)
    event: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


type TriggerDocument = ScheduleTrigger | DependencyTrigger | ManualTrigger


class CursorStrategyDocument(ControlModel):
    field: str = Field(min_length=1)
    kind: Literal["timestamp", "sequence", "opaque_token"]
    params: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class PositionDocument(ControlModel):
    x: float
    y: float


class NodeVisualDocument(ControlModel):
    position: PositionDocument | None = None
    color: str | None = None
    icon: str | None = None


class RequestSpecDocument(ControlModel):
    method: HttpMethod = HttpMethod.GET
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: JsonObject | str | None = None


class ExecutableJoinKeyDocument(ControlModel):
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class TransformJoinDocument(ControlModel):
    left_input: str = Field(min_length=1)
    right_input: str = Field(min_length=1)
    type: ExecutableJoinType
    keys: list[ExecutableJoinKeyDocument] = Field(min_length=1)


class TruncateStringParamsDocument(ControlModel):
    field: str = Field(pattern=_IDENTIFIER_PATTERN)
    max_length: int = Field(ge=0)


class TrimWhitespaceParamsDocument(ControlModel):
    field: str = Field(pattern=_IDENTIFIER_PATTERN)


class DefaultValueParamsDocument(ControlModel):
    field: str = Field(pattern=_IDENTIFIER_PATTERN)
    default: str | int | float | bool


class FieldConditionDocument(ControlModel):
    field: str = Field(pattern=_IDENTIFIER_PATTERN)
    op: ComparisonOperator
    value: str | int | float | bool | list[str | int | float | bool] | None = None


class FilterRowsParamsDocument(ControlModel):
    conditions: list[FieldConditionDocument] = Field(min_length=1)
    logic: MatchLogic = MatchLogic.ALL


class TruncateStringOperation(ControlModel):
    kind: Literal["truncate_string"]
    params: TruncateStringParamsDocument
    metadata: JsonObject = Field(default_factory=dict)


class TrimWhitespaceOperation(ControlModel):
    kind: Literal["trim_whitespace"]
    params: TrimWhitespaceParamsDocument
    metadata: JsonObject = Field(default_factory=dict)


class DefaultValueOperation(ControlModel):
    kind: Literal["default_value"]
    params: DefaultValueParamsDocument
    metadata: JsonObject = Field(default_factory=dict)


class FilterRowsOperation(ControlModel):
    kind: Literal["filter_rows"]
    params: FilterRowsParamsDocument
    metadata: JsonObject = Field(default_factory=dict)


type OperationDocument = (
    TruncateStringOperation | TrimWhitespaceOperation | DefaultValueOperation | FilterRowsOperation
)


class SourceNodeConfigDocument(ExtensibleConfig):
    connector: str | None = None
    endpoint: str | None = None
    request: RequestSpecDocument | None = None


class TransformNodeConfigDocument(ExtensibleConfig):
    join: TransformJoinDocument | None = None
    operations: list[OperationDocument] = Field(default_factory=list)


class PartitioningDocument(ControlModel):
    field: str | None = None
    granularity: PartitioningType = PartitioningType.DAY
    require_partition_filter: bool = False


class DestinationDocument(ControlModel):
    project: str | None = None
    dataset: str = Field(min_length=1)
    table: str = Field(min_length=1)
    business_key: list[str] = Field(default_factory=list)


class WriterDocument(ControlModel):
    write_mode: WriteMode
    destination: DestinationDocument
    cursor_field: str | None = None
    partitioning: PartitioningDocument | None = None
    clustering: list[str] = Field(default_factory=list, max_length=4)
    max_batch_rows: int = Field(default=10_000, gt=0, le=100_000)
    schema_evolution: SchemaEvolution = SchemaEvolution.STRICT
    transport: Literal["load_job", "storage_write", "copy"] = "load_job"


class TargetNodeConfigDocument(ExtensibleConfig):
    writer: WriterDocument | None = None


class DirectTransformation(ControlModel):
    kind: Literal["direct"] = "direct"
    expression: None = None
    constant: None = None
    function: None = None
    arguments: EmptyObject = Field(default_factory=EmptyObject)
    inputs: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ExpressionTransformation(ControlModel):
    kind: Literal["expression"]
    expression: str = Field(min_length=1)
    constant: None = None
    function: None = None
    arguments: EmptyObject = Field(default_factory=EmptyObject)
    inputs: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ConstantTransformation(ControlModel):
    kind: Literal["constant"]
    expression: None = None
    constant: JsonValue
    function: None = None
    arguments: EmptyObject = Field(default_factory=EmptyObject)
    inputs: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class CustomCodeTransformation(ControlModel):
    kind: Literal["custom_code"]
    expression: None = None
    constant: None = None
    function: str = Field(pattern=_FUNCTION_KEY_PATTERN)
    arguments: JsonObject = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


type TransformationDocument = (
    DirectTransformation
    | ExpressionTransformation
    | ConstantTransformation
    | CustomCodeTransformation
)


class FieldMappingDocument(ControlModel):
    source: str | None = None
    target: str
    transformation: TransformationDocument | None = None
    metadata: JsonObject = Field(default_factory=dict)


class JoinKeyPairDocument(ControlModel):
    left: str
    right: str


class JoinDocument(ControlModel):
    type: Literal["inner", "left", "right", "full"]
    keys: list[JoinKeyPairDocument] = Field(min_length=1)
    metadata: JsonObject = Field(default_factory=dict)


class EdgeDocument(ControlModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    metadata: JsonObject = Field(default_factory=dict)
    mappings: list[FieldMappingDocument] = Field(default_factory=list)
    join: JoinDocument | None = None


class _NodeDocument(ControlModel):
    id: str
    type: str
    name: str
    fields: list[NodeFieldDocument] = Field(default_factory=list)
    trigger: TriggerDocument | None = None
    cursor: CursorStrategyDocument | None = None
    visual: NodeVisualDocument | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_config_alias(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = dict(value)
        if "config" in raw and "params" in raw:
            raise ValueError("a node may use 'config' or its legacy 'params' alias, not both")
        if "params" in raw:
            raw["config"] = raw.pop("params")
        if raw.get("config") is None:
            raw["config"] = {}
        return raw


_ALIAS_SCHEMA: dict[str, JsonValue] = {"not": {"required": ["config", "params"]}}
_PARAMS_FIELD: dict[str, JsonValue] = {
    "deprecated": True,
    "x-dander-canonical-name": "config",
}


class SourceNodeDocument(_NodeDocument):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_ALIAS_SCHEMA,
    )
    type: Literal["source"]
    config: SourceNodeConfigDocument = Field(default_factory=SourceNodeConfigDocument)
    params: SourceNodeConfigDocument | None = Field(
        default=None,
        exclude=True,
        json_schema_extra=_PARAMS_FIELD,
    )


class TransformNodeDocument(_NodeDocument):
    model_config = SourceNodeDocument.model_config
    type: Literal["transform"]
    config: TransformNodeConfigDocument = Field(default_factory=TransformNodeConfigDocument)
    params: TransformNodeConfigDocument | None = Field(
        default=None,
        exclude=True,
        json_schema_extra=_PARAMS_FIELD,
    )


class TargetNodeDocument(_NodeDocument):
    model_config = SourceNodeDocument.model_config
    type: Literal["target"]
    config: TargetNodeConfigDocument = Field(default_factory=TargetNodeConfigDocument)
    params: TargetNodeConfigDocument | None = Field(
        default=None,
        exclude=True,
        json_schema_extra=_PARAMS_FIELD,
    )


_EXTENSION_NODE_TYPE_SCHEMA: dict[str, JsonValue] = {"not": {"enum": list(_KNOWN_NODE_TYPES)}}
type ExtensionNodeType = Annotated[str, Field(json_schema_extra=_EXTENSION_NODE_TYPE_SCHEMA)]


class ExtensionNodeDocument(_NodeDocument):
    model_config = SourceNodeDocument.model_config
    type: ExtensionNodeType
    config: JsonObject = Field(default_factory=dict)
    params: JsonObject | None = Field(
        default=None,
        exclude=True,
        json_schema_extra=_PARAMS_FIELD,
    )

    @field_validator("type")
    @classmethod
    def _exclude_known_node_type(cls, value: str) -> str:
        if value in _KNOWN_NODE_TYPES:
            raise ValueError("extension node type must not shadow a typed node")
        return value


type GraphNodeDocument = (
    SourceNodeDocument | TransformNodeDocument | TargetNodeDocument | ExtensionNodeDocument
)


class PipelineGraphDocument(ControlModel):
    """Canonical graph transport whose construction reuses Dander semantic validation."""

    name: str
    nodes: list[GraphNodeDocument] = Field(default_factory=list)
    edges: list[EdgeDocument] = Field(default_factory=list)
    trigger: TriggerDocument | None = None

    @model_validator(mode="after")
    def _validate_domain_graph(self) -> Self:
        graph = PipelineGraph.model_validate(
            self.model_dump(mode="json", by_alias=True), extra="forbid"
        )
        validate_field_wiring(graph)
        return self

    def to_domain(self) -> PipelineGraph:
        """Return the canonical Dander graph after the same strict semantic checks."""
        graph = PipelineGraph.model_validate(
            self.model_dump(mode="json", by_alias=True), extra="forbid"
        )
        validate_field_wiring(graph)
        return graph

    @classmethod
    def from_domain(cls, graph: PipelineGraph) -> PipelineGraphDocument:
        """Project one already parsed domain graph into the explicit transport model."""
        return cls.model_validate(graph.model_dump(mode="json", by_alias=True))


class GraphSummaryResponse(ControlModel):
    """Document-free metadata for one graph in a bounded hosted list response."""

    project: str
    graph: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    updated_at: str


class GraphResourceResponse(GraphSummaryResponse):
    """One hosted graph resource; its opaque revision travels only in the HTTP ETag."""

    document: PipelineGraphDocument


class GraphCreateRequest(ControlModel):
    """Create one named graph through the hosted project collection route."""

    graph: str
    document: PipelineGraphDocument


class GraphPageResponse(ControlModel):
    """A bounded graph-summary page that never embeds full graph documents."""

    items: tuple[GraphSummaryResponse, ...] = Field(max_length=100)
    next_cursor: str | None = None


class ProjectSummaryResponse(ControlModel):
    """One configured logical project, never a provider project payload."""

    id: str


class ProjectListResponse(ControlModel):
    """The bounded logical projects configured for this Dander installation."""

    projects: tuple[ProjectSummaryResponse, ...] = Field(max_length=100)


class GraphValidationDetail(ControlModel):
    location: str
    message: str
    type: str


class GraphValidationResponse(ControlModel):
    valid: bool
    graph_name: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: tuple[GraphValidationDetail, ...] = Field(default_factory=tuple)


class ConnectorField(ControlModel):
    name: str
    display_name: str
    data_type: str
    required: bool = False


class ConnectorBinding(ControlModel):
    connector: str
    endpoint: str


class ConnectorEndpoint(ControlModel):
    id: str
    display_name: str
    graph_binding: ConnectorBinding
    fields: tuple[ConnectorField, ...] = Field(default_factory=tuple)


class InstalledPluginIdentity(ControlModel):
    id: str
    distribution: str
    version: str


class InstalledConnector(ControlModel):
    id: str
    display_name: str
    engine: str
    description: str = ""
    plugin: InstalledPluginIdentity
    endpoints: tuple[ConnectorEndpoint, ...] = Field(default_factory=tuple)


class ConnectorCatalogResponse(ControlModel):
    connectors: tuple[InstalledConnector, ...] = Field(default_factory=tuple)


class PluginCatalogRecord(ControlModel):
    id: str
    display_name: str
    description: str
    distribution: str
    version: str
    dander_specifier: str
    compatible: bool
    support_status: str
    validation_status: str
    documentation_url: str
    pypi_url: str
    repository_url: str
    installed: bool
    installed_version: str | None = None


class PluginCatalogResponse(ControlModel):
    schema_version: Literal[1] = 1
    dander_version: str
    connectors: tuple[PluginCatalogRecord, ...] = Field(default_factory=tuple)


class OperationParameter(ControlModel):
    name: str
    display_name: str
    control: str
    required: bool
    minimum: int | None = None
    default: JsonValue = None
    options: tuple[JsonValue, ...] = Field(default_factory=tuple)
    operators: tuple[str, ...] = Field(default_factory=tuple)


class OperationDescriptor(ControlModel):
    kind: Literal["truncate_string", "trim_whitespace", "default_value", "filter_rows"]
    display_name: str
    description: str
    parameters: tuple[OperationParameter, ...] = Field(default_factory=tuple)


class OperationCatalogResponse(ControlModel):
    schema_version: Literal[1] = 1
    operations: tuple[OperationDescriptor, ...]


class DeploymentPreviewResponse(ControlModel):
    revision: str
    candidate_image: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_summary: str
    plan_text: str
    affected_jobs: tuple[str, ...] = Field(default_factory=tuple)


class RunRequest(ControlModel):
    expected_revision: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELING = "canceling"
    CANCELED = "canceled"
    RETRYING = "retrying"


class RunTelemetrySummary(ControlModel):
    """Fixed-size telemetry totals; provider operation details remain in logs."""

    duration_ms: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    operation_count: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    retry_count: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    rows_read: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    rows_written: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    rows_affected: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    bytes_read: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    bytes_written: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    bytes_processed: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    bytes_billed: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    queue_duration_ms: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    execution_duration_ms: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    spill_bytes: int = Field(ge=0, le=_MAX_RESULT_INTEGER)


class RunPlacementDecision(ControlModel):
    """Fixed-size explanation of the execution-plan selection used by Control."""

    decision_schema: Literal["io.dander.control.placement-decision/v1"]
    mode: Literal[
        "automatic",
        "manual_override",
        "configured_default",
        "scheduled",
        "replay",
    ]
    selected_environment: str
    selected_locality: str | None = None
    estimated_cost_microusd: int | None = Field(default=None, ge=0, le=_MAX_RESULT_INTEGER)
    preferred_locality: str | None = None
    max_cost_microusd: int | None = Field(default=None, ge=0, le=_MAX_RESULT_INTEGER)
    eligible_plan_count: int = Field(ge=1, le=100)


class RunSizeClassDecision(ControlModel):
    """Fixed-size explanation of the selected single-container resource class."""

    decision_schema: Literal["io.dander.control.size-class-decision/v1"]
    mode: Literal[
        "automatic_input",
        "manual_override",
        "configured_default",
        "scheduled",
        "replay",
    ]
    selected_size_class: str
    estimated_input_bytes: int | None = Field(default=None, ge=0, le=_MAX_RESULT_INTEGER)
    max_input_bytes: int = Field(ge=0, le=_MAX_RESULT_INTEGER)
    cpu_millis: int = Field(ge=1)
    memory_mib: int = Field(ge=1)
    ephemeral_storage_mib: int | None = Field(default=None, ge=1)
    eligible_plan_count: int = Field(ge=1, le=100)


class RunStatusResponse(ControlModel):
    run_id: str
    state: RunState
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    endpoints: int = Field(default=0, ge=0)
    extracted: int = Field(default=0, ge=0)
    affected: int = Field(default=0, ge=0)
    models: int = Field(default=0, ge=0)
    assertions: int = Field(default=0, ge=0)
    assets: int = Field(default=0, ge=0)
    result_schema: Literal["io.dander.control.execution-result-summary/v1"] | None = None
    skipped: bool = False
    telemetry: RunTelemetrySummary | None = None
    placement: RunPlacementDecision | None = None
    sizing: RunSizeClassDecision | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    can_cancel: bool = False
    can_replay: bool = False
    logs_available: bool = False


class RunPageResponse(ControlModel):
    """A bounded page of normalized, non-sensitive run summaries."""

    items: tuple[RunStatusResponse, ...] = Field(max_length=100)
    next_cursor: str | None = None


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogRecord(ControlModel):
    timestamp: str
    level: LogLevel
    code: str
    message: str
    correlation_id: str


class LogPageResponse(ControlModel):
    records: tuple[LogRecord, ...]
    next_cursor: str | None = None


class MutationResult(ControlModel):
    operation: Literal["cancel", "replay"]
    accepted: bool
    run_id: str
    resulting_run_id: str | None = None
    state: RunState


class ApiErrorDetail(ControlModel):
    location: str | None = None
    code: str
    message: str


class ApiError(ControlModel):
    code: str
    message: str
    correlation_id: str
    details: tuple[ApiErrorDetail, ...] = Field(default_factory=tuple)


class ApiErrorEnvelope(ControlModel):
    error: ApiError


class ContractIdentity(ControlModel):
    id: Literal["io.dander.control.contracts/v1"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompatibilityRange(ControlModel):
    minimum_druff_contract: str
    maximum_druff_contract: str


class ControlBootstrapDescriptor(ControlModel):
    """Secret-free discovery data for one hosted Druff deployment."""

    schema_version: Literal[1] = 1
    api_url: str
    issuer: str
    public_client_id: str
    api_audience: str
    redirect_uri: str
    logout_uri: str
    contract: ContractIdentity
    compatibility: CompatibilityRange


class ControlLimits(ControlModel):
    max_graph_bytes: int = Field(gt=0)
    max_page_size: int = Field(gt=0)
    max_log_records: int = Field(gt=0)


class CapabilitiesResponse(ControlModel):
    api_version: Literal["v1"] = "v1"
    dander_version: str
    contract: ContractIdentity
    compatibility: CompatibilityRange
    operations: tuple[
        Literal[
            "graph.read",
            "graph.edit",
            "graph.delete",
            "graph.validate",
            "deployment.preview",
            "run.start",
            "run.read",
            "run.logs",
            "run.cancel",
            "run.replay",
        ],
        ...,
    ]
    limits: ControlLimits
