/*
 * ViewSpec default host component registry.
 *
 * `viewspec compile --registry REGISTRY.json` renders every compiled primitive through the export
 * this module's registry names for its kind. Each component receives the props the compiler writes:
 * `as` (the element this default renders), `id`, the `data-*` provenance attributes, `className`,
 * `style` (react-tsx only), role and aria attributes, the action or input handlers, and `children`.
 *
 * This module reproduces the elements ViewSpec emits without a registry. Copy it beside
 * ViewSpecView.tsx and replace one export at a time. A replacement must spread the `data-*` props
 * onto its root element so that `viewspec check`, `viewspec prove`, and provenance tooling keep
 * finding every node. See docs/react-registry.md for the primitive contract.
 */
import * as React from "react";

export type ViewSpecElementTag =
  | "button"
  | "dd"
  | "div"
  | "dl"
  | "dt"
  | "h1"
  | "h2"
  | "header"
  | "hr"
  | "input"
  | "li"
  | "main"
  | "p"
  | "section"
  | "table"
  | "td"
  | "th"
  | "tr"
  | "ul";

export type ViewSpecPrimitiveProps = Omit<React.AllHTMLAttributes<HTMLElement>, "as" | "onChange"> & {
  as: ViewSpecElementTag;
  children?: React.ReactNode;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
};

function renderElement({ as, children, ...rest }: ViewSpecPrimitiveProps): React.ReactElement {
  if (as === "table") {
    return React.createElement("table", rest, React.createElement("tbody", null, children));
  }
  if (as === "input" || as === "hr") {
    return React.createElement(as, rest);
  }
  return React.createElement(as, rest, children);
}

export function Root(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Stack(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Grid(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Cluster(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Surface(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Text(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Label(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Value(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Badge(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Input(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function ImageSlot(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Rule(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Svg(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function Button(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}

export function ErrorBoundary(props: ViewSpecPrimitiveProps) {
  return renderElement(props);
}
