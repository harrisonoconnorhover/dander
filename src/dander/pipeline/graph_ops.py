"""Derived structure and algorithms over a `PipelineGraph`.

This module is the correctness layer that sits on top of DANDER-2's pure `PipelineGraph` model:
structural validation (`validate`), a derived adjacency index (`AdjacencyIndex`), execution
ordering (`topological_order`), and field-wiring validation (`validate_field_wiring`, DANDER-8).
Everything here is a pure, side-effect-free function of a `PipelineGraph` — nothing is persisted
onto the model, and adjacency/field indexes are always computed from the stored `nodes`/`edges`,
never stored twice (per the decided format in `steering/00-project-overview.md`).

Kept out of `dander.pipeline.graph` so the model stays a pure value object (SRP) and so algorithms
depend on the model, never the reverse (DIP) — no import cycle between shape and behavior.

Field-wiring validation (DANDER-8) checks that mappings, transformations, joins, and operations
reference fields that the graph's nodes actually declare. It is a **companion** to, not a
replacement for,
structural `validate`: `validate_field_wiring` runs `validate` first, so a graph with both a
structural fault and a field-wiring fault always surfaces the structural error first — the
field-wiring checks assume unique node ids and resolvable edge endpoints, which only structural
validation guarantees. Per `steering/01-security.md`, every field-wiring error carries structure
only (node ids, edge endpoint ids, field names) — never a node's `config`, a field's/edge's
`metadata`, a field value, or a transformation's expression/constant payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.pipeline.errors import (
    DanglingEdgeError,
    DuplicateFieldNameError,
    DuplicateNodeIdError,
    FieldReferenceKind,
    GraphCycleError,
    JoinKeyFieldError,
    SelfLoopError,
    UnknownFieldReferenceError,
    UnknownOperationFieldError,
)
from dander.pipeline.node_config import TransformNodeConfig

if TYPE_CHECKING:
    from dander.pipeline.graph import PipelineGraph

_WHITE = 0  # not yet visited
_GREY = 1  # on the current DFS recursion stack (in progress)
_BLACK = 2  # fully processed


@dataclass(frozen=True)
class AdjacencyIndex:
    """Derived predecessor/successor lookup for a `PipelineGraph`, computed once from `edges`.

    Built after (or by an internal caller that guarantees) the duplicate-id and dangling-edge
    checks have passed — `from_graph` does not itself re-validate. Neighbour ids are kept in
    edge-insertion order so lookups are deterministic.

    Attributes:
        _successors: Node id -> ids it has outgoing edges to, in edge-insertion order.
        _predecessors: Node id -> ids it has incoming edges from, in edge-insertion order.
    """

    _successors: dict[str, list[str]]
    _predecessors: dict[str, list[str]]

    @classmethod
    def from_graph(cls, graph: PipelineGraph) -> AdjacencyIndex:
        """Build an `AdjacencyIndex` from a graph's `edges` list.

        Assumes node ids are already known-unique and every edge endpoint is a valid node id
        (i.e. called after, or as part of, structural validation) — for internal use where that
        is guaranteed, this does not re-check either invariant itself.

        Args:
            graph: The pipeline graph to index.

        Returns:
            An `AdjacencyIndex` with every node id present (mapping to an empty list if it has
            no incident edges).
        """
        successors: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
        predecessors: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
        for edge in graph.edges:
            successors.setdefault(edge.source, []).append(edge.target)
            predecessors.setdefault(edge.target, []).append(edge.source)
        return cls(_successors=successors, _predecessors=predecessors)

    def successors(self, node_id: str) -> list[str]:
        """Return the ids this node has outgoing edges to.

        Args:
            node_id: The node id to look up.

        Returns:
            A new list of successor node ids in edge-insertion order (empty if none, or if
            `node_id` is unknown to this index).
        """
        return list(self._successors.get(node_id, []))

    def predecessors(self, node_id: str) -> list[str]:
        """Return the ids this node has incoming edges from.

        Args:
            node_id: The node id to look up.

        Returns:
            A new list of predecessor node ids in edge-insertion order (empty if none, or if
            `node_id` is unknown to this index).
        """
        return list(self._predecessors.get(node_id, []))


def _check_duplicate_node_ids(graph: PipelineGraph) -> None:
    """Raise `DuplicateNodeIdError` if any two nodes share an `id`."""
    seen: set[str] = set()
    for node in graph.nodes:
        if node.id in seen:
            raise DuplicateNodeIdError(node.id)
        seen.add(node.id)


def _check_dangling_edges(graph: PipelineGraph) -> None:
    """Raise `DanglingEdgeError` if any edge endpoint is not a known node id."""
    node_ids = {node.id for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in node_ids:
            raise DanglingEdgeError(source=edge.source, target=edge.target, missing_id=edge.source)
        if edge.target not in node_ids:
            raise DanglingEdgeError(source=edge.source, target=edge.target, missing_id=edge.target)


def _check_self_loops(graph: PipelineGraph) -> None:
    """Raise `SelfLoopError` if any edge's `source` equals its `target`."""
    for edge in graph.edges:
        if edge.source == edge.target:
            raise SelfLoopError(edge.source)


