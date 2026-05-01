"use client";

import * as React from "react";

export type ViewSpecActionIntent = {
  id: string;
  kind: string;
  targetRef: string;
  payloadBindings: string[];
  payload: Record<string, unknown>;
};

type ViewSpecVisibilityRule = {
  id: string;
  sourceRef: string;
  sourceKind: string;
  operator: string;
  compareValue?: unknown;
  compareValueType?: string;
  fallbackValue?: unknown;
};

export type ViewSpecData = Record<string, React.ReactNode>;

export type ViewSpecViewProps = {
  data?: ViewSpecData;
  onAction?: (intent: ViewSpecActionIntent) => void;
  className?: string;
};

export function ViewSpecView({ data = {}, onAction, className }: ViewSpecViewProps) {
  const [inputValues, setInputValues] = React.useState<Record<string, unknown>>({"include_mobile": true, "owner_email": "launch@viewspec.dev", "phase_filter": "Demos"});
  const compiledPayloadValues: Record<string, unknown> = {"kpi_blockers_value": "1", "launch_status": "On track"};
  const visibilityRules: Record<string, ViewSpecVisibilityRule> = {"show_mobile_note": {"compareValue": "Mobile", "compareValueType": "string", "fallbackValue": "Demos", "fallbackValueType": "string", "id": "show_mobile_note", "operator": "EQUALS", "sourceKind": "input", "sourceRef": "phase_filter", "targetRef": "binding:mobile_recording_note"}};
  const setInputValue = (id: string, value: unknown) => {
    setInputValues((current) => ({ ...current, [id]: value }));
  };
  const isEmptyRuleValue = (value: unknown): boolean => value == null || value === "" || (Array.isArray(value) && value.length === 0);
  const ruleValueType = (value: unknown): string => {
    if (value == null) return "null";
    if (typeof value === "boolean") return "boolean";
    if (typeof value === "number") return "number";
    if (typeof value === "string") return "string";
    if (Array.isArray(value)) return "array";
    return "object";
  };
  const sameScalar = (left: unknown, right: unknown): boolean => {
    const leftType = ruleValueType(left);
    const rightType = ruleValueType(right);
    if (!["null", "string", "number", "boolean"].includes(leftType) || leftType !== rightType) return false;
    return left === right;
  };
  const ruleSourceValue = (rule: ViewSpecVisibilityRule): unknown => {
    if (rule.sourceKind === "input") {
      return Object.prototype.hasOwnProperty.call(inputValues, rule.sourceRef) ? inputValues[rule.sourceRef] : rule.fallbackValue;
    }
    return Object.prototype.hasOwnProperty.call(data, rule.sourceRef) ? data[rule.sourceRef] : rule.fallbackValue;
  };
  const evaluateRule = (ruleId: string, inputValuesArg: Record<string, unknown>, dataArg: ViewSpecData): boolean => {
    void inputValuesArg;
    void dataArg;
    const rule = visibilityRules[ruleId];
    if (!rule) return true;
    const value = ruleSourceValue(rule);
    if (rule.operator === "IS_EMPTY") return isEmptyRuleValue(value);
    if (rule.operator === "NOT_EMPTY") return !isEmptyRuleValue(value);
    if (rule.operator === "IS_TRUE") return value === true;
    if (rule.operator === "IS_FALSE") return value === false;
    if (rule.operator === "EQUALS") return sameScalar(value, rule.compareValue);
    if (rule.operator === "NOT_EQUALS") return !sameScalar(value, rule.compareValue);
    return true;
  };
  const collectPayload = (payloadBindings: string[]): Record<string, unknown> => {
    const payload: Record<string, unknown> = {};
    payloadBindings.forEach((bindingId) => {
      if (Object.prototype.hasOwnProperty.call(inputValues, bindingId)) {
        payload[bindingId] = inputValues[bindingId];
      } else if (Object.prototype.hasOwnProperty.call(compiledPayloadValues, bindingId)) {
        payload[bindingId] = compiledPayloadValues[bindingId];
      }
    });
    return payload;
  };
  return (
      <main id="dom-region_root" className={["min-h-screen bg-slate-50 text-slate-950 p-6 space-y-6", className].filter(Boolean).join(" ")} data-ir-id="region_root" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:view:launch_operations_dashboard\", \"viewspec:region:root\"]"} onSubmit={(event) => event.preventDefault()} style={{ "--vs-temperature": "cool", backgroundColor: "#f0fdfa", "--vs-energy-level": "0.625", "--vs-saturation": "1.113", filter: "saturate(var(--vs-saturation))", "--vs-hierarchy-ratio": "1.16", "--vs-rhythm-density": "compressed", gap: "0.52rem" } as React.CSSProperties}>
        <div id="dom-region_header" className="flex flex-row flex-wrap gap-3" data-ir-id="region_header" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:region:header\"]"}>
          <div id="dom-binding_launch_title" className="text-base leading-[var(--vs-readable-line-height,1.75rem)] text-slate-800" data-ir-id="binding_launch_title" data-content-refs={"[\"node:launch_ops#attr:title\"]"} data-intent-refs={"[\"viewspec:binding:launch_title\", \"viewspec:style:title_emphasis\"]"} style={{ opacity: "0.92", fontWeight: "780", letterSpacing: "-0.025em" } as React.CSSProperties}>
            {data["launch_title"] ?? "Launch Operations Dashboard"}
          </div>
          <div id="dom-binding_launch_status" className="inline-flex w-fit rounded-full bg-teal-50 px-3 py-1 text-sm font-semibold text-teal-800 ring-1 ring-teal-200" data-ir-id="binding_launch_status" data-content-refs={"[\"node:launch_ops#attr:status\"]"} data-intent-refs={"[\"viewspec:binding:launch_status\", \"viewspec:style:status_positive\"]"} style={{ opacity: "0.92", color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)" } as React.CSSProperties}>
            {data["launch_status"] ?? "On track"}
          </div>
          <div id="dom-binding_launch_summary" className="text-base leading-[var(--vs-readable-line-height,1.75rem)] text-slate-800" data-ir-id="binding_launch_summary" data-content-refs={"[\"node:launch_ops#attr:summary\"]"} data-intent-refs={"[\"viewspec:binding:launch_summary\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
            {data["launch_summary"] ?? "One ViewSpec source proves web, React, iOS, and Android compilation."}
          </div>
        </div>
        <div id="dom-region_kpis" className="grid gap-4" data-ir-id="region_kpis" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:region:kpis\"]"} style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" } as React.CSSProperties}>
          <div id="dom-motif_launch_kpis" className="flex flex-col gap-3" data-ir-id="motif_launch_kpis" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_kpis\", \"viewspec:style:kpi_surface\"]"} style={{ "--vs-energy-level": "0.625", "--vs-saturation": "1.113", filter: "saturate(var(--vs-saturation))", background: "#f1f5f9", border: "1px solid #94a3b8", borderRadius: "14px" } as React.CSSProperties}>
            <div id="dom-motif_launch_kpis_kpi_emitters" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm space-y-3" data-ir-id="motif_launch_kpis_kpi_emitters" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_kpis\"]"}>
              <div id="dom-binding_kpi_emitters_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_kpi_emitters_label" data-content-refs={"[\"node:kpi_emitters#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:kpi_emitters_label\"]"} style={{ position: "relative", zIndex: "1", boxShadow: "0 10px 26px rgba(15, 118, 110, 0.16)", fontWeight: "820", scale: "1.015" } as React.CSSProperties}>
                {data["kpi_emitters_label"] ?? "Emitter targets"}
              </div>
              <div id="dom-binding_kpi_emitters_value" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_kpi_emitters_value" data-content-refs={"[\"node:kpi_emitters#attr:value\"]"} data-intent-refs={"[\"viewspec:binding:kpi_emitters_value\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_emitters_value"] ?? "4"}
              </div>
            </div>
            <div id="dom-motif_launch_kpis_kpi_docs" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm space-y-3" data-ir-id="motif_launch_kpis_kpi_docs" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_kpis\"]"}>
              <div id="dom-binding_kpi_docs_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_kpi_docs_label" data-content-refs={"[\"node:kpi_docs#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:kpi_docs_label\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_docs_label"] ?? "Docs pages"}
              </div>
              <div id="dom-binding_kpi_docs_value" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_kpi_docs_value" data-content-refs={"[\"node:kpi_docs#attr:value\"]"} data-intent-refs={"[\"viewspec:binding:kpi_docs_value\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_docs_value"] ?? "8"}
              </div>
            </div>
            <div id="dom-motif_launch_kpis_kpi_assets" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm space-y-3" data-ir-id="motif_launch_kpis_kpi_assets" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_kpis\"]"}>
              <div id="dom-binding_kpi_assets_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_kpi_assets_label" data-content-refs={"[\"node:kpi_assets#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:kpi_assets_label\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_assets_label"] ?? "Demo assets"}
              </div>
              <div id="dom-binding_kpi_assets_value" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_kpi_assets_value" data-content-refs={"[\"node:kpi_assets#attr:value\"]"} data-intent-refs={"[\"viewspec:binding:kpi_assets_value\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_assets_value"] ?? "7"}
              </div>
            </div>
            <div id="dom-motif_launch_kpis_kpi_blockers" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm space-y-3" data-ir-id="motif_launch_kpis_kpi_blockers" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_kpis\"]"}>
              <div id="dom-binding_kpi_blockers_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_kpi_blockers_label" data-content-refs={"[\"node:kpi_blockers#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:kpi_blockers_label\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_blockers_label"] ?? "Blockers"}
              </div>
              <div id="dom-binding_kpi_blockers_value" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_kpi_blockers_value" data-content-refs={"[\"node:kpi_blockers#attr:value\"]"} data-intent-refs={"[\"viewspec:binding:kpi_blockers_value\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["kpi_blockers_value"] ?? "1"}
              </div>
            </div>
          </div>
        </div>
        <div id="dom-region_status" className="flex flex-col gap-3" data-ir-id="region_status" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:region:status\"]"}>
          <div id="dom-motif_launch_status_table" className="flex flex-col gap-3" data-ir-id="motif_launch_status_table" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_status_table\", \"viewspec:style:table_surface\"]"} style={{ background: "#e6fffb", border: "1px solid #10b981", borderRadius: "14px" } as React.CSSProperties}>
            <div id="dom-motif_launch_status_table_row_html" className="flex flex-row flex-wrap gap-3" data-ir-id="motif_launch_status_table_row_html" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_status_table\"]"}>
              <div id="dom-binding_row_html_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_row_html_label" data-content-refs={"[\"node:row_html#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:row_html_label\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_html_label"] ?? "HTML/Tailwind"}
              </div>
              <div id="dom-binding_row_html_state" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_html_state" data-content-refs={"[\"node:row_html#attr:state\"]"} data-intent-refs={"[\"viewspec:binding:row_html_state\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_html_state"] ?? "Live link ready"}
              </div>
              <div id="dom-binding_row_html_owner" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_html_owner" data-content-refs={"[\"node:row_html#attr:owner\"]"} data-intent-refs={"[\"viewspec:binding:row_html_owner\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_html_owner"] ?? "web"}
              </div>
            </div>
            <div id="dom-motif_launch_status_table_row_react" className="flex flex-row flex-wrap gap-3" data-ir-id="motif_launch_status_table_row_react" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_status_table\"]"}>
              <div id="dom-binding_row_react_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_row_react_label" data-content-refs={"[\"node:row_react#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:row_react_label\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_react_label"] ?? "React TSX"}
              </div>
              <div id="dom-binding_row_react_state" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_react_state" data-content-refs={"[\"node:row_react#attr:state\"]"} data-intent-refs={"[\"viewspec:binding:row_react_state\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_react_state"] ?? "Runtime page ready"}
              </div>
              <div id="dom-binding_row_react_owner" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_react_owner" data-content-refs={"[\"node:row_react#attr:owner\"]"} data-intent-refs={"[\"viewspec:binding:row_react_owner\"]"} style={{ color: "#0f766e", textShadow: "0 0 18px rgba(20, 184, 166, 0.22)", opacity: "0.92" } as React.CSSProperties}>
                {data["row_react_owner"] ?? "web"}
              </div>
            </div>
            <div id="dom-motif_launch_status_table_row_swiftui" className="flex flex-row flex-wrap gap-3" data-ir-id="motif_launch_status_table_row_swiftui" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_status_table\"]"}>
              <div id="dom-binding_row_swiftui_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_row_swiftui_label" data-content-refs={"[\"node:row_swiftui#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:row_swiftui_label\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_swiftui_label"] ?? "SwiftUI"}
              </div>
              <div id="dom-binding_row_swiftui_state" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_swiftui_state" data-content-refs={"[\"node:row_swiftui#attr:state\"]"} data-intent-refs={"[\"viewspec:binding:row_swiftui_state\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_swiftui_state"] ?? "External simulator recording"}
              </div>
              <div id="dom-binding_row_swiftui_owner" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_swiftui_owner" data-content-refs={"[\"node:row_swiftui#attr:owner\"]"} data-intent-refs={"[\"viewspec:binding:row_swiftui_owner\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_swiftui_owner"] ?? "mobile"}
              </div>
            </div>
            <div id="dom-motif_launch_status_table_row_flutter" className="flex flex-row flex-wrap gap-3" data-ir-id="motif_launch_status_table_row_flutter" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:motif:launch_status_table\"]"}>
              <div id="dom-binding_row_flutter_label" className="text-xs font-bold uppercase tracking-widest text-slate-500" data-ir-id="binding_row_flutter_label" data-content-refs={"[\"node:row_flutter#attr:label\"]"} data-intent-refs={"[\"viewspec:binding:row_flutter_label\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_flutter_label"] ?? "Flutter"}
              </div>
              <div id="dom-binding_row_flutter_state" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_flutter_state" data-content-refs={"[\"node:row_flutter#attr:state\"]"} data-intent-refs={"[\"viewspec:binding:row_flutter_state\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_flutter_state"] ?? "External emulator recording"}
              </div>
              <div id="dom-binding_row_flutter_owner" className="text-2xl font-black tracking-tight text-slate-950" data-ir-id="binding_row_flutter_owner" data-content-refs={"[\"node:row_flutter#attr:owner\"]"} data-intent-refs={"[\"viewspec:binding:row_flutter_owner\"]"} style={{ opacity: "0.92" } as React.CSSProperties}>
                {data["row_flutter_owner"] ?? "mobile"}
              </div>
            </div>
          </div>
        </div>
        <div id="dom-region_form" className="flex flex-col gap-3" data-ir-id="region_form" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:region:form\", \"viewspec:style:form_density\"]"} style={{ gap: "1rem", padding: "0.9rem 1.05rem" } as React.CSSProperties}>
          {evaluateRule("show_mobile_note", inputValues, data) && (
            <div id="dom-binding_mobile_recording_note" className="text-base leading-[var(--vs-readable-line-height,1.75rem)] text-slate-800" data-ir-id="binding_mobile_recording_note" data-content-refs={"[\"node:mobile_note#attr:text\"]"} data-intent-refs={"[\"viewspec:binding:mobile_recording_note\", \"viewspec:rule:show_mobile_note\", \"viewspec:style:mobile_note_warning\"]"} style={{ opacity: "0.92", color: "#b45309", borderColor: "#f59e0b", boxShadow: "inset 0 0 0 1px rgba(245, 158, 11, 0.28)" } as React.CSSProperties}>
              {data["mobile_recording_note"] ?? "Mobile runtime recording is handed off because this Windows workspace has no simulator tooling."}
            </div>
          )}
          <label id="dom-input_phase_filter" className="flex w-full max-w-sm flex-col gap-1.5 text-sm font-semibold text-slate-700" data-ir-id="input_phase_filter" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:input:phase_filter\"]"}>
            <span>Phase filter</span>
            <select className="rounded-lg border border-slate-300 px-3 py-2 font-normal text-slate-900" name={"phase_filter"} data-input-id={"phase_filter"} data-ir-id={"input_phase_filter"} value={String(inputValues["phase_filter"] ?? "")} onChange={(event) => setInputValue("phase_filter", event.target.value)}>
              <option key={"Demos"} value={"Demos"}>Demos</option>
              <option key={"Docs"} value={"Docs"}>Docs</option>
              <option key={"Mobile"} value={"Mobile"}>Mobile</option>
              <option key={"Launch"} value={"Launch"}>Launch</option>
            </select>
          </label>
          <label id="dom-input_owner_email" className="flex w-full max-w-sm flex-col gap-1.5 text-sm font-semibold text-slate-700" data-ir-id="input_owner_email" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:input:owner_email\"]"}>
            <span>Owner email</span>
            <input className="rounded-lg border border-slate-300 px-3 py-2 font-normal text-slate-900" type="text" name={"owner_email"} data-input-id={"owner_email"} data-ir-id={"input_owner_email"} value={String(inputValues["owner_email"] ?? "")} onChange={(event) => setInputValue("owner_email", event.target.value)} />
          </label>
          <label id="dom-input_include_mobile" className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-slate-700" data-ir-id="input_include_mobile" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:input:include_mobile\"]"}>
            <input className="h-4 w-4 rounded border-slate-300 text-teal-700" type="checkbox" name={"include_mobile"} data-input-id={"include_mobile"} data-ir-id={"input_include_mobile"} checked={Boolean(inputValues["include_mobile"])} onChange={(event) => setInputValue("include_mobile", event.target.checked)} />
            <span>Include mobile handoff</span>
          </label>
          <button id="dom-action_save_launch_review" className="inline-flex w-fit items-center rounded-xl bg-teal-700 min-h-[var(--vs-touch-target,2.5rem)] px-[var(--vs-button-px,1rem)] py-[var(--vs-button-py,0.5rem)] text-sm font-bold text-white shadow-sm hover:bg-teal-800" data-ir-id="action_save_launch_review" data-content-refs={"[]"} data-intent-refs={"[\"viewspec:action:save_launch_review\"]"} data-action-id="save_launch_review" data-action-kind="submit" data-action-target-ref="/launch-review" data-payload-bindings={"[\"phase_filter\", \"owner_email\", \"include_mobile\", \"launch_status\", \"kpi_blockers_value\"]"} data-payload-values={"{\"kpi_blockers_value\": \"1\", \"launch_status\": \"On track\"}"} onClick={() => onAction?.({ id: "save_launch_review", kind: "submit", targetRef: "/launch-review", payloadBindings: ["phase_filter", "owner_email", "include_mobile", "launch_status", "kpi_blockers_value"], payload: collectPayload(["phase_filter", "owner_email", "include_mobile", "launch_status", "kpi_blockers_value"]) })} type="button">Save launch review</button>
        </div>
      </main>
  );
}

export default ViewSpecView;
