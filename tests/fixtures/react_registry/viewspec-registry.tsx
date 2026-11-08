/*
 * Host proof fixture registry. Every component spreads the compiler's props onto the element `as`
 * names and marks the element with the host component that rendered it, so the bounded reference
 * host proves that the compiled view reaches host-owned components and keeps its provenance.
 */
import * as React from "react";

type HostProps = Omit<React.AllHTMLAttributes<HTMLElement>, "as" | "onChange"> & {
  as: string;
  children?: React.ReactNode;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
};

function host(name: string) {
  function Component({ as, children, ...rest }: HostProps) {
    const props = { ...rest, "data-host-component": name };
    if (as === "table") {
      return React.createElement("table", props, React.createElement("tbody", null, children));
    }
    if (as === "input" || as === "hr") {
      return React.createElement(as, props);
    }
    return React.createElement(as, props, children);
  }
  Component.displayName = name;
  return Component;
}

export const Root = host("Root");
export const Stack = host("Stack");
export const Grid = host("Grid");
export const Cluster = host("Cluster");
export const Surface = host("Surface");
export const Text = host("Text");
export const Label = host("Label");
export const Value = host("Value");
export const Badge = host("Badge");
export const Input = host("Input");
export const ImageSlot = host("ImageSlot");
export const Rule = host("Rule");
export const Svg = host("Svg");
export const Button = host("Button");
export const ErrorBoundary = host("ErrorBoundary");