def _dfs_topological_order(node_ids: list[str], adjacency: AdjacencyIndex) -> list[str]:
    """Compute a topological order via DFS with three-colour marking.

    Visits nodes and their successors in insertion order (the order of `node_ids` and of each
    node's successor list) so the result is deterministic. If DFS reaches a grey (in-progress)
    node, the graph has a cycle; the current recursion stack is sliced from that node to the
    current one to report the cycle path.

    Args:
        node_ids: All node ids in the graph, in insertion order.
        adjacency: The graph's derived adjacency index.

    Returns:
        Node ids in a valid execution order: for every edge, its source appears before its
        target.

    Raises:
        GraphCycleError: If the graph is not a DAG. The error's `cycle` attribute holds the
            cycle path, start node repeated at the end.
    """
    color: dict[str, int] = dict.fromkeys(node_ids, _WHITE)
    order: list[str] = []
    stack_path: list[str] = []

    def visit(node_id: str) -> None:
        color[node_id] = _GREY
        stack_path.append(node_id)
        for neighbor in adjacency.successors(node_id):
            if color[neighbor] == _WHITE:
                visit(neighbor)
            elif color[neighbor] == _GREY:
                cycle_start = stack_path.index(neighbor)
                raise GraphCycleError([*stack_path[cycle_start:], neighbor])
            # _BLACK neighbours are already fully processed; nothing to do.
        stack_path.pop()
        color[node_id] = _BLACK
        order.append(node_id)

    for node_id in node_ids:
        if color[node_id] == _WHITE:
            visit(node_id)

    order.reverse()
    return order


def _check_acyclic(graph: PipelineGraph) -> None:
    """Raise `GraphCycleError` if the graph is not a DAG.

    Assumes node ids are unique and every edge endpoint is a known node id (i.e. called after
    the duplicate-id and dangling-edge checks).
    """
    adjacency = AdjacencyIndex.from_graph(graph)
    node_ids = [node.id for node in graph.nodes]
    _dfs_topological_order(node_ids, adjacency)


def validate(graph: PipelineGraph) -> None:
    """Validate a `PipelineGraph`'s structural correctness.

    Runs four checks in a fixed order, each assuming the earlier ones held: (1) node ids are
    unique; (2) every edge endpoint references an existing node id; (3) no edge is a self-loop;
    (4) the graph is a DAG (no cycle). The order matters because later checks (adjacency, cycle
    detection) are only meaningful once ids are unique and every edge endpoint resolves.

    Args:
        graph: The pipeline graph to validate.

    Raises:
        DuplicateNodeIdError: Two or more nodes share the same `id`.
        DanglingEdgeError: An edge references a node id that does not exist.
        SelfLoopError: An edge's `from` and `to` are the same node.
        GraphCycleError: The graph contains a cycle.
    """
    _check_duplicate_node_ids(graph)
    _check_dangling_edges(graph)
    _check_self_loops(graph)
    _check_acyclic(graph)


