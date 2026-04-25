"""Developer-facing fluent builder for ViewSpec intent bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec.types import (
    ActionIntent,
    BindingSpec,
    GroupSpec,
    IntentBundle,
    MotifSpec,
    RegionSpec,
    SemanticNode,
    SemanticSubstrate,
    StyleSpec,
    ViewSpec,
)


class ViewSpecBuilder:
    """Build ViewSpec + substrate protocols without exposing raw dataclass wiring."""

    def __init__(
        self,
        id: str,
        *,
        substrate_id: str | None = None,
        root_node_id: str | None = None,
        root_kind: str = "app",
        root_attrs: dict[str, Any] | None = None,
        root_slots: dict[str, list[Any]] | None = None,
        root_edges: dict[str, list[str]] | None = None,
        complexity_tier: int = 1,
        root_region: str = "root",
        root_min_children: int = 1,
        root_max_children: int | None = None,
        default_main_region: bool = True,
    ) -> None:
        self.id = id
        self.substrate_id = substrate_id or f"{id}_substrate"
        self.root_node_id = root_node_id or id
        self.complexity_tier = complexity_tier
        self.root_region = root_region
        self._nodes: dict[str, SemanticNode] = {}
        self._regions: list[RegionSpec] = []
        self._bindings: list[BindingSpec] = []
        self._groups: list[GroupSpec] = []
        self._motifs: list[MotifSpec] = []
        self._styles: list[StyleSpec] = []
        self._actions: list[ActionIntent] = []
        self.add_node(
            self.root_node_id,
            root_kind,
            attrs=root_attrs,
            slots=root_slots,
            edges=root_edges,
        )
        self.add_region(
            root_region,
            parent_region=None,
            role="root",
            layout="stack",
            min_children=root_min_children,
            max_children=root_max_children,
        )
        if default_main_region:
            self.add_region("main", parent_region=root_region, role="main", layout="stack", min_children=1)

    @property
    def substrate(self) -> SemanticSubstrate:
        return SemanticSubstrate(id=self.substrate_id, root_id=self.root_node_id, nodes=dict(self._nodes))

    @property
    def view_spec(self) -> ViewSpec:
        return ViewSpec(
            id=self.id,
            substrate_id=self.substrate_id,
            complexity_tier=self.complexity_tier,
            root_region=self.root_region,
            regions=list(self._regions),
            bindings=list(self._bindings),
            groups=list(self._groups),
            motifs=list(self._motifs),
            styles=list(self._styles),
            actions=list(self._actions),
        )

    def build_bundle(self) -> IntentBundle:
        """Build the complete IntentBundle ready for compilation."""
        return IntentBundle(substrate=self.substrate, view_spec=self.view_spec)

    def export_json(self, filepath: str | Path) -> Path:
        """Export the intent bundle as JSON."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.build_bundle().to_json(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def add_node(
        self,
        id: str,
        kind: str,
        *,
        attrs: dict[str, Any] | None = None,
        slots: dict[str, list[Any]] | None = None,
        edges: dict[str, list[str]] | None = None,
    ) -> ViewSpecBuilder:
        self._nodes[id] = SemanticNode(
            id=id,
            kind=kind,
            attrs=dict(attrs or {}),
            slots={key: list(values) for key, values in (slots or {}).items()},
            edges={key: list(values) for key, values in (edges or {}).items()},
        )
        return self

    def append_slot(self, node_id: str, slot: str, value: Any) -> ViewSpecBuilder:
        node = self._nodes[node_id]
        slots = {key: list(values) for key, values in node.slots.items()}
        slots.setdefault(slot, []).append(value)
        self._nodes[node_id] = SemanticNode(id=node.id, kind=node.kind, attrs=dict(node.attrs), slots=slots, edges=dict(node.edges))
        return self

    def add_region(
        self,
        id: str,
        *,
        parent_region: str | None = "root",
        role: str | None = None,
        layout: str = "stack",
        min_children: int = 1,
        max_children: int | None = None,
    ) -> ViewSpecBuilder:
        region = RegionSpec(
            id=id,
            parent_region=parent_region,
            role=role or id,
            layout=layout,
            min_children=min_children,
            max_children=max_children,
        )
        self._upsert_region(region)
        return self

    def bind_attr(
        self,
        id: str,
        node_id: str,
        attr: str,
        *,
        region: str = "main",
        present_as: str = "text",
        cardinality: str = "exactly_once",
    ) -> str:
        return self.add_binding(id, f"node:{node_id}#attr:{attr}", region=region, present_as=present_as, cardinality=cardinality)

    def bind_slot(
        self,
        id: str,
        node_id: str,
        slot: str,
        *,
        index: int | None = None,
        region: str = "main",
        present_as: str = "text",
        cardinality: str = "exactly_once",
    ) -> str:
        suffix = f"#slot:{slot}" if index is None else f"#slot:{slot}[{index}]"
        return self.add_binding(id, f"node:{node_id}{suffix}", region=region, present_as=present_as, cardinality=cardinality)

    def add_binding(
        self,
        id: str,
        address: str,
        *,
        region: str = "main",
        present_as: str = "text",
        cardinality: str = "exactly_once",
    ) -> str:
        self._bindings.append(BindingSpec(id=id, address=address, target_region=region, present_as=present_as, cardinality=cardinality))
        return id

    def add_group(self, id: str, kind: str, members: list[str], *, target_region: str | None = "main") -> ViewSpecBuilder:
        self._groups.append(GroupSpec(id=id, kind=kind, members=list(members), target_region=target_region))
        return self

    def add_motif(self, id: str, kind: str, region: str, members: list[str]) -> ViewSpecBuilder:
        self._motifs.append(MotifSpec(id=id, kind=kind, region=region, members=list(members)))
        return self

    def add_style(self, id: str, target: str, token: str) -> ViewSpecBuilder:
        self._styles.append(StyleSpec(id=id, target=target, token=token))
        return self

    def add_action(
        self,
        id: str,
        kind: str,
        label: str,
        *,
        target_region: str = "main",
        target_ref: str | None = None,
        payload_bindings: list[str] | None = None,
    ) -> ViewSpecBuilder:
        self._actions.append(
            ActionIntent(
                id=id,
                kind=kind,
                label=label,
                target_region=target_region,
                target_ref=target_ref,
                payload_bindings=list(payload_bindings or []),
            )
        )
        return self

    def add_table(self, id: str, *, region: str = "main", group_id: str | None = None) -> TableBuilder:
        self.add_motif(id, "table", region, [])
        if group_id:
            self.add_group(group_id, "ordered", [], target_region=region)
        return TableBuilder(self, id, region, group_id)

    def add_dashboard(self, id: str, *, region: str = "main", group_id: str | None = None) -> DashboardBuilder:
        self.add_motif(id, "dashboard", region, [])
        if group_id:
            self.add_group(group_id, "ordered", [], target_region=region)
        return DashboardBuilder(self, id, region, group_id)

    def add_outline(self, id: str, *, region: str = "main", group_id: str | None = None) -> OutlineBuilder:
        self.add_motif(id, "outline", region, [])
        if group_id:
            self.add_group(group_id, "ordered", [], target_region=region)
        return OutlineBuilder(self, id, region, group_id)

    def add_comparison(self, id: str, *, region: str = "main", group_id: str | None = None) -> ComparisonBuilder:
        self.add_motif(id, "comparison", region, [])
        if group_id:
            self.add_group(group_id, "ordered", [], target_region=region)
        return ComparisonBuilder(self, id, region, group_id)

    def _upsert_region(self, region: RegionSpec) -> None:
        for index, existing in enumerate(self._regions):
            if existing.id == region.id:
                self._regions[index] = region
                return
        self._regions.append(region)

    def _extend_group(self, group_id: str | None, members: list[str]) -> None:
        if not group_id:
            return
        for index, group in enumerate(self._groups):
            if group.id == group_id:
                self._groups[index] = GroupSpec(group.id, group.kind, [*group.members, *members], group.target_region)
                return
        raise KeyError(f"Unknown group id: {group_id}")

    def _extend_motif(self, motif_id: str, members: list[str]) -> None:
        for index, motif in enumerate(self._motifs):
            if motif.id == motif_id:
                self._motifs[index] = MotifSpec(motif.id, motif.kind, motif.region, [*motif.members, *members])
                return
        raise KeyError(f"Unknown motif id: {motif_id}")


