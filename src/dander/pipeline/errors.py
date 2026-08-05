"""Typed error hierarchy for pipeline-graph structural and field-wiring validation.

A `PipelineGraph` (see `dander.pipeline.graph`) is only safe to hand to the orchestration layer
once it is known to be structurally sound: unique node ids, no dangling edge endpoints, no
self-loops, and no cycles. Beyond that, it must also be *semantically* wired correctly: field
names unique within each node, and every mapping/transformation/join/operation reference resolving
to a field a node actually declares (see `dander.pipeline.graph_ops.validate_field_wiring`).
This module defines one typed error per failure mode, each naming the offending element(s) so
failures are loud and actionable (per `steering/02-engineering.md`).

Error messages contain graph **structure only** — node ids, edge endpoint ids, and field names —
and never a node's free-form ``config``, a field's or edge's ``metadata``, a field value, or a
transformation's expression/constant payload, any of which may carry sensitive data (per
`steering/01-security.md`).
"""

from __future__ import annotations

from enum import StrEnum


class GraphValidationError(Exception):
    """Root of the pipeline-graph validation error hierarchy.

    Raised (via a subclass) whenever a `PipelineGraph` fails a structural check. Catch this type
    to handle any structural failure generically, or a specific subclass to handle one failure
    mode.
    """


class DuplicateNodeIdError(GraphValidationError):
    """Raised when two or more nodes in a graph share the same `id`.

    Attributes:
        node_id: The duplicated node id.
    """

    def __init__(self, node_id: str) -> None:
        """Initialize the error for a duplicated node id.

        Args:
            node_id: The node id that appears more than once in the graph.
        """
        self.node_id = node_id
        super().__init__(f"Duplicate node id: {node_id!r} appears more than once in the graph.")


class DanglingEdgeError(GraphValidationError):
    """Raised when an edge references a node id that does not exist in the graph.

    Attributes:
        source: The edge's `from` node id.
        target: The edge's `to` node id.
        missing_id: Whichever of `source`/`target` is not a known node id.
    """

    def __init__(self, *, source: str, target: str, missing_id: str) -> None:
        """Initialize the error for a dangling edge.

        Args:
            source: The edge's `from` node id.
            target: The edge's `to` node id.
            missing_id: The endpoint (equal to `source` or `target`) that has no matching node.
        """
        self.source = source
        self.target = target
        self.missing_id = missing_id
        super().__init__(
            f"Dangling edge {source!r} -> {target!r}: node id {missing_id!r} does not exist."
        )


class SelfLoopError(GraphValidationError):
    """Raised when an edge's `from` and `to` refer to the same node (a self-loop).

    Attributes:
        node_id: The node id that has an edge to itself.
    """

    def __init__(self, node_id: str) -> None:
        """Initialize the error for a self-loop.

        Args:
            node_id: The node id targeted by an edge from itself.
        """
        self.node_id = node_id
        super().__init__(f"Self-loop detected: node {node_id!r} has an edge to itself.")


class GraphCycleError(GraphValidationError):
    """Raised when the graph contains a cycle (it is not a DAG).

    Attributes:
        cycle: The cycle path as a list of node ids in visitation order, with the start node
            repeated at the end to close the loop (e.g. `["a", "b", "c", "a"]`).
    """

    def __init__(self, cycle: list[str]) -> None:
        """Initialize the error with the detected cycle path.

        Args:
            cycle: The cycle path, start node repeated at the end (e.g. `["a", "b", "c", "a"]`).
        """
        self.cycle = cycle
        super().__init__(f"Cycle detected in graph: {' -> '.join(cycle)}")


class DuplicateFieldNameError(GraphValidationError):
    """Raised when two or more fields declared on the same node share a `name`.

    Attributes:
        node_id: The node whose field schema has the duplicate.
        field_name: The field name that appears more than once on that node.
    """

    def __init__(self, *, node_id: str, field_name: str) -> None:
        """Initialize the error for a duplicated field name on one node.

        Args:
            node_id: The node whose field schema has the duplicate.
            field_name: The field name that appears more than once on that node.
        """
        self.node_id = node_id
        self.field_name = field_name
        super().__init__(f"Duplicate field name {field_name!r} on node {node_id!r}.")


class UnknownOperationFieldError(GraphValidationError):
    """Raised when a transform operation references an undeclared output field."""

    def __init__(
        self,
        *,
        node_id: str,
        field_name: str,
        operation_kind: str,
        operation_index: int,
    ) -> None:
        self.node_id = node_id
        self.field_name = field_name
        self.operation_kind = operation_kind
        self.operation_index = operation_index
        super().__init__(
            f"Operation {operation_kind!r} at index {operation_index} on transform node "
            f"{node_id!r} references undeclared output field {field_name!r}."
        )


