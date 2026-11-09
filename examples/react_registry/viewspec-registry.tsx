/*
 * Reference host component registry in a shadcn/ui style, written for react-tailwind-tsx.
 *
 * Compile with `viewspec compile viewspec.intent.json --target react-tailwind-tsx
 * --registry examples/react_registry/registry.json --out react-tailwind-output/`, then copy this
 * file to `react-tailwind-output/viewspec-registry.tsx`.
 *
 * The host owns the look; the compiler owns structure and provenance. Every component renders the
 * element `as` names and spreads the compiler's props, `data-*` provenance included, onto that root
 * element. Layout primitives (Root, Stack, Grid, Cluster) keep the compiled `className`, so the
 * compiler's grid columns, spans, and gaps still apply. Visual primitives keep only the semantic
 * `vs-*` classes and add their own Tailwind classes, so no shadcn class competes with a recipe class.
 * The module imports only React: no shadcn runtime, no class-merging package, no CSS variables.
 *
 * The reference host proof derives its computed-style checks from the compiled classes, which this
 * module replaces on visual primitives. Prove a module like this one in the host's own test suite.
 */
import * as React from "react";

type ElementTag =
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

export type PrimitiveProps = Omit<React.AllHTMLAttributes<HTMLElement>, "as" | "onChange"> & {
  as: ElementTag;
  children?: React.ReactNode;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
};

/** The compiler's semantic hooks, such as `vs-root`, `vs-motif-table`, and `vs-role-metric-card`. */
function semanticClasses(className: string | undefined): string {
  return (className ?? "")
    .split(/\s+/)
    .filter((token) => token.startsWith("vs-"))
    .join(" ");
}

function render(
  { as, children, ...rest }: PrimitiveProps,
  className: string | undefined,
  content: React.ReactNode = children,
): React.ReactElement {
  const props = { ...rest, className };
  if (as === "table") {
    return React.createElement("table", props, React.createElement("tbody", null, content));
  }
  if (as === "input" || as === "hr") {
    return React.createElement(as, props);
  }
  return React.createElement(as, props, content);
}

/** Layout: keep the compiled classes, which carry the grid columns, spans, and gaps. */
function layout(props: PrimitiveProps): React.ReactElement {
  return render(props, props.className);
}

/** Visual: keep the semantic hooks and replace the recipe with this component's own classes. */
function styled(props: PrimitiveProps, own: string, content?: React.ReactNode): React.ReactElement {
  const className = [semanticClasses(props.className), own].filter(Boolean).join(" ");
  return content === undefined ? render(props, className) : render(props, className, content);
}

const cell = "p-2 align-middle";
const badge =
  "inline-flex w-fit items-center rounded-md border border-transparent bg-slate-100 px-2.5 py-0.5 " +
  "text-xs font-semibold text-slate-900";

export function Root(props: PrimitiveProps) {
  return layout(props);
}

export function Stack(props: PrimitiveProps) {
  return layout(props);
}

export function Grid(props: PrimitiveProps) {
  return layout(props);
}

export function Cluster(props: PrimitiveProps) {
  return layout(props);
}

/** Card, in the shadcn Card shape; a plain band for page and hero headers; destructive for alerts. */
export function Surface(props: PrimitiveProps) {
  if (props.as === "header") {
    return styled(props, "flex flex-col gap-2 pb-4");
  }
  const tone =
    props.role === "alert" ? "border-red-200 bg-red-50 text-red-900" : "border-slate-200 bg-white text-slate-950";
  return styled(props, `flex flex-col gap-4 rounded-xl border p-6 shadow-sm ${tone}`);
}

export function Text(props: PrimitiveProps) {
  switch (props.as) {
    case "td":
      return styled(props, `${cell} text-sm text-slate-600`);
    case "dd":
      return styled(props, "text-sm text-slate-900");
    case "p":
      return styled(props, "leading-7 text-slate-600");
    default:
      return styled(props, "text-sm leading-6 text-slate-600");
  }
}

export function Label(props: PrimitiveProps) {
  switch (props.as) {
    case "th":
      return styled(props, `${cell} text-left text-sm font-medium text-slate-500`);
    case "dt":
    case "p":
      return styled(props, "text-sm font-medium text-slate-500");
    default:
      return styled(props, "text-sm font-medium leading-none text-slate-950");
  }
}

export function Value(props: PrimitiveProps) {
  switch (props.as) {
    case "h1":
      return styled(props, "scroll-m-20 text-4xl font-extrabold tracking-tight");
    case "h2":
      return styled(props, "text-2xl font-semibold tracking-tight");
    case "td":
      return styled(props, `${cell} text-sm font-medium`);
    case "dd":
      return styled(props, "text-sm font-medium text-slate-900");
    default:
      return styled(props, "text-2xl font-bold tracking-tight");
  }
}

/** Badge: the shadcn `secondary` variant; inside a table or detail cell it wraps the cell's text. */
export function Badge(props: PrimitiveProps) {
  if (props.as === "td" || props.as === "dd") {
    return styled(props, props.as === "td" ? cell : "", <span className={badge}>{props.children}</span>);
  }
  return styled(props, badge);
}

/** Input: the shadcn Input shape with a visible focus ring. */
export function Input(props: PrimitiveProps) {
  return styled(
    props,
    "flex h-9 w-full rounded-md border border-slate-200 bg-transparent px-3 py-1 text-sm shadow-sm " +
      "placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-slate-950",
  );
}

export function ImageSlot(props: PrimitiveProps) {
  return styled(
    props,
    "flex min-h-24 items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 " +
      "text-xs text-slate-500",
  );
}

export function Rule(props: PrimitiveProps) {
  return styled(props, "my-4 border-slate-200");
}

export function Svg(props: PrimitiveProps) {
  return styled(
    props,
    "flex min-h-24 items-center justify-center rounded-md border border-slate-200 text-xs text-slate-500",
  );
}

/** Button: the shadcn `default` variant. */
export function Button(props: PrimitiveProps) {
  return styled(
    props,
    "inline-flex h-9 w-fit items-center justify-center gap-2 whitespace-nowrap rounded-md bg-slate-900 px-4 py-2 " +
      "text-sm font-medium text-slate-50 shadow transition-colors hover:bg-slate-900/90 " +
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-slate-950",
  );
}

/** Alert: the shadcn Alert `destructive` variant. */
export function ErrorBoundary(props: PrimitiveProps) {
  return styled(props, "rounded-lg border border-red-500/50 p-4 font-mono text-sm text-red-600");
}