class _MotifBuilder:
    def __init__(self, builder: ViewSpecBuilder, motif_id: str, region: str, group_id: str | None) -> None:
        self.builder = builder
        self.motif_id = motif_id
        self.region = region
        self.group_id = group_id
        self._count = 0

    def _append_members(self, members: list[str]) -> None:
        self.builder._extend_motif(self.motif_id, members)
        self.builder._extend_group(self.group_id, members)


class TableBuilder(_MotifBuilder):
    """Fluent builder for table motifs."""

    def add_row(
        self,
        *,
        label: Any,
        value: Any | None = None,
        values: dict[str, Any] | None = None,
        id: str | None = None,
        node_kind: str = "table_row",
    ) -> TableBuilder:
        self._count += 1
        row_id = id or f"{self.motif_id}_row_{self._count}"
        attrs: dict[str, Any] = {"label": label}
        if value is not None:
            attrs["value"] = value
        attrs.update(values or {})
        self.builder.add_node(row_id, node_kind, attrs=attrs)
        members = [self.builder.bind_attr(f"{row_id}_label", row_id, "label", region=self.region, present_as="label")]
        for attr in attrs:
            if attr == "label":
                continue
            members.append(self.builder.bind_attr(f"{row_id}_{attr}", row_id, attr, region=self.region, present_as="value"))
        self._append_members(members)
        return self