def topological_order(graph: PipelineGraph) -> list[str]:
    """Return the graph's node ids in a valid execution order.

    Validates the graph first (see `validate`), so an invalid graph raises the corresponding
    typed error — including `GraphCycleError` for a cyclic graph — rather than returning a
    meaningless order.

    Args:
        graph: The pipeline graph to order.

    Returns:
        Node ids ordered so that for every edge, its source appears before its target.

    Raises:
        DuplicateNodeIdError: Two or more nodes share the same `id`.
        DanglingEdgeError: An edge references a node id that does not exist.
        SelfLoopError: An edge's `from` and `to` are the same node.
        GraphCycleError: The graph contains a cycle.
    """
    validate(graph)
    adjacency = AdjacencyIndex.from_graph(graph)
    node_ids = [node.id for node in graph.nodes]
    return _dfs_topological_order(node_ids, adjacency)


@dataclass(frozen=True)
class _FieldIndex:
    """Derived node-id -> declared-field-name lookup for a `PipelineGraph`.

    Mirrors the `AdjacencyIndex` pattern: a small, frozen, once-built index over the graph's
    `nodes`. Must be built **after** `_check_duplicate_field_names` has passed — a set-based
    index would otherwise silently collapse a node's duplicate field names, masking the very
    condition that check exists to catch. Also assumes node ids are already known-unique (i.e.
    built after structural `validate`), matching `AdjacencyIndex.from_graph`'s precondition.

    Attributes:
        _fields_by_node: Node id -> the frozenset of field names that node declares.
    """

    _fields_by_node: dict[str, frozenset[str]]

    @classmethod
    def from_graph(cls, graph: PipelineGraph) -> _FieldIndex:
        """Build a `_FieldIndex` from a graph's `nodes`.

        Args:
            graph: The pipeline graph to index. Assumes duplicate field names within any single
                node have already been rejected (see `_check_duplicate_field_names`) and node ids
                are unique (see `validate`).

        Returns:
            A `_FieldIndex` with every node id present, mapping to the frozenset of its declared
            field names (empty if the node declares none).
        """
        return cls(
            _fields_by_node={
                node.id: frozenset(field.name for field in node.fields) for node in graph.nodes
            }
        )

    def has(self, node_id: str, field_name: str) -> bool:
        """Return whether `node_id` declares a field named `field_name`.

        Args:
            node_id: The node id to look up.
            field_name: The field name to check for.

        Returns:
            `True` if `node_id` is known to this index and declares `field_name`; `False`
            otherwise (including if `node_id` is unknown to this index).
        """
        return field_name in self._fields_by_node.get(node_id, frozenset())


def _check_duplicate_field_names(graph: PipelineGraph) -> None:
    """Raise `DuplicateFieldNameError` if any node declares two fields with the same `name`.

    Runs per node, tracking seen names in declaration order; the first repeat raises. Must run
    before `_FieldIndex` is built (see `_FieldIndex`).
    """
    for node in graph.nodes:
        seen: set[str] = set()
        for field in node.fields:
            if field.name in seen:
                raise DuplicateFieldNameError(node_id=node.id, field_name=field.name)
            seen.add(field.name)


def _check_mapping_fields(graph: PipelineGraph, index: _FieldIndex) -> None:
    """Raise `UnknownFieldReferenceError` if a `FieldMapping` references an undeclared field.

    Per edge, per `FieldMapping`: `source` (when not `None` — a derived field has no single
    source) must resolve on the edge's source node (`MAPPING_SOURCE`); `target` must resolve on
    the edge's target node (`MAPPING_TARGET`).
    """
    for edge in graph.edges:
        for mapping in edge.mappings:
            if mapping.source is not None and not index.has(edge.source, mapping.source):
                raise UnknownFieldReferenceError(
                    node_id=edge.source,
                    field_name=mapping.source,
                    edge=(edge.source, edge.target),
                    reference_kind=FieldReferenceKind.MAPPING_SOURCE,
                )
            if not index.has(edge.target, mapping.target):
                raise UnknownFieldReferenceError(
                    node_id=edge.target,
                    field_name=mapping.target,
                    edge=(edge.source, edge.target),
                    reference_kind=FieldReferenceKind.MAPPING_TARGET,
                )


