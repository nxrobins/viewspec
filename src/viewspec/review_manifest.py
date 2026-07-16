"""Server-side manifest index for reconstructing untrusted browser review targets."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any, Mapping

from viewspec.review_contract import ReviewContractError, ReviewTarget


MAX_MANIFEST_NODES = 4096
MAX_ANCESTOR_DEPTH = 32


@dataclass(frozen=True, slots=True)
class ReviewManifestNode:
    dom_id: str
    ir_id: str
    primitive: str
    intent_refs: tuple[str, ...]
    content_refs: tuple[str, ...]
    binding_id: str | None
    action_id: str | None

    @property
    def intent_ref_families(self) -> tuple[str, ...]:
        return tuple(sorted({":".join(ref.split(":")[:2]) for ref in self.intent_refs}))

    @property
    def content_ref_families(self) -> tuple[str, ...]:
        return tuple(sorted({ref.split(":", 1)[0] for ref in self.content_refs}))


class ReviewManifestIndex:
    """Immutable, ambiguity-checked projection of one provenance manifest."""

    def __init__(
        self,
        *,
        nodes: Mapping[str, ReviewManifestNode],
        manifest_sha256: str,
        screen_id: str | None,
    ) -> None:
        self._nodes = dict(nodes)
        self.manifest_sha256 = manifest_sha256
        self.screen_id = screen_id
        self._by_ir_id = {node.ir_id: node for node in self._nodes.values()}

    def __len__(self) -> int:
        return len(self._nodes)

    @classmethod
    def from_bytes(cls, content: bytes, *, screen_id: str | None) -> ReviewManifestIndex:
        if not isinstance(content, bytes):
            raise TypeError("manifest content must be bytes")
        try:
            payload = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
        except ReviewContractError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                f"Provenance manifest is not strict UTF-8 JSON: {exc}",
                "Recompile and check the source before opening Review.",
                http_status=422,
            ) from exc
        if not isinstance(payload, dict) or payload.get("manifest_schema_version") != 1:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                "Provenance manifest must use manifest_schema_version 1.",
                "Recompile with the current ViewSpec compiler.",
                http_status=422,
            )
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, dict):
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                "Provenance manifest nodes must be an object.",
                "Recompile and check the source before opening Review.",
                http_status=422,
            )
        if len(raw_nodes) > MAX_MANIFEST_NODES:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                f"Provenance manifest exposes more than {MAX_MANIFEST_NODES} annotatable nodes.",
                "Reduce the compiled interface before opening Review.",
                http_status=422,
            )
        nodes: dict[str, ReviewManifestNode] = {}
        seen_ir_ids: set[str] = set()
        manifest_sha256 = hashlib.sha256(content).hexdigest()
        for dom_id, entry in raw_nodes.items():
            node = _manifest_node(dom_id, entry)
            if node.ir_id in seen_ir_ids:
                raise ReviewContractError(
                    "REVIEW_MANIFEST_AMBIGUOUS",
                    f"Provenance manifest repeats ir_id {node.ir_id!r}.",
                    "Fix compiler identity generation before opening Review.",
                    http_status=422,
                )
            seen_ir_ids.add(node.ir_id)
            # Constructing the public target here proves all ref/count/hash bounds before readiness.
            _target_for(node, manifest_sha256=manifest_sha256, screen_id=screen_id, resolution="exact")
            nodes[node.dom_id] = node
        return cls(nodes=nodes, manifest_sha256=manifest_sha256, screen_id=screen_id)

    def target_for_dom_id(self, dom_id: str) -> ReviewTarget:
        node = self._nodes.get(dom_id)
        if node is None:
            raise ReviewContractError(
                "REVIEW_TARGET_NOT_IN_MANIFEST",
                "Browser target is not present in the checked provenance manifest.",
                "Select a compiler-owned manifest-backed element.",
                http_status=422,
            )
        return _target_for(
            node,
            manifest_sha256=self.manifest_sha256,
            screen_id=self.screen_id,
            resolution="exact",
        )

    def resolve_dom_ancestors(self, dom_ids: tuple[str, ...]) -> ReviewTarget:
        candidates = tuple(dom_ids)
        for index, dom_id in enumerate(candidates[:MAX_ANCESTOR_DEPTH]):
            node = self._nodes.get(dom_id)
            if node is not None:
                target = _target_for(
                    node,
                    manifest_sha256=self.manifest_sha256,
                    screen_id=self.screen_id,
                    resolution="exact",
                )
                return target if index == 0 else replace(target, target_resolution="ancestor")
        raise ReviewContractError(
            "REVIEW_TARGET_UNSUPPORTED",
            f"No manifest-backed target exists within {MAX_ANCESTOR_DEPTH} light-DOM ancestors.",
            "Choose an explicit page-level annotation instead.",
            http_status=422,
        )

    def page_target(self) -> ReviewTarget:
        return ReviewTarget(
            kind="page",
            screen_id=self.screen_id,
            ir_id=None,
            source_ref=f"screen:{self.screen_id}" if self.screen_id is not None else "page",
            dom_id=None,
            binding_id=None,
            action_id=None,
            intent_refs=(),
            content_refs=(),
            provenance_manifest_sha256=self.manifest_sha256,
            target_resolution="page",
        )

    def assert_identity_compatible(self, previous: ReviewManifestIndex) -> None:
        if not isinstance(previous, ReviewManifestIndex) or previous.screen_id != self.screen_id:
            raise ReviewContractError(
                "REVIEW_REVISION_IDENTITY_MISMATCH",
                "Cannot compare manifest identity across different screen scopes.",
                "Compare only the same screen between exact promoted revisions.",
                http_status=500,
                cli_exit=1,
            )
        for ir_id in self._by_ir_id.keys() & previous._by_ir_id.keys():
            current = self._by_ir_id[ir_id]
            prior = previous._by_ir_id[ir_id]
            if (
                current.primitive != prior.primitive
                or current.intent_ref_families != prior.intent_ref_families
                or current.content_ref_families != prior.content_ref_families
            ):
                raise ReviewContractError(
                    "REVIEW_MANIFEST_AMBIGUOUS",
                    f"Stable ir_id {ir_id!r} changed primitive or semantic ref family.",
                    "Assign a new source identity when the semantic meaning changes.",
                    http_status=422,
                )


def _manifest_node(dom_id: object, entry: object) -> ReviewManifestNode:
    if not isinstance(dom_id, str) or not dom_id or len(dom_id.encode("utf-8")) > 256 or not isinstance(entry, dict):
        raise ReviewContractError(
            "REVIEW_MANIFEST_AMBIGUOUS",
            "Manifest DOM ids and node entries must be bounded strings and objects.",
            "Recompile with a conforming ViewSpec emitter.",
            http_status=422,
        )
    ir_id = entry.get("ir_id")
    primitive = entry.get("primitive")
    intent_refs = entry.get("intent_refs", [])
    content_refs = entry.get("content_refs", [])
    props = entry.get("props", {})
    if (
        not isinstance(ir_id, str)
        or not ir_id
        or len(ir_id.encode("utf-8")) > 128
        or not isinstance(primitive, str)
        or not primitive
        or len(primitive.encode("utf-8")) > 128
        or not isinstance(intent_refs, list)
        or not all(isinstance(ref, str) for ref in intent_refs)
        or not isinstance(content_refs, list)
        or not all(isinstance(ref, str) for ref in content_refs)
        or not isinstance(props, dict)
    ):
        raise ReviewContractError(
            "REVIEW_MANIFEST_AMBIGUOUS",
            f"Manifest node {dom_id!r} has an invalid identity or ref shape.",
            "Recompile with a conforming ViewSpec emitter.",
            http_status=422,
        )
    binding_id = entry.get("binding_id", props.get("binding_id"))
    action_id = entry.get("action_id", props.get("action_id"))
    if binding_id is not None and not isinstance(binding_id, str):
        binding_id = None
    if action_id is not None and not isinstance(action_id, str):
        action_id = None
    return ReviewManifestNode(
        dom_id=dom_id,
        ir_id=ir_id,
        primitive=primitive,
        intent_refs=tuple(intent_refs),
        content_refs=tuple(content_refs),
        binding_id=binding_id,
        action_id=action_id,
    )


def _target_for(
    node: ReviewManifestNode,
    *,
    manifest_sha256: str,
    screen_id: str | None,
    resolution: str,
) -> ReviewTarget:
    source_ref = f"screen:{screen_id}/ir:{node.ir_id}" if screen_id is not None else f"ir:{node.ir_id}"
    return ReviewTarget(
        kind="source_node",
        screen_id=screen_id,
        ir_id=node.ir_id,
        source_ref=source_ref,
        dom_id=node.dom_id,
        binding_id=node.binding_id,
        action_id=node.action_id,
        intent_refs=node.intent_refs,
        content_refs=node.content_refs,
        provenance_manifest_sha256=manifest_sha256,
        target_resolution=resolution,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                f"Provenance manifest contains duplicate object key {key!r}.",
                "Recompile with a canonical manifest serializer.",
                http_status=422,
            )
        result[key] = value
    return result


__all__ = ["MAX_ANCESTOR_DEPTH", "MAX_MANIFEST_NODES", "ReviewManifestIndex", "ReviewManifestNode"]