class DashboardBuilder(_MotifBuilder):
    """Fluent builder for dashboard motifs."""

    def add_card(self, *, label: Any, value: Any, id: str | None = None, value_present_as: str = "value") -> DashboardBuilder:
        self._count += 1
        card_id = id or f"{self.motif_id}_card_{self._count}"
        self.builder.add_node(card_id, "dashboard_card", attrs={"label": label, "value": value})
        members = [
            self.builder.bind_attr(f"{card_id}_label", card_id, "label", region=self.region, present_as="label"),
            self.builder.bind_attr(f"{card_id}_value", card_id, "value", region=self.region, present_as=value_present_as),
        ]
        self._append_members(members)
        return self


class OutlineBuilder(_MotifBuilder):
    """Fluent builder for outline motifs."""

    def add_branch(self, *, label: Any, id: str | None = None, kind: str = "outline_item") -> OutlineBuilder:
        self._count += 1
        branch_id = id or f"{self.motif_id}_branch_{self._count}"
        self.builder.add_node(branch_id, kind, attrs={"label": label})
        members = [self.builder.bind_attr(f"{branch_id}_label", branch_id, "label", region=self.region, present_as="label")]
        self._append_members(members)
        return self


class ComparisonBuilder(_MotifBuilder):
    """Fluent builder for comparison motifs."""

    def add_item(self, *, label: Any, value: Any, id: str | None = None) -> ComparisonBuilder:
        self._count += 1
        item_id = id or f"{self.motif_id}_item_{self._count}"
        self.builder.add_node(item_id, "comparison_item", attrs={"label": label, "value": value})
        members = [
            self.builder.bind_attr(f"{item_id}_label", item_id, "label", region=self.region, present_as="label"),
            self.builder.bind_attr(f"{item_id}_value", item_id, "value", region=self.region, present_as="text"),
        ]
        self._append_members(members)
        return self