def _check_transformation_fields(graph: PipelineGraph, index: _FieldIndex) -> None:
    """Raise `UnknownFieldReferenceError` if a transformation input references an undeclared field.

    Per edge, per `FieldMapping` with a `transformation`: each declared input field name in
    `transformation.inputs` must resolve on the edge's source node. Reference resolution only —
    the expression/constant payload is never inspected or included in any error message, and a
    transformation with zero inputs (e.g. a `constant`) is a no-op for this check.
    """
    for edge in graph.edges:
        for mapping in edge.mappings:
            if mapping.transformation is None:
                continue
            for input_field in mapping.transformation.inputs:
                if not index.has(edge.source, input_field):
                    raise UnknownFieldReferenceError(
                        node_id=edge.source,
                        field_name=input_field,
                        edge=(edge.source, edge.target),
                        reference_kind=FieldReferenceKind.TRANSFORMATION_INPUT,
                    )


def _check_join_fields(graph: PipelineGraph, index: _FieldIndex) -> None:
    """Raise `JoinKeyFieldError` if a join key pair references an undeclared field.

    Per edge with a `join`: each key pair's `left` field must resolve on the edge's source
    (`from`) node (`JOIN_LEFT`), and `right` must resolve on the edge's target (`to`) node
    (`JOIN_RIGHT`) — consistent with `JoinSpec`'s left<->from / right<->to convention.
    """
    for edge in graph.edges:
        if edge.join is None:
            continue
        for key_index, key_pair in enumerate(edge.join.keys):
            if not index.has(edge.source, key_pair.left):
                raise JoinKeyFieldError(
                    node_id=edge.source,
                    field_name=key_pair.left,
                    edge=(edge.source, edge.target),
                    reference_kind=FieldReferenceKind.JOIN_LEFT,
                    key_index=key_index,
                )
            if not index.has(edge.target, key_pair.right):
                raise JoinKeyFieldError(
                    node_id=edge.target,
                    field_name=key_pair.right,
                    edge=(edge.source, edge.target),
                    reference_kind=FieldReferenceKind.JOIN_RIGHT,
                    key_index=key_index,
                )


def _check_operation_fields(graph: PipelineGraph, index: _FieldIndex) -> None:
    """Require every transform operation to reference one of its node's output fields."""
    for node in graph.nodes:
        config = node.config
        if not isinstance(config, TransformNodeConfig):
            continue
        for operation_index, operation in enumerate(config.operations):
            for field_name in operation.referenced_fields():
                if not index.has(node.id, field_name):
                    raise UnknownOperationFieldError(
                        node_id=node.id,
                        field_name=field_name,
                        operation_kind=operation.kind.value,
                        operation_index=operation_index,
                    )


def validate_field_wiring(graph: PipelineGraph) -> None:
    """Validate a `PipelineGraph`'s field-level wiring, on top of its structural correctness.

    Runs the structural gate first, then six field-wiring checks in a fixed order: (1) `validate`
    (DANDER-3) — node ids unique, no dangling edges, no self-loops, a DAG; (2) field names unique
    within each node; (3) every `FieldMapping`'s `source`/`target` resolves on the edge's
    source/target node; (4) every transformation's declared input fields resolve on the edge's
    source node; (5) every join key pair resolves on the edge's source (left) / target (right)
    node; (6) every transform operation references a field declared on its owning node. Running
    `validate` first guarantees that on a graph with both a structural fault and a
    field-wiring fault, the structural error surfaces first — the field-wiring checks assume
    unique node ids and resolvable edge endpoints. The duplicate-name check runs before the
    `_FieldIndex` is built so a node's duplicate field names are rejected rather than silently
    collapsed into one index entry.

    Args:
        graph: The pipeline graph to validate.

    Raises:
        DuplicateNodeIdError: Two or more nodes share the same `id`.
        DanglingEdgeError: An edge references a node id that does not exist.
        SelfLoopError: An edge's `from` and `to` are the same node.
        GraphCycleError: The graph contains a cycle.
        DuplicateFieldNameError: A node declares two fields with the same name.
        UnknownFieldReferenceError: A mapping's `source`/`target` or a transformation's input
            references a field the relevant node does not declare.
        JoinKeyFieldError: A join key pair references a field missing on its joined node (a
            subclass of `UnknownFieldReferenceError`).
        UnknownOperationFieldError: A transform operation references an undeclared output field.
    """
    validate(graph)
    _check_duplicate_field_names(graph)
    index = _FieldIndex.from_graph(graph)
    _check_mapping_fields(graph, index)
    _check_transformation_fields(graph, index)
    _check_join_fields(graph, index)
    _check_operation_fields(graph, index)