class FieldReferenceKind(StrEnum):
    """The site of a field reference that failed to resolve to a declared field.

    Discriminates where an `UnknownFieldReferenceError` (or its `JoinKeyFieldError` subclass)
    originated, so one error type serves every reference site while the message and attributes
    stay precise about which reference failed.

    Attributes:
        MAPPING_SOURCE: A `FieldMapping.source` reference, checked against the edge's source
            node.
        MAPPING_TARGET: A `FieldMapping.target` reference, checked against the edge's target
            node.
        TRANSFORMATION_INPUT: A `Transformation.inputs` reference, checked against the edge's
            source node.
        JOIN_LEFT: A `JoinKeyPair.left` reference, checked against the edge's source (`from`)
            node.
        JOIN_RIGHT: A `JoinKeyPair.right` reference, checked against the edge's target (`to`)
            node.
    """

    MAPPING_SOURCE = "mapping_source"
    MAPPING_TARGET = "mapping_target"
    TRANSFORMATION_INPUT = "transformation_input"
    JOIN_LEFT = "join_left"
    JOIN_RIGHT = "join_right"


_REFERENCE_KIND_DESCRIPTIONS: dict[FieldReferenceKind, str] = {
    FieldReferenceKind.MAPPING_SOURCE: "Mapping",
    FieldReferenceKind.MAPPING_TARGET: "Mapping",
    FieldReferenceKind.TRANSFORMATION_INPUT: "Transformation input",
    FieldReferenceKind.JOIN_LEFT: "Join key",
    FieldReferenceKind.JOIN_RIGHT: "Join key",
}

_REFERENCE_KIND_SIDE: dict[FieldReferenceKind, str] = {
    FieldReferenceKind.MAPPING_SOURCE: "source",
    FieldReferenceKind.MAPPING_TARGET: "target",
    FieldReferenceKind.TRANSFORMATION_INPUT: "source",
    FieldReferenceKind.JOIN_LEFT: "source",
    FieldReferenceKind.JOIN_RIGHT: "target",
}


class UnknownFieldReferenceError(GraphValidationError):
    """Raised when a mapping/transformation/join references a field a node does not declare.

    A single, reusable error for every field-reference site (`FieldMapping.source`/`.target`,
    `Transformation.inputs`, `JoinKeyPair.left`/`.right`) — `reference_kind` discriminates which
    site failed so callers/messages stay precise while the hierarchy stays small.

    Attributes:
        node_id: The node that should declare `field_name` but does not.
        field_name: The referenced field name that is missing from `node_id`'s field schema.
        edge: The offending edge as `(source_id, target_id)`.
        reference_kind: Which reference site raised this error.
    """

    def __init__(
        self,
        *,
        node_id: str,
        field_name: str,
        edge: tuple[str, str],
        reference_kind: FieldReferenceKind,
    ) -> None:
        """Initialize the error for an unresolved field reference.

        Args:
            node_id: The node that should declare `field_name` but does not.
            field_name: The referenced field name that is missing from `node_id`'s field schema.
            edge: The offending edge as `(source_id, target_id)`.
            reference_kind: Which reference site raised this error.
        """
        self.node_id = node_id
        self.field_name = field_name
        self.edge = edge
        self.reference_kind = reference_kind
        description = _REFERENCE_KIND_DESCRIPTIONS[reference_kind]
        side = _REFERENCE_KIND_SIDE[reference_kind]
        source_id, target_id = edge
        super().__init__(
            f"{description} on edge {source_id!r} -> {target_id!r} references field "
            f"{field_name!r} not declared on {side} node {node_id!r}."
        )


class JoinKeyFieldError(UnknownFieldReferenceError):
    """Raised when a join key pair references a field missing on its joined node.

    A subclass of `UnknownFieldReferenceError` (not a sibling), so join-wiring failures are still
    caught by handlers targeting `UnknownFieldReferenceError` or `GraphValidationError`, while a
    caller that cares specifically about join wiring can catch this narrower type. Raised for
    `FieldReferenceKind.JOIN_LEFT`/`JOIN_RIGHT` reference kinds.

    Attributes:
        key_index: The index of the offending key pair within the join's `keys` list.
    """

    def __init__(
        self,
        *,
        node_id: str,
        field_name: str,
        edge: tuple[str, str],
        reference_kind: FieldReferenceKind,
        key_index: int,
    ) -> None:
        """Initialize the error for an unresolved join key field reference.

        Args:
            node_id: The node that should declare `field_name` but does not.
            field_name: The referenced field name that is missing from `node_id`'s field schema.
            edge: The offending edge as `(source_id, target_id)`.
            reference_kind: Which join side raised this error (`JOIN_LEFT`/`JOIN_RIGHT`).
            key_index: The index of the offending key pair within the join's `keys` list.
        """
        self.key_index = key_index
        super().__init__(
            node_id=node_id, field_name=field_name, edge=edge, reference_kind=reference_kind
        )
